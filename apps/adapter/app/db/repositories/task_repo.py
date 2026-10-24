# ruff: noqa: S608 - 表名/列名均为模块常量（经 assert_identifier 校验），值一律命名参数绑定。
"""插件任务与步骤仓储（阶段三 B19，`architecture-phase3.md` §4.1 / §4.4 / D-22）。

**为什么任务要本地持久化**（D-22）：A 域在阶段一只是底座的代理，任务状态在底座；
阶段三的插件任务由平台声明式推进，底座不再是唯一真相源。若仍以底座为准，
"确认后继续同一任务""重试本步不重跑前序"这两条语义都无法表达。

**双后端**（沿用 `plugin_repo` 体例）：`memory` 供开发/测试，`postgres` 为生产实现，
方法签名与语义一致。

**四条与契约绑定的口径**：

1. **一步一事务**：每个步骤的写入都是独立事务（长任务短事务，§3.3 硬约定 1）。
2. **`queued` 不是错误**：并发超限的任务落 `status='queued'` + `queue_position`，
   由 `release_slot()` 在腾出额度时**自动转 `running`**（PRD E-12）。
3. **`retry_of` 只指向原任务**：`retry_step`/`retry_all` 新建任务并记来源，
   原任务状态**永不改变**（否则"重试"会改写历史，审计无法回答"第一次到底怎么了"）。
4. **`plugin_version` 是快照**：历史任务按创建时版本渲染（PRD R2 对策），
   不回读 `plugin_installation.version`——插件升级后旧任务的时间线不能变。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Protocol

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.db.repositories.base import BaseRepository, as_datetime
from app.db.session import tenant_scope

logger = get_logger(__name__)

TASK_TABLE: Final[str] = "plugin_task"
STEP_TABLE: Final[str] = "plugin_task_step"

#: 占并发额度的状态：`waiting_confirm` 也算——
#: 它停着等用户点确认，但这单执行仍然是"活的"；不计入会让用户确认后
#: 又要重新排队，体验与口径都更差（且"排队中又被叫去确认"无法解释）。
OCCUPYING_STATUSES: Final[tuple[str, ...]] = ("running", "waiting_confirm")

#: 终态（不再推进）
TERMINAL_STATUSES: Final[tuple[str, ...]] = ("completed", "failed", "cancelled")

#: 排队态（**不是错误**：并发超限时任务停在这里等额度，A1 返回 200 + queue_position）
STATUS_QUEUED: Final[str] = "queued"

#: 任务**模型层**可变字段白名单（防动态列名注入）。
#:
#: `queue_position` 在这里，但**不是** `plugin_task` 的存储列——它是派生值
#: （排队语义 = `status='queued'` + `started_at` 先来后到，见 `idx_task_status_time`）。
#: 内存后端可以直接改它（就是内存里的一个字段）；PG 端由
#: `_PG_TASK_COLUMNS` 过滤掉，读取时由 `list_queued()` 回填位次。
TASK_MUTABLE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "status",
        "progress",
        "current_step_seq",
        "queue_position",
        "result_summary",
        "checklist",
        "warnings",
        "error_message",
        "confirm_step_seq",
        "confirmed_step_seq",
        "cancel_requested",
        "usage_units",
        "finished_at",
        "inputs",
    }
)

#: 真正存在的 `plugin_task` 列（PG 端写库前用它过滤"模型有、库里没有"的字段）。
#:
#: 与 DDL 一一对应；新增列时**两处都要改**（`init-schema-phase3.sql` + 这里），
#: 否则会出现"字段能改、写库丢掉"的静默偏差。
_PG_TASK_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "status",
        "progress",
        "current_step_seq",
        "result_summary",
        "checklist",
        "warnings",
        "error_message",
        "confirm_step_seq",
        "confirmed_step_seq",
        "cancel_requested",
        "usage_units",
        "finished_at",
        "inputs",
    }
)

#: 步骤可变字段白名单
STEP_MUTABLE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "status",
        "attempt",
        "retryable",
        "output_summary",
        "reason_text",
        "operator_action",
        "tool_execution_id",
        "duration_ms",
        "started_at",
        "finished_at",
    }
)


def now_iso() -> str:
    """当前 UTC 时间（ISO 8601，带 Z）。

    Returns:
        str: 形如 `2026-11-02T09:00:00Z` 的时间戳。
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class TaskRecord:
    """任务行（`plugin_task`）。

    Attributes:
        tenant_id: 企业 ID。
        task_id: 任务 ID（`task_` 前缀，与 A 域同前缀）。
        plugin_id: 插件标识。
        plugin_version: 创建时的声明版本（**快照**，历史任务按它渲染）。
        conversation_id: 会话 ID。
        initiator_id: 发起人。
        trigger_type: `manual` / `retry`。
        status: `queued|pending|running|waiting_confirm|completed|failed|cancelled`。
        progress: 0..100。
        current_step_seq: 当前步骤序号（0 表示未开始）。
        queue_position: 排队位次（非排队时为 None）。
        inputs: 业务输入（禁凭据）。
        result_summary: 结果摘要（业务语言）。
        checklist: 核查清单。
        warnings: 未完成但不中断的提示。
        error_message: 失败原因（业务语言，禁堆栈）。
        retry_of: 重试来源任务 ID。
        from_step: 重试起点（`retry_step`）。
        confirm_step_seq: 当前待确认步骤。
        confirmed_step_seq: 最近一次被用户确认的步骤（`redo` 前用它定位起点）。
        cancel_requested: 是否已请求取消。
        usage_units: 累计额度。
        idem_key: 幂等键。
        started_at: 开始时间。
        finished_at: 结束时间。
        expires_at: 产物过期时间（180 天）。
    """

    tenant_id: str = ""
    task_id: str = ""
    plugin_id: str = ""
    plugin_version: str = ""
    conversation_id: str = ""
    initiator_id: str = ""
    trigger_type: str = "manual"
    status: str = "pending"
    progress: int = 0
    current_step_seq: int = 0
    queue_position: int | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    result_summary: str = ""
    checklist: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error_message: str = ""
    retry_of: str = ""
    from_step: int = 0
    confirm_step_seq: int = 0
    confirmed_step_seq: int = 0
    cancel_requested: bool = False
    usage_units: int = 0
    idem_key: str = ""
    started_at: str = ""
    finished_at: str = ""
    expires_at: str = ""

    @property
    def terminal(self) -> bool:
        """是否终态。

        Returns:
            bool: 终态返回 True。
        """
        return self.status in TERMINAL_STATUSES

    @property
    def occupying(self) -> bool:
        """是否占用并发额度。

        Returns:
            bool: 占用返回 True。
        """
        return self.status in OCCUPYING_STATUSES


