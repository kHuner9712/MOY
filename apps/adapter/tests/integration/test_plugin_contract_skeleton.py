"""B16 契约级验收骨架（阶段三插件域地基：声明模型 + 加载器 + 6 份 manifest + G-26 schema）。

**状态：只写不跑（QA 与 PM 2026-10-23 约定）**

B16 源码尚未提交冻结，本文件先落地「可执行断言骨架」，用于提前暴露
「判据写得漂亮但没落点」的问题；**实测（真正跑绿）等 B16 冻结号到位后由 QA 执行**。

完成标准（PM 2026-10-23 四条，缺一条算没做完）：

1. **每条断言标注追溯表编号**（`TC-P3-xx-y`；一条用例多断言写 `⑥`），
   便于失败时直接对回 `docs/prd-phase3-traceability.md`；
2. **未实现项用 `xfail(reason=...)`**，不用裸 `skip`——`xfail` 会在报告里留
   「预期失败 N 条」，B16 落地后自动转绿，本身就是 B16 的验收信号；
3. **顺序：契约型优先**。不依赖业务实现完成的断言（错误码口径、`16001` 不复活）
   现在就有效；依赖真实执行的（PL5 授权不变、PL13 幂等、WF2 重渲染、WF2 只读）靠后；
4. **骨架自身要能自检**：自检用例放在**独立文件** `selftest_plugin_contract.py`
   （文件名不带 `test_`，默认不收集 → 正常全量跑 **0 skipped**，与阶段二验收口径一致）。
   显式运行：`OA_P3_SKELETON_SELFTEST=1 pytest tests/integration/selftest_plugin_contract.py -q`，
   期望 `1 passed + 1 failed`、exit 1。

QA 自加铁律：**派生数字一律从源数据重算**（数函数、数 marker），不引用文档里的声明值——
文档数字是二手真相，源数据才是一手真相。

追溯依据：
- `docs/prd-phase3-traceability.md`（TC-P3-03 / TC-P3-MK08 / TC-P3-PL02 / TC-P3-PL03 / TC-P3-WF12）
- `docs/qa-phase3-test-plan.md` §4.1 / §4.2 / §4.3（用例判据）
- 架构 D-24：插件域 `16002-16012`（11 条）+ 任务域 `17001-17002`（2 条）= 13 条，`16001` 注销不复用
"""

from __future__ import annotations

import sys
from types import ModuleType

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.errors import ERROR_REGISTRY, ErrorCode
from tests.expected_xfail import (
    DEFECT_LEDGER,
    EXPECTED_OPEN,
    EXPECTED_RED,
    EXPECTED_XFAIL,
    EXPECTED_XFAIL_BY_BATCH,
    OPEN_HISTORY,
    RED_HISTORY,
)

pytestmark = pytest.mark.integration


def _owner_headers(settings: Settings) -> dict[str, str]:
    """构造管理员请求头（owner + 全部权限点）。

    Args:
        settings: 配置实例。

    Returns:
        dict[str, str]: 请求头。
    """
    from tests.conftest import make_token

    token = make_token(settings=settings, user_id="usr_owner", roles=["*"])
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": "default"}


#: 本阶段应存在的插件域 + 任务域错误码（架构 D-24：11 + 2 = 13）
EXPECTED_PLUGIN_CODES = {
    16002: "PLUGIN_NOT_FOUND",
    16003: "PLUGIN_NOT_INSTALLED",
    16004: "PLUGIN_DISABLED",
    16005: "CAPABILITY_NOT_GRANTED",
    16006: "CAPABILITY_POLICY_BLOCKED",
    16007: "PLUGIN_VERSION_INCOMPATIBLE",
    16008: "PLUGIN_ROLLBACK_UNAVAILABLE",
    16009: "PLUGIN_DEPENDENCY_MISSING",
    16010: "PLUGIN_STATE_CONFLICT",
    16011: "PLUGIN_REVIEW_NOT_ALLOWED",
    16012: "PLUGIN_VERSION_NOT_FOUND",
    17001: "TASK_RETRY_UNAVAILABLE",
    17002: "TASK_INTERNAL_AUDIT",
}

