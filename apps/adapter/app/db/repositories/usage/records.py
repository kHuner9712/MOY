"""用量事件仓储 · records 层（`usage_repo` 三拆后的数据记录与口径常量）。

本文件是 `app/db/repositories/usage/` 包拆分后的**最底层**：只有数据记录（dataclass）、
两侧共用的口径常量（能力/维度/结算来源/额度单位文案）与两个纯函数
（`current_period` / `period_bounds`），**不依赖** protocol / memory / postgres，
因此可被它们任意引用而不成环（依赖方向 `records ← protocol ← {memory, postgres} ← __init__`）。

**表名常量不在这里**：`USAGE_EVENT_TABLE` / `USAGE_MONTHLY_TABLE` /
`SUBSCRIPTION_TABLE` / `PLAN_TABLE` / `PLATFORM_TENANT` 只服务 PG 的 SQL
（内存后端不需要表名），放 `postgres.py`；放进 records 会让"数据记录"层混入持久化细节。

`current_period()` / `period_bounds()` 留在本层而不是 `shared.py`：它们是**账期口径**
（`MonthlyUsageRecord.period` 的取值与区间换算），与记录强耦合；为两个函数新建
`shared.py` 只会多一层没有语义的转发。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

#: 能力枚举（与 DDL CHECK 一致）
CAPABILITY_CHAT: Final[str] = "chat"
CAPABILITY_DOC_QA: Final[str] = "doc_qa"
CAPABILITY_DOC_PROCESS: Final[str] = "doc_process"
CAPABILITY_AUTOMATION: Final[str] = "automation"

#: 能力全集
CAPABILITIES: Final[tuple[str, ...]] = (
    CAPABILITY_CHAT,
    CAPABILITY_DOC_QA,
    CAPABILITY_DOC_PROCESS,
    CAPABILITY_AUTOMATION,
)

#: 聚合维度枚举（与 DDL CHECK 一致）
SCOPE_TENANT: Final[str] = "tenant"
SCOPE_DEPARTMENT: Final[str] = "department"
SCOPE_MEMBER: Final[str] = "member"

#: 结算来源：下游确认的精确值
SOURCE_SETTLED: Final[str] = "settled"

#: 结算来源：中断/超时折算值
SOURCE_ESTIMATED: Final[str] = "estimated"

#: 默认额度单位文案（无订阅时与套餐目录缺省值一致）
DEFAULT_QUOTA_UNIT_LABEL: Final[str] = "字数额度"


def current_period(moment: datetime | None = None) -> str:
    """取计量周期（UTC 的 `YYYY-MM`）。

    Args:
        moment: 结算时刻（缺省取当前 UTC 时间）。

    Returns:
        str: 形如 `2026-11` 的周期串。
    """
    stamp = moment or datetime.now(UTC)
    return stamp.astimezone(UTC).strftime("%Y-%m")


def period_bounds(period: str) -> tuple[str, str]:
    """把 `YYYY-MM` 周期换算为左闭右开的 UTC 时间区间（用于事件计数）。

    Args:
        period: 形如 `2026-11` 的周期串。

    Returns:
        tuple[str, str]: `(起始 ISO 时间, 结束 ISO 时间)`；格式非法时返回空串对。
    """
    try:
        start = datetime.strptime(period, "%Y-%m").replace(tzinfo=UTC)
    except ValueError:
        return "", ""
    end = datetime(start.year + (start.month // 12), (start.month % 12) + 1, 1, tzinfo=UTC)
    return start.isoformat(), end.isoformat()


@dataclass(frozen=True, slots=True)
class UsageEventRecord:
    """一条用量事件（`usage_event` 行）。

    Attributes:
        tenant_id: 租户 ID。
        event_id: 事件 ID，形如 `use_01HF8...`。
        user_id: 产生用量的用户 ID。
        capability: 能力（`chat` / `doc_qa` / `doc_process` / `automation`）。
        idem_key: 幂等键（`generation_id` / `document_id:run_id` / `task_id`）。
        units: 字数额度（`>= 0`）。
        period: 计量周期 `YYYY-MM`。
        source: 结算来源（`settled` / `estimated`）。
        org_id: 组织 ID（可空）。
        department_id: 主部门 ID（可空；为空时只写 tenant/member 两行聚合）。
        resource_type: 资源类型（`generation` / `document` / `task`）。
        resource_id: 资源 ID。
        occurred_at: 发生时间（ISO 8601）。
        profile: 能力档位（阶段四 M11；空串表示未知）。
        provider: 生成方标识（阶段四 M11；空串表示未知）。
        prompt_tokens: 输入 token（真实值或估算值）。
        completion_tokens: 输出 token（真实值或估算值）。
    """

    tenant_id: str
    user_id: str
    capability: str
    idem_key: str
    units: int = 0
    period: str = ""
    source: str = SOURCE_SETTLED
    org_id: str = ""
    department_id: str = ""
    resource_type: str = ""
    resource_id: str = ""
    event_id: str = ""
    # 阶段四 T4-04.2（M11）：4 个计量维度（迁移 0007 的加性列）。
    # 无法取得真实 token 时写估算值，并把 `source` 置为 `estimated`。
    profile: str = ""
    provider: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    occurred_at: str = ""

    @property
    def normalized_units(self) -> int:
        """非负整数化的额度值（负值按 0 处理，防止 CHECK 违规）。"""
        return max(int(self.units), 0)


@dataclass(frozen=True, slots=True)
class MonthlyUsageRecord:
    """一条月度聚合（`usage_monthly` 行）。

    Attributes:
        period: 计量周期 `YYYY-MM`。
        scope_type: 维度（`tenant` / `department` / `member`）。
        scope_id: 维度取值（租户 ID / 部门 ID / 用户 ID）。
        capability: 能力。
        units: 聚合额度。
    """

    period: str
    scope_type: str
    scope_id: str
    capability: str
    units: int = 0


@dataclass(frozen=True, slots=True)
class QuotaRecord:
    """套餐额度快照（总量口径）。

    Attributes:
        plan_code: 套餐标识；无订阅时为空串。
        plan_name: 套餐展示名。
        total_units: 额度总量（`席位 × 每席位额度`）。
        quota_unit_label: 额度单位文案。
    """

    plan_code: str = ""
    plan_name: str = ""
    total_units: int = 0
    quota_unit_label: str = DEFAULT_QUOTA_UNIT_LABEL


@dataclass(frozen=True, slots=True)
class RangeEventStats:
    """时间区间内的事件级统计（运营后台 OPS6 使用，B10）。

    与 `monthly_rows()` 的区别：本结构按 `occurred_at` 落在**任意区间**统计
    （`7d` / `30d` 会跨越自然月边界），而月度聚合按账期切分。
    额度口径仍与 BL5 **同源**：`units` 由同一张 `usage_event` 表求和，
    因此「各企业区间额度之和」与「按账期的企业维度求和」在整月区间上**误差 0**。

    Attributes:
        units: 区间内额度总和。
        event_count: 区间内事件条数。
        distinct_users: 区间内产生用量的成员数（去重）。
        by_capability: `能力 → 事件条数`。
    """

    units: int = 0
    event_count: int = 0
    distinct_users: int = 0
    by_capability: dict[str, int] = field(default_factory=dict)

    @property
    def conversation_count(self) -> int:
        """对话次数（`chat` + `doc_qa` 事件条数）。"""
        return int(self.by_capability.get(CAPABILITY_CHAT, 0)) + int(
            self.by_capability.get(CAPABILITY_DOC_QA, 0)
        )

    @property
    def document_count(self) -> int:
        """文档处理次数（`doc_process` 事件条数）。"""
        return int(self.by_capability.get(CAPABILITY_DOC_PROCESS, 0))


__all__: list[str] = [
    "CAPABILITIES",
    "CAPABILITY_AUTOMATION",
    "CAPABILITY_CHAT",
    "CAPABILITY_DOC_PROCESS",
    "CAPABILITY_DOC_QA",
    "DEFAULT_QUOTA_UNIT_LABEL",
    "SCOPE_DEPARTMENT",
    "SCOPE_MEMBER",
    "SCOPE_TENANT",
    "SOURCE_ESTIMATED",
    "SOURCE_SETTLED",
    "MonthlyUsageRecord",
    "QuotaRecord",
    "RangeEventStats",
    "UsageEventRecord",
    "current_period",
    "period_bounds",
]