@dataclass(slots=True)
class StepRecord:
    """步骤行（`plugin_task_step`）。

    Attributes:
        tenant_id: 企业 ID。
        step_id: 步骤 ID（`step_` 前缀）。
        task_id: 任务 ID。
        seq: 序号 1..8。
        title: 步骤名（**声明快照**，历史任务不随声明变化）。
        action: 执行原语（内部字段，不进响应）。
        status: `pending|running|completed|failed|skipped`。
        attempt: 尝试次数（0 或 1）。
        retryable: 是否可重试。
        output_summary: 输出摘要（业务语言）。
        reason_text: 失败原因（业务语言）。
        operator_action: 建议动作。
        tool_execution_id: 工具执行记录 ID（不复制明细，§3.6 第 4 条）。
        duration_ms: 耗时毫秒。
        started_at: 开始时间。
        finished_at: 结束时间。
    """

    tenant_id: str = ""
    step_id: str = ""
    task_id: str = ""
    seq: int = 0
    title: str = ""
    action: str = ""
    status: str = "pending"
    attempt: int = 0
    retryable: bool = False
    output_summary: str = ""
    reason_text: str = ""
    operator_action: str = ""
    tool_execution_id: str = ""
    duration_ms: int = 0
    started_at: str = ""
    finished_at: str = ""


@dataclass(slots=True)
class TaskStats:
    """任务域窗口聚合原始值（**不进响应契约**；比率与取整在端点层统一计算）。

    本类是 M1/M3/M4/M5 的唯一计算产物。双后端铁律（AC-A7）：字段口径与
    `PostgresTaskStore.task_stats` 的聚合 SQL **逐条对应**——

    - 窗口按 `started_at ∈ [since, until)` 归属（含期末未终态任务）；
    - `cancelled` 只数**用户主动取消**（`status='cancelled'` 且
      `cancel_requested=true`）；系统取消留在完成率分母；
    - `active_users` / `repeat_users` 剔 `trigger_type='retry'`
      （重试不改变"谁发起过"的事实）；
    - 时长只统计 `completed` 且 `finished_at` 非空的任务；
    - `duration_p50_seconds` 用线性插值，与 PG `percentile_cont(0.5)` 同口径。

    Attributes:
        total: 窗口内发起任务数（含期末未终态）。
        completed: `status='completed'` 数。
        failed: `status='failed'` 数。
        cancelled: 用户主动取消数。
        active_users: 发起 ≥1 次的去重用户数（剔 retry）。
        repeat_users: 同一插件发起 ≥2 次的去重用户数（剔 retry）。
        duration_sum_seconds: completed 任务 `(finished_at − started_at)` 合计（秒）。
        duration_p50_seconds: 同口径 P50（线性插值，秒）。
        units_sum: completed 任务 `usage_units` 合计。
    """

    total: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    active_users: int = 0
    repeat_users: int = 0
    duration_sum_seconds: float = 0.0
    duration_p50_seconds: float = 0.0
    units_sum: int = 0


