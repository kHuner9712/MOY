"""`services/action_gateway.py` 分支补测（阶段三遗留观察项：分支余量 0 → ≥50 条）。

**为什么单独立文件**：`action_gateway` 是插件执行原语与平台能力的唯一接缝
（`plugin_deps.build_action_gateway()` 注入给 `PluginEngine`），生产路径上
每个任务步骤都要过它，而既有测试全部用 `EchoGateway` 替身绕开了它——
42 个分支、0 覆盖，是覆盖率缺口最大的文件，也是安全语义最密集的文件
（凭据拒绝、白名单复查、检索上限、脱敏截断都落在这一层）。

**测试策略**：三个下游服务（M1 / R7 / T5）用鸭子类型替身注入门面的**延迟构造点**
（`monkeypatch` 掉模块级 `get_*_service`），不走真实下游；被测逻辑
（分派、参数装配、脱敏、错误码翻译）全部走真实实现。纯函数
（`_prompt_of` / `_tool_params` / `_render_payload` / `_render_rows` /
`_auth_of` / `_session_id`）直接调，因为它们承载"坏数据进来会不会崩"的判据。

**已知缺陷（不在本文件修，另行报告）**：`_run_retrieve` 读的是
`segment.content`，而 `schemas.rag.SearchSegment` 的字段名是
`segment_content`——检索**命中非空**时该行必抛 `AttributeError`，
被引擎按 `30001` 翻译成"这一步没有完成，可以重试"。因此本文件的检索
成功用例只覆盖"零命中"这一真实可达路径；非零命中路径留待实现修复后补钉子。
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass
from typing import Any

import pytest

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.security import AuthContext
from app.plugins.actions import ACTION_INTERNAL_LABELS, ACTION_WHITELIST, ActionResult, RunContext
from app.schemas.model import GenerateRequest, MessageRole, ModelProfile, Usage
from app.schemas.rag import SearchData
from app.schemas.tool import ExecutionData
from app.services.action_gateway import (
    MAX_INLINE_TEXT,
    PLATFORM_TARGETS,
    RETRIEVE_TOP_N,
    InertActionGateway,
    ServicesActionGateway,
    _auth_of,
    _prompt_of,
    _render_payload,
    _render_rows,
    _session_id,
    _tool_params,
)

# ===========================================================================
# 下游服务替身（鸭子类型：门面只用到下列方法）
# ===========================================================================


@dataclass(frozen=True, slots=True)
class _UsageWithoutTotal:
    """缺 `total_units` 字段的用量替身（覆盖 `getattr` 默认值分支）。"""


@dataclass(frozen=True, slots=True)
class _GenerateOutcome:
    """M1 生成替身返回体（门面只读 `content` 与 `usage`）。"""

    content: str = ""
    usage: Any = None


class StubModelService:
    """M1 生成替身（记录调用；按配置返回或抛错）。

    Attributes:
        content: 返回的生成文本。
        total_units: 返回的总用量。
        usage: 直接指定 usage 对象（缺省按 `total_units` 构造）。
        error: 非 None 时改为抛出该错误。
        calls: 调用记录（`request` / `user_id` / `auth`）。
    """

    def __init__(
        self,
        *,
        content: str = "替身生成内容",
        total_units: int = 7,
        usage: Any = None,
        error: ApiError | None = None,
    ) -> None:
        """初始化替身。

        Args:
            content: 生成文本。
            total_units: 总用量。
            usage: 显式 usage 对象。
            error: 要抛出的错误。
        """
        self.content = content
        self.total_units = total_units
        self.usage = usage
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def generate(
        self,
        request: GenerateRequest,
        *,
        user_id: str = "",
        auth: AuthContext | None = None,
    ) -> _GenerateOutcome:
        """记录调用并返回预置结果。

        Args:
            request: 生成请求。
            user_id: 发起人 ID。
            auth: 发起人身份。

        Returns:
            _GenerateOutcome: 预置返回体。

        Raises:
            ApiError: 预置了错误时原样抛出。
        """
        self.calls.append({"request": request, "user_id": user_id, "auth": auth})
        if self.error is not None:
            raise self.error
        usage = self.usage if self.usage is not None else Usage(total_units=self.total_units)
        return _GenerateOutcome(content=self.content, usage=usage)


class StubRagService:
    """R7 检索替身（记录调用；返回零命中或抛错）。

    Attributes:
        segments: 预置命中片段（缺省空——见模块 docstring 的已知缺陷）。
        error: 非 None 时改为抛出该错误。
        calls: 调用记录（`request` / `auth`）。
    """

    def __init__(self, *, segments: list[Any] | None = None, error: ApiError | None = None) -> None:
        """初始化替身。

        Args:
            segments: 预置命中片段。
            error: 要抛出的错误。
        """
        self.segments = segments or []
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def search(self, request: Any, auth: AuthContext | None = None) -> SearchData:
        """记录调用并返回预置检索结果。

        Args:
            request: 检索请求。
            auth: 发起人身份。

        Returns:
            SearchData: 预置检索结果。

        Raises:
            ApiError: 预置了错误时原样抛出。
        """
        self.calls.append({"request": request, "auth": auth})
        if self.error is not None:
            raise self.error
        return SearchData(segments=list(self.segments))


class StubToolService:
    """T5 工具执行替身（记录调用；按预置结果或抛错应答）。

    Attributes:
        result: 预置执行结果（`None` 也是一种真实形态：执行成功但无返回体）。
        error: 非 None 时改为抛出该错误。
        calls: 调用记录（`request` / `auth`）。
    """

    def __init__(
        self, *, result: dict[str, Any] | None = None, error: ApiError | None = None
    ) -> None:
        """初始化替身。

        Args:
            result: 预置执行结果。
            error: 要抛出的错误。
        """
        self.result = result
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def execute(self, request: Any, auth: AuthContext | None = None) -> ExecutionData:
        """记录调用并回显执行记录。

        Args:
            request: 执行请求。
            auth: 发起人身份。

        Returns:
            ExecutionData: 预置执行记录。

        Raises:
            ApiError: 预置了错误时原样抛出。
        """
        self.calls.append({"request": request, "auth": auth})
        if self.error is not None:
            raise self.error
        return ExecutionData(
            execution_id="exec_stub",
            tool_type=request.tool_type,
            operation=request.operation,
            result=self.result,
        )


def _patch_model(monkeypatch: pytest.MonkeyPatch, stub: StubModelService) -> None:
    """把 M1 替身注入门面的延迟构造点。

    Args:
        monkeypatch: pytest monkeypatch 夹具。
        stub: 生成替身。
    """
    monkeypatch.setattr("app.services.model_service.get_model_service", lambda: stub)


def _patch_rag(monkeypatch: pytest.MonkeyPatch, stub: StubRagService) -> None:
    """把 R7 替身注入门面的延迟构造点。

    Args:
        monkeypatch: pytest monkeypatch 夹具。
        stub: 检索替身。
    """
    monkeypatch.setattr("app.services.rag_service.get_rag_service", lambda: stub)


def _patch_tool(monkeypatch: pytest.MonkeyPatch, stub: StubToolService) -> None:
    """把 T5 替身注入门面的延迟构造点。

    Args:
        monkeypatch: pytest monkeypatch 夹具。
        stub: 工具执行替身。
    """
    monkeypatch.setattr("app.services.tool_service.get_tool_service", lambda: stub)


def _ctx(**overrides: Any) -> RunContext:
    """构造单步执行上下文（默认携带真实 `AuthContext`）。

    Args:
        **overrides: 覆盖字段（如 `task_id` / `auth` / `step_seq`）。

    Returns:
        RunContext: 执行上下文。
    """
    base: dict[str, Any] = {
        "tenant_id": "tnt_test",
        "task_id": "task_01HTEST",
        "step_seq": 1,
        "plugin_id": "plg_test",
        "installation_id": "inst_01HTEST",
        "initiator_id": "usr_writer",
        "trace_id": "trace_01",
        "granted": frozenset({"personal.knowledge.read", "personal.tool.use"}),
        "settings": {},
        "inputs": {},
        "outputs": {},
        "auth": AuthContext(user_id="usr_writer", tenant_id="tnt_test"),
    }
    base.update(overrides)
    return RunContext(**base)


@pytest.fixture
def gateway() -> ServicesActionGateway:
    """带显式配置的门面（避免依赖全局配置单例）。"""
    return ServicesActionGateway(Settings(env="test", mock_mode=True))


# ===========================================================================
# 白名单复查（resolve）
# ===========================================================================
def test_resolve_returns_platform_target_for_each_action(gateway: ServicesActionGateway) -> None:
    """六个白名单原语各自解析到平台落点标识（运行期复查的放行支）。

    走了这一支意味着"声明被加载期校验过、运行期再查一次仍然成立"；
    映射值与 `PLATFORM_TARGETS` 逐项对应，改映射必须同步改这里。
    """
    assert [gateway.resolve(action) for action in ACTION_WHITELIST] == [
        PLATFORM_TARGETS[action] for action in ACTION_WHITELIST
    ]


async def test_invoke_unknown_action_raises_config_error(gateway: ServicesActionGateway) -> None:
    """白名单外原语在分派前即被 `resolve` 拦下：30004 配置异常。

    不是 500、也不是静默执行——声明被篡改时必须在第一道门就红，
    且错误码指向"配置/声明"而不是"业务失败"（两者处理路径不同）。
    """
    with pytest.raises(ApiError) as excinfo:
        await gateway.invoke(_ctx(), "exfiltrate", {"produces": ["x"]})
    assert int(excinfo.value.code) == int(ErrorCode.CONFIG_ERROR)


# ===========================================================================
# 依赖延迟构造
# ===========================================================================
async def test_downstream_services_construct_once_then_reuse(
    monkeypatch: pytest.MonkeyPatch, gateway: ServicesActionGateway
) -> None:
    """三个下游服务首次调用时才构造，之后复用同一实例。

    这一对分支守卫的是"启动期不硬依赖 M1/R7/T5"：若每次都重建，
    进程内单例（连接池、计量）会被绕过；若从不构造，调用直接崩。
    """
    model = StubModelService()
    rag = StubRagService()
    tool = StubToolService(result={"url": "https://example.com/r"})
    constructions = {"model": 0, "rag": 0, "tool": 0}

    def _model_factory() -> StubModelService:
        constructions["model"] += 1
        return model

    def _rag_factory() -> StubRagService:
        constructions["rag"] += 1
        return rag

    def _tool_factory() -> StubToolService:
        constructions["tool"] += 1
        return tool

    monkeypatch.setattr("app.services.model_service.get_model_service", _model_factory)
    monkeypatch.setattr("app.services.rag_service.get_rag_service", _rag_factory)
    monkeypatch.setattr("app.services.tool_service.get_tool_service", _tool_factory)

    ctx = _ctx()
    await gateway.invoke(ctx, "understand", {"produces": ["points"]})
    await gateway.invoke(ctx, "understand", {"produces": ["points"]})
    await gateway.invoke(ctx, "retrieve", {"base_id": "kb_1", "inputs": {"topic": "制度"}})
    await gateway.invoke(ctx, "tool", {"tool_type": "wecom", "operation": "list_members"})
    await gateway.invoke(ctx, "tool", {"tool_type": "wecom", "operation": "list_members"})

    assert constructions == {"model": 1, "rag": 1, "tool": 1}
    assert len(model.calls) == 2
    assert len(rag.calls) == 1
    assert len(tool.calls) == 2


# ===========================================================================
# 分派链（invoke 的 if/elif/else 六支）
# ===========================================================================
@pytest.mark.parametrize(
    ("action", "params"),
    [
        ("understand", {"title": "理解步骤", "produces": ["points"], "inputs": {"topic": "制度"}}),
        ("compose", {"title": "撰写步骤", "produces": ["draft"]}),
        ("retrieve", {"base_id": "kb_1", "inputs": {"topic": "制度"}, "produces": ["evidence"]}),
        ("tool", {"tool_type": "wecom", "operation": "send_message", "produces": ["receipt"]}),
        ("render_file", {"render_format": "md", "title": "导出", "produces": ["file"]}),
        ("summarize", {"prior": {"draft": "前序稿件"}}),
    ],
)
async def test_invoke_routes_each_primitive_to_its_landing(
    monkeypatch: pytest.MonkeyPatch,
    gateway: ServicesActionGateway,
    action: str,
    params: dict[str, Any],
) -> None:
    """六个原语各自触发对应下游，且**不误触**其它下游。

    分派链的每一支都钉一个"真实调用发生"：只有 `understand`/`compose`
    会调 M1，只有 `retrieve` 会调 R7，只有 `tool` 会调 T5——
    接错线的表现是"步骤显示成功但下游什么都没发生"，必须在这里被拦住。
    """
    model = StubModelService(content="替身内容", total_units=3)
    rag = StubRagService()
    tool = StubToolService(result={"url": "https://example.com/r", "summary": "已完成"})
    _patch_model(monkeypatch, model)
    _patch_rag(monkeypatch, rag)
    _patch_tool(monkeypatch, tool)

    result = await gateway.invoke(_ctx(), action, params)

    assert result.status == "ok"
    assert len(model.calls) == (1 if action in {"understand", "compose"} else 0)
    assert len(rag.calls) == (1 if action == "retrieve" else 0)
    assert len(tool.calls) == (1 if action == "tool" else 0)


# ===========================================================================
# M1：understand / compose
# ===========================================================================
async def test_generate_returns_artifacts_data_and_usage(
    monkeypatch: pytest.MonkeyPatch, gateway: ServicesActionGateway
) -> None:
    """生成成功：按 `produces` 落文本产物，`data` 键→正文，用量透传。

    对外表现：每个产物键都拿到**同一段脱敏正文**（前序步骤按 `needs` 取）；
    会话 ID 必须是 `task_` 前缀的内部会话，不能和用户会话混在一起。
    """
    model = StubModelService(content="要点一二三", total_units=7)
    _patch_model(monkeypatch, model)
    ctx = _ctx()

    result = await gateway.invoke(
        ctx,
        "compose",
        {"title": "周报", "produces": ["draft", "summary"], "inputs": {"topic": "迭代进展"}},
    )

    assert result.status == "ok"
    assert [draft.output_key for draft in result.artifacts] == ["draft", "summary"]
    assert result.data == {"draft": "要点一二三", "summary": "要点一二三"}
    assert result.usage_units == 7

    draft = result.artifacts[0]
    assert draft.type == "text"
    assert draft.name == "周报"
    assert draft.inline_text == "要点一二三"
    assert draft.preview_text == "要点一二三"
    assert draft.size_bytes == len("要点一二三".encode())

    call = model.calls[0]
    assert call["user_id"] == ctx.initiator_id
    assert call["auth"] is ctx.auth
    request = call["request"]
    assert request.conversation_id == "task_task_01HTEST"
    assert request.profile is ModelProfile.GENERAL
    assert len(request.messages) == 1
    assert request.messages[0].role is MessageRole.USER
    assert "周报" in request.messages[0].content
    assert "迭代进展" in request.messages[0].content


async def test_generate_masks_pii_before_persisting(
    monkeypatch: pytest.MonkeyPatch, gateway: ServicesActionGateway
) -> None:
    """生成正文里的手机号/邮箱在落产物前被掩码（不是落库后再说）。

    判据是**替换后的字面值**：`13812345678` → `138****0000`、
    `zhang@example.com` → `user***@example.com`；`data` 与产物内联文本
    必须同一份脱敏结果，不能一个掩一个不掩。
    """
    model = StubModelService(content="联系 13812345678 或 zhang@example.com 确认")
    _patch_model(monkeypatch, model)

    result = await gateway.invoke(_ctx(), "understand", {"produces": ["points"]})

    expected = "联系 138****0000 或 user***@example.com 确认"
    assert result.data["points"] == expected
    assert result.artifacts[0].inline_text == expected
    assert "13812345678" not in expected


async def test_generate_without_usage_reports_zero_units(
    monkeypatch: pytest.MonkeyPatch, gateway: ServicesActionGateway
) -> None:
    """下游没给用量字段时记 0 额度，不猜、不崩。

    `getattr(..., 0)` 的默认值分支：底座返回瘦身对象时任务汇总显示 0，
    比整个步骤 500 要好；真额度以计量台账为准，不在这一层补拍。
    """
    model = StubModelService(content="无用量返回", usage=_UsageWithoutTotal())
    _patch_model(monkeypatch, model)

    result = await gateway.invoke(_ctx(), "compose", {"produces": ["draft"]})

    assert result.status == "ok"
    assert result.usage_units == 0


async def test_generate_without_produces_returns_no_artifacts(
    monkeypatch: pytest.MonkeyPatch, gateway: ServicesActionGateway
) -> None:
    """步骤没声明产物时返回空产物集（成功态，不是失败）。

    `produces` 为空是合法声明（如只做中间计算）；此时 `data` 也为空，
    后续步骤按 `needs` 取不到键是预期行为，由引擎判"产物缺失"。
    """
    model = StubModelService(content="没有产物声明的生成")
    _patch_model(monkeypatch, model)

    result = await gateway.invoke(_ctx(), "understand", {"inputs": {"topic": "x"}})

    assert result.status == "ok"
    assert result.artifacts == ()
    assert result.data == {}


async def test_generate_error_propagates_unchanged(
    monkeypatch: pytest.MonkeyPatch, gateway: ServicesActionGateway
) -> None:
    """M1 抛出的 `ApiError` 原码透传（不包装成 500、不吞掉）。

    透传后由引擎的 `degrade()` 翻译成用户语言；这里若改成通用异常，
    "模型超长/配额耗尽"的区分度就丢了，用户看到的都是同一句"系统繁忙"。
    """
    model = StubModelService(
        error=ApiError(ErrorCode.MODEL_GENERATE_FAILED, internal_detail="stub")
    )
    _patch_model(monkeypatch, model)

    with pytest.raises(ApiError) as excinfo:
        await gateway.invoke(_ctx(), "compose", {"produces": ["draft"]})
    assert int(excinfo.value.code) == int(ErrorCode.MODEL_GENERATE_FAILED)


# ===========================================================================
# _prompt_of（指令拼装）
# ===========================================================================
def test_prompt_of_assembles_title_inputs_and_prior() -> None:
    """指令 = 标题 + 字符串/列表输入 + 前序产物引用，各类只进该进的。

    分支语义：空字符串与空白字符串**不进**指令（否则模型会看到一堆空行）；
    列表用顿号连接；非字符串非列表（如数字）被跳过——参数装配在引擎层
    已校验类型，这里是最后一道不把垃圾喂给模型的闸。
    """
    params = {
        "title": "登录页改版",
        "inputs": {
            "requirement": "换成新版设计稿",
            "blank": "   ",
            "count": 3,
            "tags": ["前端", "UI"],
            "empty_tags": [],
        },
        "prior": {"draft": "前序稿件", "metrics": {"lines": 12}},
    }

    prompt = _prompt_of("compose", params)

    assert "请完成「登录页改版」。" in prompt
    assert "requirement：换成新版设计稿" in prompt
    assert "tags：前端、UI" in prompt
    assert "参考（draft）：前序稿件" in prompt
    assert "参考（metrics）：{'lines': 12}" in prompt
    assert "blank" not in prompt
    assert "count" not in prompt
    assert "empty_tags" not in prompt


def test_prompt_of_falls_back_to_internal_label_without_title() -> None:
    """步骤没给标题时回落原语内部标签（`understand` → "理解抽取"）。

    标题缺失时指令不能退化成空标题句；内部标签只进指令与日志，
    不进任何响应（AC-06），所以这里是它唯一的出口之一。
    """
    prompt = _prompt_of("understand", {})

    assert f"请完成「{ACTION_INTERNAL_LABELS['understand']}」。" in prompt


def test_prompt_of_truncates_prompt_and_prior_values() -> None:
    """整条指令截到 4000 字，单条前序产物引用截到 1000 字。

    超长输入会把上下文预算吃光（模型直接报超长），也会把提示词注入面
    放大；两个上限都是硬截断，不抛错——这一步没有"参数非法"可言。
    """
    long_input = {"inputs": {"requirement": "长" * 5000}}
    assert len(_prompt_of("compose", long_input)) == 4000

    long_prior = {"prior": {"draft": "参" * 1500}}
    prompt = _prompt_of("compose", long_prior)
    assert f"参考（draft）：{'参' * 1000}" in prompt
    assert "参" * 1001 not in prompt


# ===========================================================================
# R7：retrieve
# ===========================================================================
async def test_retrieve_without_base_or_query_is_skipped(
    monkeypatch: pytest.MonkeyPatch, gateway: ServicesActionGateway
) -> None:
    """缺知识库或缺查询词时如实返回 `skipped`，不做假检索。

    对外表现是产物带"没有指定知识库，这一步没有检索素材"的原因文案；
    两个入口条件（无 base_id / 无 query）分别走同一支，R7 都不会被调用——
    "没有素材还调检索"只会拿到空结果并伪装成已检索。
    """
    rag = StubRagService()
    _patch_rag(monkeypatch, rag)

    no_base = await gateway.invoke(_ctx(), "retrieve", {"inputs": {"topic": "制度"}})
    no_query = await gateway.invoke(_ctx(), "retrieve", {"base_id": "kb_1"})

    for result in (no_base, no_query):
        assert result.status == "skipped"
        assert result.reason_text == "没有指定知识库，这一步没有检索素材"
    assert rag.calls == []


async def test_retrieve_calls_rag_with_capped_top_k(
    monkeypatch: pytest.MonkeyPatch, gateway: ServicesActionGateway
) -> None:
    """检索成功路径（零命中）：top_k 锁死 10、阈值 0.5、产物为表格型。

    `top_n ≤ 10` 是 §3.2 的硬口径——把整个库塞进上下文既烧额度又稀释
    相关性；`hits` 进 `data` 供后续步骤判断"有没有依据"，零命中也返回
    `ok`（不是失败：没检索到和检索失败是两件事）。
    """
    rag = StubRagService()
    _patch_rag(monkeypatch, rag)
    ctx = _ctx()

    result = await gateway.invoke(
        ctx,
        "retrieve",
        {"base_id": "kb_1", "inputs": {"topic": "差旅制度"}, "produces": ["evidence"]},
    )

    assert result.status == "ok"
    assert result.data == {"evidence": "", "hits": 0}
    assert [draft.type for draft in result.artifacts] == ["table"]

    request = rag.calls[0]["request"]
    assert request.base_id == "kb_1"
    assert request.query == "差旅制度"
    assert request.top_k == RETRIEVE_TOP_N == 10
    assert request.score_threshold == 0.5
    assert rag.calls[0]["auth"] is ctx.auth


async def test_retrieve_query_and_base_source_priority(
    monkeypatch: pytest.MonkeyPatch, gateway: ServicesActionGateway
) -> None:
    """查询词取值顺序 `inputs.topic` → `inputs.query` → `params.title`。

    三种声明写法都必须能检索到（历史声明只写 `title`）；顺序固定，
    否则同一个输入在不同插件里会检索出不同结果。
    """
    rag = StubRagService()
    _patch_rag(monkeypatch, rag)

    await gateway.invoke(
        _ctx(),
        "retrieve",
        {"base_id": "kb_top", "inputs": {"topic": "主题", "query": "查询"}, "produces": []},
    )
    await gateway.invoke(
        _ctx(), "retrieve", {"base_id": "kb_query", "inputs": {"query": "查询"}, "produces": []}
    )
    await gateway.invoke(
        _ctx(), "retrieve", {"base_id": "kb_title", "title": "标题", "produces": []}
    )
    await gateway.invoke(
        _ctx(),
        "retrieve",
        {"inputs": {"base_id": "kb_inputs", "topic": "主题"}, "produces": []},
    )

    assert rag.calls[0]["request"].query == "主题"
    assert rag.calls[0]["request"].base_id == "kb_top"
    assert rag.calls[1]["request"].query == "查询"
    assert rag.calls[1]["request"].base_id == "kb_query"
    assert rag.calls[2]["request"].query == "标题"
    assert rag.calls[2]["request"].base_id == "kb_title"
    assert rag.calls[3]["request"].base_id == "kb_inputs"


async def test_retrieve_error_propagates_unchanged(
    monkeypatch: pytest.MonkeyPatch, gateway: ServicesActionGateway
) -> None:
    """知识库不存在（11001）由 R7 抛出后原码透传，不伪装成空检索。

    把 11001 吞成"零命中"会让用户以为"库里确实没有"，
    而真相是库 ID 写错/已删除——错误码必须原样到达引擎。
    """
    rag = StubRagService(error=ApiError(ErrorCode.KNOWLEDGE_BASE_NOT_FOUND, internal_detail="kb_1"))
    _patch_rag(monkeypatch, rag)

    with pytest.raises(ApiError) as excinfo:
        await gateway.invoke(_ctx(), "retrieve", {"base_id": "kb_1", "inputs": {"topic": "制度"}})
    assert int(excinfo.value.code) == int(ErrorCode.KNOWLEDGE_BASE_NOT_FOUND)


# ===========================================================================
# T5：tool
# ===========================================================================
async def test_tool_with_url_builds_link_artifact(
    monkeypatch: pytest.MonkeyPatch, gateway: ServicesActionGateway
) -> None:
    """工具带回外链时产物为 `link` 型，执行记录四字段全进 `data`。

    对外表现：用户点产物直接跳到 MR/邮件；`tool_execution_id` 必须进
    `data`——审计与"哪一步建的"回溯都靠它，丢了就对不上账。
    """
    tool = StubToolService(
        result={"url": "https://github.com/acme/web/pull/7", "summary": "已创建 MR"}
    )
    _patch_tool(monkeypatch, tool)
    ctx = _ctx()

    result = await gateway.invoke(
        ctx,
        "tool",
        {
            "tool_type": "github",
            "operation": "create_pull_request",
            "produces": ["mr_link"],
            "inputs": {"repo": "acme/web"},
            "prior": {"draft": "diff 内容", "doc_ids": ["doc_1"]},
        },
    )

    assert result.status == "ok"
    request = tool.calls[0]["request"]
    assert request.tool_type == "github"
    assert request.operation == "create_pull_request"
    assert request.params == {"repo": "acme/web", "draft": "diff 内容"}
    assert tool.calls[0]["auth"] is ctx.auth

    draft = result.artifacts[0]
    assert draft.type == "link"
    assert draft.external_url == "https://github.com/acme/web/pull/7"
    assert draft.inline_text == "已创建 MR"
    assert result.data["tool_execution_id"] == "exec_stub"
    assert result.data["tool_type"] == "github"
    assert result.data["operation"] == "create_pull_request"
    assert result.data["mr_link"] == "已创建 MR"


async def test_tool_without_url_uses_message_and_text_type(
    monkeypatch: pytest.MonkeyPatch, gateway: ServicesActionGateway
) -> None:
    """无外链时回落 `message` 文案、产物退为 `text` 型。

    不是所有工具都回 URL（发企微只回发送状态）；此时 `external_url`
    必须为空串而不是占位假链——假链点进去 404 比没有链接更糟。
    """
    tool = StubToolService(result={"message": "已发送"})
    _patch_tool(monkeypatch, tool)

    result = await gateway.invoke(
        _ctx(), "tool", {"tool_type": "wecom", "operation": "send_message", "produces": ["receipt"]}
    )

    draft = result.artifacts[0]
    assert draft.type == "text"
    assert draft.external_url == ""
    assert draft.inline_text == "已发送"
    assert result.data["receipt"] == "已发送"


async def test_tool_with_none_result_treats_as_empty(
    monkeypatch: pytest.MonkeyPatch, gateway: ServicesActionGateway
) -> None:
    """执行成功但无返回体时 `result` 归一为 `{}`，不崩。

    `ExecutionData.result` 可为 `None`（执行记录已落库、无业务返回）；
    归一后 `data["result"]` 是空字典而不是 `None`——下游步骤按 dict 取值。
    """
    tool = StubToolService(result=None)
    _patch_tool(monkeypatch, tool)

    result = await gateway.invoke(
        _ctx(), "tool", {"tool_type": "wecom", "operation": "list_members", "produces": []}
    )

    assert result.status == "ok"
    assert result.data["result"] == {}
    assert result.artifacts == ()


async def test_tool_rejects_secret_keys_in_result(
    monkeypatch: pytest.MonkeyPatch, gateway: ServicesActionGateway
) -> None:
    """工具返回体出现凭据键名 → 30003，宁可步骤失败也不落库。

    §3.6 第 4 条：键名命中即拒绝（不看值像不像）。若这一步放行，
    任何"工具把 token 回在结果里"的事故都会直接写进产物表。
    """
    tool = StubToolService(
        result={"url": "https://example.com/x", "api_key": "sk-should-never-land"}
    )
    _patch_tool(monkeypatch, tool)

    with pytest.raises(ApiError) as excinfo:
        await gateway.invoke(
            _ctx(),
            "tool",
            {"tool_type": "github", "operation": "create_pull_request", "produces": ["mr"]},
        )
    assert int(excinfo.value.code) == int(ErrorCode.INTERNAL_ERROR)


async def test_tool_not_connected_error_propagates(
    monkeypatch: pytest.MonkeyPatch, gateway: ServicesActionGateway
) -> None:
    """工具未连接（13002）原码透传，由引擎翻译成"先去连接"。

    这一支若被吞成通用失败，用户看到的是"重试"，而重试永远无效——
    未连接只能靠连接修复，错误码必须带着处理路径一起上去。
    """
    tool = StubToolService(error=ApiError(ErrorCode.TOOL_NOT_CONNECTED, internal_detail="wecom"))
    _patch_tool(monkeypatch, tool)

    with pytest.raises(ApiError) as excinfo:
        await gateway.invoke(
            _ctx(), "tool", {"tool_type": "wecom", "operation": "send_message", "produces": []}
        )
    assert int(excinfo.value.code) == int(ErrorCode.TOOL_NOT_CONNECTED)


def test_tool_params_merges_inputs_prior_and_drops_doc_ids() -> None:
    """T5 参数 = 用户输入 + 前序产物，且 `doc_ids` 一律剥掉。

    `doc_ids` 是适配层内部文档引用，透给工具既无效也可能泄漏内部 ID；
    合并顺序固定为 prior 覆盖 inputs（前序产物是更新的那半）。
    """
    merged = _tool_params(
        {
            "inputs": {"repo": "acme/web", "doc_ids": ["doc_1"]},
            "prior": {"branch_policy": "新建分支", "doc_ids": ["doc_2"]},
        }
    )

    assert merged == {"repo": "acme/web", "branch_policy": "新建分支"}
    assert "doc_ids" not in merged


# ===========================================================================
# render_file（本地渲染）
# ===========================================================================
async def test_render_csv_builds_file_artifact_with_storage_key(
    gateway: ServicesActionGateway,
) -> None:
    """CSV 渲染：文件产物带 `task_id/step_seq/文件名` 存储键与 BOM 头。

    存储键三段式是对象存储寻址的唯一格式（任务/步骤/文件），
    少一段就会和其它任务的同名产物互相覆盖；BOM 头保证 Excel 不乱码。
    """
    ctx = _ctx()

    result = await gateway.invoke(
        ctx,
        "render_file",
        {
            "render_format": "csv",
            "title": "差旅清单",
            "produces": ["table_file"],
            "prior": {"rows": [{"姓名": "张三", "金额": 100}], "note": "备注文字"},
        },
    )

    assert result.status == "ok"
    draft = result.artifacts[0]
    assert draft.type == "file"
    assert draft.name == "差旅清单"
    assert draft.storage_key == f"{ctx.task_id}/{ctx.step_seq}/差旅清单.csv"
    assert draft.content.startswith(codecs.BOM_UTF8)
    assert draft.size_bytes == len(draft.content)
    assert draft.preview_text == "备注文字"
    assert draft.inline_text == "备注文字"
    assert result.data == {"table_file": "差旅清单.csv"}


async def test_render_without_produces_is_skipped(gateway: ServicesActionGateway) -> None:
    """没声明产物时渲染仍执行但返回 `skipped`（不产文件、不占存储）。

    与生成的"无产物但 ok"不同：渲染的唯一目的就是文件，没有产物键
    就没有落点，返回 skipped 让引擎记"这一步跳过了"而不是假装成功。
    """
    result = await gateway.invoke(_ctx(), "render_file", {"render_format": "md", "title": "空导出"})

    assert result.status == "skipped"
    assert result.artifacts == ()
    assert result.data == {}


async def test_render_defaults_to_md_and_rejects_unknown_format(
    gateway: ServicesActionGateway,
) -> None:
    """格式缺省回落 `md`；给了白名单外值一律 20001，不悄悄换格式。

    "用户要 pdf、系统悄悄给了 md"是假回执；缺省回落只发生在
    **完全没给**格式的历史路径，两者必须分开处理。
    """
    default = await gateway.invoke(_ctx(), "render_file", {"title": "无格式", "produces": ["f"]})
    assert default.status == "ok"
    assert default.artifacts[0].storage_key.endswith(".md")

    with pytest.raises(ApiError) as excinfo:
        await gateway.invoke(
            _ctx(), "render_file", {"render_format": "pdf", "title": "要 pdf", "produces": ["f"]}
        )
    assert int(excinfo.value.code) == int(ErrorCode.PARAM_INVALID)


# ===========================================================================
# _render_payload / _render_rows（渲染数据装配）
# ===========================================================================
def test_render_rows_covers_all_prior_shapes() -> None:
    """表格行装配：字典列表逐行、标量列表包键名、字符串截 200、其它丢弃。

    四种前序产物形态（list-of-dict / list-of-scalar / str / 其它）各有
    归宿；数字这类非表格数据被丢弃而不是变成 `"5"` 行——
    "把什么都渲进 CSV"会让表格里混进无意义行。
    """
    rows = _render_rows(
        {
            "prior": {
                "table": [{"姓名": "张三", "分数": 90}, "裸字符串"],
                "note": "y" * 300,
                "number": 5,
            }
        }
    )

    assert rows[0] == {"姓名": "张三", "分数": 90}
    assert rows[1] == {"项目": "table", "内容": "裸字符串"}
    assert rows[2] == {"项目": "note", "内容": "y" * 200}
    assert len(rows) == 3


def test_render_payload_prefers_doc_type_and_truncates() -> None:
    """渲染载荷标题优先 `inputs.doc_type`，正文只取字符串前序且截 32KB。

    标题优先级来自"公文必须 docx"那类契约：文档类型比步骤标题更具体；
    正文上限与 `plugin_artifact.ck_inline_size` 同口径，超了写对象存储。
    """
    payload = _render_payload(
        {
            "title": "兜底标题",
            "inputs": {"doc_type": "公文"},
            "prior": {"a": "第一段", "b": ["非字符串"], "c": 5},
        }
    )
    assert payload["title"] == "公文"
    assert payload["content"] == "第一段"
    assert payload["sections"] == []

    fallback = _render_payload({"title": "兜底标题", "prior": {}})
    assert fallback["title"] == "兜底标题"

    truncated = _render_payload({"prior": {"a": "x" * (MAX_INLINE_TEXT + 100)}})
    assert len(truncated["content"]) == MAX_INLINE_TEXT


# ===========================================================================
# summarize（本地聚合）
# ===========================================================================
async def test_summarize_lists_prior_keys(gateway: ServicesActionGateway) -> None:
    """汇总列出前序产物键（数量 + 顿号连接），无下游调用。

    对外表现是一段业务语言摘要进 `data["summary"]`；本地聚合不调任何
    服务，所以这一支永远不该触发下游替身（也无从触发）。
    """
    result = await gateway.invoke(
        _ctx(), "summarize", {"prior": {"draft": "稿件内容", "review": "评审意见"}}
    )

    assert result.status == "ok"
    assert result.data == {"summary": "本次共生成 2 项结果：draft、review"}
    assert result.artifacts == ()


async def test_summarize_without_prior_returns_empty_wording(
    gateway: ServicesActionGateway,
) -> None:
    """没有前序产物时明说"本次没有生成结果"，不编造汇总。

    与有产物分支的措辞必须不同：把空汇总写成"共生成 0 项"会让用户
    以为系统数错了；`prior` 为 `None` 与 `{}` 走同一支。
    """
    for params in ({}, {"prior": None}):
        result = await gateway.invoke(_ctx(), "summarize", params)
        assert result.data == {"summary": "本次没有生成结果"}


# ===========================================================================
# 纯函数：_auth_of / _session_id
# ===========================================================================
def test_auth_of_only_passes_real_auth_context() -> None:
    """只有真 `AuthContext` 才透传；`None` 与裸字符串都按无身份处理。

    透传一个非身份对象会让下游按"有身份"路径解析凭据——
    那是把字符串当令牌用；这里的选择是宁可无身份（下游拒绝）。
    """
    ctx = _ctx()
    assert _auth_of(ctx) is ctx.auth
    assert _auth_of(_ctx(auth=None)) is None
    assert _auth_of(_ctx(auth="raw-token-string")) is None


def test_session_id_prefixes_task_id_and_truncates() -> None:
    """内部会话 ID = `task_` 前缀 + 任务 ID，总长封顶 128。

    前缀保证不与用户会话混淆（会话列表里一眼可辨）；截断保证
    `conversation_id` 列（varchar(128)）不因超长任务 ID 而插入失败。
    """
    assert _session_id(_ctx(task_id="01HABC")) == "task_01HABC"

    session = _session_id(_ctx(task_id="t" * 300))
    assert session.startswith("task_")
    assert len(session) == 128


# ===========================================================================
# InertActionGateway（测试门面，与生产实现同判据）
# ===========================================================================
def test_inert_gateway_resolves_and_rejects() -> None:
    """惰性门面的白名单判据与生产实现一致（同放行、同拒绝）。"""
    inert = InertActionGateway()
    assert inert.resolve("tool") == "tools.execute"

    with pytest.raises(ApiError) as excinfo:
        inert.resolve("surprise")
    assert int(excinfo.value.code) == int(ErrorCode.CONFIG_ERROR)


async def test_inert_gateway_returns_fixed_result() -> None:
    """惰性门面无视入参返回固定结果；缺省结果是成功空结果。"""
    fixed = ActionResult(status="skipped", reason_text="占位结果")
    inert = InertActionGateway(fixed)

    got = await inert.invoke(_ctx(), "compose", {"anything": 1})
    assert got is fixed

    default = await InertActionGateway().invoke(_ctx(), "compose", {})
    assert default.status == "ok"
    assert default.artifacts == ()


# ===========================================================================
# 构造：配置注入与全局回落
# ===========================================================================
def test_gateway_uses_explicit_or_global_settings() -> None:
    """显式配置优先，未给时回落全局单例（启动期两种装配都要成立）。"""
    explicit = Settings(env="test", mock_mode=True)
    assert ServicesActionGateway(explicit)._settings is explicit  # noqa: SLF001
    assert ServicesActionGateway()._settings is get_settings()  # noqa: SLF001
