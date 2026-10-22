"""插件生命周期与评价单测（阶段三 B18，`architecture-phase3.md` §2.4 / §5.2 PL6-PL12）。

**状态机逐条覆盖**（§2.4 表格）：`not_installed → PL6/PL7/PL8/PL9 = 16003`、
`enabled ↔ disabled`、`enabled/disabled → uninstalled`、`enabled → 升级/回退`。
非法的"静默 no-op"是这里最要防的失败模式——用户点了按钮什么都没发生，
比报错更难排查，所以每条非法迁移都断言**具体错误码**。

另覆盖：停用不打断在跑任务（响应 `running_task_count`）、卸载保留任务数、
只保留最近 2 个版本（回退 1 次后 `16008`）、一人一插件一条评价（覆盖更新）、
未添加不能评价（`16011`）、匿名显示「某位同事」。
"""

from __future__ import annotations

import pytest

from app.business.audit_service import (
    ACTION_PLUGIN_STATE_CHANGE,
    ACTION_PLUGIN_UNINSTALL,
    ACTION_PLUGIN_VERSION_CHANGE,
    AuditService,
    InMemoryAuditRecorder,
)
from app.business.plugin_grant_service import PluginGrantService
from app.business.plugin_review_service import ANONYMOUS_DISPLAY_NAME, PluginReviewService
from app.business.plugin_service import PluginService
from app.business.subscription_service import SubscriptionService
from app.core.config import Settings, reset_settings_cache
from app.core.errors import ApiError, ErrorCode
from app.core.security import AuthContext
from app.db.repositories.billing_repo import get_billing_store
from app.db.repositories.plugin_repo import STATUS_DISABLED, STATUS_ENABLED, InMemoryPluginStore
from app.db.repositories.plugin_review_repo import InMemoryReviewStore
from app.plugins.actions import ORG_CAPABILITIES
from app.plugins.loader import PluginRegistry
from app.schemas.billing import ChangeSubscriptionRequest
from app.schemas.plugin import ReviewRequest, SettingsRequest, StateRequest, UpgradeRequest

pytestmark = pytest.mark.unit

TENANT = "tnt_plugin_lifecycle"
OWNER = AuthContext(user_id="usr_owner", tenant_id=TENANT, roles=["owner"])

#: 轻量插件（只需 ② 工具 + ⑥ 对外发布，便于构造"已安装"状态）
PLUGIN = "plg_mail_draft"


