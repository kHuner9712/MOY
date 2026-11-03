# ruff: noqa: S608 - 表名/列名均为模块常量（经 assert_identifier 校验），值一律命名参数绑定。


"""任务与步骤仓储 · PostgreSQL 实现（生产）。

两张表：`plugin_task` / `plugin_task_step`。**四条与契约绑定的口径**（与拆分前一致）：

1. **一步一事务**：每个步骤的写入都是独立事务（长任务短事务，§3.3 硬约定 1）；
2. **`queued` 不是错误**：并发超限的任务落 `status='queued'` + 派生位次；
3. **`retry_of` 只指向原任务**：重试新建任务并记来源，原任务状态永不改变；
4. **`plugin_version` 是快照**：历史任务按创建时版本渲染，不回读安装表。

本文件还承载**行映射器**（`_to_task` / `_to_step` / `_to_task_stats`）、
**写库参数构造**（`_task_params` / `_task_filters`）与 **JSONB/时间还原**
（`_json_field` / `_json_list` / `_timestamp`）：它们只服务"从 PG 行/列值还原 record"，
内存后端直接拿对象，用不到。

`_PG_TASK_COLUMNS` 是"库里真实存在的列"，`TASK_MUTABLE_FIELDS` 是"模型层允许改的字段"：
两者**故意不同**（前者缺 `queue_position`），`update_task` 用前者过滤，
否则"释放额度后重排位次"这条正常业务路径在 PG 上会直接失败。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Final

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.db.repositories.base import BaseRepository, as_datetime
from app.db.repositories.task.records import (
    OCCUPYING_STATUSES,
    QUEUE_SCAN_LIMIT,
    STATUS_QUEUED,
    STEP_MUTABLE_FIELDS,
    TASK_MUTABLE_FIELDS,
    StepRecord,
    TaskRecord,
    TaskStats,
    _expires_at,
    normalize_expected_status,
)
from app.db.session import tenant_scope

TASK_TABLE: Final[str] = "plugin_task"
STEP_TABLE: Final[str] = "plugin_task_step"


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
        # P4-11 ③：`plugin_task.hold_reason`（迁移 0012）——只在 UPDATE 里出现，
        # **不进 INSERT 列名列表**（DEFAULT '' → R7 omittable、G-SQL arity 不变）。
        "hold_reason",
    }
)


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

    async def update_task(
        self,
        tenant_id: str,
        task_id: str,
        *,
        expected_status: str | Sequence[str] | None = None,
        **fields: Any,
    ) -> TaskRecord | None:
        """更新任务可变字段（可带乐观状态条件）。

        P1-2：`expected_status` 拼成 `AND status = ANY(:expected_status)`，
        与内存实现同语义——条件不满足时**一行都不改**，`RETURNING` 无行 → 返回 `None`。
        用条件 UPDATE 而不是"先 SELECT 再判断再 UPDATE"：后者在并发下是典型的
        TOCTOU，取消与收尾仍会互相覆盖（本缺陷的根因）。

        Args:
            tenant_id: 企业 ID。
            task_id: 任务 ID。
            expected_status: 乐观条件（期望的当前状态；`None` 表示不加条件）。
            **fields: 待更新字段。

        Returns:
            TaskRecord | None: 更新后的任务行；条件不满足或任务不存在时返回 `None`。

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
        where = "tenant_id = :tenant_id AND task_id = :task_id"
        extra: dict[str, Any] = {"task_id": task_id}
        allowed = normalize_expected_status(expected_status)
        if allowed is not None:
            where += " AND status = ANY(:expected_status)"
            extra["expected_status"] = allowed
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _TaskRepository(uow)
            row = await repo.fetch_one(
                f"""
                UPDATE {TASK_TABLE} SET {", ".join(assignments)}
                 WHERE {where}
                RETURNING *
                """,
                repo.params(**extra, **params),
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

    async def clear_hold_reasons(self, tenant_id: str) -> int:
        """按租户批量清空额度挂起标记（懒恢复三触点的统一动作）。

        **为什么用 `UPDATE ... WHERE hold_reason <> ''` 而不是逐行改**：懒恢复挂在
        读路径上（`list_tasks` / 建任务前 / 上传前），逐行会让一次列表请求打出 N 次
        UPDATE；一条批量更新只命中少量被挂起的行，且没有挂起行时更新 0 行。

        只清 `plugin_task` 一张表 —— 文档侧的同类清理在 `DocumentStore` 上
        （`document_repo.clear_hold_reasons`），两侧各自清自己的表，避免跨表事务。

        Args:
            tenant_id: 企业 ID。

        Returns:
            int: 被清空的任务条数。
        """
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _TaskRepository(uow)
            removed = await repo.fetch_scalar(
                f"""
                WITH cleared AS (
                    UPDATE {TASK_TABLE}
                       SET hold_reason = ''
                     WHERE tenant_id = :tenant_id AND hold_reason <> ''
                    RETURNING 1
                )
                SELECT count(*) FROM cleared
                """,
                repo.params(),
            )
        return int(removed or 0)

    async def counts_occupying(self, tenant_id: str, plugin_ids: Sequence[str]) -> dict[str, int]:
        """批量统计各插件占用额度的任务数（一次 `GROUP BY plugin_id`）。

        Args:
            tenant_id: 企业 ID。
            plugin_ids: 插件标识列表。

        Returns:
            dict[str, int]: 插件标识 → 任务数（只含非零项，与逐条调用口径一致）。
        """
        wanted = list(dict.fromkeys(plugin_ids))
        if not wanted:
            return {}
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _TaskRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT plugin_id, count(*) AS total FROM {TASK_TABLE}
                 WHERE tenant_id = :tenant_id AND plugin_id = ANY(:plugin_ids)
                   AND status = ANY(:statuses)
                 GROUP BY plugin_id
                """,
                repo.params(plugin_ids=wanted, statuses=list(OCCUPYING_STATUSES)),
            )
        return {str(row["plugin_id"]): int(row["total"] or 0) for row in rows}

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
            TaskRecord | None: 任务行（`queue_position` 恒为 1）。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _TaskRepository(uow)
            row = await repo.fetch_one(
                f"""
                SELECT * FROM {TASK_TABLE}
                 WHERE tenant_id = :tenant_id AND plugin_id = :plugin_id
                   AND status = '{STATUS_QUEUED}'
                 ORDER BY started_at, task_id
                 LIMIT 1
                """,
                repo.params(plugin_id=plugin_id),
            )
        if row is None:
            return None
        record = _to_task(row)
        record.queue_position = 1
        return record

    async def list_queued(
        self, tenant_id: str, plugin_id: str, *, limit: int = QUEUE_SCAN_LIMIT
    ) -> list[TaskRecord]:
        """列出排队任务（位次**派生**，不读存储列）。

        `queue_position` 不是 `plugin_task` 的列：排队语义是
        「`status='queued'` + 按 `started_at` 先来后到」。因此在取回后按顺序
        回填 `1..n`，与 `InMemoryTaskStore` 的口径一致，也让 A1 返回给前端的
        `queue_position` 有确定来源。

        Args:
            tenant_id: 企业 ID。
            plugin_id: 插件标识。
            limit: 单次读取上限（`LIMIT :limit` 下推，审计 2026-11-03 QA-03）。

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
                 LIMIT :limit
                """,
                repo.params(plugin_id=plugin_id, limit=max(1, int(limit))),
            )
        queued = [_to_task(row) for row in rows]
        for index, record in enumerate(queued, start=1):
            record.queue_position = index
        return queued

    async def _queue_rank(self, tenant_id: str, plugin_id: str, row: Mapping[str, Any]) -> int:
        """取某个排队任务在队列里的位次（1 起）。

        用一条 `COUNT(*)` 计算「排序键严格在目标之前的排队任务数」，不再拉取
        整条队列（审计 2026-10-31 M2：新建任务/查详情都触发全量队列读取）。
        排序键与 `list_queued` 一致：`(started_at, task_id)`。

        Args:
            tenant_id: 企业 ID。
            plugin_id: 插件标识。
            row: 刚插入的任务行（至少含 `task_id` 与 `started_at`）。

        Returns:
            int: 位次；`started_at` 缺失时无法比较，回落到 1（写入侧恒有 `now()`）。
        """
        target = str(row["task_id"])
        started_at = as_datetime(row.get("started_at"))
        if started_at is None:  # pragma: no cover - 写库侧 COALESCE(:started_at, now())
            return 1
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _TaskRepository(uow)
            before = await repo.fetch_scalar(
                f"""
                SELECT count(*) FROM {TASK_TABLE}
                 WHERE tenant_id = :tenant_id AND plugin_id = :plugin_id
                   AND status = '{STATUS_QUEUED}'
                   AND (started_at, task_id) < (CAST(:started_at AS timestamptz), :task_id)
                """,
                repo.params(plugin_id=plugin_id, started_at=started_at, task_id=target),
            )
        return max(1, int(before or 0) + 1)

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
        # 防御式取列：未列出该列的 SELECT 按「未挂起」处理（见 records.py 的字段注释）
        hold_reason=str(row.get("hold_reason", "") or ""),
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


__all__: list[str] = [
    "STEP_TABLE",
    "TASK_TABLE",
    "PostgresTaskStore",
]
