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

from app.core.errors import ERROR_REGISTRY, ErrorCode

pytestmark = pytest.mark.integration

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
        "tc": "TC-P3-MK08-01",
        "batch": "B17",
        "why": "PL5 安装响应模型未实现（already_installed 常驻字段）",
        "landed_in": None,
    },
    {
        "name": "test_b2_pl14_update_grants_before_install_returns_16003",
        "tc": "TC-P3-PL03-11",
        "batch": "B17",
        "why": "PL14 授权写接口未实现",
        "landed_in": None,
    },
    {
        "name": "test_c1_pl05_reinstall_does_not_touch_grants_or_audit",
        "tc": "TC-P3-MK08-06",
        "batch": "B17",
        "why": "PL5 重复安装幂等 + 审计计数未实现",
        "landed_in": None,
    },
    {
        "name": "test_c2_pl13_capability_policy_idempotent_by_idempotency_key",
        "tc": "TC-P3-PL03-07",
        "batch": "B17",
        "why": "PL13 企业能力策略写接口未实现",
        "landed_in": None,
    },
    {
        "name": "test_c3_wf2_edit_rerenders_download_and_keeps_steps_untouched",
        "tc": "TC-P3-WF12-05",
        "batch": "B19",
        "why": "WF2 产物编辑接口未实现",
        "landed_in": None,
    },
    {
        "name": "test_c4_wf2_non_editable_types_rejected_without_version_bump",
        "tc": "TC-P3-WF12-06",
        "batch": "B19",
        "why": "WF2 只读类型拒绝未实现",
        "landed_in": None,
    },
]

#: **当前仍应 xfail 的条数——显式常量，必须人工维护**
#:
#: 刻意不用 `len(...)` 推导：若有人「删掉一条登记 + 同时改这个数」，漂移会被掩盖。
#: 写成常量后，任何变化都必须改这里 —— 这条断言的价值就在"逼人手动确认"。
EXPECTED_ACTIVE_XFAIL = 6


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
@pytest.mark.xfail(reason="B17 未实现：PL5 安装响应模型 / already_installed 常驻字段", strict=False)
def test_b1_pl05_already_installed_field_is_always_present() -> None:
    """TC-P3-MK08-01 / TC-P3-MK08-06：`already_installed` 是**常驻字段**。

    首次安装必须返回 `false`、重复安装返回 `true`；**不允许「只在重复时才出现」**——
    否则前端 `if (data.already_installed)` 会拿到 `undefined` 静默走错分支。
    """
    # B16 落地后此导入才成立；未落地时由 xfail 兜住，不影响收集
    from app.schemas.plugin import InstallData

    fields = set(InstallData.model_fields)
    assert "already_installed" in fields, (
        f"[TC-P3-MK08-01 ①] 安装响应必须常驻 already_installed，实际字段 {sorted(fields)}"
    )
    field = InstallData.model_fields["already_installed"]
    assert field.is_required() or field.default is not None, (
        "[TC-P3-MK08-01 ②] already_installed 必须有默认值（不得缺省），"
        f"实际 required={field.is_required()}, default={field.default!r}"
    )


@pytest.mark.xfail(reason="B17 未实现：PL14 授权写接口", strict=False)
def test_b2_pl14_update_grants_before_install_returns_16003() -> None:
    """TC-P3-PL03-11：未安装直接改授权 → `16003`，且**不产生任何副作用**。

    三条断言缺一不算覆盖：① 码值；② `plugin_grant` 无新增行；③ 审计无
    `plugin.capability_grant` 事件（专防「报错但已写库/已写审计」）。
    """
    pytest.fail(
        "[TC-P3-PL03-11] 骨架占位：B16 落地后改为 —— "
        "① PUT /plugins/{id}/grants 未安装 → 16003（非 16004/非成功）；"
        "② 按 plugin_id+user_id 查 plugin_grant 为空（+0）；"
        "③ 审计无 plugin.capability_grant（+0）"
    )


# ===========================================================================
# C 组：需要真实执行才有意义（靠后；xfail 直到 B16/B17/B19 落地）
# ===========================================================================
@pytest.mark.xfail(reason="B17 未实现：PL5 重复安装不改授权 + 审计计数", strict=False)
def test_c1_pl05_reinstall_does_not_touch_grants_or_audit() -> None:
    """TC-P3-MK08-06：重复安装 = `200 + already_installed=true`，授权与审计**零增量**。"""
    pytest.fail(
        "[TC-P3-MK08-06] 骨架占位：plugin_install 总量恒定 1 条（重复调用 +0）、"
        "plugin_grant +0、plugin.install 审计 +0（总量 1）、plugin.capability_grant +0"
    )


