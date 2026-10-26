"""插件安装与授权单测（阶段三 B17，`architecture-phase3.md` §5.2 PL5 / §2.5）。

**本文件的主体是"校验顺序"**：`15001（套餐）→ 16005（本人未授权）→ 16006（企业未开放）
→ 16009（前置插件）→ 20001（参数）`。这个顺序不是审美问题——三条出路的 CTA 完全不同
（升级套餐 / 重新授权 / 找管理员开启），顺序错了用户就会被引到错误的下一步。
所以每条分支都单独断言**只**返回它该返回的码。

另外覆盖：安装即启用（Q3-3）、授权写 `plugin_grant` 而非角色权限点（PRD R4）、
重复安装不重复计数（幂等友好）、撤销授权立即生效（5s 缓存失效）。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.business.audit_service import ACTION_PLUGIN_INSTALL, AuditService, InMemoryAuditRecorder
from app.business.plugin_grant_service import PluginGrantService
from app.business.plugin_service import MESSAGE_PLAN_BLOCKED, PluginService
from app.business.subscription_service import SubscriptionService
from app.core.config import Settings, reset_settings_cache
from app.core.errors import ApiError, ErrorCode
from app.core.security import AuthContext
from app.db.repositories.billing import InMemoryBillingStore, get_billing_store
from app.db.repositories.plugin import (
    STATUS_ENABLED,
    InMemoryPluginStore,
)
from app.plugins.actions import ORG_CAPABILITIES
from app.plugins.loader import PluginRegistry
from app.schemas.billing import ChangeSubscriptionRequest
from app.schemas.plugin import CapabilityPolicyRequest, CapabilityPolicyRequestItem, InstallRequest

pytestmark = pytest.mark.unit

TENANT = "tnt_plugin_install"
OWNER = AuthContext(user_id="usr_owner", tenant_id=TENANT, roles=["owner"])

#: 三个标杆插件里最"重"的一个（需要 ⑤ 写仓库 + ② 工具 + ③ 读文件）
DEV_PLUGIN = "plg_dev_workbench"

#: 轻量插件（只需要 ② 工具 + ⑥ 对外发布）
MAIL_PLUGIN = "plg_mail_draft"


def _settings() -> Settings:
    """构造测试配置（内存后端）。

    Returns:
        Settings: 配置实例。
    """
    reset_settings_cache()
    return Settings(
        env="test",
        deploy_mode="private",
        downstream_enabled=False,
        auth_mode="stub",
        jwt_secret="unit-test-secret",
        ratelimit_enabled=False,
    )


async def _upgrade_to_professional(settings: Settings) -> None:
    """把租户套餐升到专业版（含 `plugin_market=true`）。

    只关心套餐功能开关，因此不支付订单（`plan_code` 已足以决定 `features`）。

    Args:
        settings: 配置实例。
    """
    # 必须写**全局**计费存储：`PluginService._plan_allows_market()` 读的是
    # `SubscriptionService.current()`（同一个单例），用局部实例会让套餐变更不可见。
    billing = get_billing_store(settings)
    subscriptions = SubscriptionService(settings, billing)
    await subscriptions.change(
        tenant_id=TENANT,
        actor_id=OWNER.user_id,
        request=ChangeSubscriptionRequest(plan_code="professional", seats=5, action="upgrade"),
    )


class _Fixture:
    """安装测试装配（同一租户内可连续操作）。

    Attributes:
        service: 插件服务。
        store: 内存插件存储。
        registry: 声明注册表。
        grants: 授权服务。
        audit: 审计存储。
        billing: 计费存储。
    """

    def __init__(self, settings: Settings, *, with_market: bool = True) -> None:
        """初始化。

        Args:
            settings: 配置实例。
            with_market: 是否把套餐升级到含插件市场的档位。
        """
        self.settings = settings
        self.registry = PluginRegistry()
        assert self.registry.load_all() == []
        self.store = InMemoryPluginStore(self.registry)
        self.audit = InMemoryAuditRecorder()
        self.grants = PluginGrantService(settings, self.store)
        self.service = PluginService(
            settings,
            store=self.store,
            registry=self.registry,
            grants=self.grants,
            audit=AuditService(settings, recorder=self.audit),
        )
        self.billing = InMemoryBillingStore()
        self.with_market = with_market

    async def prepare(self) -> None:
        """按需准备套餐与声明快照。"""
        if self.with_market:
            await _upgrade_to_professional(self.settings)
        await self.service.ensure_versions()

    async def enable_all_org_capabilities(self) -> None:
        """把 ⑥ 企业能力全部开启（模拟管理员在团队设置里打开）。"""
        await self.store.save_policies(TENANT, dict.fromkeys(ORG_CAPABILITIES, True), OWNER.user_id)

    def full_grants(self, plugin_id: str) -> dict[str, bool]:
        """构造"全部声明能力都同意"的授权意图。

        Args:
            plugin_id: 插件标识。

        Returns:
            dict[str, bool]: 能力 → True。
        """
        manifest = self.registry.get(plugin_id)
        assert manifest is not None
        return dict.fromkeys(manifest.capability_keys, True)


@pytest.fixture
def settings() -> Settings:
    """提供测试配置。

    Returns:
        Settings: 配置实例。
    """
    return _settings()


# ===========================================================================
# 校验顺序
# ===========================================================================
@pytest.mark.asyncio
async def test_free_plan_cannot_install(settings: Settings) -> None:
    """免费版安装 → `15001`（市场可见、安装被拦：勘误 #1 的落点）。"""
    fixture = _Fixture(settings, with_market=False)
    await fixture.prepare()
    with pytest.raises(ApiError) as excinfo:
        await fixture.service.install(
            plugin_id=MAIL_PLUGIN,
            request=InstallRequest(grants=fixture.full_grants(MAIL_PLUGIN)),
            auth=OWNER,
        )
    assert excinfo.value.code is ErrorCode.PLAN_FEATURE_UNAVAILABLE
    assert excinfo.value.message == MESSAGE_PLAN_BLOCKED
    assert await fixture.store.get_installation(TENANT, MAIL_PLUGIN) is None, "不得落安装行"


