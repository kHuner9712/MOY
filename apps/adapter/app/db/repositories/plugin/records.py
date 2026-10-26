"""插件域仓储 · records 层（`plugin_repo` 三拆后的数据记录、域常量与注册表适配）。

本文件是 `app/db/repositories/plugin/` 包拆分后的**最底层**：数据记录（dataclass）、
两侧共用的状态/字段白名单常量、域错误，以及两个与注册表相关的纯函数
（`resolve_registry` 由 `get_registry` 直接转出、`version_records_from_registry`），
**不依赖** protocol / memory / postgres，因此可被它们任意引用而不成环
（依赖方向 `records ← protocol ← {memory, postgres} ← __init__`）。

**表名与 `PLATFORM_TENANT` 不在这里**：`CATALOG_TABLE` / `VERSION_TABLE` /
`INSTALLATION_TABLE` / `GRANT_TABLE` / `POLICY_TABLE` 只服务 PG 的 SQL，
放 `postgres.py`。

**为什么 `ORG_CAPABILITIES` 与注册表门面（`PluginRegistry` / `get_registry` /
`manifest_digest`）在本层转出**：`db → plugins` 是**已登记的**方向违规
（P2-11，`deploy/import_direction_baseline.txt` 里 3 行）。拆包前它只在
`plugin_repo.py` 一处，拆包后若 memory 与 postgres 各自 import，同一个违规会变成
两条独立基线（"禁止新增基线条目"）。集中到本层后：**一处 import、一份基线**，
memory/postgres 一律 `from ...records import ...` 取用。这既不改方向矩阵，
也不给门禁开后门——`version_records_from_registry` 本来就要读注册表，
它的自然位置就是"记录构造层"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

from app.plugins.actions import ORG_CAPABILITIES
from app.plugins.loader import PluginRegistry, get_registry, manifest_digest

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


#: 安装态可变字段白名单（防动态列名注入）。
#:
#: 目前**尚未被调用**（B18 的 `update_installation` 会用）；此处先声明，以保证
#: 白名单与 DDL 列集合同步演进，避免后续批次写 SQL 时漏校验。它列在本模块
#: `__all__` 里，因此不需要（也不该）用 `_ = INSTALLATION_MUTABLE_FIELDS` 这种
#: "占位赋值"去骗过未使用检查。
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


__all__: list[str] = [
    "CATALOG_AVAILABLE",
    "INSTALLATION_MUTABLE_FIELDS",
    "LIVE_STATUSES",
    "ORG_CAPABILITIES",
    "STATUS_DISABLED",
    "STATUS_ENABLED",
    "STATUS_INSTALLED",
    "STATUS_UNINSTALLED",
    "VERSION_ACTIVE",
    "VERSION_DEPRECATED",
    "CatalogRecord",
    "InstallationConflictError",
    "InstallationRecord",
    "PluginRegistry",
    "VersionRecord",
    "get_registry",
    "manifest_digest",
    "now_iso",
    "version_records_from_registry",
]
