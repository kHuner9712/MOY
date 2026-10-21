# ruff: noqa: S608 - 表名/列名均为模块常量（经 assert_identifier 校验），值一律命名参数绑定。
"""运营后台仓储（阶段二 B10，架构文档 §4.6 OPS1-OPS8）。

**职责边界**（为什么本文件很薄）：

- 企业（`tenant`）与订单（`billing_order`）的权威存储是 `billing_repo`——
  它们与 BL1-BL10 共用同一套读写口径，**不允许运营侧另写一套 SQL**
  （那会出现「企业看到的订单」与「运营看到的订单」两套数据）；
- 本模块只补充运营专属的 `ops_settings`（平台级配置表），并把
  「跨租户读取」封装成带平台作用域的方法，供 `business/ops_service.py` 单一依赖。

**平台作用域**：所有跨租户查询都必须走 `db/session.platform_scope()`
（会话变量 = `__platform__`，RLS 放开全部行）。该函数内部强制 `allow_platform=True`，
而未通过 `core/security.require_ops_access()` 的企业令牌根本走不到这里。

**最小可见原则**（PRD 风险 R5）：本模块不查询、不返回任何
对话内容、文档原文、成员邮箱明文字段——统计只取聚合数字。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Final, Protocol

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.repositories.base import BaseRepository
from app.db.repositories.billing_repo import (
    PLATFORM_TENANT,
    BillingStore,
    OrderRecord,
    TenantRecord,
    get_billing_store,
    now_iso,
)
from app.db.session import platform_scope

logger = get_logger(__name__)

#: 运营系统配置表名
OPS_SETTINGS_TABLE: Final[str] = "ops_settings"

#: 配置键：额度告警阈值（百分比）
SETTING_QUOTA_WARN_THRESHOLD: Final[str] = "quota_warn_threshold"

#: 配置键：试用天数
SETTING_TRIAL_DAYS: Final[str] = "trial_days"

#: 配置键：额度单位文案
SETTING_QUOTA_UNIT_LABEL: Final[str] = "quota_unit_label"

#: 配置键：平台公告
SETTING_ANNOUNCEMENT: Final[str] = "announcement"

#: 全部可配置键（OPS7/OPS8 的字段集合）
SETTING_KEYS: Final[tuple[str, ...]] = (
    SETTING_QUOTA_WARN_THRESHOLD,
    SETTING_TRIAL_DAYS,
    SETTING_QUOTA_UNIT_LABEL,
    SETTING_ANNOUNCEMENT,
)

#: 配置默认值（未被运营覆盖时的口径，与 PRD 5.1 默认配置一致）
DEFAULT_SETTINGS: Final[dict[str, Any]] = {
    SETTING_QUOTA_WARN_THRESHOLD: 80,
    SETTING_TRIAL_DAYS: 14,
    SETTING_QUOTA_UNIT_LABEL: "字数额度",
    SETTING_ANNOUNCEMENT: "",
}


@dataclass(frozen=True, slots=True)
class OpsSettings:
    """运营系统配置。

    Attributes:
        quota_warn_threshold: 额度告警阈值（百分比，0-100）。
        trial_days: 默认试用天数。
        quota_unit_label: 额度单位文案。
        announcement: 平台公告（空串表示无公告）。
        updated_by: 最后修改人。
        updated_at: 最后修改时间。
    """

    quota_warn_threshold: int = 80
    trial_days: int = 14
    quota_unit_label: str = "字数额度"
    announcement: str = ""
    updated_by: str = ""
    updated_at: str = ""


def _coerce_settings(values: Mapping[str, Any]) -> OpsSettings:
    """把键值映射规范化为配置对象（缺失键回落默认值）。

    Args:
        values: `setting_key → setting_value` 映射。

    Returns:
        OpsSettings: 配置对象。
    """
    merged = dict(DEFAULT_SETTINGS)
    merged.update({key: value for key, value in values.items() if key in SETTING_KEYS})
    return OpsSettings(
        quota_warn_threshold=max(min(int(merged[SETTING_QUOTA_WARN_THRESHOLD]), 100), 0),
        trial_days=max(int(merged[SETTING_TRIAL_DAYS]), 0),
        quota_unit_label=str(merged[SETTING_QUOTA_UNIT_LABEL] or "字数额度"),
        announcement=str(merged[SETTING_ANNOUNCEMENT] or ""),
        updated_by=str(values.get("updated_by", "") or ""),
        updated_at=str(values.get("updated_at", "") or ""),
    )


class OpsStore(Protocol):
    """运营后台存储契约（内存 / PostgreSQL 双实现）。"""

    async def get_settings(self) -> OpsSettings:
        """读取运营配置（未写入时返回默认值）。"""
        ...

    async def save_settings(self, values: Mapping[str, Any], actor_id: str) -> OpsSettings:
        """写入运营配置并返回全量结果。"""
        ...

    async def list_tenants(self, *, keyword: str = "", status: str = "") -> list[TenantRecord]:
        """跨租户列出企业（平台作用域）。"""
        ...

    async def get_tenant(self, tenant_id: str) -> TenantRecord | None:
        """读取单个企业。"""
        ...

    async def update_tenant_status(
        self, tenant_id: str, status: str, reason: str = ""
    ) -> TenantRecord | None:
        """启用 / 停用企业。"""
        ...

    async def list_orders(
        self, *, tenant_id: str = "", status: str = "", offset: int = 0, limit: int = 20
    ) -> tuple[list[OrderRecord], int]:
        """跨租户分页列出订单（平台作用域）。"""
        ...

    async def get_order(self, order_id: str) -> OrderRecord | None:
        """按订单 ID 读取（运营核销用）。"""
        ...


class InMemoryOpsStore:
    """进程内运营配置存储（development/test）。

    企业与订单**复用**进程内计费存储（同一份数据），避免运营侧与租户侧对不上。

    Attributes:
        _billing: 计费存储（企业/订单的权威源）。
    """

    def __init__(self, store: BillingStore, settings: Settings | None = None) -> None:
        """初始化。

        Args:
            store: 计费存储（共用企业与订单数据）。
            settings: 可选配置实例。
        """
        self._settings = settings or get_settings()
        self._billing = store
        self._values: dict[str, Any] = {}
        self._updated_by = ""

    @property
    def billing(self) -> BillingStore:
        """共用的计费存储（企业/订单的权威源）。

        Returns:
            BillingStore: 计费存储。
        """
        return self._billing

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------
    async def get_settings(self) -> OpsSettings:
        """读取运营配置。

        Returns:
            OpsSettings: 配置对象。
        """
        return _coerce_settings({**self._values, "updated_by": self._updated_by})

    async def save_settings(self, values: Mapping[str, Any], actor_id: str) -> OpsSettings:
        """写入运营配置。

        Args:
            values: 待更新键值（只接受 `SETTING_KEYS` 中的键）。
            actor_id: 操作人。

        Returns:
            OpsSettings: 更新后的全量配置。
        """
        for key, value in values.items():
            if key in SETTING_KEYS:
                self._values[key] = value
        self._updated_by = actor_id
        logger.info("ops_settings_saved", actor_id=actor_id, keys=sorted(values))
        return await self.get_settings()

    # ------------------------------------------------------------------
    # 企业与订单（委托计费存储，保持单一数据源）
    # ------------------------------------------------------------------
    async def list_tenants(self, *, keyword: str = "", status: str = "") -> list[TenantRecord]:
        """跨租户列出企业。

        Args:
            keyword: 名称关键字。
            status: 状态过滤。

        Returns:
            list[TenantRecord]: 企业列表。
        """
        return await self._billing.list_tenants(keyword=keyword, status=status)

    async def get_tenant(self, tenant_id: str) -> TenantRecord | None:
        """读取单个企业。

        Args:
            tenant_id: 企业 ID。

        Returns:
            TenantRecord | None: 企业；不存在返回 None。
        """
        return await self._billing.get_tenant(tenant_id)

    async def update_tenant_status(
        self, tenant_id: str, status: str, reason: str = ""
    ) -> TenantRecord | None:
        """启用 / 停用企业。

        Args:
            tenant_id: 企业 ID。
            status: 目标状态。
            reason: 停用原因。

        Returns:
            TenantRecord | None: 更新后的企业；不存在返回 None。
        """
        return await self._billing.update_tenant(tenant_id, status=status, suspended_reason=reason)

    async def list_orders(
        self, *, tenant_id: str = "", status: str = "", offset: int = 0, limit: int = 20
    ) -> tuple[list[OrderRecord], int]:
        """跨租户分页列出订单。

        Args:
            tenant_id: 企业过滤。
            status: 状态过滤。
            offset: 偏移量。
            limit: 每页条数。

        Returns:
            tuple[list[OrderRecord], int]: 当前页订单与总条数。
        """
        return await self._billing.list_all_orders(
            tenant_id=tenant_id, status=status, offset=offset, limit=limit
        )

    async def get_order(self, order_id: str) -> OrderRecord | None:
        """按订单 ID 读取订单。

        Args:
            order_id: 订单 ID。

        Returns:
            OrderRecord | None: 订单；不存在返回 None。
        """
        return await self._billing.find_order_by_id(order_id)

    def clear(self) -> None:
        """清空配置（测试隔离用）。"""
        self._values.clear()
        self._updated_by = ""


class _OpsSettingsTableRepository(BaseRepository):
    """`ops_settings` 表仓储（平台共享行）。"""

    table = OPS_SETTINGS_TABLE


class PostgresOpsStore:
    """PostgreSQL 运营存储（企业/订单委托计费存储，配置读 `ops_settings`）。"""

    def __init__(self, settings: Settings | None = None, store: BillingStore | None = None) -> None:
        """初始化。

        Args:
            settings: 可选配置实例。
            store: 可选计费存储（企业/订单权威源）。
        """
        self._settings = settings or get_settings()
        self._billing = store or get_billing_store(self._settings)

    @property
    def billing(self) -> BillingStore:
        """共用的计费存储（企业/订单的权威源）。

        Returns:
            BillingStore: 计费存储。
        """
        return self._billing

    async def get_settings(self) -> OpsSettings:
        """读取运营配置（`ops_settings` 行 → 配置对象）。

        Returns:
            OpsSettings: 配置对象。
        """
        async with platform_scope(self._settings, read_only=True) as uow:
            repo = _OpsSettingsTableRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT setting_key, setting_value, updated_by, updated_at
                FROM {OPS_SETTINGS_TABLE}
                WHERE tenant_id = :platform
                """,
                {"tenant_id": PLATFORM_TENANT, "platform": PLATFORM_TENANT},
            )
        values: dict[str, Any] = {}
        updated_by = ""
        updated_at = ""
        for row in rows:
            key = str(row.get("setting_key", ""))
            values[key] = _json_value(row.get("setting_value"))
            updated_by = str(row.get("updated_by", "") or "") or updated_by
            updated_at = str(row.get("updated_at", "") or "") or updated_at
        values["updated_by"] = updated_by
        values["updated_at"] = updated_at
        return _coerce_settings(values)

    async def save_settings(self, values: Mapping[str, Any], actor_id: str) -> OpsSettings:
        """写入运营配置（逐键 upsert）。

        Args:
            values: 待更新键值。
            actor_id: 操作人。

        Returns:
            OpsSettings: 更新后的全量配置。
        """
        payload = {key: value for key, value in values.items() if key in SETTING_KEYS}
        if payload:
            async with platform_scope(self._settings) as uow:
                repo = _OpsSettingsTableRepository(uow)
                for key, value in payload.items():
                    await repo.execute(
                        f"""
                        INSERT INTO {OPS_SETTINGS_TABLE}
                            (tenant_id, setting_key, setting_value, updated_by, updated_at)
                        VALUES (:tenant_id, :setting_key, CAST(:setting_value AS jsonb),
                                :updated_by, now())
                        ON CONFLICT (tenant_id, setting_key) DO UPDATE SET
                            setting_value = EXCLUDED.setting_value,
                            updated_by = EXCLUDED.updated_by,
                            updated_at = now()
                        """,
                        {
                            "tenant_id": PLATFORM_TENANT,
                            "setting_key": key,
                            "setting_value": json.dumps(value, ensure_ascii=False),
                            "updated_by": actor_id,
                        },
                    )
        logger.info("ops_settings_saved", actor_id=actor_id, keys=sorted(payload))
        return await self.get_settings()

    async def list_tenants(self, *, keyword: str = "", status: str = "") -> list[TenantRecord]:
        """跨租户列出企业。

        Args:
            keyword: 名称关键字。
            status: 状态过滤。

        Returns:
            list[TenantRecord]: 企业列表。
        """
        return await self._billing.list_tenants(keyword=keyword, status=status)

    async def get_tenant(self, tenant_id: str) -> TenantRecord | None:
        """读取单个企业。

        Args:
            tenant_id: 企业 ID。

        Returns:
            TenantRecord | None: 企业；不存在返回 None。
        """
        return await self._billing.get_tenant(tenant_id)

    async def update_tenant_status(
        self, tenant_id: str, status: str, reason: str = ""
    ) -> TenantRecord | None:
        """启用 / 停用企业。

        Args:
            tenant_id: 企业 ID。
            status: 目标状态。
            reason: 停用原因。

        Returns:
            TenantRecord | None: 更新后的企业；不存在返回 None。
        """
        return await self._billing.update_tenant(tenant_id, status=status, suspended_reason=reason)

    async def list_orders(
        self, *, tenant_id: str = "", status: str = "", offset: int = 0, limit: int = 20
    ) -> tuple[list[OrderRecord], int]:
        """跨租户分页列出订单。

        Args:
            tenant_id: 企业过滤。
            status: 状态过滤。
            offset: 偏移量。
            limit: 每页条数。

        Returns:
            tuple[list[OrderRecord], int]: 当前页订单与总条数。
        """
        return await self._billing.list_all_orders(
            tenant_id=tenant_id, status=status, offset=offset, limit=limit
        )

    async def get_order(self, order_id: str) -> OrderRecord | None:
        """按订单 ID 读取订单。

        Args:
            order_id: 订单 ID。

        Returns:
            OrderRecord | None: 订单；不存在返回 None。
        """
        return await self._billing.find_order_by_id(order_id)