@pytest.mark.asyncio
async def test_missing_required_capability_returns_16005(settings: Settings) -> None:
    """缺必需能力 → `16005`（先于企业开关校验）。"""
    fixture = _Fixture(settings)
    await fixture.prepare()
    with pytest.raises(ApiError) as excinfo:
        await fixture.service.install(
            plugin_id=DEV_PLUGIN,
            request=InstallRequest(grants={"personal.tool.use": True}),
            auth=OWNER,
        )
    assert excinfo.value.code is ErrorCode.CAPABILITY_NOT_GRANTED


@pytest.mark.asyncio
async def test_disabled_org_policy_returns_16006(settings: Settings) -> None:
    """企业能力未开放 → `16006`（能力已同意，但企业没开）。"""
    fixture = _Fixture(settings)
    await fixture.prepare()
    with pytest.raises(ApiError) as excinfo:
        await fixture.service.install(
            plugin_id=DEV_PLUGIN,
            request=InstallRequest(grants=fixture.full_grants(DEV_PLUGIN)),
            auth=OWNER,
        )
    assert excinfo.value.code is ErrorCode.CAPABILITY_POLICY_BLOCKED
    assert "org.repo.write" in (excinfo.value.internal_detail or "")


@pytest.mark.asyncio
async def test_missing_requires_plugin_returns_16009(settings: Settings) -> None:
    """前置插件未添加 → `16009`（6 份内置声明互不依赖，故用合成声明验证）。"""
    registry = PluginRegistry()
    assert registry.load_all() == []
    base = registry.get(MAIL_PLUGIN)
    assert base is not None
    payload: dict[str, Any] = base.model_dump(mode="json")
    payload["plugin_id"] = "plg_needs_other"
    payload["requires_plugins"] = ["plg_meeting_minutes"]
    assert registry.load_manifest(payload, source="synthetic.json") is None

    store = InMemoryPluginStore(registry)
    grants = PluginGrantService(settings, store)
    service = PluginService(settings, store=store, registry=registry, grants=grants)
    await _upgrade_to_professional(settings)
    await service.ensure_versions()
    await store.save_policies(TENANT, dict.fromkeys(ORG_CAPABILITIES, True), OWNER.user_id)

    manifest = registry.get("plg_needs_other")
    assert manifest is not None
    with pytest.raises(ApiError) as excinfo:
        await service.install(
            plugin_id="plg_needs_other",
            request=InstallRequest(grants=dict.fromkeys(manifest.capability_keys, True)),
            auth=OWNER,
        )
    assert excinfo.value.code is ErrorCode.PLUGIN_DEPENDENCY_MISSING
    assert "plg_meeting_minutes" in (excinfo.value.internal_detail or "")


