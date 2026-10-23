"""租户月度预算仓储（阶段四 T4-04.3，架构 §3.4 / §7.5）。

**这张表很小，但两个字段的语义必须分清**（排障时最容易搞混）：

- `monthly_budget_units = 0` → **没设额度**（默认），护栏只按套餐额度判；
- `enabled = false` → **明确关掉**预算护栏（即使有额度也不拦）。
  两者都表现为"不拦"，但一个是"没配"、一个是"关掉"，日志里必须能区分。

**预算属于租户级**：一次超预算影响的是全租户的对话，因此读取走租户作用域
（`tenant_scope`），而不是平台作用域——运营后台改预算另走 `/ops`（本批不动）。
"""

# ruff: noqa: S608 - 表名/列名为模块常量（经 assert_identifier 校验），值一律命名参数绑定。
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final, Protocol

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.repositories.base import BaseRepository
from app.db.session import tenant_scope

logger = get_logger(__name__)

BUDGET_TABLE: Final[str] = "tenant_budget"

#: 默认预警比例（与 DDL 默认值一致：80%）
DEFAULT_WARN_RATIO: Final[float] = 0.80


@dataclass(slots=True)
class TenantBudgetRecord:
    """租户月度预算行。

    Attributes:
        tenant_id: 企业 ID。
        monthly_budget_units: 月度额度（0 = 不限）。
        warn_ratio: 预警比例（0~1）。
        enabled: 是否启用预算护栏。
        updated_at: 更新时间。
    """

    tenant_id: str = ""
    monthly_budget_units: int = 0
    warn_ratio: float = DEFAULT_WARN_RATIO
    enabled: bool = True
    updated_at: str = ""

    @property
    def effective(self) -> bool:
        """是否真的在管（启用且设了额度）。

        Returns:
            bool: 启用且额度 > 0 时返回 True。
        """
        return self.enabled and self.monthly_budget_units > 0

    def usage_ratio(self, used_units: int) -> float:
        """已用比例（未设额度时恒为 0，避免除零与"假预警"）。

        Args:
            used_units: 当月已用额度。

        Returns:
            float: 0~1 的比例（可能 > 1，用于判断超额）。
        """
        if self.monthly_budget_units <= 0:
            return 0.0
        return max(0, used_units) / self.monthly_budget_units

    def exhausted(self, used_units: int) -> bool:
        """当月是否已用尽预算。

        Args:
            used_units: 当月已用额度。

        Returns:
            bool: 启用且已用 >= 额度时返回 True。
        """
        return self.effective and max(0, used_units) >= self.monthly_budget_units

    def should_warn(self, used_units: int) -> bool:
        """是否应发"即将用尽"提示（达到 `warn_ratio` 但未用尽）。

        Args:
            used_units: 当月已用额度。

        Returns:
            bool: 需要提示时返回 True。
        """
        if not self.effective or self.exhausted(used_units):
            return False
        return self.usage_ratio(used_units) >= self.warn_ratio


class BudgetStore(Protocol):
    """预算存储契约（内存 / PostgreSQL 双实现）。"""

    async def get(self, tenant_id: str) -> TenantBudgetRecord:
        """读预算（无行时返回"不限"的默认记录）。"""
        ...

    async def upsert(self, record: TenantBudgetRecord) -> TenantBudgetRecord:
        """写预算（整行覆盖，运营后台用）。"""
        ...


def _to_budget(row: Mapping[str, Any]) -> TenantBudgetRecord:
    """把数据库行转成预算记录。

    Args:
        row: 行映射。

    Returns:
        TenantBudgetRecord: 预算记录。
    """
    ratio = row.get("warn_ratio", DEFAULT_WARN_RATIO)
    return TenantBudgetRecord(
        tenant_id=str(row.get("tenant_id", "")),
        monthly_budget_units=int(row.get("monthly_budget_units", 0) or 0),
        warn_ratio=float(Decimal(str(ratio))) if ratio is not None else DEFAULT_WARN_RATIO,
        enabled=bool(row.get("enabled", True)),
        updated_at=_timestamp(row.get("updated_at")),
    )


def _timestamp(value: Any) -> str:
    """把时间值规范成 ISO 8601（UTC，`Z` 结尾）。

    Args:
        value: 时间值。

    Returns:
        str: ISO 字符串；空值返回空串。
    """
    from datetime import UTC, datetime

    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    text = str(value).strip()
    return text.replace("+00:00", "Z") if text else ""