class TaskStore(Protocol):
    """任务域存储契约（内存 / PostgreSQL 双实现）。"""

    async def create_task(self, record: TaskRecord) -> TaskRecord:
        """插入任务行。"""
        ...

    async def get_task(self, tenant_id: str, task_id: str) -> TaskRecord | None:
        """读取任务行。"""
        ...

    async def find_by_idem(
        self, tenant_id: str, plugin_id: str, idem_key: str
    ) -> TaskRecord | None:
        """按幂等键查任务（`uq_task_idem`）。"""
        ...

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
        """分页列出任务（返回 `(items, total)`）。"""
        ...

    async def update_task(self, tenant_id: str, task_id: str, **fields: Any) -> TaskRecord | None:
        """更新任务可变字段。"""
        ...

    async def count_occupying(self, tenant_id: str, plugin_id: str) -> int:
        """统计占用并发额度的任务数。"""
        ...

    async def count_queued(self, tenant_id: str, plugin_id: str) -> int:
        """统计排队任务数。"""
        ...

    async def next_queued(self, tenant_id: str, plugin_id: str) -> TaskRecord | None:
        """取最早排队的任务（FIFO）。"""
        ...

    async def list_queued(self, tenant_id: str, plugin_id: str) -> list[TaskRecord]:
        """列出排队任务（按位次）。"""
        ...

    async def create_steps(self, records: Iterable[StepRecord]) -> list[StepRecord]:
        """批量插入步骤骨架。"""
        ...

    async def list_steps(self, tenant_id: str, task_id: str) -> list[StepRecord]:
        """列出任务的步骤（按 seq）。"""
        ...

    async def update_step(self, tenant_id: str, step_id: str, **fields: Any) -> StepRecord | None:
        """更新步骤可变字段。"""
        ...

    async def reset_steps_from(self, tenant_id: str, task_id: str, seq: int) -> int:
        """把 `seq` 及之后的步骤重置为 `pending`（WF1 `redo` 用）。"""
        ...

    async def task_stats(self, tenant_id: str, since: str, until: str) -> TaskStats:
        """窗口内任务聚合（M1/M3/M4/M5 的唯一计算入口，内存/PG 同公式）。

        Args:
            tenant_id: 企业 ID。
            since: 窗口起点（ISO 8601，含）。
            until: 窗口终点（ISO 8601，不含）。

        Returns:
            TaskStats: 原始聚合值；空窗口全 0。
        """
        ...


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

    async def update_task(self, tenant_id: str, task_id: str, **fields: Any) -> TaskRecord | None:
        """更新任务可变字段。

        Args:
            tenant_id: 企业 ID。
            task_id: 任务 ID。
            **fields: 待更新字段（受白名单约束）。

        Returns:
            TaskRecord | None: 更新后的任务行。

        Raises:
            ApiError: 字段不在白名单（30003）。
        """
        current = self._tasks.get((tenant_id, task_id))
        if current is None:
            return None
        for key, value in fields.items():
            if key not in TASK_MUTABLE_FIELDS:
                raise ApiError(ErrorCode.INTERNAL_ERROR, internal_detail=f"task field {key}")
            setattr(current, key, value)
        return current

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

    async def count_queued(self, tenant_id: str, plugin_id: str) -> int:
        """统计排队任务数。

        Args:
            tenant_id: 企业 ID。
            plugin_id: 插件标识。

        Returns:
            int: 任务数。
        """
        return len(await self.list_queued(tenant_id, plugin_id))

    async def next_queued(self, tenant_id: str, plugin_id: str) -> TaskRecord | None:
        """取最早排队的任务。

        Args:
            tenant_id: 企业 ID。
            plugin_id: 插件标识。

        Returns:
            TaskRecord | None: 任务行。
        """
        queued = await self.list_queued(tenant_id, plugin_id)
        return queued[0] if queued else None

    async def list_queued(self, tenant_id: str, plugin_id: str) -> list[TaskRecord]:
        """列出排队任务（按位次升序）。

        Args:
            tenant_id: 企业 ID。
            plugin_id: 插件标识。

        Returns:
            list[TaskRecord]: 排队任务。
        """
        queued = [
            record
            for record in self._tasks.values()
            if record.tenant_id == tenant_id
            and record.plugin_id == plugin_id
            and record.status == "queued"
        ]
        queued.sort(key=lambda item: (item.queue_position or 1_000, item.started_at))
        return queued

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

        stats = TaskStats(total=len(scoped))
        non_retry = [record for record in scoped if record.trigger_type != "retry"]
        durations: list[float] = []
        for record in scoped:
            if record.status == "completed":
                stats.completed += 1
                stats.units_sum += record.usage_units
                seconds = _elapsed_seconds(record.started_at, record.finished_at)
                if seconds is not None:
                    durations.append(seconds)
                    stats.duration_sum_seconds += seconds
            elif record.status == "failed":
                stats.failed += 1
            elif record.status == "cancelled" and record.cancel_requested:
                # 只数用户主动取消；系统取消（cancel_requested=false）留在完成率分母
                stats.cancelled += 1

        stats.active_users = len({record.initiator_id for record in non_retry})
        per_plugin: dict[tuple[str, str], int] = {}
        for record in non_retry:
            key = (record.initiator_id, record.plugin_id)
            per_plugin[key] = per_plugin.get(key, 0) + 1
        stats.repeat_users = len(
            {user for (user, _plugin), count in per_plugin.items() if count >= 2}
        )
        stats.duration_p50_seconds = _percentile(sorted(durations), 0.5)
        return stats


