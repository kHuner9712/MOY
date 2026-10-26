"""插件域仓储 · 内存实现（development / test）。

内存后端就是生产语义的**可执行规格**：G-38 门禁逐条比对本文件与 `postgres.py` 的
公开方法集、签名与 record 构造点，任何一侧单独改动都会被判红。因此本文件的每个方法
都必须与 PG 侧同名方法**保持同形**（参数名、默认值、同步性），差异只能通过
`TEST_HOOKS`（`clear` / `counts`）这类显式白名单存在。

**内存模式没有 SQL 种子**：目录与版本行由 `_seed()` 从 `PluginRegistry` 播种，
`manifest_sha256` 取注册表算好的 sha256（与磁盘文件同源），因此内存/PG 两侧
"库里摘要 == 磁盘摘要"是同一份证据。`list_versions` 的 `version_tuple` 是
**函数内延迟导入**（`:442` 起）：版本排序只在被调用时才需要 loader，
模块级导入会让"仓储层 import 插件加载器"这条已登记的 P2-11 依赖多出一处。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace

from app.db.repositories.plugin.records import (
    CATALOG_AVAILABLE,
    ORG_CAPABILITIES,
    VERSION_ACTIVE,
    CatalogRecord,
    InstallationConflictError,
    InstallationRecord,
    PluginRegistry,
    VersionRecord,
    get_registry,
    now_iso,
)


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

    async def catalogs_of(self, plugin_ids: Sequence[str]) -> dict[str, CatalogRecord]:
        """批量读取目录行（含下架行；缺失的 id 不进字典）。

        Args:
            plugin_ids: 插件标识列表（可重复，内部去重）。

        Returns:
            dict[str, CatalogRecord]: 插件标识 → 目录行。
        """
        wanted = list(dict.fromkeys(plugin_ids))
        if not wanted:
            return {}
        async with self._lock:
            self._seed()
            return {
                plugin_id: self._catalog[plugin_id]
                for plugin_id in wanted
                if plugin_id in self._catalog
            }

    async def count_catalog_by_category(self) -> dict[str, int]:
        """按分类统计可售目录条数（口径同 `list_catalog()` 无参调用）。

        Returns:
            dict[str, int]: 分类 → 条数（只含非零分类，缺省由服务层零填充）。
        """
        async with self._lock:
            self._seed()
            counts: dict[str, int] = {}
            for record in self._catalog.values():
                if not record.installable:
                    continue
                counts[record.category] = counts.get(record.category, 0) + 1
            return counts

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


__all__: list[str] = ["InMemoryPluginStore"]