#: **本骨架的延后用例登记表——append-only（只增不改不删）**
#:
#: 每条记四件事：函数名、追溯表编号、应由哪个批次转绿、当前状态。
#: **转绿后不要删除条目**，只把 `landed_in` 填成实际批次——这样：
#: ① 历史清单永久可查（审计能回答「这 6 条曾经是未实现」）；
#: ② 「xfail 6 → 0」有据可查，而不是"某个数字变了"；
#: ③ 验收报告能直接引「哪几条由哪批做绿」，不必靠回忆。
DEFERRED_CASES: list[dict[str, str | None]] = [
    {
        "name": "test_b1_pl05_already_installed_field_is_always_present",
        "tc": "TC-P3-08 ⑥",
        "batch": "B18",
        "why": "PL5 响应缺 already_installed 常驻字段（B17 已交付 PL5，但字段缺失）",
        "status": "landed",
        "defect": "P3-B17-01",
    },
    {
        "name": "test_b2_pl14_update_grants_before_install_returns_16003",
        "tc": "TC-P3-PL03-11",
        "batch": "B18",
        "why": "PL14 授权写接口未实现（路由未注册）",
        "status": "landed",
    },
    {
        "name": "test_c1_pl05_reinstall_does_not_touch_grants_or_audit",
        "tc": "TC-P3-MK08-06",
        "batch": "B18",
        "why": "依赖 already_installed 与重复安装零增量（B17 未达标）",
        "status": "landed",
        "defect": "P3-B17-01",
    },
    {
        "name": "test_c2_pl13_capability_policy_idempotent_by_idempotency_key",
        "tc": "TC-P3-PL03-07 ⑥",
        "batch": "B18",
        "why": "PL13 写后回读只返 3 项，契约要求全量 6 项",
        "status": "landed",
        "defect": "P3-B17-02",
    },
    {
        "name": "test_c3_wf2_edit_rerenders_download_and_keeps_steps_untouched",
        "tc": "TC-P3-WF12-05",
        "batch": "B19",
        "why": "WF2 产物编辑接口未实现（路由未注册）",
        "status": "xfail",
    },
    {
        "name": "test_c4_wf2_non_editable_types_rejected_without_version_bump",
        "tc": "TC-P3-WF12-06",
        "batch": "B19",
        "why": "WF2 只读类型拒绝未实现（路由未注册）",
        "status": "xfail",
    },
]

EXPECTED_ACTIVE_XFAIL = EXPECTED_XFAIL


# ===========================================================================
# A 组：契约型，**现在就能跑出结果**（不依赖 B16 业务实现完成）
# ===========================================================================
def _assert_codes_not_inverted(
    *, not_installed: int, disabled: int, message_not_installed: str, message_disabled: str
) -> None:
    """校验「未安装码 / 已停用码」不变量（纯函数，便于用负样本证明它能抓错）。

    抽成纯函数的目的：**负样本只需传错参数**即可验证「这条断言真的会红」，
    不需要去改 `errors.py` 或 `ERROR_REGISTRY` 这类真实常量——避免污染进程内状态、
    避免「临时改回」被中断后引发连锁失败。

    Args:
        not_installed: 未安装码值。
        disabled: 已停用码值。
        message_not_installed: 未安装码文案。
        message_disabled: 已停用码文案。

    Raises:
        AssertionError: 任一不变量被破坏。
    """
    assert not_installed == 16003, f"[TC-P3-PL02-01 ①] 未安装码应为 16003，实际 {not_installed}"
    assert disabled == 16004, f"[TC-P3-PL02-05 ①] 已停用码应为 16004，实际 {disabled}"
    assert not_installed != disabled, "[TC-P3-PL02-01 ②] 两个码不得相同（倒置/合并）"
    assert "还没有添加" in message_not_installed, (
        f"[TC-P3-PL02-01 ③] 16003 文案应表达「还没有添加」，实际 {message_not_installed!r}"
    )
    assert "已停用" in message_disabled, (
        f"[TC-P3-PL02-05 ③] 16004 文案应表达「已停用」，实际 {message_disabled!r}"
    )
    assert message_not_installed != message_disabled, (
        "[TC-P3-PL02-01 ④] 两码文案不得相同（用户出路不同：去添加 / 找管理员启用）"
    )


