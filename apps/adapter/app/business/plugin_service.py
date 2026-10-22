"""插件目录与安装服务（阶段三 B17，`architecture-phase3.md` §2.4 / §5.2 PL1-PL5 / PL13）。

**套餐拦截点后移**（PRD v1.1 勘误 #1，本文件最容易做错的一条）：
PL1/PL2/PL4 **不受** `features.plugin_market` 影响，一律正常返回，只在条目上给
`installable=false` + `install_block_reason`；`15001` 只在**安装**时抛。
理由：整页降级成"升级提示"会让免费版用户看不到自己能买什么，也违反
"错误只在真出错时出现"——市场可见性与可安装性是两件事。

**校验顺序固定**（§5.2 要点，顺序决定用户看到哪一条 CTA）：
`15001（套餐）→ 16005（本人未授权）→ 16006（企业未开放）→ 16009（前置插件）→ 20001（参数）`。

**安装即启用**（Q3-3）：`not_installed → enabled` 一步到位，`installed` 只是
事务内的瞬时值（保留它是为将来"待管理员审批"留位）。

**授权与安装解耦**：授权写 `plugin_grant`（成员 × 插件 × 能力），
不写 `role_permission`——角色权限点总数必须仍是 20（PRD R4 硬要求）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from app.business.audit_service import (
    ACTION_PLUGIN_CAPABILITY_GRANT,
    ACTION_PLUGIN_CAPABILITY_POLICY,
    ACTION_PLUGIN_INSTALL,
    ACTION_PLUGIN_STATE_CHANGE,
    ACTION_PLUGIN_UNINSTALL,
    ACTION_PLUGIN_VERSION_CHANGE,
    RESOURCE_PLUGIN,
    AuditService,
)
from app.business.plan_service import PlanService, get_plan_service
from app.business.plugin_grant_service import PluginGrantService, get_plugin_grant_service
from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.core.security import AuthContext
from app.db.repositories.plugin_repo import (
    PLATFORM_TENANT,
    STATUS_DISABLED,
    STATUS_ENABLED,
    STATUS_UNINSTALLED,
    CatalogRecord,
    InstallationConflictError,
    InstallationRecord,
    PluginStore,
    get_plugin_store,
    now_iso,
    version_records_from_registry,
)
from app.plugins.actions import (
    CAPABILITY_CATALOG,
    CAPABILITY_KEYS,
    ORG_CAPABILITIES,
    PERSONAL_CAPABILITIES,
    capability_display_name,
)
from app.plugins.loader import PluginRegistry, get_registry, version_tuple
from app.plugins.schema import PluginManifest
from app.schemas.plugin import (
    CapabilityListData,
    CapabilityPolicyData,
    CapabilityPolicyItem,
    CapabilityPolicyRequest,
    CategoryCountItem,
    InstallData,
    InstalledData,
    InstalledItem,
    InstallRequest,
    MarketData,
    MarketDetailData,
    MarketItem,
    MarketSort,  # noqa: F401 - 供类型引用与文档对齐
    OutputBriefItem,
    PluginCapabilityItem,
    SettingBriefItem,
    SettingsData,
    SettingsRequest,
    StateData,
    UninstallData,
    UpdateGrantsData,
    UpdateGrantsRequest,
    VersionBriefItem,
    VersionData,
)

logger = get_logger(__name__)

#: 分类展示名（横筛文案）
CATEGORY_NAMES: Final[dict[str, str]] = {
    "dev": "研发",
    "content": "内容",
    "hr": "人事",
    "collab": "协同",
    "data": "数据",
    "other": "其他",
}

#: 分类固定顺序（前端横筛常显，不随后端数据抖动）
CATEGORY_ORDER: Final[tuple[str, ...]] = ("dev", "content", "hr", "collab", "data", "other")

#: 套餐未含插件市场时的提示（业务语言，无技术词）
MESSAGE_PLAN_BLOCKED: Final[str] = "当前套餐不含插件市场，升级后即可使用"

#: 已下架提示（与 `16002` 同码多文案）
MESSAGE_OFFLINE: Final[str] = "该插件已下架，去市场看看其他插件"

#: 默认每页条数（市场 12 条/页，PRD 要求）
DEFAULT_MARKET_PAGE_SIZE: Final[int] = 12


@dataclass(frozen=True, slots=True)
class _MarketContext:
    """市场/详情的公共上下文（一次查询复用，避免 N+1）。

    Attributes:
        catalog: 目录行。
        manifest: 声明（最新版本）。
        installation: 当前企业安装行（可能为 None）。
        plan_enabled: 套餐是否含插件市场。
        policies: 企业能力开关。
    """

    catalog: CatalogRecord
    manifest: PluginManifest | None
    installation: InstallationRecord | None
    plan_enabled: bool
    policies: dict[str, bool]


class PluginService:
    """插件目录与安装服务（PL1-PL5 / PL13 的唯一业务入口）。

    Attributes:
        _settings: 配置实例。
        _store: 插件存储。
        _registry: 声明注册表。
        _grants: 授权服务。
        _plans: 套餐服务。
        _audit: 操作记录服务。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        store: PluginStore | None = None,
        registry: PluginRegistry | None = None,
        grants: PluginGrantService | None = None,
        plans: PlanService | None = None,
        audit: AuditService | None = None,
    ) -> None:
        """初始化服务。

        Args:
            settings: 可选配置实例。
            store: 可选插件存储（测试注入）。
            registry: 可选声明注册表（测试注入）。
            grants: 可选授权服务（测试注入）。
            plans: 可选套餐服务（测试注入）。
            audit: 可选操作记录服务（测试注入）。
        """
        self._settings = settings or get_settings()
        self._store: PluginStore = store or get_plugin_store(self._settings)
        self._registry: PluginRegistry = registry or get_registry()
        self._grants: PluginGrantService = grants or get_plugin_grant_service(self._settings)
        self._plans: PlanService = plans or get_plan_service(self._settings)
        self._audit = audit or AuditService(self._settings)

    @property
    def store(self) -> PluginStore:
        """当前插件存储（测试可据此断言）。

        Returns:
            PluginStore: 插件存储。
        """
        return self._store

    @property
    def registry(self) -> PluginRegistry:
        """当前声明注册表。

        Returns:
            PluginRegistry: 注册表。
        """
        return self._registry

    # ------------------------------------------------------------------
    # 公共：声明快照登记（首次访问时幂等补齐，版本不可变）
    # ------------------------------------------------------------------
    async def ensure_versions(self) -> int:
        """把注册表里的声明幂等登记到 `plugin_version`。

        内存后端在构造时已从注册表播种；PG 后端由这里把 6 份声明写库
        （`ON CONFLICT DO NOTHING`，重复调用不产生新行）。

        Returns:
            int: 新插入的条数。
        """
        return await self._store.ensure_versions(version_records_from_registry(self._registry))

    # ------------------------------------------------------------------
    # PL1 市场列表
    # ------------------------------------------------------------------
    async def market(
        self,
        *,
        auth: AuthContext,
        category: str = "",
        keyword: str = "",
        sort: str = "recommended",
        page: int = 1,
        page_size: int = DEFAULT_MARKET_PAGE_SIZE,
    ) -> MarketData:
        """市场列表（PL1）。

        Args:
            auth: 鉴权上下文。
            category: 分类过滤。
            keyword: 关键字。
            sort: 排序（`recommended` / `newest` / `rating`）。
            page: 页码。
            page_size: 每页条数。

        Returns:
            MarketData: 市场列表（含分类计数与可安装性）。
        """
        await self.ensure_versions()
        catalog = await self._store.list_catalog(category=category, keyword=keyword)
        installations = {
            record.plugin_id: record
            for record in await self._store.list_installations(auth.tenant_id)
            if record.is_live
        }
        plan_enabled = await self._plan_allows_market(auth.tenant_id)
        items = [
            await self._market_item(record, installations.get(record.plugin_id), plan_enabled)
            for record in catalog
        ]
        items = _sorted_items(items, sort)

        # 分类计数基于**过滤前**的全量可售目录：横筛要能看到"切过去还有多少"
        all_catalog = await self._store.list_catalog()
        categories = _category_counts(all_catalog)

        total = len(items)
        start = max(page - 1, 0) * page_size
        policies = await self._store.list_policies(auth.tenant_id)
        return MarketData(
            items=items[start : start + page_size],
            categories=categories,
            org_capabilities=_policy_items(policies),
            page=page,
            page_size=page_size,
            total=total,
            total_pages=(total + page_size - 1) // page_size if page_size else 0,
        )

    async def _market_item(
        self,
        catalog: CatalogRecord,
        installation: InstallationRecord | None,
        plan_enabled: bool,
    ) -> MarketItem:
        """组装一条市场条目。

        Args:
            catalog: 目录行。
            installation: 安装行（可能为 None）。
            plan_enabled: 套餐是否含插件市场。

        Returns:
            MarketItem: 市场条目。
        """
        installed_status = "none"
        has_update = False
        if installation is not None:
            installed_status = installation.status
            has_update = version_tuple(catalog.latest_version) > version_tuple(installation.version)
        installable = catalog.installable and plan_enabled
        reason = "" if installable else MESSAGE_PLAN_BLOCKED
        return MarketItem(
            plugin_id=catalog.plugin_id,
            name=catalog.name,
            summary=catalog.summary,
            category=catalog.category,
            icon_key=catalog.icon_key,
            rating_avg=catalog.rating_avg,
            rating_count=catalog.rating_count,
            latest_version=catalog.latest_version,
            installed_status=installed_status,
            has_update=has_update,
            installable=installable,
            install_block_reason=reason,
        )

    # ------------------------------------------------------------------
    # PL2 市场详情
    # ------------------------------------------------------------------
    async def detail(self, *, plugin_id: str, auth: AuthContext) -> MarketDetailData:
        """市场详情（PL2）。

        Args:
            plugin_id: 插件标识。
            auth: 鉴权上下文。

        Returns:
            MarketDetailData: 详情。

        Raises:
            ApiError: 插件不存在/已下架（16002）。
        """
        await self.ensure_versions()
        context = await self._load_market_context(plugin_id, auth)
        manifest = context.manifest
        versions = await self._store.list_versions(plugin_id)
        installation = context.installation
        installed_version = installation.version if installation is not None else ""
        has_update = bool(
            installed_version
            and version_tuple(context.catalog.latest_version) > version_tuple(installed_version)
        )
        installable = context.catalog.installable and context.plan_enabled
        return MarketDetailData(
            plugin_id=context.catalog.plugin_id,
            name=context.catalog.name,
            summary=context.catalog.summary,
            description=context.catalog.description,
            category=context.catalog.category,
            icon_key=context.catalog.icon_key,
            latest_version=context.catalog.latest_version,
            rating_avg=context.catalog.rating_avg,
            rating_count=context.catalog.rating_count,
            install_count=context.catalog.install_count,
            capabilities=_capability_items(manifest, context.policies),
            step_titles=list(versions[0].step_titles) if versions else [],
            outputs=[
                OutputBriefItem(key=item.key, label=item.label, type=item.type)
                for item in (manifest.outputs if manifest is not None else [])
            ],
            settings_schema=[
                SettingBriefItem(
                    key=item.key, label=item.label, type=item.type, options=item.options
                )
                for item in (manifest.settings_schema if manifest is not None else [])
            ],
            versions=[
                VersionBriefItem(
                    version=record.version,
                    published_at=record.published_at,
                    changelog=str(record.manifest.get("changelog", "") or ""),
                )
                for record in versions
            ],
            installed_status=(installation.status if installation is not None else "none"),
            installed_version=installed_version,
            has_update=has_update,
            installable=installable,
            install_block_reason="" if installable else MESSAGE_PLAN_BLOCKED,
        )

    # ------------------------------------------------------------------
    # PL3 已安装列表
    # ------------------------------------------------------------------
    async def installed(
        self, *, auth: AuthContext, status: str = "", page: int = 1, page_size: int = 20
    ) -> InstalledData:
        """已安装列表（PL3）。

        `disabled` / `installed` 行只对持有 `team:role:manage` 的人可见
        （§5.5 判权矩阵）——普通成员看到的是一个"不存在的插件"，
        避免把企业策略暴露成可点但不可用的灰块。

        Args:
            auth: 鉴权上下文。
            status: 状态过滤。
            page: 页码。
            page_size: 每页条数。

        Returns:
            InstalledData: 已安装列表。
        """
        from app.business.policy_service import get_policy_service

        policy = get_policy_service(self._settings)
        is_admin = await policy.admin_of(auth)
        records = [
            record
            for record in await self._store.list_installations(auth.tenant_id, status=status)
            if record.is_live and (is_admin or record.status == STATUS_ENABLED)
        ]
        items: list[InstalledItem] = []
        for record in records:
            catalog = await self._store.get_catalog(record.plugin_id)
            latest = catalog.latest_version if catalog is not None else record.version
            items.append(
                InstalledItem(
                    plugin_id=record.plugin_id,
                    name=catalog.name if catalog is not None else record.plugin_id,
                    category=catalog.category if catalog is not None else "",
                    icon_key=catalog.icon_key if catalog is not None else "",
                    version=record.version,
                    latest_version=latest,
                    has_update=version_tuple(latest) > version_tuple(record.version),
                    status=record.status,
                    last_used_at=record.last_used_at,
                    running_task_count=0,
                )
            )
        total = len(items)
        start = max(page - 1, 0) * page_size
        return InstalledData(
            items=items[start : start + page_size],
            page=page,
            page_size=page_size,
            total=total,
            total_pages=(total + page_size - 1) // page_size if page_size else 0,
        )

    # ------------------------------------------------------------------
    # PL4 能力清单
    # ------------------------------------------------------------------
    async def capabilities(self, *, plugin_id: str, auth: AuthContext) -> CapabilityListData:
        """能力清单（PL4）。

        Args:
            plugin_id: 插件标识。
            auth: 鉴权上下文。

        Returns:
            CapabilityListData: 能力清单（含 `org_switch_enabled`）。

        Raises:
            ApiError: 插件不存在（16002）。
        """
        await self.ensure_versions()
        context = await self._load_market_context(plugin_id, auth)
        granted = await self._grants.snapshot(auth.tenant_id, plugin_id, auth.user_id)
        installable = context.catalog.installable and context.plan_enabled
        return CapabilityListData(
            items=_capability_items(context.manifest, context.policies),
            granted_capabilities=sorted(granted),
            org_switch_updated_at="",
            installable=installable,
            install_block_reason="" if installable else MESSAGE_PLAN_BLOCKED,
        )

    # ------------------------------------------------------------------
    # PL5 安装（安装即启用）
    # ------------------------------------------------------------------
    async def install(
        self, *, plugin_id: str, request: InstallRequest, auth: AuthContext
    ) -> InstallData:
        """安装插件（PL5）。

        Args:
            plugin_id: 插件标识。
            request: 安装请求（授权意图 + 配置）。
            auth: 鉴权上下文。

        Returns:
            InstallData: 安装结果。

        Raises:
            ApiError: 套餐不支持（15001）、插件不可用（16002）、
                缺少必需能力（16005）、企业未开放（16006）、
                前置插件缺失（16009）、参数非法（20001）。
        """
        await self.ensure_versions()
        context = await self._load_market_context(plugin_id, auth)
        manifest = context.manifest
        if manifest is None:  # pragma: no cover - 目录有行必然有快照
            raise ApiError(ErrorCode.PLUGIN_NOT_FOUND, internal_detail=f"{plugin_id} no manifest")

        # ① 套餐（勘误 #1：只有安装/启用才拦）
        if not context.plan_enabled:
            raise ApiError(
                ErrorCode.PLAN_FEATURE_UNAVAILABLE,
                message=MESSAGE_PLAN_BLOCKED,
                internal_detail=f"plugin_market disabled for tenant {auth.tenant_id}",
            )

        # ② 授权意图：必须覆盖全部必需能力，且不得出现未声明的能力
        declared = set(manifest.capability_keys)
        requested = {key for key, value in request.grants.items() if value}
        unknown = sorted(set(request.grants) - declared)
        if unknown:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                internal_detail=f"undeclared capabilities: {', '.join(unknown)}",
            )
        missing = sorted(set(manifest.required_capability_keys) - requested)
        if missing:
            raise ApiError(
                ErrorCode.CAPABILITY_NOT_GRANTED,
                internal_detail=f"missing capabilities: {', '.join(missing)}",
                # 具体缺哪几项由端点层放进 data.missing_capabilities
            )

        # ③ 企业级能力总开关（只对 org.*）
        blocked = sorted(
            capability
            for capability in requested
            if capability in ORG_CAPABILITIES and not context.policies.get(capability, False)
        )
        if blocked:
            raise ApiError(
                ErrorCode.CAPABILITY_POLICY_BLOCKED,
                internal_detail=f"policy disabled: {', '.join(blocked)}",
            )

        # ④ 前置插件
        missing_plugins = await self._missing_requires(manifest, auth.tenant_id)
        if missing_plugins:
            raise ApiError(
                ErrorCode.PLUGIN_DEPENDENCY_MISSING,
                internal_detail=f"missing plugins: {', '.join(missing_plugins)}",
            )

        # ⑤ 配置项键必须 ⊆ 声明（防注入未声明配置）
        _assert_settings_keys(manifest, request.settings)

        version = context.catalog.latest_version
        already_installed = context.installation is not None and context.installation.is_live
        record = InstallationRecord(
            tenant_id=auth.tenant_id,
            plugin_id=plugin_id,
            version=version,
            previous_version=(
                context.installation.version if context.installation is not None else ""
            ),
            status=STATUS_ENABLED,
            settings=request.settings,
            capability_version=(
                (context.installation.capability_version + 1)
                if context.installation is not None
                else 1
            ),
            installed_by=auth.user_id,
            installed_at=(
                context.installation.installed_at if context.installation is not None else now_iso()
            ),
            enabled_at=now_iso(),
        )
        if context.installation is None:
            # 首次安装走「仅插入」：并发两个请求都判"未安装"时，后到者撞唯一键
            # `uq_installation_active` → `16010`（架构 §5.4 场景 ①）。
            # 这里**不能**用 upsert —— 那会让后到者静默覆盖先到者的配置。
            try:
                installation = await self._store.create_installation(record)
            except InstallationConflictError as exc:
                logger.warning(
                    "plugin_install_conflict", tenant_id=auth.tenant_id, plugin_id=plugin_id
                )
                raise ApiError(
                    ErrorCode.PLUGIN_STATE_CONFLICT,
                    internal_detail=f"concurrent install {plugin_id}: {exc}",
                ) from exc
        else:
            installation = await self._store.save_installation(record)
        # 重复添加（已添加状态再点一次）：**不动授权、不写审计、不改安装数**
        # —— 那是 PL14（授权）与 PL6（停用）的职责。重复点按钮不该产生"授权被重置"
        # 或"审计里多一条安装"的副作用（TC-P3-MK08-06）。
        if already_installed:
            current = await self._grants.snapshot(auth.tenant_id, plugin_id, auth.user_id)
            logger.info(
                "plugin_already_installed",
                tenant_id=auth.tenant_id,
                plugin_id=plugin_id,
                version=installation.version,
            )
            return InstallData(
                plugin_id=plugin_id,
                status=installation.status,
                version=installation.version,
                granted_capabilities=sorted(current),
                denied_capabilities=sorted(declared - current),
                already_installed=True,
            )
        granted = await self._grants.grant(
            tenant_id=auth.tenant_id,
            plugin_id=plugin_id,
            user_id=auth.user_id,
            capabilities=requested,
        )
        await self._store.adjust_install_count(plugin_id, 1)
        await self._audit.record(
            tenant_id=auth.tenant_id,
            actor_id=auth.user_id,
            action=ACTION_PLUGIN_INSTALL,
            resource_type=RESOURCE_PLUGIN,
            resource_id=plugin_id,
            actor_role=auth.roles[0] if auth.roles else "",
            change_summary={
                "plugin_id": plugin_id,
                "version": version,
                "capabilities": sorted(requested),
                "status": installation.status,
            },
        )
        logger.info(
            "plugin_installed",
            tenant_id=auth.tenant_id,
            plugin_id=plugin_id,
            version=version,
            granted=sorted(requested),
        )
        return InstallData(
            plugin_id=plugin_id,
            status=installation.status,
            version=version,
            granted_capabilities=sorted(granted),
            denied_capabilities=sorted(declared - requested),
            already_installed=False,
        )

    # ------------------------------------------------------------------
    # PL14 更新本人授权
    # ------------------------------------------------------------------
    async def update_grants(
        self, *, plugin_id: str, request: UpdateGrantsRequest, auth: AuthContext
    ) -> UpdateGrantsData:
        """增减**本人**对该插件的能力授权（PL14，闭环 P0-P3-03 ④）。

        三条硬约束（架构 §5.2 PL14）：
        1. `required: true` 的能力**不可撤**（撤了插件直接不可用，用户会当成故障）→ `20001`；
        2. 只能改本人，管理员改他人授权不在本版（避免绕过企业策略）；
        3. 不新增权限点，登录即可；授予企业能力时同样要过企业开关（`16006`）。

        Args:
            plugin_id: 插件标识。
            request: 增减意图。
            auth: 鉴权上下文。

        Returns:
            UpdateGrantsData: 变更后的授权全貌。

        Raises:
            ApiError: 未添加（16003）、撤销必需能力（20001）、企业未开放（16006）。
        """
        installation = await self._require_live(plugin_id, auth)
        manifest = self._registry.get(plugin_id, installation.version)
        declared = set(manifest.capability_keys) if manifest is not None else set()
        required = set(manifest.required_capability_keys) if manifest is not None else set()
        unknown = sorted(
            item.capability_key for item in request.grants if item.capability_key not in declared
        )
        if unknown:
            raise ApiError(
                ErrorCode.PARAM_INVALID, internal_detail=f"undeclared: {', '.join(unknown)}"
            )
        current = set(await self._grants.snapshot(auth.tenant_id, plugin_id, auth.user_id))
        target = set(current)
        for item in request.grants:
            if item.granted:
                target.add(item.capability_key)
            else:
                target.discard(item.capability_key)
        revoked_required = sorted(required - target)
        if revoked_required:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message="必需能力不能撤销，如不再使用请停用或移除该插件",
                internal_detail=f"cannot revoke required: {', '.join(revoked_required)}",
            )
        policies = await self._store.list_policies(auth.tenant_id)
        blocked = sorted(
            capability
            for capability in target
            if capability in ORG_CAPABILITIES and not policies.get(capability, False)
        )
        if blocked:
            raise ApiError(
                ErrorCode.CAPABILITY_POLICY_BLOCKED,
                internal_detail=f"policy disabled: {', '.join(blocked)}",
            )
        granted = await self._grants.grant(
            tenant_id=auth.tenant_id,
            plugin_id=plugin_id,
            user_id=auth.user_id,
            capabilities=target,
        )
        await self._audit.record(
            tenant_id=auth.tenant_id,
            actor_id=auth.user_id,
            action=ACTION_PLUGIN_CAPABILITY_GRANT,
            resource_type=RESOURCE_PLUGIN,
            resource_id=plugin_id,
            actor_role=auth.roles[0] if auth.roles else "",
            change_summary={
                "granted": sorted(target - current),
                "revoked": sorted(current - target),
            },
        )
        return UpdateGrantsData(
            granted_capabilities=sorted(granted),
            denied_capabilities=sorted(declared - granted),
            required_capabilities=sorted(required),
        )

    # ------------------------------------------------------------------
    # PL13 企业能力开关
    # ------------------------------------------------------------------
    async def set_capability_policy(
        self, *, request: CapabilityPolicyRequest, auth: AuthContext
    ) -> CapabilityPolicyData:
        """写入企业能力开关（PL13，仅 `org.*`）。

        Args:
            request: 开关列表。
            auth: 鉴权上下文。

        Returns:
            CapabilityPolicyData: 写后回读的全量开关。

        Raises:
            ApiError: 含个人能力（20001）。
        """
        personal = sorted(
            item.capability_key
            for item in request.policies
            if item.capability_key not in ORG_CAPABILITIES
        )
        if personal:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message="个人能力始终开启，无法被企业关闭",
                internal_detail=f"non-org capabilities: {', '.join(personal)}",
            )
        values = {item.capability_key: item.enabled for item in request.policies}
        stored = await self._store.save_policies(auth.tenant_id, values, auth.user_id)
        self._grants.invalidate(auth.tenant_id, plugin_id="", user_id="")
        await self._audit.record(
            tenant_id=auth.tenant_id,
            actor_id=auth.user_id,
            action=ACTION_PLUGIN_CAPABILITY_POLICY,
            resource_type=RESOURCE_PLUGIN,
            resource_id="capability-policy",
            actor_role=auth.roles[0] if auth.roles else "",
            change_summary={"changed": values, "effective": stored},
        )
        logger.info(
            "plugin_capability_policy_updated", tenant_id=auth.tenant_id, changed=sorted(values)
        )
        # 回读**全量 6 项**：个人能力恒 True（不可被企业关闭），企业能力取存储值。
        # 前端一次拿全，避免"个人能力要另调一个端点"。
        stamp = now_iso()
        return CapabilityPolicyData(
            policies=[
                CapabilityPolicyItem(
                    capability_key=capability,
                    display_name=capability_display_name(capability),
                    enabled=(
                        True
                        if capability in PERSONAL_CAPABILITIES
                        else stored.get(capability, False)
                    ),
                    updated_by_name=auth.user_id,
                    updated_at=stamp,
                )
                for capability in CAPABILITY_KEYS
            ]
        )

    # ------------------------------------------------------------------
    # PL6 停用 / 启用
    # ------------------------------------------------------------------
    async def set_state(self, *, plugin_id: str, status: str, auth: AuthContext) -> StateData:
        """停用或启用插件（PL6）。

        **停用不打断在跑任务**（PRD P0-P3-02 ⑤）：只改安装态，任务继续完成；
        `running_task_count` 回给前端做提示（B19 接入任务计数，本批为 0）。
        启用时要重新确认「能力授权仍在 + 企业开关仍开」——停用期间管理员可能把
        企业能力关掉了，直接启用会得到一个"看起来能用、一跑就 16006"的插件。

        Args:
            plugin_id: 插件标识。
            status: 目标状态（`enabled` / `disabled`）。
            auth: 鉴权上下文。

        Returns:
            StateData: 变更结果。

        Raises:
            ApiError: 未添加（16003）、缺少能力（16005）、企业未开放（16006）。
        """
        installation = await self._require_live(plugin_id, auth)
        if installation.status == status:
            return StateData(plugin_id=plugin_id, status=status, running_task_count=0)
        if status == STATUS_ENABLED:
            manifest = self._registry.get(plugin_id, installation.version)
            if manifest is not None:
                granted = await self._grants.snapshot(auth.tenant_id, plugin_id, auth.user_id)
                missing = sorted(set(manifest.required_capability_keys) - granted)
                if missing:
                    raise ApiError(
                        ErrorCode.CAPABILITY_NOT_GRANTED,
                        internal_detail=f"missing capabilities: {', '.join(missing)}",
                    )
                policies = await self._store.list_policies(auth.tenant_id)
                blocked = sorted(
                    capability
                    for capability in manifest.capability_keys
                    if capability in ORG_CAPABILITIES and not policies.get(capability, False)
                )
                if blocked:
                    raise ApiError(
                        ErrorCode.CAPABILITY_POLICY_BLOCKED,
                        internal_detail=f"policy disabled: {', '.join(blocked)}",
                    )
        stamp = now_iso()
        updated = await self._store.save_installation(
            InstallationRecord(
                tenant_id=installation.tenant_id,
                plugin_id=installation.plugin_id,
                version=installation.version,
                previous_version=installation.previous_version,
                status=status,
                settings=installation.settings,
                capability_version=installation.capability_version + 1,
                installed_by=installation.installed_by,
                installed_at=installation.installed_at,
                enabled_at=stamp if status == STATUS_ENABLED else installation.enabled_at,
                disabled_at=stamp if status == STATUS_DISABLED else installation.disabled_at,
            )
        )
        self._grants.invalidate(auth.tenant_id, plugin_id)
        await self._audit.record(
            tenant_id=auth.tenant_id,
            actor_id=auth.user_id,
            action=ACTION_PLUGIN_STATE_CHANGE,
            resource_type=RESOURCE_PLUGIN,
            resource_id=plugin_id,
            actor_role=auth.roles[0] if auth.roles else "",
            change_summary={"status": {"from": installation.status, "to": updated.status}},
        )
        return StateData(plugin_id=plugin_id, status=updated.status, running_task_count=0)

    # ------------------------------------------------------------------
    # PL7 卸载
    # ------------------------------------------------------------------
    async def uninstall(self, *, plugin_id: str, auth: AuthContext) -> UninstallData:
        """卸载插件（PL7，**保留历史任务与产物**）。

        Args:
            plugin_id: 插件标识。
            auth: 鉴权上下文。

        Returns:
            UninstallData: 卸载结果。

        Raises:
            ApiError: 未添加（16003）。
        """
        installation = await self._require_live(plugin_id, auth)
        stamp = now_iso()
        await self._store.save_installation(
            InstallationRecord(
                tenant_id=installation.tenant_id,
                plugin_id=installation.plugin_id,
                version=installation.version,
                previous_version=installation.previous_version,
                status=STATUS_UNINSTALLED,
                settings=installation.settings,
                capability_version=installation.capability_version + 1,
                installed_by=installation.installed_by,
                installed_at=installation.installed_at,
                enabled_at=installation.enabled_at,
                disabled_at=installation.disabled_at,
                uninstalled_at=stamp,
            )
        )
        # 卸载即撤权：重新添加时必须重新走授权抽屉（能力边界不能"记住上次同意"）
        await self._grants.grant(
            tenant_id=auth.tenant_id,
            plugin_id=plugin_id,
            user_id=auth.user_id,
            capabilities=set(),
        )
        await self._store.adjust_install_count(plugin_id, -1)
        await self._audit.record(
            tenant_id=auth.tenant_id,
            actor_id=auth.user_id,
            action=ACTION_PLUGIN_UNINSTALL,
            resource_type=RESOURCE_PLUGIN,
            resource_id=plugin_id,
            actor_role=auth.roles[0] if auth.roles else "",
            change_summary={"kept_task_count": 0},
        )
        logger.info("plugin_uninstalled", tenant_id=auth.tenant_id, plugin_id=plugin_id)
        return UninstallData(
            plugin_id=plugin_id, uninstalled=True, running_task_count=0, kept_task_count=0
        )

    # ------------------------------------------------------------------
    # PL8 升级 / PL9 回滚
    # ------------------------------------------------------------------
    async def upgrade(
        self, *, plugin_id: str, target_version: str, auth: AuthContext
    ) -> VersionData:
        """升级到指定版本（PL8）。

        只保留最近 2 个版本（`version` + `previous_version`，PRD：只允许回退 1 次）。

        Args:
            plugin_id: 插件标识。
            target_version: 目标版本。
            auth: 鉴权上下文。

        Returns:
            VersionData: 变更结果。

        Raises:
            ApiError: 未添加（16003）、版本不存在（16012）、平台版本过低（16007）。
        """
        installation = await self._require_live(plugin_id, auth)
        version = await self._store.get_version(plugin_id, target_version)
        if version is None:
            raise ApiError(
                ErrorCode.PLUGIN_VERSION_NOT_FOUND,
                internal_detail=f"{plugin_id}@{target_version}",
            )
        if version_tuple(version.min_platform_version) > version_tuple(self._settings.app_version):
            raise ApiError(
                ErrorCode.PLUGIN_VERSION_INCOMPATIBLE,
                internal_detail=(
                    f"require {version.min_platform_version} > {self._settings.app_version}"
                ),
            )
        return await self._switch_version(installation, target_version, auth, action="upgrade")

    async def rollback(self, *, plugin_id: str, auth: AuthContext) -> VersionData:
        """回退到上一版本（PL9，只保留 1 次回退能力）。

        Args:
            plugin_id: 插件标识。
            auth: 鉴权上下文。

        Returns:
            VersionData: 变更结果。

        Raises:
            ApiError: 未添加（16003）、无上一版本（16008）。
        """
        installation = await self._require_live(plugin_id, auth)
        if not installation.previous_version:
            raise ApiError(
                ErrorCode.PLUGIN_ROLLBACK_UNAVAILABLE,
                internal_detail=f"{plugin_id} has no previous_version",
            )
        return await self._switch_version(
            installation, installation.previous_version, auth, action="rollback"
        )

    async def _switch_version(
        self,
        installation: InstallationRecord,
        target_version: str,
        auth: AuthContext,
        *,
        action: str,
    ) -> VersionData:
        """切换版本（升级/回滚共用），双指针互换。

        **乐观锁**：只允许在「观测到的版本仍是当前版本」时切换。两个管理员同时
        点升级/回退时，后提交者会看到指针已被改 → `16010`（架构 §5.4 场景 ②），
        而不是把 `previous_version` 写成中间态（那会让"只能回退一次"变成"回退到自己"）。

        Args:
            installation: 当前安装行。
            target_version: 目标版本。
            auth: 鉴权上下文。
            action: `upgrade` / `rollback`（写进审计）。

        Returns:
            VersionData: 变更结果。

        Raises:
            ApiError: 版本指针已被并发修改（16010）。
        """
        updated = await self._store.swap_version(
            installation.tenant_id,
            installation.plugin_id,
            expected_version=installation.version,
            target_version=target_version,
        )
        if updated is None:
            logger.warning(
                "plugin_version_conflict",
                tenant_id=installation.tenant_id,
                plugin_id=installation.plugin_id,
                expected=installation.version,
                action=action,
            )
            raise ApiError(
                ErrorCode.PLUGIN_STATE_CONFLICT,
                internal_detail=(
                    f"version pointer changed: {installation.plugin_id}"
                    f" expected {installation.version} -> {target_version}"
                ),
            )
        catalog = await self._store.get_catalog(installation.plugin_id)
        latest = catalog.latest_version if catalog is not None else target_version
        await self._audit.record(
            tenant_id=auth.tenant_id,
            actor_id=auth.user_id,
            action=ACTION_PLUGIN_VERSION_CHANGE,
            resource_type=RESOURCE_PLUGIN,
            resource_id=installation.plugin_id,
            actor_role=auth.roles[0] if auth.roles else "",
            change_summary={
                "version": {"from": installation.version, "to": target_version},
                "action": action,
            },
        )
        logger.info(
            "plugin_version_switched",
            tenant_id=auth.tenant_id,
            plugin_id=installation.plugin_id,
            action=action,
            version=target_version,
        )
        return VersionData(
            plugin_id=installation.plugin_id,
            version=updated.version,
            previous_version=updated.previous_version,
            has_update=version_tuple(latest) > version_tuple(updated.version),
        )

    # ------------------------------------------------------------------
    # PL10 插件配置
    # ------------------------------------------------------------------
    async def set_settings(
        self, *, plugin_id: str, request: SettingsRequest, auth: AuthContext
    ) -> SettingsData:
        """写入插件配置（PL10，键须 ⊆ 声明）。

        Args:
            plugin_id: 插件标识。
            request: 配置请求。
            auth: 鉴权上下文。

        Returns:
            SettingsData: 当前配置。

        Raises:
            ApiError: 未添加（16003）、出现未声明的配置键（20001）。
        """
        installation = await self._require_live(plugin_id, auth)
        manifest = self._registry.get(plugin_id, installation.version)
        declared = (
            {item.key for item in manifest.settings_schema} if manifest is not None else set()
        )
        unknown = sorted(set(request.settings) - declared)
        if unknown:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                internal_detail=f"undeclared settings: {', '.join(unknown)}",
            )
        updated = await self._store.save_installation(
            InstallationRecord(
                tenant_id=installation.tenant_id,
                plugin_id=installation.plugin_id,
                version=installation.version,
                previous_version=installation.previous_version,
                status=installation.status,
                settings=dict(request.settings),
                capability_version=installation.capability_version,
                installed_by=installation.installed_by,
                installed_at=installation.installed_at,
                enabled_at=installation.enabled_at,
                disabled_at=installation.disabled_at,
            )
        )
        return SettingsData(plugin_id=plugin_id, settings=updated.settings, updated_at=now_iso())

    async def _require_live(self, plugin_id: str, auth: AuthContext) -> InstallationRecord:
        """读取安装行并要求「已添加」（否则 16003）。

        Args:
            plugin_id: 插件标识。
            auth: 鉴权上下文。

        Returns:
            InstallationRecord: 安装行。

        Raises:
            ApiError: 未添加或已卸载（16003）。
        """
        installation = await self._store.get_installation(auth.tenant_id, plugin_id)
        if installation is None or not installation.is_live:
            raise ApiError(
                ErrorCode.PLUGIN_NOT_INSTALLED,
                internal_detail=f"tenant={auth.tenant_id} plugin={plugin_id}",
            )
        return installation

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    async def _plan_allows_market(self, tenant_id: str) -> bool:
        """套餐是否含插件市场（`features.plugin_market`）。

        未开通订阅 = 免费版 = `plugin_market: false`（PRD 5.1 默认配置）。

        Args:
            tenant_id: 企业 ID。

        Returns:
            bool: 含市场返回 True。
        """
        from app.business.subscription_service import get_subscription_service

        subscriptions = get_subscription_service(self._settings)
        current = await subscriptions.current(tenant_id=tenant_id)
        plan = await self._plans.get(current.plan_code)
        if plan is None:
            return False
        return bool(plan.features.get("plugin_market", False))

    async def _load_market_context(self, plugin_id: str, auth: AuthContext) -> _MarketContext:
        """装载市场/详情需要的上下文（一次查询复用）。

        Args:
            plugin_id: 插件标识。
            auth: 鉴权上下文。

        Returns:
            _MarketContext: 上下文。

        Raises:
            ApiError: 插件不存在或已下架（16002）。
        """
        catalog = await self._store.get_catalog(plugin_id)
        if catalog is None or not catalog.installable:
            raise ApiError(
                ErrorCode.PLUGIN_NOT_FOUND,
                internal_detail=f"plugin_id={plugin_id} missing_or_offline",
            )
        manifest = self._registry.get(plugin_id, catalog.latest_version)
        installation = await self._store.get_installation(auth.tenant_id, plugin_id)
        if installation is not None and installation.status == STATUS_UNINSTALLED:
            installation = None
        policies = await self._store.list_policies(auth.tenant_id)
        return _MarketContext(
            catalog=catalog,
            manifest=manifest,
            installation=installation,
            plan_enabled=await self._plan_allows_market(auth.tenant_id),
            policies=policies,
        )

    async def _missing_requires(self, manifest: PluginManifest, tenant_id: str) -> list[str]:
        """检查前置插件是否已启用。

        Args:
            manifest: 声明。
            tenant_id: 企业 ID。

        Returns:
            list[str]: 未满足的前置插件标识。
        """
        missing: list[str] = []
        for required in manifest.requires_plugins:
            installation = await self._store.get_installation(tenant_id, required)
            if installation is None or not installation.is_live:
                missing.append(required)
        return missing


