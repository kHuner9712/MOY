# ruff: noqa: S608 - 表名/列名均为模块常量（经 assert_identifier 校验），值一律命名参数绑定。
"""插件域仓储（阶段三 B17，`architecture-phase3.md` §4.1 / §4.4）。

覆盖 B17 需要的 5 张表：`plugin_catalog`（共享目录）/ `plugin_version`（不可变快照）/
`plugin_installation`（安装态 + 版本双指针）/ `plugin_grant`（成员 × 插件 × 能力）/
`plugin_capability_policy`（企业能力总开关）。B18 再补 `plugin_review`，
B19 补 `plugin_task*` 与 `plugin_artifact`（分别在 `task_repo` / `artifact_repo`）。

**双后端**（沿用阶段二 `billing_repo` 体例）：`memory` 供开发/测试（无 PG 也全绿），
`postgres` 为生产实现，两条路径方法签名与语义完全一致。

**两个关键口径**：

1. **声明快照不手写**：`plugin_version` 行由 `ensure_versions()` 从
   `app/plugins/manifests/*.json` 幂等登记（`ON CONFLICT DO NOTHING`），
   `manifest_sha256` 与文件内容**同源同算**。把 6 份 JSON 复制进 SQL 会立刻产生
   「库里的 manifest 与磁盘不一致」的漂移源，而版本不可变的判据正是 sha256 一致。
2. **授权撤销用 `revoked_at` 软失效**：历史任务审计要能回答"当时授没授"；
   物理删除会把这条证据抹掉。查询侧统一过滤 `revoked_at IS NULL`（走部分索引）。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Final, Protocol

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.db.repositories.base import BaseRepository
from app.db.session import platform_scope, tenant_scope
from app.plugins.actions import ORG_CAPABILITIES
from app.plugins.loader import PluginRegistry, get_registry, manifest_digest

logger = get_logger(__name__)

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

#: 安装态取值
STATUS_INSTALLED: Final[str] = "installed"
STATUS_ENABLED: Final[str] = "enabled"
STATUS_DISABLED: Final[str] = "disabled"
STATUS_UNINSTALLED: Final[str] = "uninstalled"

#: 「已添加」的状态集合（`not_installed` = 无行或 uninstalled）
LIVE_STATUSES: Final[tuple[str, ...]] = (STATUS_INSTALLED, STATUS_ENABLED, STATUS_DISABLED)

#: 目录可售状态
CATALOG_AVAILABLE: Final[str] = "available"

#: 版本状态
VERSION_ACTIVE: Final[str] = "active"
VERSION_DEPRECATED: Final[str] = "deprecated"

#: 安装态可变字段白名单（防动态列名注入）
INSTALLATION_MUTABLE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "version",
        "previous_version",
        "status",
        "settings",
        "capability_version",
        "enabled_at",
        "disabled_at",
        "uninstalled_at",
        "last_used_at",
    }
)


def now_iso() -> str:
    """当前 UTC 时间（ISO 8601）。

    Returns:
        str: 形如 `2026-11-02T09:00:00Z`。
    """
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class CatalogRecord:
    """市场目录行（`plugin_catalog`）。

    Attributes:
        plugin_id: 插件标识。
        name: 插件名称。
        summary: 一句话说明。
        description: 详细说明。
        category: 市场分类。
        icon_key: 图标名。
        latest_version: 最新版本。
        rating_avg: 平均评分。
        rating_count: 评分人数。
        install_count: 安装企业数。
        status: 上架状态（`available` / `hidden` / `offline`）。
        sort_order: 排序号。
    """

    plugin_id: str = ""
    name: str = ""
    summary: str = ""
    description: str = ""
    category: str = ""
    icon_key: str = ""
    latest_version: str = "1.0.0"
    rating_avg: float = 0.0
    rating_count: int = 0
    install_count: int = 0
    status: str = CATALOG_AVAILABLE
    sort_order: int = 0

    @property
    def installable(self) -> bool:
        """目录侧是否可安装（只看上架状态，套餐判定在服务层）。

        Returns:
            bool: 上架中返回 True。
        """
        return self.status == CATALOG_AVAILABLE


@dataclass(frozen=True, slots=True)
class VersionRecord:
    """声明快照行（`plugin_version`，**不可变**）。

    Attributes:
        plugin_id: 插件标识。
        version: 版本号。
        manifest: 声明全文。
        manifest_sha256: 声明摘要（防篡改）。
        min_platform_version: 最低平台版本。
        capabilities: 能力键快照。
        actions: 原语快照。
        step_titles: 步骤标题快照（历史任务渲染用）。
        status: `active` / `deprecated` / `revoked`。
        published_at: 发布时间。
    """

    plugin_id: str = ""
    version: str = ""
    manifest: dict[str, Any] = field(default_factory=dict)
    manifest_sha256: str = ""
    min_platform_version: str = "1.0.0"
    capabilities: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    step_titles: tuple[str, ...] = ()
    status: str = VERSION_ACTIVE
    published_at: str = ""


@dataclass(frozen=True, slots=True)
class InstallationRecord:
    """安装态行（`plugin_installation`）。

    Attributes:
        tenant_id: 企业 ID。
        plugin_id: 插件标识。
        version: 当前版本。
        previous_version: 上一版本（只保留 1 个，支持回退 1 次）。
        status: `installed` / `enabled` / `disabled` / `uninstalled`。
        settings: 插件配置（键 ⊆ 声明 `settings_schema`）。
        capability_version: 能力版本（授权变更时 +1，供前端缓存失效）。
        installed_by: 安装人。
        installed_at: 安装时间。
        enabled_at: 最近启用时间。
        disabled_at: 最近停用时间。
        uninstalled_at: 卸载时间。
        last_used_at: 最近使用时间。
    """

    tenant_id: str = ""
    plugin_id: str = ""
    version: str = "1.0.0"
    previous_version: str = ""
    status: str = STATUS_ENABLED
    settings: dict[str, Any] = field(default_factory=dict)
    capability_version: int = 1
    installed_by: str = ""
    installed_at: str = ""
    enabled_at: str = ""
    disabled_at: str = ""
    uninstalled_at: str = ""
    last_used_at: str = ""

    @property
    def is_live(self) -> bool:
        """是否处于「已添加」状态（未卸载）。

        Returns:
            bool: 已添加返回 True。
        """
        return self.status in LIVE_STATUSES


class InstallationConflictError(RuntimeError):
    """安装行并发写入冲突（业务层翻译为 `16010`）。

    只用于**两个**场景（架构 §5.4 `16010` 的内部含义，多一个都不许）：

    1. 首次安装的唯一键冲突（`uq_installation_active`：并发两个请求都判"未安装"，
       其中一个插入时撞主键）；
    2. `plugin_installation.version` 指针竞争（并发升级/回退时，乐观锁
       `WHERE version = 观测到的版本` 命中 0 行）。

    **不复用给**"缺授权（16005）/企业未开放（16006）/未添加（16003）"等语义冲突——
    那是各自语义码的职责，把它们塞进 `16010` 会让前端失去「该刷新」与「该补授权」
    的区分。
    """


class PluginStore(Protocol):
    """插件域存储契约（内存 / PostgreSQL 双实现）。"""

    async def ensure_versions(self, records: Sequence[VersionRecord]) -> int:
        """幂等登记声明快照，返回新插入条数。"""
        ...

    async def get_version(self, plugin_id: str, version: str) -> VersionRecord | None:
        """按版本读取声明快照。"""
        ...

    async def list_versions(self, plugin_id: str) -> list[VersionRecord]:
        """列出某插件的全部版本（新→旧）。"""
        ...

    async def list_catalog(self, *, category: str = "", keyword: str = "") -> list[CatalogRecord]:
        """列出可售目录（`status='available'`，按排序号）。"""
        ...

    async def get_catalog(self, plugin_id: str) -> CatalogRecord | None:
        """读取目录行（含下架行，由服务层判 `16002`）。"""
        ...

    async def set_catalog_rating(
        self, plugin_id: str, *, rating_avg: float, rating_count: int
    ) -> None:
        """写回评分聚合（评价提交后重算）。"""
        ...

    async def adjust_install_count(self, plugin_id: str, delta: int) -> None:
        """调整安装企业数（安装 +1 / 卸载 -1，不为负）。"""
        ...

    async def get_installation(self, tenant_id: str, plugin_id: str) -> InstallationRecord | None:
        """读取安装行（含已卸载行）。"""
        ...

    async def list_installations(
        self, tenant_id: str, *, status: str = ""
    ) -> list[InstallationRecord]:
        """列出企业的安装行。"""
        ...

    async def save_installation(self, record: InstallationRecord) -> InstallationRecord:
        """写入（或按 `(tenant_id, plugin_id)` 更新）安装行。"""
        ...

    async def create_installation(self, record: InstallationRecord) -> InstallationRecord:
        """**仅插入**安装行；行已存在时抛 `InstallationConflictError`（唯一键语义）。"""
        ...

    async def swap_version(
        self,
        tenant_id: str,
        plugin_id: str,
        *,
        expected_version: str,
        target_version: str,
    ) -> InstallationRecord | None:
        """乐观锁切换版本指针；`version` 已被他人改动时返回 `None`（指针竞争）。"""
        ...

    async def get_grants(self, tenant_id: str, plugin_id: str, user_id: str) -> set[str]:
        """读取某成员在某插件上的**有效**能力集合。"""
        ...

    async def set_grants(
        self,
        tenant_id: str,
        plugin_id: str,
        user_id: str,
        capabilities: Iterable[str],
    ) -> set[str]:
        """覆盖写入成员能力（新增授权 / 撤销未列出的能力），返回有效集合。"""
        ...

    async def list_policies(self, tenant_id: str) -> dict[str, bool]:
        """读取企业能力开关（未登记视为关闭）。"""
        ...

    async def save_policies(
        self, tenant_id: str, values: Mapping[str, bool], actor_id: str
    ) -> dict[str, bool]:
        """写入企业能力开关（仅 `org.*`）。"""
        ...


class InMemoryPluginStore:
    """进程内插件存储（development/test）。

    目录与版本行从 `PluginRegistry` 播种（内存模式没有 SQL 种子），
    与 PG 模式同源同算：`manifest_sha256` 都是对磁盘文件算的。
    """

    def __init__(self, registry: PluginRegistry | None = None) -> None:
        """初始化并播种目录与声明快照。

        Args:
            registry: 可选注册表（测试注入）。
        """
        import asyncio

        self._registry = registry
        self._catalog: dict[str, CatalogRecord] = {}
        self._versions: dict[tuple[str, str], VersionRecord] = {}
        self._installations: dict[tuple[str, str], InstallationRecord] = {}
        self._grants: dict[tuple[str, str, str], dict[str, str]] = {}
        self._policies: dict[str, dict[str, bool]] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 目录 / 版本
    # ------------------------------------------------------------------
    def _seed(self) -> None:
        """从注册表播种目录与版本（幂等；只补缺失项）。"""
        if self._catalog:
            return
        registry = self._registry or get_registry()
        for index, manifest in enumerate(registry.all_manifests(), start=1):
            self._catalog[manifest.plugin_id] = CatalogRecord(
                plugin_id=manifest.plugin_id,
                name=manifest.name,
                summary=manifest.summary,
                description=manifest.description,
                category=manifest.category,
                icon_key=manifest.icon_key,
                latest_version=registry.latest(manifest.plugin_id) or manifest.version,
                rating_avg=0.0,
                rating_count=0,
                install_count=0,
                status=CATALOG_AVAILABLE,
                sort_order=manifest.ui_extension.order or index,
            )
            for version in registry.versions(manifest.plugin_id):
                record = registry.record(manifest.plugin_id, version)
                if record is None:
                    continue
                self._versions[(manifest.plugin_id, version)] = VersionRecord(
                    plugin_id=manifest.plugin_id,
                    version=version,
                    manifest=record.manifest.model_dump(mode="json"),
                    manifest_sha256=record.sha256,
                    min_platform_version=record.manifest.min_platform_version,
                    capabilities=record.manifest.capability_keys,
                    actions=record.manifest.action_keys,
                    step_titles=tuple(record.manifest.step_titles()),
                    status=VERSION_ACTIVE,
                    published_at=now_iso(),
                )

    async def ensure_versions(self, records: Sequence[VersionRecord]) -> int:
        """幂等登记声明快照。

        Args:
            records: 待登记的快照。

        Returns:
            int: 新插入的条数。
        """
        inserted = 0
        async with self._lock:
            self._seed()
            for record in records:
                key = (record.plugin_id, record.version)
                if key in self._versions:
                    continue
                self._versions[key] = record
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
        async with self._lock:
            self._seed()
            return self._versions.get((plugin_id, version))

    async def list_versions(self, plugin_id: str) -> list[VersionRecord]:
        """列出某插件的全部版本（新→旧）。

        Args:
            plugin_id: 插件标识。

        Returns:
            list[VersionRecord]: 版本列表。
        """
        from app.plugins.loader import version_tuple

        async with self._lock:
            self._seed()
            items = [rec for (pid, _), rec in self._versions.items() if pid == plugin_id]
        items.sort(key=lambda item: version_tuple(item.version), reverse=True)
        return items

    async def list_catalog(self, *, category: str = "", keyword: str = "") -> list[CatalogRecord]:
        """列出可售目录。

        Args:
            category: 分类过滤。
            keyword: 名称/说明关键字。

        Returns:
            list[CatalogRecord]: 目录列表（按排序号）。
        """
        async with self._lock:
            self._seed()
            items = [rec for rec in self._catalog.values() if rec.installable]
        if category:
            items = [item for item in items if item.category == category]
        needle = keyword.strip().lower()
        if needle:
            items = [
                item
                for item in items
                if needle in item.name.lower()
                or needle in item.summary.lower()
                or needle in item.description.lower()
            ]
        items.sort(key=lambda item: (item.sort_order, item.plugin_id))
        return items

    async def get_catalog(self, plugin_id: str) -> CatalogRecord | None:
        """读取目录行。

        Args:
            plugin_id: 插件标识。

        Returns:
            CatalogRecord | None: 目录行；不存在返回 None。
        """
        async with self._lock:
            self._seed()
            return self._catalog.get(plugin_id)

    async def set_catalog_rating(
        self, plugin_id: str, *, rating_avg: float, rating_count: int
    ) -> None:
        """写回评分聚合。

        Args:
            plugin_id: 插件标识。
            rating_avg: 平均分。
            rating_count: 人数。
        """
        async with self._lock:
            self._seed()
            current = self._catalog.get(plugin_id)
            if current is None:
                return
            self._catalog[plugin_id] = replace(
                current, rating_avg=rating_avg, rating_count=rating_count
            )

    async def adjust_install_count(self, plugin_id: str, delta: int) -> None:
        """调整安装企业数（不为负）。

        Args:
            plugin_id: 插件标识。
            delta: 增量。
        """
        async with self._lock:
            self._seed()
            current = self._catalog.get(plugin_id)
            if current is None:
                return
            self._catalog[plugin_id] = replace(
                current, install_count=max(current.install_count + delta, 0)
            )

    # ------------------------------------------------------------------
    # 安装态
    # ------------------------------------------------------------------
    async def get_installation(self, tenant_id: str, plugin_id: str) -> InstallationRecord | None:
        """读取安装行。

        Args:
            tenant_id: 企业 ID。
            plugin_id: 插件标识。

        Returns:
            InstallationRecord | None: 安装行；不存在返回 None。
        """
        async with self._lock:
            return self._installations.get((tenant_id, plugin_id))

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
        async with self._lock:
            items = [rec for (tid, _), rec in self._installations.items() if tid == tenant_id]
        if status:
            items = [item for item in items if item.status == status]
        items.sort(key=lambda item: item.plugin_id)
        return items

    async def save_installation(self, record: InstallationRecord) -> InstallationRecord:
        """写入或更新安装行。

        Args:
            record: 安装行。

        Returns:
            InstallationRecord: 落库后的安装行。
        """
        async with self._lock:
            current = self._installations.get((record.tenant_id, record.plugin_id))
            payload = replace(
                record,
                installed_at=record.installed_at
                or (current.installed_at if current else now_iso()),
            )
            self._installations[(record.tenant_id, record.plugin_id)] = payload
        return payload

    async def create_installation(self, record: InstallationRecord) -> InstallationRecord:
        """**仅插入**安装行（模拟 `uq_installation_active` 唯一键）。

        并发安装的判据必须是"插入时才发现已存在"，不能用"先查后插"——
        先查后插在并发下两个请求都读到"没有"，然后各写一次，后者覆盖前者，
        用户看到的是"装成功了但版本/配置被另一个请求改掉"。

        Args:
            record: 安装行。

        Returns:
            InstallationRecord: 落库后的安装行。

        Raises:
            InstallationConflictError: 该企业已存在该插件的安装行。
        """
        key = (record.tenant_id, record.plugin_id)
        async with self._lock:
            if key in self._installations:
                raise InstallationConflictError(
                    f"installation exists: {record.tenant_id}/{record.plugin_id}"
                )
            payload = replace(record, installed_at=record.installed_at or now_iso())
            self._installations[key] = payload
        return payload

    async def swap_version(
        self,
        tenant_id: str,
        plugin_id: str,
        *,
        expected_version: str,
        target_version: str,
    ) -> InstallationRecord | None:
        """乐观锁切换版本指针（并发升级/回退时指针被他人改动 → 返回 `None`）。

        Args:
            tenant_id: 企业 ID。
            plugin_id: 插件标识。
            expected_version: 调用方观测到的当前版本。
            target_version: 目标版本。

        Returns:
            InstallationRecord | None: 切换后的安装行；指针已被改动返回 None。
        """
        async with self._lock:
            current = self._installations.get((tenant_id, plugin_id))
            if current is None or current.version != expected_version:
                return None
            payload = replace(
                current,
                version=target_version,
                previous_version=current.version,
                capability_version=current.capability_version + 1,
            )
            self._installations[(tenant_id, plugin_id)] = payload
        return payload

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
        async with self._lock:
            table = self._grants.get((tenant_id, plugin_id, user_id), {})
            return {cap for cap, revoked in table.items() if not revoked}

    async def set_grants(
        self,
        tenant_id: str,
        plugin_id: str,
        user_id: str,
        capabilities: Iterable[str],
    ) -> set[str]:
        """覆盖写入成员能力（撤销记录保留 revoked_at）。

        Args:
            tenant_id: 企业 ID。
            plugin_id: 插件标识。
            user_id: 成员 ID。
            capabilities: 目标有效能力集合。

        Returns:
            set[str]: 写入后的有效集合。
        """
        targets = set(capabilities)
        key = (tenant_id, plugin_id, user_id)
        async with self._lock:
            table = dict(self._grants.get(key, {}))
            stamp = now_iso()
            for capability in targets:
                table[capability] = ""
            for capability, revoked in list(table.items()):
                if capability not in targets and not revoked:
                    table[capability] = stamp
            self._grants[key] = table
        return targets

    # ------------------------------------------------------------------
    # 企业能力开关
    # ------------------------------------------------------------------
    async def list_policies(self, tenant_id: str) -> dict[str, bool]:
        """读取企业能力开关（未登记视为关闭）。

        Args:
            tenant_id: 企业 ID。

        Returns:
            dict[str, bool]: 能力 → 是否开启（含全部 `org.*` 键）。
        """
        async with self._lock:
            stored = dict(self._policies.get(tenant_id, {}))
        return {cap: bool(stored.get(cap, False)) for cap in ORG_CAPABILITIES}

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
        del actor_id
        async with self._lock:
            stored = dict(self._policies.get(tenant_id, {}))
            for capability, enabled in values.items():
                if capability in ORG_CAPABILITIES:
                    stored[capability] = bool(enabled)
            self._policies[tenant_id] = stored
            return {cap: bool(stored.get(cap, False)) for cap in ORG_CAPABILITIES}

    def clear(self) -> None:
        """清空业务数据（保留目录与版本种子；测试隔离用）。"""
        self._installations.clear()
        self._grants.clear()
        self._policies.clear()

    def counts(self) -> dict[str, int]:
        """返回各表行数（测试断言用）。

        Returns:
            dict[str, int]: 行数统计。
        """
        self._seed()
        return {
            "catalog": len(self._catalog),
            "versions": len(self._versions),
            "installations": len(self._installations),
            "grants": sum(len(item) for item in self._grants.values()),
        }


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


def version_records_from_registry(registry: PluginRegistry | None = None) -> list[VersionRecord]:
    """把注册表中的声明转成待登记的快照行。

    `manifest_sha256` 与 `loader.manifest_digest()` 同源，确保"库里摘要 == 磁盘摘要"
    （版本不可变判据）；`step_titles` 一并快照，历史任务按创建时版本渲染（D-18）。

    Args:
        registry: 可选注册表。

    Returns:
        list[VersionRecord]: 快照行列表（每个已注册版本一条）。
    """
    source = registry or get_registry()
    records: list[VersionRecord] = []
    for plugin_id in source.plugin_ids():
        for version in source.versions(plugin_id):
            record = source.record(plugin_id, version)
            if record is None:  # pragma: no cover - 刚枚举到即消失属并发异常
                continue
            manifest = record.manifest
            records.append(
                VersionRecord(
                    plugin_id=plugin_id,
                    version=version,
                    manifest=manifest.model_dump(mode="json"),
                    manifest_sha256=record.sha256 or manifest_digest(manifest.model_dump()),
                    min_platform_version=manifest.min_platform_version,
                    capabilities=manifest.capability_keys,
                    actions=manifest.action_keys,
                    step_titles=tuple(manifest.step_titles()),
                    status=VERSION_ACTIVE,
                    published_at=now_iso(),
                )
            )
    return records


# ===========================================================================
# 进程级单例
# ===========================================================================
_memory_store: InMemoryPluginStore | None = None


def get_plugin_store(settings: Settings | None = None) -> PluginStore:
    """获取插件存储。

    Args:
        settings: 可选配置实例。

    Returns:
        PluginStore: `business_store=postgres` 时为数据库实现，否则为内存实现。
    """
    global _memory_store

    conf = settings or get_settings()
    if conf.uses_postgres:
        return PostgresPluginStore(conf)
    if _memory_store is None:
        _memory_store = InMemoryPluginStore()
    return _memory_store


def get_memory_plugin_store() -> InMemoryPluginStore | None:
    """取内存插件存储单例（不创建；测试与工具用）。

    Returns:
        InMemoryPluginStore | None: 已创建的实例；未创建返回 None。
    """
    return _memory_store


def reset_plugin_store() -> None:
    """重置内存后端单例（测试隔离用）。"""
    global _memory_store
    if _memory_store is not None:
        _memory_store.clear()
    _memory_store = None


#: 未使用的可变字段白名单（B18 的 `update_installation` 会用；此处声明以保证
#: 白名单与 DDL 列集合同步演进，避免后续批次写 SQL 时漏校验）
_ = INSTALLATION_MUTABLE_FIELDS

__all__: list[str] = [
    "CATALOG_AVAILABLE",
    "CATALOG_TABLE",
    "GRANT_TABLE",
    "INSTALLATION_MUTABLE_FIELDS",
    "INSTALLATION_TABLE",
    "LIVE_STATUSES",
    "PLATFORM_TENANT",
    "POLICY_TABLE",
    "STATUS_DISABLED",
    "STATUS_ENABLED",
    "STATUS_INSTALLED",
    "STATUS_UNINSTALLED",
    "VERSION_ACTIVE",
    "VERSION_DEPRECATED",
    "VERSION_TABLE",
    "CatalogRecord",
    "InMemoryPluginStore",
    "InstallationConflictError",
    "InstallationRecord",
    "PluginStore",
    "PostgresPluginStore",
    "VersionRecord",
    "get_memory_plugin_store",
    "get_plugin_store",
    "now_iso",
    "reset_plugin_store",
    "version_records_from_registry",
]