def test_a1_16003_means_not_installed_and_16004_means_disabled() -> None:
    """TC-P3-PL02-01 / TC-P3-PL03-11：`16003`=未添加、`16004`=已停用，两者不得倒置。

    一正一反多条断言：只断一边（如只断 `16003==16003`）在码值互换时仍会通过。
    """
    _assert_codes_not_inverted(
        not_installed=int(ErrorCode.PLUGIN_NOT_INSTALLED),
        disabled=int(ErrorCode.PLUGIN_DISABLED),
        message_not_installed=ERROR_REGISTRY[ErrorCode.PLUGIN_NOT_INSTALLED].message,
        message_disabled=ERROR_REGISTRY[ErrorCode.PLUGIN_DISABLED].message,
    )


def test_a2_plugin_and_task_error_catalog_matches_whitelist() -> None:
    """TC-P3-PL03-11 ⑤ / G-13：插件域 + 任务域码集合必须与白名单**集合相等**。

    为什么用**集合相等**而不是「数量 == 13」：
    - 计数相等有新码误红的风险（合法新增第 14 个码时，报错会指向"码丢了"的错误方向）；
    - 计数相等挡不住「删一个旧码 + 加一个新码」（总数不变，仍然绿）；
    - 集合相等的失败信息能直接给出「缺哪个 / 多哪个」，不用再回查。

    这是「能表达『具体哪个』时不要退化成『一共几个』」的具体应用；
    计数只在**数量本身就是契约**时使用（如"一个插件只有 1 条安装记录"）。
    """
    actual_values = {int(code) for code in ErrorCode}
    for code_value, name in EXPECTED_PLUGIN_CODES.items():
        assert code_value in actual_values, (
            f"[G-13 ①] 错误码 {code_value}（{name}）未在 ErrorCode 中定义"
        )
        enum_member = ErrorCode(code_value)
        assert enum_member in ERROR_REGISTRY, (
            f"[G-13 ②] 错误码 {code_value}（{name}）未注册进 ERROR_REGISTRY"
        )

    # 本阶段新增两域的实际码集合：插件域 160xx + 任务域 170xx
    registered_phase3 = {int(code) for code in ERROR_REGISTRY if 16000 <= int(code) < 18000}
    expected = set(EXPECTED_PLUGIN_CODES)
    missing = sorted(expected - registered_phase3)
    unexpected = sorted(registered_phase3 - expected)
    assert not missing and not unexpected, (
        f"[G-13 ③] 插件/任务域码集合与白名单不一致：缺失={missing} 多出={unexpected}。"
        "新增合法码请先改 EXPECTED_PLUGIN_CODES（人工确认），不要靠总数掩盖"
    )


def test_a3_retired_code_16001_is_not_reused() -> None:
    """架构 D-24：`16001` 按「注销值不复用」跳过，不得复活成任何语义。"""
    actual_values = {int(code) for code in ErrorCode}
    assert 16001 not in actual_values, "[D-24] 16001 为已注销码，不得复用"


# ===========================================================================
# B 组：契约型但需要 B16 实现落地 → 未实现前 xfail（落地后自动转绿）
# ===========================================================================
def test_b1_pl05_already_installed_field_is_always_present() -> None:
    """TC-P3-MK08-01 / TC-P3-MK08-06：`already_installed` 必须是**常驻字段**。

    首次安装返回 `false`、重复安装返回 `true`；**不允许「只在重复时才出现」**——
    否则前端 `if (data.already_installed)` 会拿到 `undefined` 静默走错分支。
    本用例只验契约（字段存在且非必填），不需要真实安装，因此不受套餐/DB 影响。
    """
    from app.schemas.plugin import InstallData

    fields = set(InstallData.model_fields)
    assert "already_installed" in fields, (
        f"[TC-P3-MK08-01 ①] PL5 安装响应必须常驻 already_installed 字段，"
        f"实际字段为 {sorted(fields)}（PRD 勘误 #10 ②：首次 false、重复 true，不允许缺省）"
    )
    field = InstallData.model_fields["already_installed"]
    assert not field.is_required(), (
        "[TC-P3-MK08-01 ②] already_installed 不得为必填（首个安装的调用方不会传它），"
        f"实际 required={field.is_required()}"
    )
    # ③ 首次安装的缺省值必须是 False——否则前端 `if (data.already_installed)` 会走错分支
    assert field.default is False, (
        f"[TC-P3-MK08-01 ③] already_installed 缺省值必须为 False，实际 {field.default!r}"
    )


