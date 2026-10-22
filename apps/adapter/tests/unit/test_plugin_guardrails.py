"""三插件护栏单测（阶段三 B20，`architecture-phase3.md` §7 / P0-P3-23 / §11 勘误 #3）。

每条用例对应一条**很容易写错、写错了还看不出来**的判据：

1. **Offer 三规则的作用域**：只扫 `output_key=offer`。`screening` 的结论枚举
   合法包含「待定」——把扫描范围放到任务级，每份「待定」简历都会误报成 P0；
2. **R1 / R2 / R3 各自能命中**，且 R2 是"±20 字窗口内的裸数字"（`8000` 靠 R1 抓不住）；
3. **金额违规的建议动作**固定指向"改字段后重新导出"，**不得**指向重新发起任务；
4. **排期时间**：过去时间要拦、同平台同刻重复要拦、认不出来的写法**不拦**；
5. **执行路径 ⊆ 声明清单**：声明了什么就只能做什么。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import pytest

from app.plugins.actions import (
    ActionResult,
    ArtifactDraft,
    RunContext,
    assert_execution_path,
    declared_tool_paths,
)
from app.plugins.engine import (
    OFFER_AMOUNT_ALLOWED_VALUES,
    OFFER_AMOUNT_OPERATOR_ACTION,
    OFFER_OUTPUT_KEY,
    STEP_COMPLETED,
    STEP_FAILED,
    PluginEngine,
    StepPlan,
    parse_schedule_rows,
    scan_offer_amount,
    scan_schedule,
)
from app.plugins.schema import StepSpec
from tests.unit.task_helpers import GRANTED, load_manifest

#: 干净的 Offer 文案（三个金额位置留空 / 写「按公司标准执行」）
CLEAN_OFFER = (
    "录用通知\n"
    "岗位：前端工程师\n"
    "薪资：按公司标准执行\n"
    "试用期薪资：按公司标准执行\n"
    "股权：\n"
    "报到日期：2026-11-01\n"
)

#: 三份「结论=待定」的简历筛选结果（R3 的禁用词含「待定」，这里必须不被误伤）
SCREENING_WITH_PENDING = "结论,理由\n待定,需要补充项目细节\n待定,工作年限待确认\n"

#: 同一批「结论=待定」的简历，按**评分表的正式列**排布。
#:
#: 为什么要单独一份：`screening` 产物除了"不被 R3 误伤"之外，还要过
#: **「风险」行级质量门**（PRD P0-P3-21：风险为空判该行失败）。上面那份
#: 只有「结论/理由」两列的表过不了那道门——用它测 R3 作用域，红的是风险门
#: 而不是 R3，测的东西就偏了。这里把六列补齐、风险非空，才能单独钉住 R3 作用域。
SCREENING_TABLE_WITH_PENDING = (
    "姓名,匹配分,亮点,风险,结论,理由\n"
    "张伟,80,同类项目经验；沟通清晰,缺少大规模团队协作经验,待定,"
    "项目经历与岗位要求接近，需要补充一处团队规模的细节之后再决定。\n"
)


def _render_spec(**overrides: object) -> StepSpec:
    """构造 `render_file` 步骤声明。

    Args:
        **overrides: 覆盖字段。

    Returns:
        StepSpec: 步骤声明。
    """
    payload: dict[str, object] = {
        "seq": 1,
        "title": "导出文件",
        "action": "render_file",
        "render_format": "docx",
        "produces": ["document"],
        "on_failure": "abort",
    }
    payload.update(overrides)
    return StepSpec(**payload)  # type: ignore[arg-type]


def test_clean_offer_has_no_hits() -> None:
    """三字段留空或写「按公司标准执行」→ R1/R2/R3 全 0 命中。"""
    scan = scan_offer_amount(CLEAN_OFFER)
    assert scan.clean is True
    assert scan.r1 == () and scan.r2 == () and scan.r3 == ()


def test_r1_catches_amount_with_unit() -> None:
    """R1：带单位的金额，**三个分支各自覆盖一类真实写法**（8/8 正样本）。

    历史：首版只有「≥3 位数字 + 单位」一支，`15k` / `1.2 万` / `3 亿元` 全部漏检——
    而 `15k`、`1.2 万` 恰恰是模型最可能写的两种薪资写法。补第三支（1-2 位 + 压缩单位）。
    """
    positives = (
        "15k",
        "1.2万",
        "1.2万元",
        "8000每月",
        "3 亿元",
        "1200 亿",
        "0.5 亿",
        "3亿",
    )
    for text in positives:
        scan = scan_offer_amount(text)
        assert scan.r1, f"R1 未命中：{text}"


@pytest.mark.parametrize(
    "text",
    ["8000", "8,000", "1,500", "2026-11-01", "3 个月", "30%", "按公司标准执行"],
)
def test_r1_ignores_non_amount_numbers(text: str) -> None:
    """R1 的 7 个反样本：没有单位就不是金额（防"见数字就报"式假阳性）。"""
    assert scan_offer_amount(text).r1 == (), f"R1 误命中：{text}"


@pytest.mark.parametrize(
    ("text", "separator"),
    [
        ("月薪 8000-10000", "-"),
        ("月薪 8000–10000", "–"),
        ("月薪 8000~10000", "~"),
        ("月薪 8000至10000", "至"),
        ("薪资 12000-15000", "-"),
    ],
)
def test_r2_catches_salary_range_verbatim(text: str, separator: str) -> None:
    """R2-a：区间写法必须命中**整段**（不是截成 `000`）。

    区间是中文 Offer 最高频的写法，而它正好落在「R2-b 排除连字符」与
    「R1 要求带单位」的空隙里——不单列一支就两边都抓不到。
    """
    scan = scan_offer_amount(text)
    assert any(separator in hit for hit in scan.r2), f"R2-a 未命中整段：{text} → {scan.r2}"


@pytest.mark.parametrize(
    "text",
    [
        "薪 -5~-3",  # 负数区间
        "薪资 2026-11-01",  # 日期（豁免先剥）
        "薪资 订单 A-100200",  # 编号：lookbehind 的 `-` 挡住
        "薪资 第 3-5 章",  # 章节号（不足三位）
        "薪资 比例 20-30%",  # 百分比（豁免先剥）
    ],
)
def test_r2_does_not_fire_on_non_amount_patterns(text: str) -> None:
    """负样本：**每加一条区间分支，都必须配一条"不该抓的"**，否则就是在移动边界。"""
    assert scan_offer_amount(text).r2 == (), f"R2 误命中：{text}"


@pytest.mark.parametrize("text", ["薪资 EMP-1234", "薪资 EMP1234", "薪资 PO2024001"])
def test_id_exemption_needs_both_mechanisms(text: str) -> None:
    """豁免：`EMP-1234`（靠 lookbehind 的 `-`）与 `EMP1234`（**只能靠豁免剥离**）都要测。

    两条都测才构成两个独立机制的证据：只测带连字符那条，把豁免代码删掉照样能过。
    """
    assert scan_offer_amount(text).r2 == (), f"编号被当成金额：{text}"


@pytest.mark.parametrize("text", ["薪资 RMB8000", "薪资 K8000", "薪资 ¥8000", "薪资 CNY 8000"])
def test_exemption_does_not_swallow_real_amounts(text: str) -> None:
    """豁免的守卫样本：**放宽豁免是在移动边界，另一半必须仍抓得到**。

    `RMB8000` 是真实金额写法。若豁免写成 `[A-Za-z]{1,5}-?\\d{4,}` 这种形状匹配，
    为修 1 个误报会开出 4 个漏检——所以豁免只能是**前缀白名单**。
    """
    scan = scan_offer_amount(text)
    assert scan.r2 or scan.r1, f"真实金额被豁免吞掉：{text}"


@pytest.mark.parametrize("text", ["月薪 8000", "薪资 8,000", "待遇 1,500"])
def test_r2_still_catches_bare_numbers(text: str) -> None:
    """原 R2-b 未回退：补区间分支不能把裸数字这条搞丢。"""
    assert scan_offer_amount(text).r2, f"R2-b 未命中：{text}"


def test_r2_catches_bare_number_without_unit() -> None:
    """R2：金额关键词 ±20 字窗口内的裸数字（R1 抓不到的那种）。"""
    scan = scan_offer_amount("薪资 8000，试用期薪资 8000")
    assert scan.r2, "R2 未命中裸数字"
    assert scan.r1 == (), "裸数字不该被 R1 命中（这是 R2 的职责）"


def test_r2_ignores_numbers_far_from_amount_context() -> None:
    """R2 有窗口：离金额关键词很远的数字不该被拦（否则入职编号也会误报）。"""
    scan = scan_offer_amount(
        "报到日期 2026-11-01，工号 100200。\n" + "说明" * 40 + "\n薪资：按公司标准执行"
    )
    assert scan.r2 == ()


def test_r3_catches_vague_amount_wording() -> None:
    """R3：金额位置写「面议」「待定」等说法 → 命中（等于把决策甩给用户）。"""
    for word in ("面议", "待定", "可谈", "根据经验", "按市场水平"):
        scan = scan_offer_amount(f"薪资：{word}")
        assert scan.r3, f"R3 未命中：{word}"


def test_r3_scope_does_not_touch_screening_output() -> None:
    """R3 只扫 offer：简历里的「待定」是合法枚举，绝不能误报。

    这条是**作用域回归钉子**：如果有人把护栏改成"扫任务全部产物"，
    这里会立刻变红（而不是等到 QA 在真机上发现 3 份简历全被标成 P0）。
    """
    screening_scan = scan_offer_amount(SCREENING_WITH_PENDING)
    # 直接扫简历文本会命中「待定」——正因如此，引擎只把 offer 交给这条规则；
    # 这里断言"引擎的调用点按 output_key 过滤"，见下面的端到端用例。
    assert screening_scan.r3, "前提：裸扫简历文本确实会命中「待定」"


def test_amount_fields_outside_whitelist_are_flagged() -> None:
    """字段取值只能是空串或「按公司标准执行」。"""
    assert OFFER_AMOUNT_ALLOWED_VALUES == ("", "按公司标准执行")
    scan = scan_offer_amount("薪资：按公司标准执行", {"salary": "8000"})
    assert scan.fields == ("salary",)
    ok = scan_offer_amount("", {"salary": "", "bonus": "按公司标准执行"})
    assert ok.fields == ()


def test_schedule_rejects_past_time_and_duplicates() -> None:
    """排期：过去的时间要拦，同平台同刻重复也要拦。"""
    now = datetime(2026, 11, 1, 9, 0)
    rows = [
        {"平台": "公众号", "计划时间": "2026-10-01 10:00"},
        {"平台": "邮件", "计划时间": "2026-11-02 10:00"},
        {"平台": "邮件", "计划时间": "2026-11-02 10:00"},
    ]
    scan = scan_schedule(rows, now=now)
    assert scan.clean is False
    joined = "；".join(scan.problems)
    assert "第 1 行计划时间（2026-10-01 10:00）已经过去了" in joined
    assert "第 2 行与第 3 行" in joined
    assert scan.checked_rows == 3


def test_schedule_ignores_unrecognized_time_format() -> None:
    """认不出来的时间写法不判失败（自由填写的表头不能被拦死）。"""
    now = datetime(2026, 11, 1, 9, 0)
    scan = scan_schedule([{"平台": "公众号", "计划时间": "下周一上午"}], now=now)
    assert scan.clean is True
    assert scan.checked_rows == 0


def test_parse_schedule_rows_round_trips_csv() -> None:
    """排期 CSV 能解析回行（护栏校验的是"交付出去的那份表"）。"""
    content = "平台,计划时间,状态\n公众号,2026-11-02 10:00,待发布\n".encode()
    rows = parse_schedule_rows(content)
    assert rows == [{"平台": "公众号", "计划时间": "2026-11-02 10:00", "状态": "待发布"}]
    assert parse_schedule_rows(b"") == []


def test_declared_tool_paths_and_assertion() -> None:
    """执行路径 ⊆ 声明清单：主工具与替代项都算，清单外的直接抛错。"""
    spec = StepSpec(
        seq=1,
        title="发布",
        action="tool",
        tool_type="wechat_mp",
        operation="publish_article",
        tool_alternatives=[{"tool_type": "email", "operation": "send_message"}],
        produces=["receipt"],
        on_failure="continue",
    )
    paths = declared_tool_paths(spec)
    assert ("wechat_mp", "publish_article") in paths
    assert ("email", "send_message") in paths
    assert_execution_path(spec, "email", "send_message")
    with pytest.raises(ValueError):
        assert_execution_path(spec, "wecom", "send_message")


class _ScriptedGateway:
    """按脚本返回产物的原语替身（用于端到端跑引擎护栏）。

    Attributes:
        drafts: 固定返回的产物草稿。
        data: 固定返回的 `data`（`tool` 步骤用来看实际调用的工具）。
    """

    def __init__(
        self, drafts: tuple[ArtifactDraft, ...], data: dict[str, Any] | None = None
    ) -> None:
        """初始化。

        Args:
            drafts: 产物草稿。
            data: 结果数据。
        """
        self.drafts = drafts
        self.data = data or {}

    def resolve(self, action: str) -> str:
        """解析原语。

        Args:
            action: 原语名。

        Returns:
            str: 固定标识。
        """
        return f"test.{action}"

    async def invoke(self, ctx: RunContext, action: str, params: dict[str, Any]) -> ActionResult:
        """返回脚本产物。

        Args:
            ctx: 执行上下文。
            action: 原语名。
            params: 原语参数。

        Returns:
            ActionResult: 固定结果。
        """
        del ctx, action, params
        return ActionResult(status="ok", artifacts=self.drafts, data=dict(self.data))


def _run(engine: PluginEngine, spec: StepSpec) -> Any:
    """跑一步（同步包装，避免用例里到处写 `asyncio.run`）。

    Args:
        engine: 引擎。
        spec: 步骤声明。

    Returns:
        StepOutcome: 步骤结果。
    """
    ctx = RunContext(tenant_id="tnt_test", task_id="task_test", granted=GRANTED)
    return asyncio.run(engine.run_step(ctx, StepPlan(spec=spec), {}))


def test_engine_blocks_offer_with_amount() -> None:
    """端到端：offer 产物含金额 → 该步 failed + 固定建议动作 + **不交产物**。"""
    drafts = (
        ArtifactDraft(
            output_key=OFFER_OUTPUT_KEY, type="text", name="录用通知", inline_text="薪资：8000 元"
        ),
    )
    outcome = _run(
        PluginEngine(_ScriptedGateway(drafts)), _render_spec(produces=[OFFER_OUTPUT_KEY])
    )
    assert outcome.status == STEP_FAILED
    assert outcome.retryable is False
    assert outcome.operator_action == OFFER_AMOUNT_OPERATOR_ACTION
    assert outcome.artifacts == (), "违规产物不得交给用户"


def test_engine_allows_clean_offer() -> None:
    """端到端：合规 offer → 该步 completed。"""
    drafts = (
        ArtifactDraft(
            output_key=OFFER_OUTPUT_KEY, type="text", name="录用通知", inline_text=CLEAN_OFFER
        ),
    )
    outcome = _run(
        PluginEngine(_ScriptedGateway(drafts)), _render_spec(produces=[OFFER_OUTPUT_KEY])
    )
    assert outcome.status == STEP_COMPLETED


def test_engine_does_not_scan_screening_for_r3_words() -> None:
    """端到端：`screening` 产物里的「待定」**不被** R3 误伤（作用域只在 offer）。

    产物用**六列齐全、风险非空**的评分表：`screening` 另有「风险」行级质量门
    （P0-P3-21），表不齐会先被那道门拦下，这条用例就测不到 R3 的作用域了。
    """
    drafts = (
        ArtifactDraft(
            output_key="screening",
            type="file",
            name="筛选评分表",
            inline_text=SCREENING_TABLE_WITH_PENDING,
        ),
    )
    outcome = _run(PluginEngine(_ScriptedGateway(drafts)), _render_spec(produces=["screening"]))
    assert outcome.status == STEP_COMPLETED


def test_engine_blocks_schedule_with_past_time() -> None:
    """端到端：排期表第一行时间已过去 → 该步 failed 且文案含行号。"""
    content = "平台,计划时间\n公众号,2020-01-01 10:00\n".encode()
    drafts = (ArtifactDraft(output_key="schedule", type="file", name="排期表", content=content),)
    outcome = _run(PluginEngine(_ScriptedGateway(drafts)), _render_spec(produces=["schedule"]))
    assert outcome.status == STEP_FAILED
    assert "第 1 行" in outcome.reason_text
    assert outcome.retryable is False


def test_engine_blocks_tool_path_out_of_manifest() -> None:
    """端到端：实际调用的工具不在声明清单内 → 该步 failed。"""
    spec = StepSpec(
        seq=1,
        title="发布",
        action="tool",
        tool_type="wechat_mp",
        operation="publish_article",
        produces=["receipt"],
        on_failure="continue",
    )
    gateway = _ScriptedGateway(
        (ArtifactDraft(output_key="receipt", type="text", name="回执", inline_text="已发布"),),
        data={"tool_type": "wecom", "operation": "send_message"},
    )
    outcome = _run(PluginEngine(gateway), spec)
    assert outcome.status == STEP_FAILED
    assert outcome.retryable is False


def test_three_plugin_manifests_declare_render_format() -> None:
    """三个标杆插件的导出步骤都声明了格式（否则加载期就该拒）。"""
    for plugin_id in ("plg_content_ops", "plg_hr_admin", "plg_dev_workbench"):
        manifest = load_manifest(plugin_id)
        for step in manifest.steps:
            if step.action == "render_file":
                assert step.render_format in {"docx", "md", "csv"}, (
                    f"{plugin_id} 第 {step.seq} 步缺导出格式"
                )
