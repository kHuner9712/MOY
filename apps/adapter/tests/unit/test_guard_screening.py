"""评分表「风险」行级质量门 + 简历「一次最多 20 份」的单测（PRD P0-P3-21）。

两条判据都是 PRD 明确要求、平台侧此前**完全没有实现**的（QA 已登记为缺口）：

- **缺陷 A（P1）**：PRD 原文「『风险』为空判该行失败」。未实现时模型返回空风险，
  该行照样算通过——一张"全绿"的评分表会把不合格候选人送进面试；
- **缺陷 B（P2）**：PRD 原文「第 21 份 → 一次最多 20 份，请分批上传」。未实现时
  用户一次拖 50 份简历，任务跑很久才失败，或静默只处理一部分。

用例的写法遵循三条纪律：

1. **判据必须落在引擎/声明层，不落在人事插件特判上**：这里断言的是
   `PluginEngine.guard()` 与 `PluginEngine.preflight()` 的行为，
   插件只贡献声明（`max_items: 20`）；
2. **每条判据都要有"改坏即红"的对照**：缺列、空行、别名表头、零行、单个字符串，
   都是"写宽了会漏判、写窄了会误判"的具体边界；
3. **失败文案必须是业务语言**：用户要能照着改（带行号与候选人名），
   不许出现 `risk` / `max_items` / 错误码这类内部术语。
"""

from __future__ import annotations

import asyncio
import csv
import io
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.errors import ApiError, ErrorCode
from app.plugins.actions import ActionResult, ArtifactDraft, RunContext
from app.plugins.engine import (
    DEFAULT_OPERATOR_ACTION,
    SCREENING_OUTPUT_KEY,
    STEP_COMPLETED,
    STEP_FAILED,
    PluginEngine,
    StepOutcome,
    StepPlan,
)
from app.plugins.schema import InputSpec, StepSpec
from tests.unit.task_helpers import GRANTED, EchoGateway, load_manifest

#: 评分表六列（与 e2e 的 `SCREENING_COLUMNS` 同口径）
SCREENING_COLUMNS: list[str] = ["姓名", "匹配分", "亮点", "风险", "结论", "理由"]

#: 行政人事的必填输入（`resumes` 由各用例按需覆盖）
HR_INPUTS: dict[str, Any] = {
    "position": "前端工程师",
    "department": "技术部",
    "duties": "负责产品前端开发与交付；参与需求评审与技术方案设计。",
}