async def test_b2_pl14_update_grants_before_install_returns_16003() -> None:
    """TC-P3-PL03-11：未安装直接改授权 → `16003`，且**不产生任何副作用**。

    四条断言（缺任一条都只能算「部分验证」）：
    ⓿ **场景前提（两条断言）**：该成员无授权行 **且** 该插件无安装行；
    ① 码值 `16003`（该插件还没有添加，不是 `16004`、不是成功）；
    ② `plugin_grant` **无该成员的行**（按 `plugin_id + user_id` 双键查）；
    ③ 审计**无** `plugin.capability_grant` 事件。
    """
    from app.business.audit_service import (
        ACTION_PLUGIN_CAPABILITY_GRANT,
        AuditService,
        InMemoryAuditRecorder,
    )
    from app.business.plugin_grant_service import PluginGrantService
    from app.business.plugin_service import PluginService
    from app.core.config import Settings, reset_settings_cache
    from app.core.errors import ApiError, ErrorCode
    from app.core.security import AuthContext
    from app.db.repositories.plugin_repo import InMemoryPluginStore
    from app.plugins.loader import PluginRegistry
    from app.schemas.plugin import GrantToggleItem, UpdateGrantsRequest

    reset_settings_cache()
    settings = Settings(
        env="test",
        deploy_mode="private",
        downstream_enabled=False,
        auth_mode="stub",
        jwt_secret="unit-test-secret",
        ratelimit_enabled=False,
    )
    tenant = "default"
    plugin_id = "plg_mail_draft"
    owner = AuthContext(user_id="usr_owner", tenant_id=tenant, roles=["owner"])

    registry = PluginRegistry()
    assert registry.load_all() == []
    store = InMemoryPluginStore(registry)
    recorder = InMemoryAuditRecorder()
    grants = PluginGrantService(settings, store)
    audit = AuditService(settings, recorder=recorder)
    service = PluginService(
        settings,
        store=store,
        registry=registry,
        grants=grants,
        audit=audit,
    )

    # ⓿ 场景前提（两条，必须先于其余断言）：
    #   把"未安装"从「构造事实」变成「断言事实」——否则 setup 若预置了安装行/授权行，
    #   ①②③ 仍会全绿，而验的是"已装改授权"这个完全不同的场景。
    assert await grants.snapshot(tenant, plugin_id, owner.user_id) == frozenset(), (
        f"[TC-P3-PL03-11 ⓿] 前置不成立：该成员在 {plugin_id} 已有授权行，后续断言验错场景"
    )
    assert await store.get_installation(tenant, plugin_id) is None, (
        f"[TC-P3-PL03-11 ⓿] 前置不成立：{plugin_id} 已存在安装行，本用例将验错场景"
    )

    with pytest.raises(ApiError) as excinfo:
        await service.update_grants(
            plugin_id=plugin_id,
            request=UpdateGrantsRequest(
                grants=[GrantToggleItem(capability_key="org.external.publish", granted=False)]
            ),
            auth=owner,
        )
    assert excinfo.value.code is ErrorCode.PLUGIN_NOT_INSTALLED, (
        f"[TC-P3-PL03-11 ①] 未安装改授权必须返回 16003，实际 {excinfo.value.code}"
    )
    # ② 按 plugin_id + user_id 双键查：只按 plugin_id 会把其他成员的行算进来（多人场景假红）
    assert await grants.snapshot(tenant, plugin_id, owner.user_id) == frozenset(), (
        "[TC-P3-PL03-11 ②] 拒绝路径不得为该成员写入任何 plugin_grant 行（防「报错但已写库」）"
    )
    entries = await audit.recent(tenant, action=ACTION_PLUGIN_CAPABILITY_GRANT)
    assert entries == [], (
        f"[TC-P3-PL03-11 ③] 拒绝路径不得写 plugin.capability_grant 审计，实际 {len(entries)} 条"
    )