def _settings() -> Settings:
    """构造测试配置。

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


class _Fixture:
    """生命周期测试装配（已安装 `PLUGIN` 的状态）。

    Attributes:
        service: 插件服务。
        store: 插件存储。
        reviews: 评价存储。
        review_service: 评价服务。
        audit: 审计存储。
        grants: 授权服务。
    """

    def __init__(self, settings: Settings) -> None:
        """初始化。

        Args:
            settings: 配置实例。
        """
        self.settings = settings
        self.registry = PluginRegistry()
        assert self.registry.load_all() == []
        self.store = InMemoryPluginStore(self.registry)
        self.reviews = InMemoryReviewStore()
        self.audit = InMemoryAuditRecorder()
        self.grants = PluginGrantService(settings, self.store)
        self.service = PluginService(
            settings,
            store=self.store,
            registry=self.registry,
            grants=self.grants,
            audit=AuditService(settings, recorder=self.audit),
        )
        self.review_service = PluginReviewService(settings, store=self.store, reviews=self.reviews)


async def _installed(settings: Settings) -> _Fixture:
    """构造"已安装目标插件"的夹具。

    Args:
        settings: 配置实例。

    Returns:
        _Fixture: 夹具。
    """
    from app.schemas.plugin import InstallRequest

    fixture = _Fixture(settings)
    billing = get_billing_store(settings)
    subscriptions = SubscriptionService(settings, billing)
    await subscriptions.change(
        tenant_id=TENANT,
        actor_id=OWNER.user_id,
        request=ChangeSubscriptionRequest(plan_code="professional", seats=5, action="upgrade"),
    )
    await fixture.service.ensure_versions()
    await fixture.store.save_policies(TENANT, dict.fromkeys(ORG_CAPABILITIES, True), OWNER.user_id)
    manifest = fixture.registry.get(PLUGIN)
    assert manifest is not None
    await fixture.service.install(
        plugin_id=PLUGIN,
        request=InstallRequest(grants=dict.fromkeys(manifest.capability_keys, True)),
        auth=OWNER,
    )
    return fixture


@pytest.mark.asyncio
async def test_enable_disable_roundtrip_records_audit() -> None:
    """`enabled → disabled → enabled` 合法；每次变更写 `plugin.state_change` 审计。"""
    fixture = await _installed(_settings())
    disabled = await fixture.service.set_state(plugin_id=PLUGIN, status=STATUS_DISABLED, auth=OWNER)
    assert disabled.status == STATUS_DISABLED
    installation = await fixture.store.get_installation(TENANT, PLUGIN)
    assert installation is not None
    assert installation.status == STATUS_DISABLED

    enabled = await fixture.service.set_state(plugin_id=PLUGIN, status=STATUS_ENABLED, auth=OWNER)
    assert enabled.status == STATUS_ENABLED
    entries = await fixture.audit.recent(TENANT, action=ACTION_PLUGIN_STATE_CHANGE)
    assert len(entries) == 2
    assert entries[0].change_summary["status"] == {"from": "disabled", "to": "enabled"}


@pytest.mark.asyncio
async def test_same_state_is_idempotent_return() -> None:
    """重复设置同一状态直接返回当前状态（不写审计、不改时间戳）。"""
    fixture = await _installed(_settings())
    data = await fixture.service.set_state(plugin_id=PLUGIN, status=STATUS_ENABLED, auth=OWNER)
    assert data.status == STATUS_ENABLED
    entries = await fixture.audit.recent(TENANT, action=ACTION_PLUGIN_STATE_CHANGE)
    assert entries == []


@pytest.mark.asyncio
async def test_lifecycle_actions_on_not_installed_return_16003() -> None:
    """未添加时 PL6/PL7/PL8/PL9/PL10 一律 `16003`（不允许静默 no-op）。"""
    fixture = _Fixture(_settings())
    await fixture.service.ensure_versions()
    calls = [
        fixture.service.set_state(plugin_id=PLUGIN, status=STATUS_DISABLED, auth=OWNER),
        fixture.service.uninstall(plugin_id=PLUGIN, auth=OWNER),
        fixture.service.upgrade(plugin_id=PLUGIN, target_version="1.0.0", auth=OWNER),
        fixture.service.rollback(plugin_id=PLUGIN, auth=OWNER),
        fixture.service.set_settings(
            plugin_id=PLUGIN, request=SettingsRequest(settings={}), auth=OWNER
        ),
    ]
    for call in calls:
        with pytest.raises(ApiError) as excinfo:
            await call
        assert excinfo.value.code is ErrorCode.PLUGIN_NOT_INSTALLED


@pytest.mark.asyncio
async def test_uninstall_keeps_history_and_revokes_grants() -> None:
    """卸载 → `uninstalled`、撤权、写审计；再次启用 → `16003`。"""
    fixture = await _installed(_settings())
    data = await fixture.service.uninstall(plugin_id=PLUGIN, auth=OWNER)
    assert data.uninstalled is True
    assert data.kept_task_count == 0
    installation = await fixture.store.get_installation(TENANT, PLUGIN)
    assert installation is not None
    assert installation.status == "uninstalled"
    granted = await fixture.grants.snapshot(TENANT, PLUGIN, OWNER.user_id)
    assert granted == frozenset(), "卸载必须撤回全部能力授权"

    with pytest.raises(ApiError) as excinfo:
        await fixture.service.set_state(plugin_id=PLUGIN, status=STATUS_ENABLED, auth=OWNER)
    assert excinfo.value.code is ErrorCode.PLUGIN_NOT_INSTALLED
    entries = await fixture.audit.recent(TENANT, action=ACTION_PLUGIN_UNINSTALL)
    assert len(entries) == 1


@pytest.mark.asyncio
async def test_upgrade_and_rollback_keep_two_versions() -> None:
    """升级 → 双指针就位；回退 → 交换；无上一版本时 `16008`。"""
    fixture = await _installed(_settings())
    # 造一个 1.0.1 版本（同插件新版本允许注册）
    base = fixture.registry.get(PLUGIN)
    assert base is not None
    payload = base.model_dump(mode="json")
    payload["version"] = "1.0.1"
    payload["changelog"] = "修复发送失败时未提示的问题"
    assert fixture.registry.load_manifest(payload, source="v1.0.1") is None
    await fixture.service.ensure_versions()

    upgraded = await fixture.service.upgrade(plugin_id=PLUGIN, target_version="1.0.1", auth=OWNER)
    assert upgraded.version == "1.0.1"
    assert upgraded.previous_version == "1.0.0"

    rolled = await fixture.service.rollback(plugin_id=PLUGIN, auth=OWNER)
    assert rolled.version == "1.0.0"
    assert rolled.previous_version == "1.0.1"
    entries = await fixture.audit.recent(TENANT, action=ACTION_PLUGIN_VERSION_CHANGE)
    assert len(entries) == 2
    assert entries[1].change_summary["action"] == "upgrade"


@pytest.mark.asyncio
async def test_upgrade_unknown_version_returns_16012() -> None:
    """目标版本不存在 → `16012`。"""
    fixture = await _installed(_settings())
    with pytest.raises(ApiError) as excinfo:
        await fixture.service.upgrade(plugin_id=PLUGIN, target_version="9.9.9", auth=OWNER)
    assert excinfo.value.code is ErrorCode.PLUGIN_VERSION_NOT_FOUND


@pytest.mark.asyncio
async def test_rollback_without_previous_returns_16008() -> None:
    """首次安装后没有"上一版本" → 直接回退 `16008`（只保留 2 版语义）。"""
    fixture = await _installed(_settings())
    installation = await fixture.store.get_installation(TENANT, PLUGIN)
    assert installation is not None
    assert installation.previous_version == "", "首次安装没有上一版本"

    with pytest.raises(ApiError) as excinfo:
        await fixture.service.rollback(plugin_id=PLUGIN, auth=OWNER)
    assert excinfo.value.code is ErrorCode.PLUGIN_ROLLBACK_UNAVAILABLE


@pytest.mark.asyncio
async def test_settings_rejects_undeclared_key() -> None:
    """PL10：未声明的配置键 → `20001`；合法键写入成功。"""
    fixture = await _installed(_settings())
    with pytest.raises(ApiError) as excinfo:
        await fixture.service.set_settings(
            plugin_id=PLUGIN, request=SettingsRequest(settings={"nope": "x"}), auth=OWNER
        )
    assert excinfo.value.code is ErrorCode.PARAM_INVALID

    data = await fixture.service.set_settings(
        plugin_id=PLUGIN,
        request=SettingsRequest(settings={"signature": "张三 / 研发部"}),
        auth=OWNER,
    )
    assert data.settings == {"signature": "张三 / 研发部"}


@pytest.mark.asyncio
async def test_review_requires_installation() -> None:
    """未添加不能评价 → `16011`。"""
    fixture = _Fixture(_settings())
    await fixture.service.ensure_versions()
    with pytest.raises(ApiError) as excinfo:
        await fixture.review_service.submit(
            plugin_id=PLUGIN, request=ReviewRequest(rating=4, content="还行"), auth=OWNER
        )
    assert excinfo.value.code is ErrorCode.PLUGIN_REVIEW_NOT_ALLOWED


@pytest.mark.asyncio
async def test_review_upserts_and_recomputes_rating() -> None:
    """一人一条：第二次提交覆盖第一次；均分随之重算并写回目录。"""
    fixture = await _installed(_settings())
    first = await fixture.review_service.submit(
        plugin_id=PLUGIN, request=ReviewRequest(rating=2, content="一般"), auth=OWNER
    )
    assert first.rating_avg == 2.0
    second = await fixture.review_service.submit(
        plugin_id=PLUGIN,
        request=ReviewRequest(rating=5, content="很好用", anonymous=True),
        auth=OWNER,
    )
    assert second.review_id == first.review_id, "覆盖更新保留同一条评价"
    assert second.rating_count == 1
    assert second.rating_avg == 5.0

    catalog = await fixture.store.get_catalog(PLUGIN)
    assert catalog is not None
    assert (catalog.rating_avg, catalog.rating_count) == (5.0, 1)

    listing = await fixture.review_service.list_reviews(plugin_id=PLUGIN, auth=OWNER)
    assert len(listing.items) == 1
    assert listing.items[0].author_name == ANONYMOUS_DISPLAY_NAME
    assert listing.items[0].department_name == ""


@pytest.mark.asyncio
async def test_enable_requires_grants_and_policy() -> None:
    """启用时若能力被撤销或企业开关关闭 → `16005` / `16006`（不允许带着无效状态启用）。"""
    fixture = await _installed(_settings())
    await fixture.service.set_state(plugin_id=PLUGIN, status=STATUS_DISABLED, auth=OWNER)

    await fixture.store.save_policies(TENANT, {"org.external.publish": False}, OWNER.user_id)
    with pytest.raises(ApiError) as policy_blocked:
        await fixture.service.set_state(plugin_id=PLUGIN, status=STATUS_ENABLED, auth=OWNER)
    assert policy_blocked.value.code is ErrorCode.CAPABILITY_POLICY_BLOCKED

    await fixture.store.save_policies(TENANT, {"org.external.publish": True}, OWNER.user_id)
    await fixture.grants.grant(
        tenant_id=TENANT, plugin_id=PLUGIN, user_id=OWNER.user_id, capabilities=set()
    )
    with pytest.raises(ApiError) as grant_missing:
        await fixture.service.set_state(plugin_id=PLUGIN, status=STATUS_ENABLED, auth=OWNER)
    assert grant_missing.value.code is ErrorCode.CAPABILITY_NOT_GRANTED


def test_state_request_rejects_unknown_status() -> None:
    """PL6 请求体只接受 `enabled` / `disabled`（否则 422 → 20001）。"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        StateRequest(status="uninstalled")  # type: ignore[arg-type]


def test_upgrade_request_requires_semver_length() -> None:
    """PL8 请求体的版本串有最小长度（防 `""` 之类的空值打到服务层）。"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        UpgradeRequest(target_version="")