class _TaskRepository(BaseRepository):
    """任务表仓储基类（统一表名）。

    `table` **必须显式声明**：`BaseRepository.__init__` 会对它跑
    `assert_identifier()`，空表名直接抛 `30003`——而这一步在**任何 SQL 执行之前**，
    所以漏声明时整个 `PostgresTaskStore` 的每个方法都失败，且被包装成
    "系统繁忙，请稍后再试"，现场完全看不出是表名问题（B21c 实测）。
    """

    table = TASK_TABLE


class _StepRepository(BaseRepository):
    """步骤表仓储基类（统一表名）。"""

    table = STEP_TABLE


class PostgresTaskStore:
    """PostgreSQL 任务存储（生产实现）。"""

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化。

        Args:
            settings: 可选配置实例。
        """
        self._settings = settings or get_settings()

    async def create_task(self, record: TaskRecord) -> TaskRecord:
        """插入任务行。

        Args:
            record: 任务行。

        Returns:
            TaskRecord: 落库后的任务行。
        """
        async with tenant_scope(record.tenant_id, settings=self._settings) as uow:
            repo = _TaskRepository(uow)
            row = await repo.fetch_one(
                f"""
                INSERT INTO {TASK_TABLE} (
                    tenant_id, task_id, plugin_id, plugin_version, conversation_id,
                    initiator_id, trigger_type, status, progress, current_step_seq,
                    inputs, result_summary, checklist, warnings, error_message,
                    retry_of, from_step, confirm_step_seq, confirmed_step_seq,
                    cancel_requested, usage_units, idem_key, started_at, expires_at
                ) VALUES (
                    :tenant_id, :task_id, :plugin_id, :plugin_version, :conversation_id,
                    :initiator_id, :trigger_type, :status, :progress, :current_step_seq,
                    CAST(:inputs AS jsonb), :result_summary,
                    CAST(:checklist AS jsonb), CAST(:warnings AS jsonb), NULLIF(:error_message, ''),
                    NULLIF(:retry_of, ''), NULLIF(:from_step, 0), :confirm_step_seq,
                    :confirmed_step_seq, :cancel_requested, :usage_units, :idem_key,
                    COALESCE(:started_at, now()), :expires_at
                )
                RETURNING *
                """,
                repo.params(**_task_params(record)),
            )
        if row is None:  # pragma: no cover - RETURNING 缺失属数据库异常
            raise ApiError(ErrorCode.INTERNAL_ERROR, internal_detail="task insert lost")
        created = _to_task(row)
        if created.status == STATUS_QUEUED:
            # `queue_position` 不是存储列（见 TASK_MUTABLE_FIELDS 注释），排队位次
            # 由 `started_at` 先来后到**派生**：落库后按同一口径回填，保证
            # A1 返回给前端的 `queue_position` 与 `list_queued` 看到的一致。
            created.queue_position = await self._queue_rank(record.tenant_id, record.plugin_id, row)
        return created

    async def get_task(self, tenant_id: str, task_id: str) -> TaskRecord | None:
        """读取任务行。

        Args:
            tenant_id: 企业 ID。
            task_id: 任务 ID。

        Returns:
            TaskRecord | None: 任务行。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _TaskRepository(uow)
            row = await repo.fetch_one(
                f"SELECT * FROM {TASK_TABLE} WHERE tenant_id = :tenant_id AND task_id = :task_id",
                repo.params(task_id=task_id),
            )
        return _to_task(row) if row is not None else None

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
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _TaskRepository(uow)
            row = await repo.fetch_one(
                f"""
                SELECT * FROM {TASK_TABLE}
                 WHERE tenant_id = :tenant_id AND plugin_id = :plugin_id AND idem_key = :idem_key
                """,
                repo.params(plugin_id=plugin_id, idem_key=idem_key),
            )
        return _to_task(row) if row is not None else None

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
            created_from: 起始时间。
            created_to: 结束时间。
            page: 页码。
            page_size: 每页条数。

        Returns:
            tuple[list[TaskRecord], int]: `(items, total)`。
        """
        clause, params = _task_filters(
            plugin_id=plugin_id,
            status=status,
            initiator_id=initiator_id,
            created_from=created_from,
            created_to=created_to,
        )
        offset = max(0, (page - 1) * page_size)
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _TaskRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT * FROM {TASK_TABLE}
                 WHERE tenant_id = :tenant_id {clause}
                 ORDER BY started_at DESC
                 LIMIT :limit OFFSET :offset
                """,
                repo.params(**params, limit=page_size, offset=offset),
            )
            total = await repo.fetch_scalar(
                f"SELECT count(*) FROM {TASK_TABLE} WHERE tenant_id = :tenant_id {clause}",
                repo.params(**params),
            )
        return [_to_task(row) for row in rows], int(total or 0)

    async def update_task(self, tenant_id: str, task_id: str, **fields: Any) -> TaskRecord | None:
        """更新任务可变字段。

        Args:
            tenant_id: 企业 ID。
            task_id: 任务 ID。
            **fields: 待更新字段。

        Returns:
            TaskRecord | None: 更新后的任务行。

        Raises:
            ApiError: 字段不在白名单（30003）。
        """
        assignments: list[str] = []
        params: dict[str, Any] = {}
        for key, value in fields.items():
            if key not in TASK_MUTABLE_FIELDS:
                raise ApiError(ErrorCode.INTERNAL_ERROR, internal_detail=f"task field {key}")
            if key not in _PG_TASK_COLUMNS:
                # 模型层有、库里没有的字段（目前只有 `queue_position`）：**静默跳过**，
                # 读取时由 `list_queued()` 派生。抛错会让"释放额度后重排位次"
                # 这条正常业务路径在 PG 上直接失败。
                continue
            if key in {"checklist", "warnings", "inputs"}:
                assignments.append(f"{key} = CAST(:{key} AS jsonb)")
                params[key] = json.dumps(value, ensure_ascii=False)
            elif key == "finished_at":
                assignments.append("finished_at = :finished_at")
                params[key] = as_datetime(value)
            else:
                assignments.append(f"{key} = :{key}")
                params[key] = value
        if not assignments:
            return await self.get_task(tenant_id, task_id)
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _TaskRepository(uow)
            row = await repo.fetch_one(
                f"""
                UPDATE {TASK_TABLE} SET {", ".join(assignments)}
                 WHERE tenant_id = :tenant_id AND task_id = :task_id
                RETURNING *
                """,
                repo.params(task_id=task_id, **params),
            )
        return _to_task(row) if row is not None else None

    async def count_occupying(self, tenant_id: str, plugin_id: str) -> int:
        """统计占用额度的任务数。

        Args:
            tenant_id: 企业 ID。
            plugin_id: 插件标识。

        Returns:
            int: 任务数。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _TaskRepository(uow)
            total = await repo.fetch_scalar(
                f"""
                SELECT count(*) FROM {TASK_TABLE}
                 WHERE tenant_id = :tenant_id AND plugin_id = :plugin_id
                   AND status = ANY(:statuses)
                """,
                repo.params(plugin_id=plugin_id, statuses=list(OCCUPYING_STATUSES)),
            )
        return int(total or 0)

    async def count_queued(self, tenant_id: str, plugin_id: str) -> int:
        """统计排队任务数。

        Args:
            tenant_id: 企业 ID。
            plugin_id: 插件标识。

        Returns:
            int: 任务数。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _TaskRepository(uow)
            total = await repo.fetch_scalar(
                f"""
                SELECT count(*) FROM {TASK_TABLE}
                 WHERE tenant_id = :tenant_id AND plugin_id = :plugin_id AND status = 'queued'
                """,
                repo.params(plugin_id=plugin_id),
            )
        return int(total or 0)

    async def next_queued(self, tenant_id: str, plugin_id: str) -> TaskRecord | None:
        """取最早排队的任务。

        Args:
            tenant_id: 企业 ID。
            plugin_id: 插件标识。

        Returns:
            TaskRecord | None: 任务行。
        """
        queued = await self.list_queued(tenant_id, plugin_id)
        return queued[0] if queued else None

    async def list_queued(self, tenant_id: str, plugin_id: str) -> list[TaskRecord]:
        """列出排队任务（位次**派生**，不读存储列）。

        `queue_position` 不是 `plugin_task` 的列：排队语义是
        「`status='queued'` + 按 `started_at` 先来后到」。因此在取回后按顺序
        回填 `1..n`，与 `InMemoryTaskStore` 的口径一致，也让 A1 返回给前端的
        `queue_position` 有确定来源。

        Args:
            tenant_id: 企业 ID。
            plugin_id: 插件标识。

        Returns:
            list[TaskRecord]: 排队任务（按位次，`queue_position` 已回填）。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _TaskRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT * FROM {TASK_TABLE}
                 WHERE tenant_id = :tenant_id AND plugin_id = :plugin_id
                   AND status = '{STATUS_QUEUED}'
                 ORDER BY started_at, task_id
                """,
                repo.params(plugin_id=plugin_id),
            )
        queued = [_to_task(row) for row in rows]
        for index, record in enumerate(queued, start=1):
            record.queue_position = index
        return queued

    async def _queue_rank(self, tenant_id: str, plugin_id: str, row: Mapping[str, Any]) -> int:
        """取某个排队任务在队列里的位次（1 起）。

        Args:
            tenant_id: 企业 ID。
            plugin_id: 插件标识。
            row: 刚插入的任务行（至少含 `task_id`）。

        Returns:
            int: 位次；该任务已不在队列里时回落到队列长度（至少 1）。
        """
        target = str(row["task_id"])
        queued = await self.list_queued(tenant_id, plugin_id)
        for index, record in enumerate(queued, start=1):
            if record.task_id == target:
                return index
        return max(1, len(queued))

    async def create_steps(self, records: Iterable[StepRecord]) -> list[StepRecord]:
        """批量插入步骤骨架。

        Args:
            records: 步骤行列表。

        Returns:
            list[StepRecord]: 落库后的步骤行。
        """
        created: list[StepRecord] = []
        for record in records:
            async with tenant_scope(record.tenant_id, settings=self._settings) as uow:
                repo = _StepRepository(uow)
                row = await repo.fetch_one(
                    f"""
                    INSERT INTO {STEP_TABLE} (
                        tenant_id, step_id, task_id, seq, title, action, status, attempt,
                        retryable, output_summary, reason_text, operator_action
                    ) VALUES (
                        :tenant_id, :step_id, :task_id, :seq, :title, :action, :status, :attempt,
                        :retryable, :output_summary, :reason_text, :operator_action
                    )
                    RETURNING *
                    """,
                    repo.params(
                        step_id=record.step_id,
                        task_id=record.task_id,
                        seq=record.seq,
                        title=record.title,
                        action=record.action,
                        status=record.status,
                        attempt=record.attempt,
                        retryable=record.retryable,
                        output_summary=record.output_summary,
                        reason_text=record.reason_text,
                        operator_action=record.operator_action,
                    ),
                )
            if row is not None:
                created.append(_to_step(row))
        return created

    async def list_steps(self, tenant_id: str, task_id: str) -> list[StepRecord]:
        """列出任务步骤。

        Args:
            tenant_id: 企业 ID。
            task_id: 任务 ID。

        Returns:
            list[StepRecord]: 步骤列表。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _StepRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT * FROM {STEP_TABLE}
                 WHERE tenant_id = :tenant_id AND task_id = :task_id
                 ORDER BY seq
                """,
                repo.params(task_id=task_id),
            )
        return [_to_step(row) for row in rows]

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
        assignments: list[str] = []
        params: dict[str, Any] = {}
        for key, value in fields.items():
            if key not in STEP_MUTABLE_FIELDS:
                raise ApiError(ErrorCode.INTERNAL_ERROR, internal_detail=f"step field {key}")
            if key in {"started_at", "finished_at"}:
                assignments.append(f"{key} = :{key}")
                params[key] = as_datetime(value)
                continue
            elif key in {"reason_text", "operator_action"}:
                # 这两列在 DDL 里是 `NOT NULL DEFAULT ''`：写成 NULL 会直接触发
                # `NotNullViolationError`（B21c 实测），所以空串就存空串。
                assignments.append(f"{key} = :{key}")
            else:
                assignments.append(f"{key} = :{key}")
            params[key] = value
        if not assignments:
            return None
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _StepRepository(uow)
            row = await repo.fetch_one(
                f"""
                UPDATE {STEP_TABLE} SET {", ".join(assignments)}
                 WHERE tenant_id = :tenant_id AND step_id = :step_id
                RETURNING *
                """,
                repo.params(step_id=step_id, **params),
            )
        return _to_step(row) if row is not None else None

    async def reset_steps_from(self, tenant_id: str, task_id: str, seq: int) -> int:
        """把 `seq` 及之后的步骤重置为 `pending`。

        Args:
            tenant_id: 企业 ID。
            task_id: 任务 ID。
            seq: 起始序号。

        Returns:
            int: 重置条数。
        """
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _StepRepository(uow)
            rows = await repo.fetch_all(
                f"""
                UPDATE {STEP_TABLE}
                   SET status = 'pending', attempt = 0, retryable = FALSE,
                       reason_text = '', operator_action = '', output_summary = '',
                       started_at = NULL, finished_at = NULL, duration_ms = 0
                 WHERE tenant_id = :tenant_id AND task_id = :task_id AND seq >= :seq
                RETURNING step_id
                """,
                repo.params(task_id=task_id, seq=seq),
            )
        return len(rows)

    async def task_stats(self, tenant_id: str, since: str, until: str) -> TaskStats:
        """窗口内任务聚合（**一条**聚合 SQL；公式与内存实现逐条对应，AC-A7）。

        - 主过滤 `tenant_id + started_at ∈ [since, until)`（借
          `idx_task_status_time` 的 tenant_id 前缀定位，零新索引）；
        - `user_plugin` CTE 按 `(initiator_id, plugin_id)` 分组（剔 retry），
          借 `idx_task_initiator` 前缀；
        - 时间参数经 `as_datetime()` 转 `datetime`（asyncpg 类型化绑定只收
          `datetime`，B21c 实测教训）；
        - `percentile_cont(0.5)` 线性插值，取回后 `float(...)`；
          空窗口由 `COALESCE(..., 0)` 兜底为 0。

        Args:
            tenant_id: 企业 ID。
            since: 窗口起点（ISO 8601，含）。
            until: 窗口终点（ISO 8601，不含）。

        Returns:
            TaskStats: 原始聚合值；空窗口全 0。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _TaskRepository(uow)
            row = await repo.fetch_one(
                f"""
                WITH scoped AS (
                    SELECT task_id, initiator_id, plugin_id, status, cancel_requested,
                           trigger_type, usage_units, started_at, finished_at
                      FROM {TASK_TABLE}
                     WHERE tenant_id = :tenant_id
                       AND started_at >= :since AND started_at < :until
                ),
                user_plugin AS (
                    SELECT initiator_id, plugin_id, count(*) AS cnt
                      FROM scoped
                     WHERE trigger_type <> 'retry'
                     GROUP BY initiator_id, plugin_id
                )
                SELECT
                    (SELECT count(*) FROM scoped) AS total,
                    (SELECT count(*) FROM scoped WHERE status = 'completed') AS completed,
                    (SELECT count(*) FROM scoped WHERE status = 'failed') AS failed,
                    (SELECT count(*) FROM scoped
                      WHERE status = 'cancelled' AND cancel_requested) AS cancelled,
                    (SELECT count(DISTINCT initiator_id) FROM scoped
                      WHERE trigger_type <> 'retry') AS active_users,
                    (SELECT count(DISTINCT initiator_id) FROM user_plugin
                      WHERE cnt >= 2) AS repeat_users,
                    (SELECT COALESCE(sum(EXTRACT(EPOCH FROM (finished_at - started_at))), 0)
                       FROM scoped
                      WHERE status = 'completed' AND finished_at IS NOT NULL) AS duration_sum,
                    (SELECT COALESCE(percentile_cont(0.5) WITHIN GROUP (
                                ORDER BY EXTRACT(EPOCH FROM (finished_at - started_at)))
                                FILTER (WHERE status = 'completed' AND finished_at IS NOT NULL), 0)
                       FROM scoped) AS duration_p50,
                    (SELECT COALESCE(sum(usage_units), 0) FROM scoped
                      WHERE status = 'completed') AS units_sum
                """,
                repo.params(since=as_datetime(since), until=as_datetime(until)),
            )
        return _to_task_stats(row)


def _task_filters(
    *,
    plugin_id: str,
    status: str,
    initiator_id: str,
    created_from: str,
    created_to: str,
) -> tuple[str, dict[str, Any]]:
    """构造任务列表的过滤片段（命名参数绑定，不拼值）。

    Args:
        plugin_id: 插件筛选。
        status: 状态筛选。
        initiator_id: 发起人筛选。
        created_from: 起始时间。
        created_to: 结束时间。

    Returns:
        tuple[str, dict[str, Any]]: `(SQL 片段, 参数)`。
    """
    clause = ""
    params: dict[str, Any] = {}
    if plugin_id:
        clause += " AND plugin_id = :plugin_id"
        params["plugin_id"] = plugin_id
    if status:
        clause += " AND status = :status"
        params["status"] = status
    if initiator_id:
        clause += " AND initiator_id = :initiator_id"
        params["initiator_id"] = initiator_id
    if created_from:
        clause += " AND started_at >= :created_from"
        params["created_from"] = as_datetime(created_from)
    if created_to:
        clause += " AND started_at <= :created_to"
        params["created_to"] = as_datetime(created_to)
    return clause, params


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


def _to_task_stats(row: Mapping[str, Any] | None) -> TaskStats:
    """把聚合行转成 `TaskStats`（空窗口全 0，无 NaN/null）。

    Args:
        row: 聚合 SQL 的返回行；无行时按空窗口处理。

    Returns:
        TaskStats: 原始聚合值。
    """
    if row is None:  # pragma: no cover - 无 GROUP BY 的聚合恒有一行
        return TaskStats()
    return TaskStats(
        total=int(row.get("total", 0) or 0),
        completed=int(row.get("completed", 0) or 0),
        failed=int(row.get("failed", 0) or 0),
        cancelled=int(row.get("cancelled", 0) or 0),
        active_users=int(row.get("active_users", 0) or 0),
        repeat_users=int(row.get("repeat_users", 0) or 0),
        duration_sum_seconds=float(row.get("duration_sum", 0) or 0),
        duration_p50_seconds=float(row.get("duration_p50", 0) or 0),
        units_sum=int(row.get("units_sum", 0) or 0),
    )


def _task_params(record: TaskRecord) -> dict[str, Any]:
    """把任务行转成 INSERT 参数。

    Args:
        record: 任务行。

    Returns:
        dict[str, Any]: 命名参数。
    """
    return {
        "task_id": record.task_id,
        "plugin_id": record.plugin_id,
        "plugin_version": record.plugin_version,
        "conversation_id": record.conversation_id,
        "initiator_id": record.initiator_id,
        "trigger_type": record.trigger_type,
        "status": record.status,
        "progress": record.progress,
        "current_step_seq": record.current_step_seq,
        "inputs": json.dumps(record.inputs, ensure_ascii=False),
        "result_summary": record.result_summary,
        "checklist": json.dumps(record.checklist, ensure_ascii=False),
        "warnings": json.dumps(record.warnings, ensure_ascii=False),
        "error_message": record.error_message,
        "retry_of": record.retry_of,
        "from_step": record.from_step,
        "confirm_step_seq": record.confirm_step_seq,
        "confirmed_step_seq": record.confirmed_step_seq,
        "cancel_requested": record.cancel_requested,
        "usage_units": record.usage_units,
        "idem_key": record.idem_key,
        # `started_at` 由调用方给（内存后端同样尊重它）；没给就交给 DDL 的 now()
        "started_at": as_datetime(record.started_at),
        "expires_at": as_datetime(record.expires_at) or as_datetime(_expires_at()),
    }


def _expires_at() -> str:
    """计算产物过期时间（180 天，缺省保留期）。

    Returns:
        str: ISO 8601 时间戳。
    """
    return (datetime.now(UTC) + timedelta(days=180)).isoformat().replace("+00:00", "Z")


def _to_task(row: Mapping[str, Any]) -> TaskRecord:
    """把数据库行转成任务行。

    Args:
        row: 数据库行。

    Returns:
        TaskRecord: 任务行。
    """
    return TaskRecord(
        tenant_id=str(row.get("tenant_id", "")),
        task_id=str(row.get("task_id", "")),
        plugin_id=str(row.get("plugin_id", "")),
        plugin_version=str(row.get("plugin_version", "")),
        conversation_id=str(row.get("conversation_id", "") or ""),
        initiator_id=str(row.get("initiator_id", "")),
        trigger_type=str(row.get("trigger_type", "manual")),
        status=str(row.get("status", "pending")),
        progress=int(row.get("progress", 0) or 0),
        current_step_seq=int(row.get("current_step_seq", 0) or 0),
        # `queue_position` 由 `list_queued()` 派生回填，读单行时恒为 None
        queue_position=None,
        inputs=_json_field(row.get("inputs")),
        result_summary=str(row.get("result_summary", "") or ""),
        checklist=_json_list(row.get("checklist")),
        warnings=[str(item) for item in _json_list(row.get("warnings"))],
        error_message=str(row.get("error_message", "") or ""),
        retry_of=str(row.get("retry_of", "") or ""),
        from_step=int(row.get("from_step", 0) or 0),
        confirm_step_seq=int(row.get("confirm_step_seq", 0) or 0),
        confirmed_step_seq=int(row.get("confirmed_step_seq", 0) or 0),
        cancel_requested=bool(row.get("cancel_requested", False)),
        usage_units=int(row.get("usage_units", 0) or 0),
        idem_key=str(row.get("idem_key", "") or ""),
        started_at=_timestamp(row.get("started_at")),
        finished_at=_timestamp(row.get("finished_at")),
        expires_at=_timestamp(row.get("expires_at")),
    )


def _to_step(row: Mapping[str, Any]) -> StepRecord:
    """把数据库行转成步骤行。

    Args:
        row: 数据库行。

    Returns:
        StepRecord: 步骤行。
    """
    return StepRecord(
        tenant_id=str(row.get("tenant_id", "")),
        step_id=str(row.get("step_id", "")),
        task_id=str(row.get("task_id", "")),
        seq=int(row.get("seq", 0) or 0),
        title=str(row.get("title", "") or ""),
        action=str(row.get("action", "") or ""),
        status=str(row.get("status", "pending")),
        attempt=int(row.get("attempt", 0) or 0),
        retryable=bool(row.get("retryable", False)),
        output_summary=str(row.get("output_summary", "") or ""),
        reason_text=str(row.get("reason_text", "") or ""),
        operator_action=str(row.get("operator_action", "") or ""),
        tool_execution_id=str(row.get("tool_execution_id", "") or ""),
        duration_ms=int(row.get("duration_ms", 0) or 0),
        started_at=_timestamp(row.get("started_at")),
        finished_at=_timestamp(row.get("finished_at")),
    )


def _json_field(value: Any) -> dict[str, Any]:
    """把 JSONB 值转成字典（驱动返回字符串时反序列化）。

    Args:
        value: 数据库值。

    Returns:
        dict[str, Any]: 字典。
    """
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    if isinstance(value, str) and value:
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError:  # pragma: no cover - 脏数据兜底
            return {}
        return loaded if isinstance(loaded, dict) else {}
    return {}


def _json_list(value: Any) -> list[Any]:
    """把 JSONB 值转成列表。

    Args:
        value: 数据库值。

    Returns:
        list[Any]: 列表。
    """
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str) and value:
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError:  # pragma: no cover - 脏数据兜底
            return []
        return loaded if isinstance(loaded, list) else []
    return []


def _timestamp(value: Any) -> str:
    """把时间值转成 ISO 8601 字符串。

    Args:
        value: 时间值（datetime / 字符串 / None）。

    Returns:
        str: ISO 字符串（空值返回空串）。
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return str(value)