# ===========================================================================
# C 组：需要真实执行才有意义（靠后；xfail 直到 B16/B17/B19 落地）
# ===========================================================================
async def test_c1_pl05_reinstall_does_not_touch_grants_or_audit() -> None:
    """TC-P3-MK08-06：重复安装 = 200 + `already_installed=true`，**授权与审计零增量**。

    计数口径为**增量**：`plugin.install` 审计只应 +1（首次），重复安装 **+0**；
    授权集合 **+0**。只断言"授权内容不变"会被"报错但已写库"的实现骗过。
    """
    from app.business.audit_service import (
        ACTION_PLUGIN_INSTALL,
        AuditService,
        InMemoryAuditRecorder,
    )
    from app.business.plugin_grant_service import PluginGrantService
    from app.business.plugin_service import PluginService
    from app.business.subscription_service import SubscriptionService
    from app.core.config import Settings, reset_settings_cache
    from app.core.security import AuthContext
    from app.db.repositories.billing_repo import get_billing_store
    from app.db.repositories.plugin_repo import InMemoryPluginStore
    from app.plugins.loader import PluginRegistry
    from app.schemas.billing import ChangeSubscriptionRequest
    from app.schemas.plugin import InstallRequest

    reset_settings_cache()
    settings = Settings(
        env="test",
        deploy_mode="private",
        downstream_enabled=False,
        auth_mode="stub",
        jwt_secret="unit-test-secret",
        ratelimit_enabled=False,
    )
    tenant = "default"
    owner = AuthContext(user_id="usr_owner", tenant_id=tenant, roles=["owner"])

    registry = PluginRegistry()
    assert registry.load_all() == []
    store = InMemoryPluginStore(registry)
    recorder = InMemoryAuditRecorder()
    service = PluginService(
        settings,
        store=store,
        registry=registry,
        grants=PluginGrantService(settings, store),
        audit=AuditService(settings, recorder=recorder),
    )
    subscriptions = SubscriptionService(settings, get_billing_store(settings))
    await subscriptions.change(
        tenant_id=tenant,
        actor_id=owner.user_id,
        request=ChangeSubscriptionRequest(plan_code="professional", seats=5, action="upgrade"),
    )

    # 该插件两个能力均 required=true；其中 org.external.publish 属企业能力，
    # 默认总开关关闭会被 16006 拦在安装前。先按 PL13 开启，再安装。
    from app.schemas.plugin import CapabilityPolicyRequest, CapabilityPolicyRequestItem

    await service.set_capability_policy(
        request=CapabilityPolicyRequest(
            policies=[
                CapabilityPolicyRequestItem(capability_key="org.external.publish", enabled=True)
            ]
        ),
        auth=owner,
    )

    plugin_id = "plg_mail_draft"
    grants = {"personal.tool.use": True, "org.external.publish": True}
    first = await service.install(
        plugin_id=plugin_id, request=InstallRequest(grants=grants, settings={}), auth=owner
    )
    events_first = await recorder.recent(tenant, action=ACTION_PLUGIN_INSTALL)
    grants_first = await store.get_grants(tenant, plugin_id, owner.user_id)

    second = await service.install(
        plugin_id=plugin_id, request=InstallRequest(grants=grants, settings={}), auth=owner
    )
    events_second = await recorder.recent(tenant, action=ACTION_PLUGIN_INSTALL)
    grants_second = await store.get_grants(tenant, plugin_id, owner.user_id)

    assert second.status == first.status, "[TC-P3-MK08-06 ①] 重复安装不得改变安装态"
    assert getattr(second, "already_installed", None) is True, (
        "[TC-P3-MK08-06 ②] 重复安装必须返回 already_installed=true（字段恒有），"
        f"实际 {getattr(second, 'already_installed', '字段不存在')!r}"
    )
    assert list(first.granted_capabilities) == list(second.granted_capabilities), (
        "[TC-P3-MK08-06 ③] 重复安装不得改变 granted_capabilities"
    )
    assert len(events_first) == len(events_second) == 1, (
        f"[TC-P3-MK08-06 ④] plugin.install 审计应只 +1（总量 1），"
        f"实际首次 {len(events_first)} 条、重复后 {len(events_second)} 条"
    )
    assert grants_second == grants_first, (
        f"[TC-P3-MK08-06 ⑤] plugin_grant 应 +0，实际 {sorted(grants_first)} → "
        f"{sorted(grants_second)}"
    )