@pytest.mark.asyncio
async def test_undeclared_capability_returns_20001(settings: Settings) -> None:
    """授权意图里出现未声明的能力 → `20001`（防"多要权限"）。"""
    fixture = _Fixture(settings)
    await fixture.prepare()
    grants = fixture.full_grants(MAIL_PLUGIN)
    grants["org.repo.write"] = True
    with pytest.raises(ApiError) as excinfo:
        await fixture.service.install(
            plugin_id=MAIL_PLUGIN, request=InstallRequest(grants=grants), auth=OWNER
        )
    assert excinfo.value.code is ErrorCode.PARAM_INVALID
    assert "org.repo.write" in (excinfo.value.internal_detail or "")


@pytest.mark.asyncio
async def test_undeclared_setting_key_returns_20001(settings: Settings) -> None:
    """配置项出现未声明的键 → `20001`（防注入未声明配置）。"""
    fixture = _Fixture(settings)
    await fixture.prepare()
    await fixture.enable_all_org_capabilities()
    with pytest.raises(ApiError) as excinfo:
        await fixture.service.install(
            plugin_id=MAIL_PLUGIN,
            request=InstallRequest(
                grants=fixture.full_grants(MAIL_PLUGIN), settings={"unknown_key": "x"}
            ),
            auth=OWNER,
        )
    assert excinfo.value.code is ErrorCode.PARAM_INVALID


# ===========================================================================
# 成功路径
# ===========================================================================
@pytest.mark.asyncio
@pytest.mark.p0
async def test_install_enables_immediately_and_records_grant(settings: Settings) -> None:
    """安装即启用（Q3-3）+ 授权写 `plugin_grant` + 审计留痕 + 安装数 +1。"""
    fixture = _Fixture(settings)
    await fixture.prepare()
    await fixture.enable_all_org_capabilities()

    data = await fixture.service.install(
        plugin_id=DEV_PLUGIN,
        request=InstallRequest(
            grants=fixture.full_grants(DEV_PLUGIN),
            settings={"default_repo": "team/app"},
        ),
        auth=OWNER,
    )
    assert data.status == STATUS_ENABLED
    assert data.version == "1.0.0"
    assert set(data.granted_capabilities) == set(fixture.full_grants(DEV_PLUGIN))
    assert data.denied_capabilities == []

    installation = await fixture.store.get_installation(TENANT, DEV_PLUGIN)
    assert installation is not None
    assert installation.status == STATUS_ENABLED
    assert installation.settings == {"default_repo": "team/app"}
    granted = await fixture.grants.snapshot(TENANT, DEV_PLUGIN, OWNER.user_id)
    assert granted == frozenset(fixture.full_grants(DEV_PLUGIN))

    catalog = await fixture.store.get_catalog(DEV_PLUGIN)
    assert catalog is not None and catalog.install_count == 1

    entries = await fixture.audit.recent(TENANT, action=ACTION_PLUGIN_INSTALL)
    assert entries and entries[0].actor_id == OWNER.user_id
    assert entries[0].change_summary["plugin_id"] == DEV_PLUGIN


@pytest.mark.asyncio
async def test_reinstall_does_not_double_count(settings: Settings) -> None:
    """重复安装（同租户再点一次）不重复累加安装企业数。"""
    fixture = _Fixture(settings)
    await fixture.prepare()
    await fixture.enable_all_org_capabilities()
    for _ in range(2):
        await fixture.service.install(
            plugin_id=MAIL_PLUGIN,
            request=InstallRequest(grants=fixture.full_grants(MAIL_PLUGIN)),
            auth=OWNER,
        )
    catalog = await fixture.store.get_catalog(MAIL_PLUGIN)
    assert catalog is not None
    assert catalog.install_count == 1


