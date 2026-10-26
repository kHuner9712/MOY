"""任务与步骤仓储 · records 层（`task_repo` 三拆后的数据记录与状态常量）。

本文件是 `app/db/repositories/task/` 包拆分后的**最底层**：只有数据记录（dataclass）、
两侧共用的状态常量/字段白名单，以及三个两侧共用的纯函数
（`now_iso` / `normalize_expected_status` / `_expires_at`），**不依赖**
protocol / memory / postgres，因此可被它们任意引用而不成环
（依赖方向 `records ← protocol ← {memory, postgres} ← __init__`）。

**表名与 PG 列白名单不在这里**：`TASK_TABLE` / `STEP_TABLE` 只服务 PG 的 SQL，
`_PG_TASK_COLUMNS`（"库里真实存在的列"）更是纯持久化事实，一律放 `postgres.py`。

`normalize_expected_status()` 必须留在两侧都能引用的层：调用方可能传单值
（`"running"`）也可能传集合，PG 侧要拼进 `status = ANY(:expected_status)`（需 list），
内存侧要做等价的 `in` 判断（需可迭代）——两处各写一遍归一，迟早出现
"内存过、PG 不过"的偏差（这正是 G-38 R3 要拦的形态）。

`_expires_at()` 同理：内存 `create_task` 与 PG `_task_params` 都要在"调用方没给"时
补 180 天保留期，两处各写一遍会让过期时间口径分叉。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final

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


def normalize_expected_status(value: str | Sequence[str] | None) -> list[str] | None:
    """把乐观并发条件归一为状态列表。

    双后端必须**用同一归一口径**：调用方可能传单值（`"running"`）也可能传集合，
    PG 侧要拼进 `status = ANY(:expected_status)`（需 list），内存侧要做等价的
    `in` 判断（需可迭代）。两处各写一遍归一，迟早出现"内存过、PG 不过"的偏差。

    Args:
        value: 期望状态（单值 / 序列 / `None` 表示不加条件）。

    Returns:
        list[str] | None: 状态列表；`None` 表示不加乐观条件。
    """
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    return list(value)


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


def _expires_at() -> str:
    """计算产物过期时间（180 天，缺省保留期）。

    Returns:
        str: ISO 8601 时间戳。
    """
    return (datetime.now(UTC) + timedelta(days=180)).isoformat().replace("+00:00", "Z")


__all__: list[str] = [
    "OCCUPYING_STATUSES",
    "STATUS_QUEUED",
    "STEP_MUTABLE_FIELDS",
    "TASK_MUTABLE_FIELDS",
    "TERMINAL_STATUSES",
    "StepRecord",
    "TaskRecord",
    "TaskStats",
    "normalize_expected_status",
    "now_iso",
]