def test_c2_pl13_capability_policy_idempotent_by_idempotency_key(
    client: TestClient, settings: Settings
) -> None:
    """TC-P3-PL03-07 ⑥：同 `Idempotency-Key` 重放 → **第二次不执行**（零增量）。

    判据用 `code=20007`（幂等键命中）而不是"审计条数"——20007 表示第二次请求**根本没进
    业务工厂**，比事后数审计更强；再用 PL1 `org_capabilities` 回读，确认"单一读源"生效
    （写只走 PL13、读只走 PL4/PL1）。
    """
    key = "55555555-5555-4555-8555-555555555555"
    payload = {"policies": [{"capability_key": "org.repo.write", "enabled": False}]}
    headers = {
        **_owner_headers(settings),
        "Idempotency-Key": key,
    }

    first = client.put("/api/v1/plugins/capability-policy", json=payload, headers=headers)
    body_first = first.json()
    assert body_first["code"] == 0, f"[TC-P3-PL03-07 ⑥-1] 首次写入应成功：{body_first}"
    policies_first = body_first["data"]["policies"]
    assert len(policies_first) == 6, (
        f"[TC-P3-PL03-07 ⑥-2] 写后回读必须返回**全量 6 项**，实际 {len(policies_first)} 项"
    )

    replay = client.put("/api/v1/plugins/capability-policy", json=payload, headers=headers)
    body_replay = replay.json()
    assert body_replay["code"] == 20007, (
        "[TC-P3-PL03-07 ⑥-3] 同键重放必须返回 20007（幂等命中、**不再执行**），"
        f"实际 code={body_replay['code']} message={body_replay.get('message')!r}"
    )

    market = client.get("/api/v1/plugins/market", headers=_owner_headers(settings))
    market_body = market.json()
    assert market_body["code"] == 0
    org_caps = market_body["data"].get("org_capabilities") or []
    assert len(org_caps) == 3, (
        f"[TC-P3-PL03-07 ⑥-4] PL1 顶层 org_capabilities 应为 3 项（仅 org.*），"
        f"实际 {len(org_caps)} 项"
    )
    written = {item["capability_key"]: item["org_switch_enabled"] for item in org_caps}
    assert written.get("org.repo.write") is False, (
        "[TC-P3-PL03-07 ⑥-5] 单一读源：PL1 回读必须看到刚写入的 false，"
        f"实际 {written.get('org.repo.write')!r}"
    )


@pytest.mark.xfail(reason="B19 未实现：WF2 产物编辑接口", strict=True)
def test_c3_wf2_edit_rerenders_download_and_keeps_steps_untouched(
    client: TestClient,
) -> None:
    """TC-P3-WF12-05：保存即重渲染（下载 sha256 == 刚保存正文），且不得触发重跑。

    当前 B19 未实现 WF2，故先断言**路由未注册即不达标**（真实契约事实）；
    路由落地后补齐：`version+1`、`download_url` 内容 sha256 相等、
    `plugin_task_step` 行数与状态集合 **+0**、任务 `status` 仍 `completed`。
    """
    paths = client.app.openapi()["paths"]
    expected = "/api/v1/agent/tasks/{task_id}/artifacts/{artifact_id}"
    assert expected in paths, (
        f"[TC-P3-WF12-05 ①] WF2 产物编辑接口未注册（B19 应实现），实际路径集合缺 {expected}"
    )


@pytest.mark.xfail(reason="B19 未实现：WF2 只读类型拒绝", strict=True)
def test_c4_wf2_non_editable_types_rejected_without_version_bump(
    client: TestClient,
) -> None:
    """TC-P3-WF12-06：`link`/`file` 不可编辑，且拒绝后 `version` 不变。

    同 c3：先断言路由存在（WF2 未实现即不达标）。路由落地后补齐：
    返回拒绝；产物 `content` / `sha256` / `version` 三处均不变（防"先写后校验"）。
    """
    paths = client.app.openapi()["paths"]
    expected = "/api/v1/agent/tasks/{task_id}/artifacts/{artifact_id}"
    assert expected in paths, (
        f"[TC-P3-WF12-06 ①] WF2 产物编辑接口未注册（B19 应实现），实际路径集合缺 {expected}"
    )