def _policy_items(policies: dict[str, bool]) -> list[PluginCapabilityItem]:
    """把企业能力开关转成条目（PL1 顶层 `org_capabilities`，3 项）。

    Args:
        policies: 开关字典。

    Returns:
        list[CapabilityPolicyItem]: 三条企业能力条目。
    """
    items: list[PluginCapabilityItem] = []
    for capability in ORG_CAPABILITIES:
        catalog = CAPABILITY_CATALOG.get(capability, {})
        items.append(
            PluginCapabilityItem(
                capability_key=capability,
                display_name=catalog.get("display_name", capability),
                reason=catalog.get("reason", ""),
                denial_effect=catalog.get("denial_effect", ""),
                category="org",
                required=True,
                org_switch_enabled=bool(policies.get(capability, False)),
            )
        )
    return items


def _capability_items(
    manifest: PluginManifest | None, policies: dict[str, bool]
) -> list[PluginCapabilityItem]:
    """把声明的能力转成授权抽屉条目（含企业开关态）。

    Args:
        manifest: 声明（可能为 None）。
        policies: 企业能力开关。

    Returns:
        list[PluginCapabilityItem]: 能力条目。
    """
    if manifest is None:
        return []
    items: list[PluginCapabilityItem] = []
    for claim in manifest.capabilities:
        catalog = CAPABILITY_CATALOG.get(claim.key, {})
        category = "personal" if claim.key in PERSONAL_CAPABILITIES else "org"
        items.append(
            PluginCapabilityItem(
                capability_key=claim.key,
                display_name=catalog.get("display_name", claim.key),
                reason=catalog.get("reason", ""),
                denial_effect=catalog.get("denial_effect", ""),
                category=category,
                required=claim.required,
                org_switch_enabled=(
                    True if category == "personal" else bool(policies.get(claim.key, False))
                ),
            )
        )
    return items