class InMemoryBudgetStore:
    """进程内预算存储（development / test）。"""

    def __init__(self) -> None:
        """初始化空存储。"""
        self._items: dict[str, TenantBudgetRecord] = {}

    async def get(self, tenant_id: str) -> TenantBudgetRecord:
        """读预算（无行返回"不限"）。

        Args:
            tenant_id: 企业 ID。

        Returns:
            TenantBudgetRecord: 预算记录。
        """
        found = self._items.get(tenant_id)
        if found is not None:
            return found
        return TenantBudgetRecord(tenant_id=tenant_id)

    async def upsert(self, record: TenantBudgetRecord) -> TenantBudgetRecord:
        """写预算。

        Args:
            record: 预算记录。

        Returns:
            TenantBudgetRecord: 落库后的记录。
        """
        from datetime import UTC, datetime

        stored = TenantBudgetRecord(
            tenant_id=record.tenant_id,
            monthly_budget_units=max(0, int(record.monthly_budget_units)),
            warn_ratio=min(max(float(record.warn_ratio), 0.01), 1.0),
            enabled=bool(record.enabled),
            updated_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )
        self._items[stored.tenant_id] = stored
        return stored

    def clear(self) -> None:
        """清空存储（测试隔离）。"""
        self._items.clear()


class _BudgetTableRepository(BaseRepository):
    """`tenant_budget` 表仓储。"""

    table = BUDGET_TABLE


class PostgresBudgetStore:
    """PostgreSQL 预算存储。"""

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化。

        Args:
            settings: 可选配置实例。
        """
        self._settings = settings or get_settings()

    async def get(self, tenant_id: str) -> TenantBudgetRecord:
        """读预算（无行返回"不限"）。

        表不存在（N-1 旧库未跑 0011）时同样返回"不限"：**预算护栏缺失不得让对话失败**。

        Args:
            tenant_id: 企业 ID。

        Returns:
            TenantBudgetRecord: 预算记录。
        """
        try:
            async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
                repo = _BudgetTableRepository(uow)
                row = await repo.fetch_one(
                    f"SELECT * FROM {BUDGET_TABLE} WHERE {repo.tenant_clause()}",
                    repo.params(),
                )
        except Exception as exc:  # 表缺失 / 连接异常 → 降级为"不限"
            logger.warning(
                "tenant_budget_unavailable", tenant_id=tenant_id, detail=type(exc).__name__
            )
            return TenantBudgetRecord(tenant_id=tenant_id)
        if row is None:
            return TenantBudgetRecord(tenant_id=tenant_id)
        return _to_budget(row)

    async def upsert(self, record: TenantBudgetRecord) -> TenantBudgetRecord:
        """写预算。

        Args:
            record: 预算记录。

        Returns:
            TenantBudgetRecord: 落库后的记录。
        """
        async with tenant_scope(record.tenant_id, settings=self._settings) as uow:
            repo = _BudgetTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                INSERT INTO {BUDGET_TABLE} (
                    tenant_id, monthly_budget_units, warn_ratio, enabled, updated_at
                ) VALUES (
                    :tenant_id, :monthly_budget_units, :warn_ratio, :enabled, now()
                )
                ON CONFLICT (tenant_id) DO UPDATE
                   SET monthly_budget_units = EXCLUDED.monthly_budget_units,
                       warn_ratio = EXCLUDED.warn_ratio,
                       enabled = EXCLUDED.enabled,
                       updated_at = now()
                RETURNING *
                """,
                repo.params(
                    monthly_budget_units=max(0, int(record.monthly_budget_units)),
                    warn_ratio=Decimal(str(min(max(float(record.warn_ratio), 0.01), 1.0))),
                    enabled=bool(record.enabled),
                ),
            )
        assert row is not None
        return _to_budget(row)


#: 预算存储单例（按配置选中实现）
_budget_store: BudgetStore | None = None


def get_budget_store(settings: Settings | None = None) -> BudgetStore:
    """取预算存储（按 `business_store` 选实现）。

    Args:
        settings: 可选配置实例。

    Returns:
        BudgetStore: 存储实现。
    """
    global _budget_store
    conf = settings or get_settings()
    if _budget_store is None:
        _budget_store = PostgresBudgetStore(conf) if conf.uses_postgres else InMemoryBudgetStore()
    return _budget_store


def reset_budget_store() -> None:
    """重置预算存储单例（测试隔离用）。"""
    global _budget_store
    _budget_store = None


__all__: list[str] = [
    "BUDGET_TABLE",
    "DEFAULT_WARN_RATIO",
    "BudgetStore",
    "InMemoryBudgetStore",
    "PostgresBudgetStore",
    "TenantBudgetRecord",
    "get_budget_store",
    "reset_budget_store",
]