def _json_value(raw: Any) -> Any:
    """把 JSONB 值还原为 Python 值。

    Args:
        raw: JSONB 值（dict/list/str/数字）。

    Returns:
        Any: Python 值。
    """
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw


# ===========================================================================
# 进程级单例
# ===========================================================================
_memory_store: InMemoryOpsStore | None = None


def get_ops_store(settings: Settings | None = None) -> OpsStore:
    """获取运营存储。

    Args:
        settings: 可选配置实例。

    Returns:
        OpsStore: `business_store=postgres` 时为数据库实现，否则为内存实现。
    """
    global _memory_store

    conf = settings or get_settings()
    billing = get_billing_store(conf)
    if conf.uses_postgres:
        return PostgresOpsStore(conf, billing)
    if _memory_store is None:
        _memory_store = InMemoryOpsStore(billing, conf)
    return _memory_store


def reset_ops_store() -> None:
    """重置内存后端单例（测试隔离用）。"""
    global _memory_store
    if _memory_store is not None:
        _memory_store.clear()
    _memory_store = None


def default_ops_settings() -> OpsSettings:
    """默认运营配置（未写入任何配置时的口径）。

    Returns:
        OpsSettings: 默认配置。
    """
    return replace(_coerce_settings(DEFAULT_SETTINGS), updated_at=now_iso())


__all__: list[str] = [
    "DEFAULT_SETTINGS",
    "OPS_SETTINGS_TABLE",
    "SETTING_ANNOUNCEMENT",
    "SETTING_KEYS",
    "SETTING_QUOTA_UNIT_LABEL",
    "SETTING_QUOTA_WARN_THRESHOLD",
    "SETTING_TRIAL_DAYS",
    "InMemoryOpsStore",
    "OpsSettings",
    "OpsStore",
    "PostgresOpsStore",
    "default_ops_settings",
    "get_ops_store",
    "reset_ops_store",
]