@pytest.mark.xfail(reason="B17 未实现：PL13 企业能力策略写接口", strict=False)
def test_c2_pl13_capability_policy_idempotent_by_idempotency_key() -> None:
    """TC-P3-PL03-07 ⑥：同 `Idempotency-Key` 重放 → 审计与策略表**零增量**。"""
    pytest.fail(
        "[TC-P3-PL03-07 ⑥ 幂等重放] 骨架占位：响应与首次一致；"
        "plugin.capability_policy 审计 +0（总量 1）；策略表行数 +0"
    )


@pytest.mark.xfail(reason="B19 未实现：WF2 产物编辑接口", strict=False)
def test_c3_wf2_edit_rerenders_download_and_keeps_steps_untouched() -> None:
    """TC-P3-WF12-05：保存即重渲染（下载 sha256 == 保存正文），且**不得触发重跑**。"""
    pytest.fail(
        "[TC-P3-WF12-05] 骨架占位：version+1；download_url 内容 sha256 == 保存正文 sha256；"
        "plugin_task_step 行数与状态集合 +0；任务 status 仍 completed"
    )


@pytest.mark.xfail(reason="B19 未实现：WF2 只读类型拒绝", strict=False)
def test_c4_wf2_non_editable_types_rejected_without_version_bump() -> None:
    """TC-P3-WF12-06：`link`/`file` 必须拒绝编辑，且 `version` **不变**（防先写后校验）。"""
    pytest.fail("[TC-P3-WF12-06] 骨架占位：返回拒绝；产物 content/sha256/version 三处均不变")


# ===========================================================================
# D 组：骨架自检与汇总（保证「骨架能红」且「xfail 数可核对」）
# ===========================================================================
def test_d1_deferred_registry_matches_markers() -> None:
    """汇总断言：登记表（append-only）与本模块实际打了 `xfail` 的函数必须一致。

    防两类漂移：
    - 「加了延后用例却忘了登记」→ 名字集合不等；
    - 「落地后只删代码没删登记」/「只删标记没记落地」→ active 集合不等。

    另防「6→0 其实是删了 6 条」：登记表 append-only，条目只填 `landed_in`、不删除，
    因此历史永远可查。
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

    active = [case for case in DEFERRED_CASES if not case["landed_in"]]
    active_names = {str(case["name"]) for case in active}
    assert marked == active_names, (
        f"[骨架汇总 ②] xfail 标记与「未落地」条目不一致："
        f"多标记 {sorted(marked - active_names)}、缺标记 {sorted(active_names - marked)}"
    )
    assert len(active) == EXPECTED_ACTIVE_XFAIL, (
        f"[骨架汇总 ③] 预期仍 xfail {EXPECTED_ACTIVE_XFAIL} 条（显式常量），实际 {len(active)} 条。"
        "落地后请把该条填 landed_in 并同步下调常量——必须人工确认，不允许自动推导"
    )
    assert len(marked) == len(active), (
        f"[骨架汇总 ④] 标记数 {len(marked)} != 未落地条目数 {len(active)}"
    )

    for case in DEFERRED_CASES:
        name = str(case["name"])
        assert case["tc"], f"[骨架汇总 ⑤] {name} 缺追溯表编号（验收报告要按 TC 对回 P0）"
        assert case["why"], f"[骨架汇总 ⑥] {name} 缺 why（xfail reason 不写全局说明，只写本条）"
        assert case["batch"], f"[骨架汇总 ⑦] {name} 缺应落地批次"
        landed = case["landed_in"]
        assert landed is None or str(landed), f"[骨架汇总 ⑧] {name} 的 landed_in 取值非法"

    per_batch: dict[str, int] = {}
    for case in active:
        batch = str(case["batch"])
        per_batch[batch] = per_batch.get(batch, 0) + 1
    assert sum(per_batch.values()) == EXPECTED_ACTIVE_XFAIL, (
        f"[骨架汇总 ⑨] 各批剩余合计 {sum(per_batch.values())} != {EXPECTED_ACTIVE_XFAIL}："
        f"分批剩余 {per_batch}"
    )