def _category_counts(catalog: list[CatalogRecord]) -> list[CategoryCountItem]:
    """统计分类计数（固定顺序，缺项也返回 0）。

    Args:
        catalog: 全量可售目录。

    Returns:
        list[CategoryCountItem]: 分类计数。
    """
    counts: dict[str, int] = {}
    for record in catalog:
        counts[record.category] = counts.get(record.category, 0) + 1
    return [
        CategoryCountItem(key=key, name=CATEGORY_NAMES.get(key, key), count=counts.get(key, 0))
        for key in CATEGORY_ORDER
    ]


def _sorted_items(items: list[MarketItem], sort: str) -> list[MarketItem]:
    """按排序方式排列市场条目。

    Args:
        items: 条目列表。
        sort: `recommended` / `newest` / `rating`。

    Returns:
        list[MarketItem]: 排序后的列表。
    """
    if sort == "rating":
        return sorted(items, key=lambda item: (-item.rating_avg, -item.rating_count))
    if sort == "newest":
        return sorted(items, key=lambda item: version_tuple(item.latest_version), reverse=True)
    return items


def _assert_settings_keys(manifest: PluginManifest, settings: dict[str, Any]) -> None:
    """校验配置项键 ⊆ 声明（防注入未声明配置）。

    Args:
        manifest: 声明。
        settings: 请求里的配置。

    Raises:
        ApiError: 出现未声明的配置项（20001）。
    """
    declared = {item.key for item in manifest.settings_schema}
    unknown = sorted(set(settings) - declared)
    if unknown:
        raise ApiError(
            ErrorCode.PARAM_INVALID,
            internal_detail=f"undeclared settings: {', '.join(unknown)}",
        )


# ===========================================================================
# 进程级单例
# ===========================================================================
_service: PluginService | None = None


def get_plugin_service(settings: Settings | None = None) -> PluginService:
    """获取插件服务单例。

    Args:
        settings: 可选配置实例。

    Returns:
        PluginService: 进程内唯一实例。
    """
    global _service
    if _service is None:
        _service = PluginService(settings)
    return _service


def reset_plugin_service() -> None:
    """重置插件服务单例（测试隔离用）。"""
    global _service
    _service = None


__all__: list[str] = [
    "CATEGORY_NAMES",
    "CATEGORY_ORDER",
    "DEFAULT_MARKET_PAGE_SIZE",
    "MESSAGE_OFFLINE",
    "MESSAGE_PLAN_BLOCKED",
    "PLATFORM_TENANT",
    "STATUS_DISABLED",
    "PluginService",
    "get_plugin_service",
    "reset_plugin_service",
]