# ----------------------------------------------------------------------
# 夹具
# ----------------------------------------------------------------------
def _csv_bytes(columns: list[str], rows: list[dict[str, str]]) -> bytes:
    """把行渲染成带表头的 CSV 字节（模拟 `render_csv` 的产物形状）。

    Args:
        columns: 表头。
        rows: 行（缺列按空串补齐，便于构造"表头多一列/少一列"的样本）。

    Returns:
        bytes: UTF-8 编码的 CSV。
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in columns})
    return buffer.getvalue().encode("utf-8")


def _row(
    name: str,
    *,
    risk: str = "项目经验与岗位要求有差距",
    score: str = "78",
    conclusion: str = "待定",
) -> dict[str, str]:
    """构造一行评分表数据。

    Args:
        name: 候选人姓名。
        risk: 「风险」列取值（空串 = 违规样本）。
        score: 匹配分。
        conclusion: 结论。

    Returns:
        dict[str, str]: 行数据。
    """
    return {
        "姓名": name,
        "匹配分": score,
        "亮点": "同类项目经验；沟通表达清晰",
        "风险": risk,
        "结论": conclusion,
        "理由": "该候选人具备岗位要求的核心能力，项目经历与业务场景接近，需要补充一处细节后再定。",
    }


def _screening_spec() -> StepSpec:
    """构造评分表步骤声明（`understand` + `continue`，与人事插件一致）。

    Returns:
        StepSpec: 步骤声明。
    """
    return StepSpec(
        seq=2,
        title="简历筛选初评",
        action="understand",
        produces=[SCREENING_OUTPUT_KEY],
        needs=["jd"],
        on_failure="continue",
    )


def _outcome_with(content: bytes = b"", inline_text: str = "") -> StepOutcome:
    """构造"成功完成"的步骤结果，带一份 `screening` 产物。

    Args:
        content: 产物字节（优先）。
        inline_text: 内联文本（`content` 为空时用）。

    Returns:
        StepOutcome: 步骤结果。
    """
    draft = ArtifactDraft(
        output_key=SCREENING_OUTPUT_KEY,
        type="file",
        name="screening.csv",
        inline_text=inline_text,
        content=content,
        preview_text=(inline_text or "")[:200],
        size_bytes=len(content) or len(inline_text.encode("utf-8")),
    )
    return StepOutcome(status=STEP_COMPLETED, artifacts=(draft,), data={"screening": "ok"})


def _guard(outcome: StepOutcome) -> StepOutcome:
    """跑一次 `guard()`（护栏入口，不经过原语网关）。

    Args:
        outcome: 步骤结果。

    Returns:
        StepOutcome: 护栏后的结果。
    """
    engine = PluginEngine(EchoGateway())
    return engine.guard(_screening_spec(), outcome)


class _DraftGateway:
    """固定返回一份产物草稿的原语替身（验证 `guard` 确实接在 `run_step` 上）。"""

    def __init__(self, draft: ArtifactDraft) -> None:
        """初始化。

        Args:
            draft: 固定返回的产物草稿。
        """
        self.draft = draft
        self.calls: list[str] = []

    def resolve(self, action: str) -> str:
        """解析原语。

        Args:
            action: 原语名。

        Returns:
            str: 固定标识。
        """
        return f"test.{action}"

    async def invoke(self, ctx: RunContext, action: str, params: dict[str, Any]) -> ActionResult:
        """返回固定产物。

        Args:
            ctx: 执行上下文。
            action: 原语名。
            params: 原语参数。

        Returns:
            ActionResult: 成功结果。
        """
        del ctx, params
        self.calls.append(action)
        return ActionResult(status="ok", artifacts=(self.draft,), data={"screening": "ok"})


# ======================================================================
# 缺陷 A：评分表「风险」为空判该行失败
# ======================================================================
def test_all_rows_with_risk_pass_unchanged() -> None:
    """① 每行风险都非空 → `guard()` 原样返回（护栏不碰合规产物）。"""
    rows = [_row("张伟"), _row("李娜"), _row("王强")]
    outcome = _outcome_with(_csv_bytes(SCREENING_COLUMNS, rows))

    guarded = _guard(outcome)

    assert guarded is outcome, "合规评分表不该被护栏改写（连对象都不该换）"
    assert guarded.status == STEP_COMPLETED


def test_blank_risk_row_fails_the_step() -> None:
    """② 某行风险为空 → 该步 failed + 可重试 + 文案点名到人。

    「可重试」是 PRD 的口径（风险为空**判该行失败重跑**）：模型换一次采样
    就可能补上风险，所以这里与 Offer/排期（不可重试）刻意不同。
    """
    rows = [_row("张伟"), _row("李娜", risk="   "), _row("王强")]
    outcome = _outcome_with(_csv_bytes(SCREENING_COLUMNS, rows))

    guarded = _guard(outcome)

    assert guarded.status == STEP_FAILED
    assert guarded.retryable is True, "PRD 要求该行重跑，必须可重试"
    assert guarded.artifacts == (), "违规产物不得交给用户"
    assert guarded.operator_action == DEFAULT_OPERATOR_ACTION
    assert "李娜" in guarded.reason_text, f"失败原因要点名到人：{guarded.reason_text}"
    assert "第 2 行" in guarded.reason_text, f"失败原因要带行号：{guarded.reason_text}"
    assert "风险" in guarded.reason_text
    # 业务语言：不得出现字段名原文或内部术语
    assert "risk" not in guarded.reason_text.lower()
    assert str(int(ErrorCode.INTERNAL_ERROR)) not in guarded.reason_text


def test_all_blank_rows_reported_at_once() -> None:
    """③ 多行部分为空 → **一次报全**所有违规行（不是只报第一条）。

    只报第一条会让用户"改一行、再跑一次、再被拦"，模型每次重新采样还可能
    换一批行——挤牙膏式的报错在真实使用里等于跑不完。
    """
    rows = [_row("张伟", risk=""), _row("李娜"), _row("王强", risk="")]
    outcome = _outcome_with(_csv_bytes(SCREENING_COLUMNS, rows))

    guarded = _guard(outcome)

    assert guarded.status == STEP_FAILED
    problems = guarded.reason_text.split("；")
    assert len(problems) == 2, f"两行违规必须报两条：{guarded.reason_text}"
    assert "张伟" in guarded.reason_text and "王强" in guarded.reason_text
    assert "李娜" not in guarded.reason_text, "合规行不得被牵连"


def test_missing_risk_column_fails() -> None:
    """④ 整张表没有风险列 → 违规（否则"缺列"会表现成"全部通过"）。

    这是最危险的一种漏判：逐行检查会一行都不报，表却看起来"验过了"。
    """
    columns = ["姓名", "匹配分", "亮点", "结论", "理由"]
    rows = [_row("张伟"), _row("李娜")]
    outcome = _outcome_with(_csv_bytes(columns, rows))

    guarded = _guard(outcome)

    assert guarded.status == STEP_FAILED
    assert "风险" in guarded.reason_text and "列" in guarded.reason_text
    assert guarded.retryable is True


@pytest.mark.parametrize("header", ["Risk", "risks", "风险点", "主要风险"])
def test_risk_header_aliases_are_recognized(header: str) -> None:
    """⑤ 表头写成别名的 → 仍能识别（别名容忍）。

    评分表的表头是模型产出的，不是声明里写死的列名。只认字面「风险」会在
    模型写 `Risk` / `风险点` 时**漏判**，而漏判会让这张表"看起来验过了"。

    断言方式：别名列存在时，空值必须被**逐行**报出来（说明列被认出来了），
    而不是被报成"缺少风险列"。
    """
    columns = ["姓名", header, "结论"]
    rows = [{"姓名": "张伟", header: "", "结论": "待定"}]
    outcome = _outcome_with(_csv_bytes(columns, rows))

    guarded = _guard(outcome)

    assert guarded.status == STEP_FAILED
    assert "缺少" not in guarded.reason_text, f"别名列被当成缺失列：{guarded.reason_text}"
    assert "张伟" in guarded.reason_text


@pytest.mark.parametrize("header", ["Risk", "风险点"])
def test_risk_header_aliases_pass_when_filled(header: str) -> None:
    """⑤ 反向：别名列且取值非空 → 通过（别名容忍不能变成一律失败）。"""
    columns = ["姓名", header, "结论"]
    rows = [{"姓名": "张伟", header: "缺少大规模项目经验", "结论": "待定"}]
    outcome = _outcome_with(_csv_bytes(columns, rows))

    guarded = _guard(outcome)

    assert guarded is outcome


def test_zero_rows_is_not_a_failure() -> None:
    """⑥ 零行 → 不判失败（没有简历就没有可判的行）。

    `resumes` 是 `required=false`，"本次不做筛选"是合法输入；把"没行"判成
    "风险为空"，用户会收到一条无从修改的报错。
    """
    outcome = _outcome_with(b"", "")

    guarded = _guard(outcome)

    assert guarded is outcome
    assert guarded.status == STEP_COMPLETED


def test_inline_text_fallback_still_checked() -> None:
    """回落口径：只有 `inline_text`（无 `content` 字节）时**照样要判**。

    `understand` 类步骤历史上只给内联文本；若只认 `content`，这类产物会整段
    跳过风险校验——"没有字节"不等于"没有表"。
    """
    text = _csv_bytes(SCREENING_COLUMNS, [_row("张伟", risk="")]).decode("utf-8")
    outcome = _outcome_with(b"", text)

    guarded = _guard(outcome)

    assert guarded.status == STEP_FAILED
    assert "张伟" in guarded.reason_text


def test_run_step_wires_screening_gate() -> None:
    """接线自证：`run_step()` → `guard()` 的链路上确实跑了这道门。

    只测 `guard()` 无法证明它被接进去了（护栏写好了却没人调用是最常见的
    "看起来实现了"）；这里走完整的一步执行。
    """
    draft = ArtifactDraft(
        output_key=SCREENING_OUTPUT_KEY,
        type="file",
        name="screening.csv",
        content=_csv_bytes(SCREENING_COLUMNS, [_row("李娜", risk="")]),
    )
    engine = PluginEngine(_DraftGateway(draft))
    ctx = RunContext(tenant_id="tnt_test", task_id="task_test", granted=GRANTED)

    outcome = asyncio.run(engine.run_step(ctx, StepPlan(spec=_screening_spec()), {}))

    assert outcome.status == STEP_FAILED
    assert outcome.retryable is True
    assert "李娜" in outcome.reason_text


# ======================================================================
# 缺陷 B：简历「一次最多 20 份」
# ======================================================================
def _engine() -> PluginEngine:
    """构造只用于 `preflight()` 的引擎（网关不会被调用）。

    Returns:
        PluginEngine: 引擎实例。
    """
    return PluginEngine(EchoGateway())


def _resumes(count: int) -> list[str]:
    """生成 `count` 个简历文件名。

    Args:
        count: 份数。

    Returns:
        list[str]: 文件名列表。
    """
    return [f"候选人{index:02d}.pdf" for index in range(1, count + 1)]


def test_twenty_resumes_pass_preflight() -> None:
    """① 20 份 → 通过（上限是闭区间，卡在 20 本身不能被拒）。"""
    manifest = load_manifest("plg_hr_admin")
    _engine().preflight(manifest, {**HR_INPUTS, "resumes": _resumes(20)}, GRANTED)


def test_twenty_one_resumes_rejected_with_verbatim_message() -> None:
    """② 21 份 → 20001，且文案**逐字**等于 PRD 验收标准里的那句。"""
    manifest = load_manifest("plg_hr_admin")
    with pytest.raises(ApiError) as excinfo:
        _engine().preflight(manifest, {**HR_INPUTS, "resumes": _resumes(21)}, GRANTED)

    assert excinfo.value.code == ErrorCode.PARAM_INVALID
    assert excinfo.value.message == "一次最多 20 份，请分批上传"


def test_single_string_resume_not_rejected() -> None:
    """③ 单个字符串（非列表）→ 按 1 份算，不误拒。

    前端历史上给 `file` 输入传的可能是字符串；把"不是列表"当成"超量"会让
    只传一份简历的用户直接被拦在门外。
    """
    manifest = load_manifest("plg_hr_admin")
    _engine().preflight(manifest, {**HR_INPUTS, "resumes": "张伟.pdf"}, GRANTED)


def test_max_items_on_single_value_input_is_rejected() -> None:
    """④ `max_items` 声明在 `text` 类型上 → 声明校验报错。

    单值输入声明数量上限属于"看上去限制了、其实没限"的假护栏：
    运行期永远算不出超限，所以必须在加载期就拒掉。
    """
    with pytest.raises(ValidationError) as excinfo:
        InputSpec(key="topic", label="主题", type="text", max_items=5)

    assert "数量上限" in str(excinfo.value)


def test_max_items_allowed_on_file_and_multi_select() -> None:
    """④ 反向：`file` / `multi_select` 允许声明（收紧约束不能收成"谁都不许"）。"""
    for input_type in ("file", "multi_select"):
        spec = InputSpec(key="items", label="条目", type=input_type, max_items=20)
        assert spec.max_items == 20
