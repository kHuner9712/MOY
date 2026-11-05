"""任务与步骤仓储 · 内存实现（development / test）。

内存后端就是生产语义的**可执行规格**：G-38 门禁逐条比对本文件与 `postgres.py` 的
公开方法集、签名与 record 构造点，任何一侧单独改动都会被判红。因此本文件的每个方法
都必须与 PG 侧同名方法**保持同形**（参数名、默认值、同步性），差异只能通过
`TEST_HOOKS`（`clear`）这类显式白名单存在。

`task_stats` 的两处构造形状（`TaskStats()` 空窗口 / 满字段）**必须与 PG 的
`_to_task_stats` 逐形状一致**：R4 按"每侧构造点形状集合相等"判定，任何一侧多出一种
"少了某些字段"的形状，就意味着那条路径上字段恒为默认值（P1-8b 就是这么来的）。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace
from typing import Any

from app.core.errors import ApiError, ErrorCode
from app.db.repositories.base import as_datetime
from app.db.repositories.task.records import (
    QUEUE_SCAN_LIMIT,
    STATUS_WAITING_PLAN,
    STEP_MUTABLE_FIELDS,
    TASK_MUTABLE_FIELDS,
    StepRecord,
    TaskRecord,
    TaskStats,
    _expires_at,
    normalize_expected_status,
    now_iso,
)


class InMemoryTaskStore:
    """进程内任务存储（development / test）。"""

    def __init__(self) -> None:
        """初始化空存储。"""
        self._tasks: dict[tuple[str, str], TaskRecord] = {}
        self._steps: dict[tuple[str, str], StepRecord] = {}
        self._seq: dict[tuple[str, str], dict[int, str]] = {}

    async def create_task(self, record: TaskRecord) -> TaskRecord:
        """插入任务行。

        Args:
            record: 任务行。

        Returns:
            TaskRecord: 落库后的任务行。
        """
        payload = replace(record, started_at=record.started_at or now_iso())
        if not payload.expires_at:
            payload.expires_at = _expires_at()
        self._tasks[(record.tenant_id, record.task_id)] = payload
        return payload

    async def get_task(self, tenant_id: str, task_id: str) -> TaskRecord | None:
        """读取任务行。

        Args:
            tenant_id: 企业 ID。
            task_id: 任务 ID。

        Returns:
            TaskRecord | None: 任务行。
        """
        return self._tasks.get((tenant_id, task_id))

    async def find_by_idem(
        self, tenant_id: str, plugin_id: str, idem_key: str
    ) -> TaskRecord | None:
        """按幂等键查任务。

        Args:
            tenant_id: 企业 ID。
            plugin_id: 插件标识。
            idem_key: 幂等键。

        Returns:
            TaskRecord | None: 命中的任务。
        """
        if not idem_key:
            return None
        for record in self._tasks.values():
            if (
                record.tenant_id == tenant_id
                and record.plugin_id == plugin_id
                and record.idem_key == idem_key
            ):
                return record
        return None

    async def list_tasks(
        self,
        tenant_id: str,
        *,
        plugin_id: str = "",
        status: str = "",
        initiator_id: str = "",
        created_from: str = "",
        created_to: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[TaskRecord], int]:
        """分页列出任务。

        Args:
            tenant_id: 企业 ID。
            plugin_id: 插件筛选。
            status: 状态筛选。
            initiator_id: 发起人筛选。
            created_from: 起始时间（ISO）。
            created_to: 结束时间（ISO）。
            page: 页码。
            page_size: 每页条数。

        Returns:
            tuple[list[TaskRecord], int]: `(items, total)`。
        """
        items = [record for (tid, _), record in self._tasks.items() if tid == tenant_id]
        if plugin_id:
            items = [item for item in items if item.plugin_id == plugin_id]
        if status:
            items = [item for item in items if item.status == status]
        if initiator_id:
            items = [item for item in items if item.initiator_id == initiator_id]
        if created_from:
            items = [item for item in items if item.started_at >= created_from]
        if created_to:
            items = [item for item in items if item.started_at <= created_to]
        items.sort(key=lambda item: item.started_at, reverse=True)
        total = len(items)
        start = max(0, (page - 1) * page_size)
        return items[start : start + page_size], total

    async def update_task(
        self,
        tenant_id: str,
        task_id: str,
        *,
        expected_status: str | Sequence[str] | None = None,
        expected_plan_version: int | None = None,
        **fields: Any,
    ) -> TaskRecord | None:
        """更新任务可变字段（可带乐观状态条件）。

        `expected_status` 的语义必须与 PG 实现**完全一致**（`status = ANY(...)`）：
        条件不满足时**什么都不写**并返回 `None`（PG 侧 `RETURNING` 无行即 `None`）。
        这里没有 await 夹在"判断"与"写入"之间，因此在事件循环内是原子的；
        PG 侧由单条 `UPDATE ... WHERE status = ANY(...)` 保证原子。

        Args:
            tenant_id: 企业 ID。
            task_id: 任务 ID。
            expected_status: 乐观条件（期望的当前状态；`None` 表示不加条件）。
            expected_plan_version: 计划版本乐观条件（`None` 表示不加条件）。
            **fields: 待更新字段（受白名单约束）。

        Returns:
            TaskRecord | None: 更新后的任务行；条件不满足或任务不存在时返回 `None`。

        Raises:
            ApiError: 字段不在白名单（30003）。
        """
        current = self._tasks.get((tenant_id, task_id))
        if current is None:
            return None
        for key in fields:
            if key not in TASK_MUTABLE_FIELDS:
                raise ApiError(ErrorCode.INTERNAL_ERROR, internal_detail=f"task field {key}")
        allowed = normalize_expected_status(expected_status)
        if allowed is not None and current.status not in allowed:
            return None
        if expected_plan_version is not None and current.plan_version != expected_plan_version:
            return None
        for key, value in fields.items():
            setattr(current, key, value)
        return current

    async def list_expired_plans(
        self, tenant_id: str, before: str, limit: int = 200
    ) -> list[TaskRecord]:
        """列出已过期、仍停在 `waiting_plan` 的任务（与 PG 同语义）。

        Args:
            tenant_id: 企业 ID。
            before: 截止时间（ISO 8601）。
            limit: 单批上限。

        Returns:
            list[TaskRecord]: 过期计划任务行（按截止时间升序）。
        """
        items = [
            record
            for record in self._tasks.values()
            if record.tenant_id == tenant_id
            and record.status == STATUS_WAITING_PLAN
            and record.plan_expires_at
            and record.plan_expires_at < before
        ]
        items.sort(key=lambda item: item.plan_expires_at)
        return items[:limit]

    async def count_occupying(self, tenant_id: str, plugin_id: str) -> int:
        """统计占用额度的任务数。

        Args:
            tenant_id: 企业 ID。
            plugin_id: 插件标识。

        Returns:
            int: 任务数。
        """
        return sum(
            1
            for record in self._tasks.values()
            if record.tenant_id == tenant_id and record.plugin_id == plugin_id and record.occupying
        )

    async def clear_hold_reasons(self, tenant_id: str) -> int:
        """按租户批量清空额度挂起标记（懒恢复）。

        与 PG 实现同语义：只清本站租户、只清非空行，返回被清条数。
        内存实现直接改原对象（与 `update_task` 的 `setattr` 一致），
        不复制记录——复制会让同一 task_id 出现两个快照，推进方拿到旧的那个。

        Args:
            tenant_id: 企业 ID。

        Returns:
            int: 被清空的任务条数。
        """
        cleared = 0
        for record in self._tasks.values():
            if record.tenant_id == tenant_id and record.hold_reason:
                record.hold_reason = ""
                cleared += 1
        return cleared

    async def counts_occupying(self, tenant_id: str, plugin_ids: Sequence[str]) -> dict[str, int]:
        """批量统计各插件占用额度的任务数。

        Args:
            tenant_id: 企业 ID。
            plugin_ids: 插件标识列表。

        Returns:
            dict[str, int]: 插件标识 → 任务数（只含非零项）。
        """
        wanted = set(plugin_ids)
        if not wanted:
            return {}
        counts: dict[str, int] = {}
        for record in self._tasks.values():
            if record.tenant_id != tenant_id or not record.occupying:
                continue
            if record.plugin_id not in wanted:
                continue
            counts[record.plugin_id] = counts.get(record.plugin_id, 0) + 1
        return counts

    async def count_queued(self, tenant_id: str, plugin_id: str) -> int:
        """统计排队任务数。

        Args:
            tenant_id: 企业 ID。
            plugin_id: 插件标识。

        Returns:
            int: 任务数。
        """
        return sum(
            1
            for record in self._tasks.values()
            if record.tenant_id == tenant_id
            and record.plugin_id == plugin_id
            and record.status == "queued"
        )

    async def next_queued(self, tenant_id: str, plugin_id: str) -> TaskRecord | None:
        """取最早排队的任务。

        Args:
            tenant_id: 企业 ID。
            plugin_id: 插件标识。

        Returns:
            TaskRecord | None: 任务行。
        """
        queued = await self.list_queued(tenant_id, plugin_id, limit=1)
        return queued[0] if queued else None

    async def list_queued(
        self, tenant_id: str, plugin_id: str, *, limit: int = QUEUE_SCAN_LIMIT
    ) -> list[TaskRecord]:
        """列出排队任务（按位次升序）。

        Args:
            tenant_id: 企业 ID。
            plugin_id: 插件标识。
            limit: 单次读取上限（与 PG 侧 `LIMIT` 同口径，审计 2026-11-03 QA-03）。

        Returns:
            list[TaskRecord]: 排队任务（最多 `limit` 条）。
        """
        queued = [
            record
            for record in self._tasks.values()
            if record.tenant_id == tenant_id
            and record.plugin_id == plugin_id
            and record.status == "queued"
        ]
        queued.sort(key=lambda item: (item.queue_position or 1_000, item.started_at))
        return queued[: max(1, int(limit))]

    async def create_steps(self, records: Iterable[StepRecord]) -> list[StepRecord]:
        """批量插入步骤骨架。

        Args:
            records: 步骤行列表。

        Returns:
            list[StepRecord]: 落库后的步骤行。
        """
        created: list[StepRecord] = []
        for record in records:
            self._steps[(record.tenant_id, record.step_id)] = record
            self._seq.setdefault((record.tenant_id, record.task_id), {})[record.seq] = (
                record.step_id
            )
            created.append(record)
        return created

    async def list_steps(self, tenant_id: str, task_id: str) -> list[StepRecord]:
        """列出任务步骤。

        Args:
            tenant_id: 企业 ID。
            task_id: 任务 ID。

        Returns:
            list[StepRecord]: 步骤列表（按 seq）。
        """
        mapping = self._seq.get((tenant_id, task_id), {})
        items = [self._steps[(tenant_id, mapping[seq])] for seq in sorted(mapping)]
        return [item for item in items if item is not None]

    async def update_step(self, tenant_id: str, step_id: str, **fields: Any) -> StepRecord | None:
        """更新步骤可变字段。

        Args:
            tenant_id: 企业 ID。
            step_id: 步骤 ID。
            **fields: 待更新字段。

        Returns:
            StepRecord | None: 更新后的步骤行。

        Raises:
            ApiError: 字段不在白名单（30003）。
        """
        current = self._steps.get((tenant_id, step_id))
        if current is None:
            return None
        for key, value in fields.items():
            if key not in STEP_MUTABLE_FIELDS:
                raise ApiError(ErrorCode.INTERNAL_ERROR, internal_detail=f"step field {key}")
            setattr(current, key, value)
        return current

    async def reset_steps_from(self, tenant_id: str, task_id: str, seq: int) -> int:
        """把 `seq` 及之后的步骤重置为 `pending`。

        Args:
            tenant_id: 企业 ID。
            task_id: 任务 ID。
            seq: 起始序号（含）。

        Returns:
            int: 重置条数。
        """
        count = 0
        for step in await self.list_steps(tenant_id, task_id):
            if step.seq >= seq:
                step.status = "pending"
                step.attempt = 0
                step.retryable = False
                step.reason_text = ""
                step.operator_action = ""
                step.output_summary = ""
                step.started_at = ""
                step.finished_at = ""
                count += 1
        return count

    def clear(self) -> None:
        """清空存储（测试隔离）。"""
        self._tasks.clear()
        self._steps.clear()
        self._seq.clear()

    async def task_stats(self, tenant_id: str, since: str, until: str) -> TaskStats:
        """窗口内任务聚合（公式与 PG 聚合 SQL 逐条对应，AC-A7）。

        窗口比较用 `as_datetime()` 解析后比较——**不依赖 ISO 字符串格式**，
        避免测试造数格式不一致导致静默错算；`started_at` 为空的记录不进窗口。
        剔 retry 的口径：`active_users` 与 `repeat_users` 都不计
        `trigger_type == 'retry'` 的任务（`total`/各状态计数照常计）。

        **两种构造形状是本方法的契约**：空窗口返回 `TaskStats()`，非空窗口返回满字段
        `TaskStats(...)`——与 PG `_to_task_stats` 的两条返回路径逐一对应。G-38 R4 按
        "每侧构造点形状集合相等"判定，少写一条形状就等于某条路径上的字段恒为默认值
        （P1-8b 的成因），因此这里宁可显式写全 9 个字段，也不用"零值构造后逐字段累加"
        （那种写法只有空窗口形状，与 PG 侧的两种形状对不上）。

        Args:
            tenant_id: 企业 ID。
            since: 窗口起点（ISO 8601，含）。
            until: 窗口终点（ISO 8601，不含）。

        Returns:
            TaskStats: 原始聚合值；空窗口全 0。
        """
        window_start = as_datetime(since)
        window_end = as_datetime(until)
        scoped: list[TaskRecord] = []
        if window_start is not None and window_end is not None:
            for record in self._tasks.values():
                if record.tenant_id != tenant_id:
                    continue
                started = as_datetime(record.started_at)
                if started is None:
                    continue
                if window_start <= started < window_end:
                    scoped.append(record)
        if not scoped:
            return TaskStats()

        completed = 0
        failed = 0
        cancelled = 0
        units_sum = 0
        durations: list[float] = []
        for record in scoped:
            if record.status == "completed":
                completed += 1
                units_sum += record.usage_units
                seconds = _elapsed_seconds(record.started_at, record.finished_at)
                if seconds is not None:
                    durations.append(seconds)
            elif record.status == "failed":
                failed += 1
            elif record.status == "cancelled" and record.cancel_requested:
                # 只数用户主动取消；系统取消（cancel_requested=false）留在完成率分母
                cancelled += 1

        non_retry = [record for record in scoped if record.trigger_type != "retry"]
        per_plugin: dict[tuple[str, str], int] = {}
        for record in non_retry:
            key = (record.initiator_id, record.plugin_id)
            per_plugin[key] = per_plugin.get(key, 0) + 1
        return TaskStats(
            total=len(scoped),
            completed=completed,
            failed=failed,
            cancelled=cancelled,
            active_users=len({record.initiator_id for record in non_retry}),
            repeat_users=len({user for (user, _plugin), count in per_plugin.items() if count >= 2}),
            duration_sum_seconds=sum(durations),
            duration_p50_seconds=_percentile(sorted(durations), 0.5),
            units_sum=units_sum,
        )


def _elapsed_seconds(started_at: str, finished_at: str) -> float | None:
    """计算任务时长（秒）；任一端时间缺失或无法解析时返回 None。

    与 PG 的 `EXTRACT(EPOCH FROM (finished_at - started_at))` +
    `finished_at IS NOT NULL` 对应：内存侧同样只统计两端齐全的任务。

    Args:
        started_at: 开始时间（ISO 8601）。
        finished_at: 结束时间（ISO 8601）。

    Returns:
        float | None: 时长（秒）；缺失返回 None。
    """
    started = as_datetime(started_at)
    finished = as_datetime(finished_at)
    if started is None or finished is None:
        return None
    return (finished - started).total_seconds()


def _percentile(sorted_values: list[float], fraction: float) -> float:
    """线性插值分位（与 PG `percentile_cont` 同口径）。

    公式：`position = (n − 1) × fraction`，在 `floor(position)` 与
    `ceil(position)` 两个相邻值之间按小数部分线性插值。空列表回 0.0
    （PG 侧由 `COALESCE(..., 0)` 兜底，两端一致）。

    Args:
        sorted_values: **升序**排列的数值列表（调用方负责排序）。
        fraction: 分位点（0..1，如 0.5 即 P50）。

    Returns:
        float: 分位值；空列表返回 0.0。
    """
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = (len(sorted_values) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = position - lower
    return float(sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight)


__all__: list[str] = ["InMemoryTaskStore"]