_store: TaskStore | None = None


def get_task_store(settings: Settings | None = None) -> TaskStore:
    """获取任务存储单例（按 `business_store` 选择后端）。

    Args:
        settings: 可选配置实例。

    Returns:
        TaskStore: 存储实现。
    """
    global _store
    effective = settings or get_settings()
    if _store is None:
        _store = (
            PostgresTaskStore(effective)
            if effective.business_store == "postgres"
            else InMemoryTaskStore()
        )
    return _store


def get_memory_task_store() -> InMemoryTaskStore | None:
    """获取内存存储（测试清库用）。

    Returns:
        InMemoryTaskStore | None: 内存实现；当前为 PG 时返回 None。
    """
    global _store
    if _store is None:
        _store = InMemoryTaskStore()
    return _store if isinstance(_store, InMemoryTaskStore) else None


def reset_task_store() -> None:
    """重置单例（测试隔离）。"""
    global _store
    _store = None


__all__: list[str] = [
    "OCCUPYING_STATUSES",
    "STEP_MUTABLE_FIELDS",
    "STEP_TABLE",
    "TASK_MUTABLE_FIELDS",
    "TASK_TABLE",
    "TERMINAL_STATUSES",
    "InMemoryTaskStore",
    "PostgresTaskStore",
    "StepRecord",
    "TaskRecord",
    "TaskStats",
    "TaskStore",
    "get_memory_task_store",
    "get_task_store",
    "now_iso",
    "reset_task_store",
]