# ===========================================================================
# D 组：汇总断言（防"占位永久化"与"登记漂移"）
# ===========================================================================
def test_d1_deferred_registry_matches_markers() -> None:
    """汇总断言：登记表（append-only，三态）与实际 `xfail` 标记必须自洽。

    三态设计（一次教训：「一个字段承载两种语义」会让人误读）：
    - `xfail`：断言已写、实现未到 → 必须有 `xfail` 标记，计入 `EXPECTED_XFAIL`；
    - `landed`：已随批次转绿 → 必须无 `xfail` 标记；
    - `red`：断言已写但**当前是红的**（真缺陷）→ 必须无 `xfail` 标记，且必须在批次报告里列为缺陷。

    早先版本用 `landed_in` 一字段同时表达「断言已实现」与「已验收通过」，
    导致 `test_b1`（已实现、但红）看起来像"已落地"—— 这正是"一字段两语义"的老毛病。
    """
    module: ModuleType = sys.modules[__name__]
    marked = {
        name
        for name, obj in vars(module).items()
        if callable(obj)
        and name.startswith("test_")
        and any(getattr(mark, "name", "") == "xfail" for mark in getattr(obj, "pytestmark", []))
    }

    names = [str(case["name"]) for case in DEFERRED_CASES]
    assert len(names) == len(set(names)), f"[骨架汇总 ①] 登记表存在重复函数名：{names}"

    allowed = {"xfail", "landed", "red"}
    for case in DEFERRED_CASES:
        name = str(case["name"])
        assert case.get("status") in allowed, (
            f"[骨架汇总 ②] {name} 的 status={case.get('status')!r} 非法，只允许 {sorted(allowed)}"
        )
        assert case["tc"], f"[骨架汇总 ③] {name} 缺追溯表编号"
        assert case["why"], f"[骨架汇总 ④] {name} 缺 why（reason 只写本条）"
        assert case["batch"], f"[骨架汇总 ⑤] {name} 缺应落地批次"

    xfail_names = {str(c["name"]) for c in DEFERRED_CASES if c["status"] == "xfail"}
    assert marked == xfail_names, (
        f"[骨架汇总 ⑥] xfail 标记与 status=xfail 条目不一致："
        f"多标记 {sorted(marked - xfail_names)}、缺标记 {sorted(xfail_names - marked)}"
    )
    assert len(xfail_names) == EXPECTED_ACTIVE_XFAIL, (
        f"[骨架汇总 ⑦] 预期 xfail {EXPECTED_ACTIVE_XFAIL} 条（唯一真源 tests/expected_xfail.py），"
        f"实际 {len(xfail_names)} 条"
    )

    per_batch: dict[str, int] = {}
    for case in DEFERRED_CASES:
        if case["status"] != "xfail":
            continue
        batch = str(case["batch"])
        per_batch[batch] = per_batch.get(batch, 0) + 1
    assert per_batch == dict(EXPECTED_XFAIL_BY_BATCH), (
        f"[骨架汇总 ⑧] 分批剩余与 `EXPECTED_XFAIL_BY_BATCH` 不一致："
        f"实际 {per_batch}、期望 {dict(EXPECTED_XFAIL_BY_BATCH)}。"
        "只比总数会漏掉「把 B18 的条目错标成 B19」这类归属错误"
    )

    red_cases = [c for c in DEFERRED_CASES if c["status"] == "red"]

    # ⑭ defect 编号唯一（防 3 条红共用一个号）
    red_defects = [str(c["defect"]) for c in red_cases]
    assert len(red_defects) == len(set(red_defects)), f"[骨架汇总 ⑭] defect 编号重复：{red_defects}"

    # ⑮ red 的 defect ⊆ 台账 open 集合；每个 open 缺陷必须写明 pinned_by
    open_defects = {d["id"] for d in DEFECT_LEDGER if d["status"] == "open"}
    unknown = set(red_defects) - open_defects
    assert not unknown, f"[骨架汇总 ⑮] red 引用了台账里不存在的缺陷编号：{sorted(unknown)}"
    for item in DEFECT_LEDGER:
        if item["status"] == "open":
            assert item.get("pinned_by"), (
                f"[骨架汇总 ⑮] 开放缺陷 {item['id']} 必须写明 pinned_by（谁钉住它）"
            )
    red_names = sorted(str(c["name"]) for c in red_cases)

    # ⑨ red 不是"未实现"，而是"实现有缺陷"——必须能追到缺陷编号与到期批次
    for case in red_cases:
        assert case.get("defect"), (
            f"[骨架汇总 ⑨] red 条目 {case['name']} 缺 defect 编号（只有批次追不到责任/工单）"
        )
        assert str(case["batch"]), f"[骨架汇总 ⑨] red 条目 {case['name']} 缺到期批次"

    # ⑩ red 条数必须等于显式常量（批次结束应被人工下调到 0）
    assert len(red_cases) == EXPECTED_RED, (
        f"[骨架汇总 ⑩] 预期 red {EXPECTED_RED} 条，实际 {len(red_cases)} 条：{red_names}"
    )

    # ⑪ 未闭口总量 = xfail + red：只盯 xfailed 会把「xfail 改 red」当成进度
    open_now = len(xfail_names) + len(red_cases)
    assert open_now == EXPECTED_OPEN, (
        f"[骨架汇总 ⑪] 未闭口总量应为 {EXPECTED_OPEN}（xfail {len(xfail_names)} + red "
        f"{len(red_cases)} = {open_now}）。改状态标记不算进度——必须两个桶之和下降"
    )

    # ⑫ 单调不增：历史序列本身不得回升，且末值必须等于当前常量
    assert all(OPEN_HISTORY[i] >= OPEN_HISTORY[i + 1] for i in range(len(OPEN_HISTORY) - 1)), (
        f"[骨架汇总 ⑫] 未闭口总量历史出现回升：{OPEN_HISTORY}"
    )
    assert OPEN_HISTORY[-1] == EXPECTED_OPEN, (
        f"[骨架汇总 ⑫] 历史末值 {OPEN_HISTORY[-1]} != 当前常量 {EXPECTED_OPEN}（漏记历史）"
    )

    # ⑬ 三个常量必须满足算术恒等：open = xfail + red。
    #    这样「把 xfail 改成 red」既不能让 xfailed 白降（⑩ 校验 red 常量），
    #    也不能靠改常量把总量做小（⑪ 校验 open 常量，⑬ 校验三者自洽）。
    #    注意 red 允许随批次上升（新交付代码发现缺陷是正常现象），因此**不对 red 判单调**；
    #    「批次签收时 red 必须为 0」由检查单 §0.4 的签收条件约束（测试无法得知"正在签收"）。
    assert EXPECTED_OPEN == EXPECTED_XFAIL + EXPECTED_RED, (
        f"[骨架汇总 ⑬] 常量不自洽：EXPECTED_OPEN({EXPECTED_OPEN}) != "
        f"EXPECTED_XFAIL({EXPECTED_XFAIL}) + EXPECTED_RED({EXPECTED_RED})"
    )
    assert RED_HISTORY[-1] == EXPECTED_RED, (
        f"[骨架汇总 ⑬] red 历史末值 {RED_HISTORY[-1]} != 当前常量 {EXPECTED_RED}（漏记历史）"
    )


def test_d2_no_pytest_fail_placeholder() -> None:
    """门禁自检：**禁止用失败即报的写法当占位**（team-lead 2026-10-23 规则）。

    占位若写成「主动抛失败 + 一句说明」，会造成「永远失败 → 永远 xfail → 永不验收」的伪装豁免；
    它比 `skip` 更隐蔽，因为报告里显示为"预期失败"，看起来像正常节奏。
    本断言扫描本文件源码，命中"未实现/TODO/占位"字样的 `pytest.fail` 即红。
    """
    import pathlib as _pathlib

    source = _pathlib.Path(__file__).read_text(encoding="utf-8")
    offenders = [
        line.strip()
        for line in source.splitlines()
        # 排除本用例自身 docstring 里的引用（写作 `` `pytest.fail(...)` ``，带反引号）
        if "pytest.fail(" in line
        and "`pytest.fail(" not in line
        and any(word in line for word in ("未实现", "TODO", "占位"))
    ]
    assert not offenders, (
        f"[骨架自检 ⑨] 发现 pytest.fail 占位（应改成真实断言 + xfail(reason)）：{offenders}"
    )
