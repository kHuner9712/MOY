# ruff: noqa: S608 - 表名/列名均为模块常量（经 assert_identifier 校验），值一律命名参数绑定。


"""插件域仓储 · PostgreSQL 实现（生产）。

五张表：

| 表 | 作用 |
| --- | --- |
| `plugin_catalog` | 市场目录（平台共享行，`tenant_id='__platform__'`） |
| `plugin_version` | 声明快照（**不可变**，`manifest_sha256` 即版本判据） |
| `plugin_installation` | 安装态 + 版本双指针（乐观锁 `WHERE version = 观测值`） |
| `plugin_grant` | 成员 × 插件 × 能力（撤销写 `revoked_at`，不物理删） |
| `plugin_capability_policy` | 企业能力总开关（仅 `org.*`） |

**两个关键口径**（与拆分前一致）：

1. **声明快照不手写**：版本行由 `ensure_versions()` 幂等登记
   （`ON CONFLICT DO NOTHING`），内容来自 `version_records_from_registry()`；
2. **授权撤销用 `revoked_at` 软失效**：历史任务审计要能回答"当时授没授"，
   查询侧统一过滤 `revoked_at IS NULL`（走部分索引）。

本文件还承载**行映射器**（`_to_catalog` / `_to_version` / `_to_installation`）
与 **JSONB/时间还原**（`_timestamp`）：它们只服务"从 PG 行/列值还原 record"，
内存后端直接拿对象，用不到。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.db.repositories.base import BaseRepository
from app.db.repositories.plugin.records import (
    CATALOG_AVAILABLE,
    ORG_CAPABILITIES,
    STATUS_INSTALLED,
    VERSION_ACTIVE,
    CatalogRecord,
    InstallationConflictError,
    InstallationRecord,
    VersionRecord,
)
from app.db.session import platform_scope, tenant_scope

#: 目录表（共享行，`tenant_id='__platform__'`）
CATALOG_TABLE: Final[str] = "plugin_catalog"

#: 声明快照表（不可变）
VERSION_TABLE: Final[str] = "plugin_version"

#: 安装态表
INSTALLATION_TABLE: Final[str] = "plugin_installation"

#: 授权表
GRANT_TABLE: Final[str] = "plugin_grant"

#: 企业能力开关表
POLICY_TABLE: Final[str] = "plugin_capability_policy"

#: 平台哨兵租户（共享目录行所属）
PLATFORM_TENANT: Final[str] = "__platform__"


class _CatalogRepository(BaseRepository):
    """`plugin_catalog` 表仓储（平台共享行）。"""

    table = CATALOG_TABLE


class _VersionRepository(BaseRepository):
    """`plugin_version` 表仓储。"""

    table = VERSION_TABLE


class _InstallationRepository(BaseRepository):
    """`plugin_installation` 表仓储。"""

    table = INSTALLATION_TABLE


class _GrantRepository(BaseRepository):
    """`plugin_grant` 表仓储。"""

    table = GRANT_TABLE


class _PolicyRepository(BaseRepository):
    """`plugin_capability_policy` 表仓储。"""

    table = POLICY_TABLE


class PostgresPluginStore:
    """PostgreSQL 插件存储（一次调用一个短事务）。"""

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化。

        Args:
            settings: 可选配置实例。
        """
        self._settings = settings or get_settings()

    # ------------------------------------------------------------------
    # 目录 / 版本（平台作用域，共享行）
    # ------------------------------------------------------------------
    async def ensure_versions(self, records: Sequence[VersionRecord]) -> int:
        """幂等登记声明快照（`ON CONFLICT DO NOTHING`，版本不可变）。

        Args:
            records: 待登记的快照。

        Returns:
            int: 新插入条数。
        """
        inserted = 0
        async with platform_scope(self._settings) as uow:
            repo = _VersionRepository(uow)
            for record in records:
                row = await repo.fetch_one(
                    f"""
                    INSERT INTO {VERSION_TABLE} (
                        tenant_id, plugin_id, version, manifest, manifest_sha256,
                        min_platform_version, capabilities, actions, step_titles, status
                    ) VALUES (
                        :platform, :plugin_id, :version, CAST(:manifest AS jsonb), :sha256,
                        :min_platform_version, :capabilities, :actions, :step_titles, 'active'
                    )
                    ON CONFLICT (tenant_id, plugin_id, version) DO NOTHING
                    RETURNING plugin_id
                    """,
                    {
                        "tenant_id": PLATFORM_TENANT,
                        "platform": PLATFORM_TENANT,
                        "plugin_id": record.plugin_id,
                        "version": record.version,
                        "manifest": json.dumps(record.manifest, ensure_ascii=False, sort_keys=True),
                        "sha256": record.manifest_sha256,
                        "min_platform_version": record.min_platform_version,
                        "capabilities": list(record.capabilities),
                        "actions": list(record.actions),
                        "step_titles": list(record.step_titles),
                    },
                )
                if row is not None:
                    inserted += 1
        return inserted

    async def get_version(self, plugin_id: str, version: str) -> VersionRecord | None:
        """按版本读取声明快照。

        Args:
            plugin_id: 插件标识。
            version: 版本号。

        Returns:
            VersionRecord | None: 快照；不存在返回 None。
        """
        async with platform_scope(self._settings, read_only=True) as uow:
            repo = _VersionRepository(uow)
            row = await repo.fetch_one(
                f"""
                SELECT plugin_id, version, manifest, manifest_sha256, min_platform_version,
                       capabilities, actions, step_titles, status, published_at
                FROM {VERSION_TABLE}
                WHERE tenant_id = :platform AND plugin_id = :plugin_id AND version = :version
                """,
                {
                    "tenant_id": PLATFORM_TENANT,
                    "platform": PLATFORM_TENANT,
                    "plugin_id": plugin_id,
                    "version": version,
                },
            )
        return _to_version(row) if row is not None else None

    async def list_versions(self, plugin_id: str) -> list[VersionRecord]:
        """列出某插件的全部版本。

        Args:
            plugin_id: 插件标识。

        Returns:
            list[VersionRecord]: 版本列表（新→旧）。
        """
        async with platform_scope(self._settings, read_only=True) as uow:
            repo = _VersionRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT plugin_id, version, manifest, manifest_sha256, min_platform_version,
                       capabilities, actions, step_titles, status, published_at
                FROM {VERSION_TABLE}
                WHERE tenant_id = :platform AND plugin_id = :plugin_id
                ORDER BY published_at DESC
                """,
                {
                    "tenant_id": PLATFORM_TENANT,
                    "platform": PLATFORM_TENANT,
                    "plugin_id": plugin_id,
                },
            )
        return [_to_version(row) for row in rows]

    async def list_catalog(self, *, category: str = "", keyword: str = "") -> list[CatalogRecord]:
        """列出可售目录。

        Args:
            category: 分类过滤。
            keyword: 关键字。

        Returns:
            list[CatalogRecord]: 目录列表。
        """
        conditions = ["tenant_id = :platform", "status = 'available'"]
        parameters: dict[str, Any] = {"tenant_id": PLATFORM_TENANT, "platform": PLATFORM_TENANT}
        if category:
            conditions.append("category = :category")
            parameters["category"] = category
        if keyword:
            conditions.append(
                "(name ILIKE :keyword OR summary ILIKE :keyword OR description ILIKE :keyword)"
            )
            parameters["keyword"] = f"%{keyword}%"
        where_clause = " AND ".join(conditions)
        async with platform_scope(self._settings, read_only=True) as uow:
            repo = _CatalogRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT plugin_id, name, summary, description, category, icon_key, latest_version,
                       rating_avg, rating_count, install_count, status, sort_order
                FROM {CATALOG_TABLE}
                WHERE {where_clause}
                ORDER BY sort_order, plugin_id
                """,
                parameters,
            )
        return [_to_catalog(row) for row in rows]

    async def catalogs_of(self, plugin_ids: Sequence[str]) -> dict[str, CatalogRecord]:
        """批量读取目录行（含下架行；缺失的 id 不进字典）。

        一次 `= ANY(:plugin_ids)` 取回（审计 P2-6）：N 个插件 1 次查询，而不是 N 次。

        Args:
            plugin_ids: 插件标识列表（可重复，内部去重）。

        Returns:
            dict[str, CatalogRecord]: 插件标识 → 目录行。
        """
        wanted = list(dict.fromkeys(plugin_ids))
        if not wanted:
            return {}
        async with platform_scope(self._settings, read_only=True) as uow:
            repo = _CatalogRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT plugin_id, name, summary, description, category, icon_key, latest_version,
                       rating_avg, rating_count, install_count, status, sort_order
                FROM {CATALOG_TABLE}
                WHERE tenant_id = :platform AND plugin_id = ANY(:plugin_ids)
                """,
                {
                    "tenant_id": PLATFORM_TENANT,
                    "platform": PLATFORM_TENANT,
                    "plugin_ids": wanted,
                },
            )
        records = [_to_catalog(row) for row in rows]
        return {record.plugin_id: record for record in records}

    async def count_catalog_by_category(self) -> dict[str, int]:
        """按分类统计可售目录条数（`GROUP BY category`，口径同 `list_catalog()`）。

        Returns:
            dict[str, int]: 分类 → 条数（只含非零分类，缺省由服务层零填充）。
        """
        async with platform_scope(self._settings, read_only=True) as uow:
            repo = _CatalogRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT category, count(*) AS total
                FROM {CATALOG_TABLE}
                WHERE tenant_id = :platform AND status = 'available'
                GROUP BY category
                """,
                {"tenant_id": PLATFORM_TENANT, "platform": PLATFORM_TENANT},
            )
        return {str(row["category"] or ""): int(row["total"] or 0) for row in rows}

    async def get_catalog(self, plugin_id: str) -> CatalogRecord | None:
        """读取目录行（含下架行）。

        Args:
            plugin_id: 插件标识。

        Returns:
            CatalogRecord | None: 目录行；不存在返回 None。
        """
        async with platform_scope(self._settings, read_only=True) as uow:
            repo = _CatalogRepository(uow)
            row = await repo.fetch_one(
                f"""
                SELECT plugin_id, name, summary, description, category, icon_key, latest_version,
                       rating_avg, rating_count, install_count, status, sort_order
                FROM {CATALOG_TABLE}
                WHERE tenant_id = :platform AND plugin_id = :plugin_id
                """,
                {
                    "tenant_id": PLATFORM_TENANT,
                    "platform": PLATFORM_TENANT,
                    "plugin_id": plugin_id,
                },
            )
        return _to_catalog(row) if row is not None else None

    async def set_catalog_rating(
        self, plugin_id: str, *, rating_avg: float, rating_count: int
    ) -> None:
        """写回评分聚合。

        Args:
            plugin_id: 插件标识。
            rating_avg: 平均分。
            rating_count: 人数。
        """
        async with platform_scope(self._settings) as uow:
            repo = _CatalogRepository(uow)
            await repo.execute(
                f"""
                UPDATE {CATALOG_TABLE}
                SET rating_avg = :rating_avg, rating_count = :rating_count, updated_at = now()
                WHERE tenant_id = :platform AND plugin_id = :plugin_id
                """,
                {
                    "tenant_id": PLATFORM_TENANT,
                    "platform": PLATFORM_TENANT,
                    "plugin_id": plugin_id,
                    "rating_avg": round(float(rating_avg), 1),
                    "rating_count": int(rating_count),
                },
            )

    async def adjust_install_count(self, plugin_id: str, delta: int) -> None:
        """调整安装企业数（不为负）。

        Args:
            plugin_id: 插件标识。
            delta: 增量。
        """
        async with platform_scope(self._settings) as uow:
            repo = _CatalogRepository(uow)
            await repo.execute(
                f"""
                UPDATE {CATALOG_TABLE}
                SET install_count = GREATEST(install_count + :delta, 0), updated_at = now()
                WHERE tenant_id = :platform AND plugin_id = :plugin_id
                """,
                {
                    "tenant_id": PLATFORM_TENANT,
                    "platform": PLATFORM_TENANT,
                    "plugin_id": plugin_id,
                    "delta": int(delta),
                },
            )

    # ------------------------------------------------------------------
    # 安装态（租户作用域）
    # ------------------------------------------------------------------
    async def get_installation(self, tenant_id: str, plugin_id: str) -> InstallationRecord | None:
        """读取安装行。

        Args:
            tenant_id: 企业 ID。
            plugin_id: 插件标识。

        Returns:
            InstallationRecord | None: 安装行；不存在返回 None。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _InstallationRepository(uow)
            row = await repo.fetch_one(
                f"""
                SELECT tenant_id, plugin_id, version, previous_version, status, settings,
                       capability_version, installed_by, installed_at, enabled_at,
                       disabled_at, uninstalled_at, last_used_at
                FROM {INSTALLATION_TABLE}
                WHERE {repo.tenant_clause()} AND plugin_id = :plugin_id
                """,
                repo.params(plugin_id=plugin_id),
            )
        return _to_installation(row) if row is not None else None

    async def list_installations(
        self, tenant_id: str, *, status: str = ""
    ) -> list[InstallationRecord]:
        """列出企业的安装行。

        Args:
            tenant_id: 企业 ID。
            status: 状态过滤。

        Returns:
            list[InstallationRecord]: 安装行列表。
        """
        status_filter = "AND status = :status" if status else ""
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _InstallationRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT tenant_id, plugin_id, version, previous_version, status, settings,
                       capability_version, installed_by, installed_at, enabled_at,
                       disabled_at, uninstalled_at, last_used_at
                FROM {INSTALLATION_TABLE}
                WHERE {repo.tenant_clause()} {status_filter}
                ORDER BY plugin_id
                """,
                repo.params(status=status),
            )
        return [_to_installation(row) for row in rows]

    async def save_installation(self, record: InstallationRecord) -> InstallationRecord:
        """写入或更新安装行。

        Args:
            record: 安装行。

        Returns:
            InstallationRecord: 落库后的安装行。
        """
        async with tenant_scope(record.tenant_id, settings=self._settings) as uow:
            repo = _InstallationRepository(uow)
            row = await repo.fetch_one(
                f"""
                INSERT INTO {INSTALLATION_TABLE} (
                    tenant_id, plugin_id, version, previous_version, status, settings,
                    capability_version, installed_by, enabled_at, disabled_at, uninstalled_at
                ) VALUES (
                    :tenant_id, :plugin_id, :version, NULLIF(:previous_version, ''), :status,
                    CAST(:settings AS jsonb), :capability_version, :installed_by,
                    CAST(NULLIF(:enabled_at, '') AS timestamptz),
                    CAST(NULLIF(:disabled_at, '') AS timestamptz),
                    CAST(NULLIF(:uninstalled_at, '') AS timestamptz)
                )
                ON CONFLICT (tenant_id, plugin_id) DO UPDATE SET
                    version = EXCLUDED.version,
                    previous_version = EXCLUDED.previous_version,
                    status = EXCLUDED.status,
                    settings = EXCLUDED.settings,
                    capability_version = EXCLUDED.capability_version,
                    enabled_at = COALESCE(EXCLUDED.enabled_at, {INSTALLATION_TABLE}.enabled_at),
                    disabled_at = COALESCE(EXCLUDED.disabled_at, {INSTALLATION_TABLE}.disabled_at),
                    uninstalled_at = COALESCE(
                        EXCLUDED.uninstalled_at, {INSTALLATION_TABLE}.uninstalled_at
                    )
                RETURNING tenant_id, plugin_id, version, previous_version, status, settings,
                          capability_version, installed_by, installed_at, enabled_at,
                          disabled_at, uninstalled_at, last_used_at
                """,
                repo.params(
                    plugin_id=record.plugin_id,
                    version=record.version,
                    previous_version=record.previous_version,
                    status=record.status,
                    settings=json.dumps(record.settings, ensure_ascii=False, sort_keys=True),
                    capability_version=record.capability_version,
                    installed_by=record.installed_by,
                    enabled_at=record.enabled_at,
                    disabled_at=record.disabled_at,
                    uninstalled_at=record.uninstalled_at,
                ),
            )
        if row is None:  # pragma: no cover - RETURNING 缺失属数据库异常
            raise ApiError(ErrorCode.INTERNAL_ERROR, internal_detail="installation upsert lost")
        return _to_installation(row)

    async def create_installation(self, record: InstallationRecord) -> InstallationRecord:
        """**仅插入**安装行（`ON CONFLICT DO NOTHING` → 冲突翻译成 `InstallationConflictError`）。

        为什么不用 `ON CONFLICT DO UPDATE`：并发安装时后者会让两个请求都"成功"，
        且后到者把先到者的 `settings`/`capability_version` 覆盖掉——用户看到已安装，
        但配置不是自己刚填的那份。这里宁可返回 `16010`（请刷新后重试），
        也不静默覆盖（架构 §5.4 `16010` 场景 ①）。

        Args:
            record: 安装行。

        Returns:
            InstallationRecord: 落库后的安装行。

        Raises:
            InstallationConflictError: 唯一键 `uq_installation_active` 冲突（并发安装）。
        """
        async with tenant_scope(record.tenant_id, settings=self._settings) as uow:
            repo = _InstallationRepository(uow)
            row = await repo.fetch_one(
                f"""
                INSERT INTO {INSTALLATION_TABLE} (
                    tenant_id, plugin_id, version, previous_version, status, settings,
                    capability_version, installed_by, enabled_at, disabled_at, uninstalled_at
                ) VALUES (
                    :tenant_id, :plugin_id, :version, NULLIF(:previous_version, ''), :status,
                    CAST(:settings AS jsonb), :capability_version, :installed_by,
                    CAST(NULLIF(:enabled_at, '') AS timestamptz),
                    CAST(NULLIF(:disabled_at, '') AS timestamptz),
                    CAST(NULLIF(:uninstalled_at, '') AS timestamptz)
                )
                ON CONFLICT (tenant_id, plugin_id) DO NOTHING
                RETURNING tenant_id, plugin_id, version, previous_version, status, settings,
                          capability_version, installed_by, installed_at, enabled_at,
                          disabled_at, uninstalled_at, last_used_at
                """,
                repo.params(
                    plugin_id=record.plugin_id,
                    version=record.version,
                    previous_version=record.previous_version,
                    status=record.status,
                    settings=json.dumps(record.settings, ensure_ascii=False, sort_keys=True),
                    capability_version=record.capability_version,
                    installed_by=record.installed_by,
                    enabled_at=record.enabled_at,
                    disabled_at=record.disabled_at,
                    uninstalled_at=record.uninstalled_at,
                ),
            )
        if row is None:
            raise InstallationConflictError(
                f"installation exists: {record.tenant_id}/{record.plugin_id}"
            )
        return _to_installation(row)

    async def swap_version(
        self,
        tenant_id: str,
        plugin_id: str,
        *,
        expected_version: str,
        target_version: str,
    ) -> InstallationRecord | None:
        """乐观锁切换版本指针（`WHERE version = :expected`，命中 0 行即指针竞争）。

        Args:
            tenant_id: 企业 ID。
            plugin_id: 插件标识。
            expected_version: 调用方观测到的当前版本。
            target_version: 目标版本。

        Returns:
            InstallationRecord | None: 切换后的安装行；指针已被他人改动返回 None。
        """
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _InstallationRepository(uow)
            row = await repo.fetch_one(
                f"""
                UPDATE {INSTALLATION_TABLE}
                   SET previous_version = version,
                       version = :target_version,
                       capability_version = capability_version + 1
                 WHERE tenant_id = :tenant_id
                   AND plugin_id = :plugin_id
                   AND version = :expected_version
                RETURNING tenant_id, plugin_id, version, previous_version, status, settings,
                          capability_version, installed_by, installed_at, enabled_at,
                          disabled_at, uninstalled_at, last_used_at
                """,
                repo.params(
                    plugin_id=plugin_id,
                    expected_version=expected_version,
                    target_version=target_version,
                ),
            )
        if row is None:
            return None
        return _to_installation(row)

    # ------------------------------------------------------------------
    # 授权
    # ------------------------------------------------------------------
    async def get_grants(self, tenant_id: str, plugin_id: str, user_id: str) -> set[str]:
        """读取有效能力集合。

        Args:
            tenant_id: 企业 ID。
            plugin_id: 插件标识。
            user_id: 成员 ID。

        Returns:
            set[str]: 有效能力集合。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _GrantRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT capability FROM {GRANT_TABLE}
                WHERE {repo.tenant_clause()} AND plugin_id = :plugin_id
                  AND user_id = :user_id AND revoked_at IS NULL
                """,
                repo.params(plugin_id=plugin_id, user_id=user_id),
            )
        return {str(row["capability"]) for row in rows}

    async def set_grants(
        self,
        tenant_id: str,
        plugin_id: str,
        user_id: str,
        capabilities: Iterable[str],
    ) -> set[str]:
        """覆盖写入成员能力。

        Args:
            tenant_id: 企业 ID。
            plugin_id: 插件标识。
            user_id: 成员 ID。
            capabilities: 目标有效集合。

        Returns:
            set[str]: 写入后的有效集合。
        """
        targets = sorted(set(capabilities))
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _GrantRepository(uow)
            await repo.execute(
                f"""
                UPDATE {GRANT_TABLE}
                SET revoked_at = now()
                WHERE {repo.tenant_clause()} AND plugin_id = :plugin_id
                  AND user_id = :user_id AND revoked_at IS NULL
                  AND NOT (capability = ANY(:targets))
                """,
                repo.params(plugin_id=plugin_id, user_id=user_id, targets=targets),
            )
            for capability in targets:
                await repo.execute(
                    f"""
                    INSERT INTO {GRANT_TABLE} (
                        tenant_id, plugin_id, user_id, capability, revoked_at
                    ) VALUES (:tenant_id, :plugin_id, :user_id, :capability, NULL)
                    ON CONFLICT (tenant_id, plugin_id, user_id, capability) DO UPDATE
                        SET revoked_at = NULL, granted_at = now()
                    """,
                    repo.params(plugin_id=plugin_id, user_id=user_id, capability=capability),
                )
        return set(targets)

    # ------------------------------------------------------------------
    # 企业能力开关
    # ------------------------------------------------------------------
    async def list_policies(self, tenant_id: str) -> dict[str, bool]:
        """读取企业能力开关。

        Args:
            tenant_id: 企业 ID。

        Returns:
            dict[str, bool]: 能力 → 是否开启（含全部 `org.*`）。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _PolicyRepository(uow)
            rows = await repo.fetch_all(
                f"SELECT capability, enabled FROM {POLICY_TABLE} WHERE {repo.tenant_clause()}",
            )
        stored = {str(row["capability"]): bool(row["enabled"]) for row in rows}
        return {cap: stored.get(cap, False) for cap in ORG_CAPABILITIES}

    async def save_policies(
        self, tenant_id: str, values: Mapping[str, bool], actor_id: str
    ) -> dict[str, bool]:
        """写入企业能力开关。

        Args:
            tenant_id: 企业 ID。
            values: 能力 → 是否开启。
            actor_id: 操作人。

        Returns:
            dict[str, bool]: 写入后的全量开关。
        """
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _PolicyRepository(uow)
            for capability, enabled in values.items():
                if capability not in ORG_CAPABILITIES:
                    continue
                await repo.execute(
                    f"""
                    INSERT INTO {POLICY_TABLE} (tenant_id, capability, enabled, updated_by)
                    VALUES (:tenant_id, :capability, :enabled, :updated_by)
                    ON CONFLICT (tenant_id, capability) DO UPDATE
                        SET enabled = EXCLUDED.enabled,
                            updated_by = EXCLUDED.updated_by,
                            updated_at = now()
                    """,
                    repo.params(capability=capability, enabled=bool(enabled), updated_by=actor_id),
                )
        return await self.list_policies(tenant_id)


def _to_catalog(row: Mapping[str, Any]) -> CatalogRecord:
    """把数据库行映射为目录记录。

    Args:
        row: 行映射。

    Returns:
        CatalogRecord: 目录记录。
    """
    return CatalogRecord(
        plugin_id=str(row.get("plugin_id", "")),
        name=str(row.get("name", "")),
        summary=str(row.get("summary", "")),
        description=str(row.get("description", "")),
        category=str(row.get("category", "")),
        icon_key=str(row.get("icon_key", "")),
        latest_version=str(row.get("latest_version", "1.0.0")),
        rating_avg=float(row.get("rating_avg", 0) or 0),
        rating_count=int(row.get("rating_count", 0) or 0),
        install_count=int(row.get("install_count", 0) or 0),
        status=str(row.get("status", CATALOG_AVAILABLE)),
        sort_order=int(row.get("sort_order", 0) or 0),
    )


def _to_version(row: Mapping[str, Any]) -> VersionRecord:
    """把数据库行映射为版本记录。

    Args:
        row: 行映射。

    Returns:
        VersionRecord: 版本记录。
    """
    raw_manifest = row.get("manifest")
    if isinstance(raw_manifest, str):
        try:
            manifest = json.loads(raw_manifest)
        except ValueError:  # pragma: no cover - 库内 JSONB 不会是坏字符串
            manifest = {}
    else:
        manifest = dict(raw_manifest or {})
    return VersionRecord(
        plugin_id=str(row.get("plugin_id", "")),
        version=str(row.get("version", "")),
        manifest=manifest,
        manifest_sha256=str(row.get("manifest_sha256", "")),
        min_platform_version=str(row.get("min_platform_version", "1.0.0")),
        capabilities=tuple(str(item) for item in (row.get("capabilities") or ())),
        actions=tuple(str(item) for item in (row.get("actions") or ())),
        step_titles=tuple(str(item) for item in (row.get("step_titles") or ())),
        status=str(row.get("status", VERSION_ACTIVE)),
        published_at=_timestamp(row.get("published_at")),
    )


def _to_installation(row: Mapping[str, Any]) -> InstallationRecord:
    """把数据库行映射为安装记录。

    Args:
        row: 行映射。

    Returns:
        InstallationRecord: 安装记录。
    """
    raw_settings = row.get("settings")
    if isinstance(raw_settings, str):
        try:
            settings = json.loads(raw_settings)
        except ValueError:  # pragma: no cover
            settings = {}
    else:
        settings = dict(raw_settings or {})
    return InstallationRecord(
        tenant_id=str(row.get("tenant_id", "")),
        plugin_id=str(row.get("plugin_id", "")),
        version=str(row.get("version", "1.0.0")),
        previous_version=str(row.get("previous_version", "") or ""),
        status=str(row.get("status", STATUS_INSTALLED)),
        settings=settings,
        capability_version=int(row.get("capability_version", 1) or 1),
        installed_by=str(row.get("installed_by", "") or ""),
        installed_at=_timestamp(row.get("installed_at")),
        enabled_at=_timestamp(row.get("enabled_at")),
        disabled_at=_timestamp(row.get("disabled_at")),
        uninstalled_at=_timestamp(row.get("uninstalled_at")),
        last_used_at=_timestamp(row.get("last_used_at")),
    )


def _timestamp(value: Any) -> str:
    """把数据库时间值规范化为 ISO 8601 字符串。

    Args:
        value: 时间值。

    Returns:
        str: ISO 8601 字符串；空值返回空串。
    """
    from datetime import UTC, datetime

    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return str(value)


__all__: list[str] = [
    "CATALOG_TABLE",
    "GRANT_TABLE",
    "INSTALLATION_TABLE",
    "PLATFORM_TENANT",
    "POLICY_TABLE",
    "VERSION_TABLE",
    "PostgresPluginStore",
]