@pytest.mark.asyncio
async def test_capabilities_expose_org_switch_per_capability(settings: Settings) -> None:
    """PL4：企业能力开关是**逐能力**返回的（PRD v1.1 勘误 #2，前端禁止硬编码）。"""
    fixture = _Fixture(settings)
    await fixture.prepare()

    before = await fixture.service.capabilities(plugin_id=DEV_PLUGIN, auth=OWNER)
    switches_before = {item.capability_key: item.org_switch_enabled for item in before.items}
    assert switches_before["org.repo.write"] is False, "默认关闭（Q3-2）"
    assert switches_before["personal.tool.use"] is True, "个人能力恒开"

    await fixture.service.set_capability_policy(
        request=CapabilityPolicyRequest(
            policies=[CapabilityPolicyRequestItem(capability_key="org.repo.write", enabled=True)]
        ),
        auth=OWNER,
    )
    after = await fixture.service.capabilities(plugin_id=DEV_PLUGIN, auth=OWNER)
    switches_after = {item.capability_key: item.org_switch_enabled for item in after.items}
    assert switches_after["org.repo.write"] is True
    # 未改动的能力保持关闭：用声明了 ⑥ 的内容运营工作台验证（dev 插件不声明 ⑥）
    content = await fixture.service.capabilities(plugin_id="plg_content_ops", auth=OWNER)
    content_switches = {item.capability_key: item.org_switch_enabled for item in content.items}
    assert content_switches["org.external.publish"] is False, "未改的能力保持关闭"


@pytest.mark.asyncio
async def test_policy_rejects_personal_capability(settings: Settings) -> None:
    """PL13 关闭个人能力 → `20001`（个人能力恒开，不可被企业关闭）。"""
    fixture = _Fixture(settings)
    await fixture.prepare()
    with pytest.raises(ApiError) as excinfo:
        await fixture.service.set_capability_policy(
            request=CapabilityPolicyRequest(
                policies=[
                    CapabilityPolicyRequestItem(capability_key="personal.tool.use", enabled=False)
                ]
            ),
            auth=OWNER,
        )
    assert excinfo.value.code is ErrorCode.PARAM_INVALID


@pytest.mark.asyncio
async def test_grant_revocation_is_immediate(settings: Settings) -> None:
    """撤销授权后下一次复检立即失败（5s 缓存被主动失效，不等 TTL）。"""
    fixture = _Fixture(settings)
    await fixture.prepare()
    await fixture.enable_all_org_capabilities()
    await fixture.service.install(
        plugin_id=MAIL_PLUGIN,
        request=InstallRequest(grants=fixture.full_grants(MAIL_PLUGIN)),
        auth=OWNER,
    )
    await fixture.grants.assert_granted(
        tenant_id=TENANT,
        plugin_id=MAIL_PLUGIN,
        user_id=OWNER.user_id,
        required={"personal.tool.use"},
    )
    await fixture.grants.grant(
        tenant_id=TENANT, plugin_id=MAIL_PLUGIN, user_id=OWNER.user_id, capabilities=set()
    )
    with pytest.raises(ApiError) as excinfo:
        await fixture.grants.assert_granted(
            tenant_id=TENANT,
            plugin_id=MAIL_PLUGIN,
            user_id=OWNER.user_id,
            required={"personal.tool.use"},
        )
    assert excinfo.value.code is ErrorCode.CAPABILITY_NOT_GRANTED


@pytest.mark.asyncio
async def test_versions_are_immutable_on_repeat_ensure(settings: Settings) -> None:
    """声明快照幂等：重复登记不新增行、不改摘要（版本不可变）。"""
    fixture = _Fixture(settings)
    await fixture.prepare()
    first = await fixture.store.get_version(MAIL_PLUGIN, "1.0.0")
    assert first is not None
    inserted = await fixture.service.ensure_versions()
    assert inserted == 0, "已登记过，不应再插入"
    again = await fixture.store.get_version(MAIL_PLUGIN, "1.0.0")
    assert again is not None
    assert again.manifest_sha256 == first.manifest_sha256
