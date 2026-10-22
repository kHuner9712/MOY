"""模型服务协议转换单元测试（架构文档 3.1 / 3.1.6）。"""

from __future__ import annotations

import pytest

from app.clients.dify_client import DifyClient, DifyResponse
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.security import AuthContext
from app.schemas.model import (
    ChatMessage,
    FinishReason,
    GenerateRequest,
    MessageRole,
    ModelProfile,
    ProfileStatus,
)
from app.services.model_service import ModelService, new_generation_id

pytestmark = pytest.mark.unit


def _request(profile: ModelProfile = ModelProfile.GENERAL) -> GenerateRequest:
    """构造生成请求。

    Args:
        profile: 能力档位。

    Returns:
        GenerateRequest: 生成请求。
    """
    return GenerateRequest(
        conversation_id="conv_01K4Q8Z2X7M9B3N5P6R8T0V2W4",
        profile=profile,
        messages=[ChatMessage(role=MessageRole.USER, content="帮我整理这段内容")],
    )


def _service(settings: Settings | None = None) -> ModelService:
    """构造桩模式模型服务。

    Args:
        settings: 可选配置实例。

    Returns:
        ModelService: 模型服务实例。
    """
    return ModelService(settings=settings or Settings(downstream_enabled=False))


def test_generation_id_prefix() -> None:
    """生成 ID 必须使用 gen_ 前缀（架构文档 9.1）。"""
    assert new_generation_id().startswith("gen_")


def test_resolve_route_returns_registered_route() -> None:
    """档位解析命中注册表。"""
    route = _service().resolve_route(ModelProfile.CODE)
    assert route.profile == "code"
    assert route.display_name == "编程助手"


def test_resolve_route_rejects_unknown_profile() -> None:
    """未注册档位返回 10004。"""
    from app.registry.loader import ProfileRoute, ProfileRouteTable

    empty = ProfileRouteTable(version=1, routes=())
    service = ModelService(settings=Settings(), routes=empty)
    with pytest.raises(ApiError) as exc_info:
        service.resolve_route(ModelProfile.GENERAL)
    assert exc_info.value.code == ErrorCode.MODEL_PROFILE_UNAVAILABLE
    assert isinstance(empty.routes, tuple)
    assert ProfileRoute is not None


def test_resolve_route_rejects_unavailable_profile() -> None:
    """状态非 available 的档位返回 10004。"""
    from app.registry.loader import ProfileRoute, ProfileRouteTable

    table = ProfileRouteTable(
        version=1,
        routes=(
            ProfileRoute(
                profile="general",
                display_name="通用助手",
                description="",
                max_context_turns=20,
                status="unavailable",
                default_app_env="OA_DIFY_DEFAULT_APP_KEY",
            ),
        ),
    )
    service = ModelService(settings=Settings(), routes=table)
    with pytest.raises(ApiError) as exc_info:
        service.resolve_route(ModelProfile.GENERAL)
    assert exc_info.value.code == ErrorCode.MODEL_PROFILE_UNAVAILABLE


def test_get_catalog_exposes_four_profiles() -> None:
    """M3 目录必须返回四个契约档位且不暴露真实模型名。"""
    catalog = _service().get_catalog()
    assert len(catalog.profiles) == 4
    assert {item.profile.value for item in catalog.profiles} == {
        "general",
        "code",
        "long_context",
        "reasoning",
    }
    assert all(item.status == ProfileStatus.AVAILABLE for item in catalog.profiles)
    serialized = catalog.model_dump_json()
    for leaked in ("doubao", "qwen", "deepseek", "gpt", "claude"):
        assert leaked not in serialized.lower()


async def test_generate_stub_returns_business_units() -> None:
    """桩模式生成返回业务化用量字段（units 而非 token）。"""
    result = await _service().generate(_request())
    assert result.content
    assert result.finish_reason == FinishReason.STOP
    assert result.usage.total_units == result.usage.prompt_units + result.usage.completion_units
    assert "token" not in result.model_dump_json().lower()


def test_transform_generate_response_maps_native_fields() -> None:
    """底座原生字段被重写为自研字段（形态防火墙）。"""
    service = _service()
    payload = {
        "event": "message",
        "answer": "改写后的内容",
        "metadata": {"usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}},
        "retriever_resources": [
            {
                "document_id": "doc_1",
                "document_name": "员工手册.pdf",
                "content": "年假申请需提前三个工作日提交",
                "score": 0.92,
            }
        ],
    }
    result = service._transform_generate_response(payload)  # noqa: SLF001 - 验证转换函数
    assert result.content == "改写后的内容"
    assert result.usage.prompt_units == 100
    assert result.usage.completion_units == 20
    assert result.usage.total_units == 120
    assert result.citations[0].document_name == "员工手册.pdf"
    assert result.citations[0].segment_content == "年假申请需提前三个工作日提交"
    serialized = result.model_dump_json()
    for leaked in ("answer", "retriever_resources", "total_tokens"):
        assert leaked not in serialized


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("context length exceeded", ErrorCode.MODEL_CONTEXT_OVERFLOW),
        ("max_tokens limit reached", ErrorCode.MODEL_CONTEXT_OVERFLOW),
        ("sensitive content detected", ErrorCode.MODEL_CONTENT_BLOCKED),
        ("risk control triggered", ErrorCode.MODEL_CONTENT_BLOCKED),
        ("insufficient quota", ErrorCode.MODEL_QUOTA_EXHAUSTED),
        ("something went wrong", ErrorCode.MODEL_GENERATE_FAILED),
    ],
)
def test_map_downstream_event_error(message: str, expected: ErrorCode) -> None:
    """底座 error 事件按语义映射为自研错误码。"""
    service = _service()
    exc = service._map_downstream_event_error({"code": "x", "message": message})  # noqa: SLF001
    assert exc.code == expected
    from app.core.errors import ERROR_REGISTRY

    assert exc.to_response().message == ERROR_REGISTRY[expected].message


@pytest.mark.parametrize(
    ("native_event", "expected_frame"),
    [
        ("message", "delta"),
        ("agent_message", "delta"),
        ("message_file", "citation"),
        ("message_end", "done"),
        ("error", "error"),
    ],
)
def test_transform_stream_event_rewrites_event_names(
    native_event: str, expected_frame: str
) -> None:
    """底座原生事件名必须被重写为自研事件名。"""
    service = _service()
    event = {"event": native_event, "answer": "文本", "filename": "a.pdf", "content": "片段"}
    frame = service._transform_stream_event(event)  # noqa: SLF001
    assert frame is not None
    assert frame.startswith(f"event: {expected_frame}\n")


def test_transform_stream_event_ignores_unknown_event() -> None:
    """未映射事件不下发。"""
    service = _service()
    assert service._transform_stream_event({"event": "ping"}) is None  # noqa: SLF001


def test_transform_stream_event_maps_error_with_rewritten_code() -> None:
    """error 事件必须重写 code 与 message，不透出底座形态。"""
    service = _service()
    frame = service._transform_stream_event(  # noqa: SLF001
        {"event": "error", "code": "500", "message": "internal server error"}
    )
    assert frame is not None
    assert "10001" in frame
    assert "AI 暂时没有给出回答，请重试" in frame
    assert "internal server error" not in frame


async def test_generate_stream_stub_emits_start_delta_done() -> None:
    """桩模式流式序列必须包含 start/delta/done 三类自研事件。"""
    frames = [frame async for frame in _service().generate_stream(_request())]
    names = [frame.split("\n", 1)[0] for frame in frames]
    assert names[0] == "event: start"
    assert "event: delta" in names
    assert names[-1] == "event: done"
    assert not any("dify" in frame.lower() for frame in frames)


async def test_generate_stream_emits_error_frame_for_bad_profile() -> None:
    """档位不可用时以 error 帧下发而非抛出异常。"""
    from app.registry.loader import ProfileRouteTable

    service = ModelService(settings=Settings(), routes=ProfileRouteTable(version=1, routes=()))
    frames = [frame async for frame in service.generate_stream(_request())]
    assert len(frames) == 1
    assert frames[0].startswith("event: error")
    assert "10004" in frames[0]


def test_build_downstream_payload_uses_native_contract() -> None:
    """构造底座原生请求体（仅服务层允许出现原生字段名）。

    T02 实联调修复 INT-04a 后的契约：
    - conversation_id 必须为 None —— Dify 只接受 null 或其签发的 UUID，
      透传内部 conv_xxx 会 400；
    - 多轮历史转写为中立对话转录并入 query（Dify chat 仅接受单条 query）；
    - user 为必填终端用户标识。
    """
    request = _request()
    # 真实多轮序列：用户 → 助手 → 用户（最后一条永远是要回答的新问题）
    request.messages.append(ChatMessage(role=MessageRole.ASSISTANT, content="上一轮回答"))
    request.messages.append(ChatMessage(role=MessageRole.USER, content="再补充一点"))
    payload = _service()._build_downstream_payload(request, "usr_1")  # noqa: SLF001
    assert payload["response_mode"] == "blocking"
    assert payload["conversation_id"] is None
    assert payload["user"] == "usr_1"
    assert payload["inputs"] == {}
    # 最后一条用户消息必须完整保留在 query 中
    assert "再补充一点" in payload["query"]
    # 多轮历史以中立转录形式并入（适配层不做语义提示词拼装）
    assert "上一轮回答" in payload["query"]
    assert "帮我整理这段内容" in payload["query"]


def test_build_downstream_payload_single_turn_no_transcript() -> None:
    """单轮对话不附加历史转录段（INT-04a）。"""
    request = _request()
    payload = _service()._build_downstream_payload(request, "usr_1")  # noqa: SLF001
    assert payload["query"] == "帮我整理这段内容"
    assert payload["conversation_id"] is None


async def test_get_usage_rejects_unknown_period() -> None:
    """非法统计周期返回 20001。"""
    auth = AuthContext(user_id="usr_1", tenant_id="tnt_1")
    with pytest.raises(ApiError) as exc_info:
        await _service().get_usage(auth, period="year")
    assert exc_info.value.code == ErrorCode.PARAM_INVALID


async def test_get_usage_returns_business_fields_only() -> None:
    """用量统计仅返回业务化口径，不含 token 精确值（B07 起改读 UsageService）。"""
    auth = AuthContext(user_id="usr_1", tenant_id="tnt_1")
    data = await _service().get_usage(auth, period="month")
    serialized = data.model_dump_json()
    assert data.period == "month"
    assert "token" not in serialized.lower()
    # 无订阅时额度总量为 0（免费版额度由 B09 落地），已用额度来自同一聚合表
    assert data.quota.total_units == 0
    assert data.quota.used_units == 0
    assert data.quota_unit


def test_native_paths_only_in_clients_and_services() -> None:
    """底座原生路径只允许出现在 clients/ 与 services/（架构文档 6.2）。"""
    import pathlib

    native_markers = ("/v1/chat-messages", "/v1/datasets", "/v1/workflows", "/v1/files/upload")
    app_root = pathlib.Path(__file__).resolve().parents[2] / "app"
    offenders: list[str] = []
    for path in app_root.rglob("*.py"):
        relative = path.relative_to(app_root).as_posix()
        if relative.startswith(("clients/", "services/")):
            continue
        content = path.read_text(encoding="utf-8")
        if any(marker in content for marker in native_markers):
            offenders.append(relative)
    assert offenders == []


def test_dify_base_url_only_configured_in_core_config() -> None:
    """底座地址只在 core/config.py 与 clients/dify_client.py 出现（架构文档 1.4）。"""
    import pathlib

    app_root = pathlib.Path(__file__).resolve().parents[2] / "app"
    allowed = {"core/config.py", "clients/dify_client.py"}
    offenders: list[str] = []
    for path in app_root.rglob("*.py"):
        relative = path.relative_to(app_root).as_posix()
        if relative in allowed:
            continue
        if "dify-api" in path.read_text(encoding="utf-8"):
            offenders.append(relative)
    assert offenders == []


def test_service_uses_configured_client() -> None:
    """服务可注入自定义底座客户端（T02 联调使用）。"""
    client = DifyClient("http://example.invalid", "app-test")
    service = ModelService(settings=Settings(), dify=client)
    assert service._dify is client  # noqa: SLF001 - 验证依赖注入


def test_reasoning_filter_strips_think_blocks() -> None:
    """INT-06 回归：推理模型思考内容不得透传给用户（技术黑盒化红线）。"""
    from app.services.model_service import _ReasoningFilter

    flt = _ReasoningFilter()
    out = []
    # 模拟跨 delta 的思考 + 正文流
    for chunk in [
        "<thi",
        "nk>思考中",
        "...</think>",
        "正文第一段",
        "<!--dify-deepseek-reasoning-->",
        "，继续",
    ]:
        out.append(flt.feed(chunk))
    out.append(flt.flush())
    visible = "".join(out)
    assert "思考中" not in visible
    assert "<think>" not in visible
    assert "dify-deepseek-reasoning" not in visible
    assert "正文第一段" in visible
    assert "继续" in visible


def test_reasoning_filter_passthrough_normal_text() -> None:
    """无思考标记的普通文本原样输出（不误伤）。"""
    from app.services.model_service import _ReasoningFilter

    flt = _ReasoningFilter()
    visible = flt.feed("你好，我是麦子。") + flt.flush()
    assert visible == "你好，我是麦子。"


# ---------------------------------------------------------------------------
# B27 回归：M2 问答**不走** R7 `RagService.search()`
#
# 为什么必须钉住：`11005` 接线落在 `RagService.search()` 的**故障路径**上。
# 若 M2 问答（带知识库的对话）也走同一个 `search()`，那条接线会让「问答」
# 从"AI 没给出回答，请重试"变成"检索服务暂时不可用"，并把 HTTP 语义从
# 200 之外的路径整体挪动——即把 P1 故障扩成 P0。
# 事实是：M2 走 `model_service` 直连底座 chat（引用来自响应里的
# `retriever_resources`），与 R7 是两条独立链路。本用例用「search 一旦被调用
# 就断言失败」把这条边界固定下来——不写这条等于没防住。
# ---------------------------------------------------------------------------


class _ChatDify:
    """只实现 blocking chat `post` 的假底座（M2 直连链路）。

    Attributes:
        paths: 被调用过的下游路径（用于断言"打的是 chat，不是 retrieve"）。
    """

    def __init__(self) -> None:
        """初始化假底座。"""
        self.paths: list[str] = []

    async def post(
        self,
        path: str,
        payload: dict[str, object],
        *,
        idempotent: bool = False,
        api_key: str | None = None,
    ) -> DifyResponse:
        """返回一条带引用片段的 blocking 响应。

        Args:
            path: 下游路径。
            payload: 请求体（本假实现不使用）。
            idempotent: 是否允许重试（本假实现不使用）。
            api_key: 下游密钥（本假实现不使用）。

        Returns:
            DifyResponse: 形如底座 chat 的响应体。
        """
        del payload, idempotent, api_key
        self.paths.append(path)
        return DifyResponse(
            200,
            {
                "event": "message",
                "answer": "年假需提前三个工作日申请。",
                "metadata": {
                    "usage": {"prompt_tokens": 12, "completion_tokens": 9, "total_tokens": 21}
                },
                "retriever_resources": [
                    {
                        "document_id": "doc_m2",
                        "document_name": "员工手册.pdf",
                        "content": "年假申请需提前三个工作日提交",
                        "score": 0.91,
                        "dataset_id": "m2",
                        "position": {"page": 3, "paragraph": 1},
                    }
                ],
            },
        )


async def test_m2_chat_with_knowledge_base_does_not_use_rag_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M2 带知识库问答走 chat 直连，绝不调用 R7 `search()`（11005 接线边界）。"""
    from app.db.repositories.knowledge_base_repo import (
        KnowledgeBaseRecord,
        get_knowledge_base_store,
    )
    from app.services.rag_service import RagService

    auth = AuthContext(user_id="usr_1", tenant_id="tnt_1")
    settings = Settings(downstream_enabled=True)
    await get_knowledge_base_store(settings).create(
        KnowledgeBaseRecord(
            tenant_id=auth.tenant_id,
            base_id="kb_m2",
            name="制度库",
            visibility="organization",
            owner_user_id=auth.user_id,
        )
    )

    async def _forbidden_search(*args: object, **kwargs: object) -> object:
        """R7 检索一旦被调用即判失败（M2 不得经过它）。"""
        raise AssertionError("M2 问答不得调用 RagService.search（R7 独立检索）")

    monkeypatch.setattr(RagService, "search", _forbidden_search)

    dify = _ChatDify()
    service = ModelService(settings=settings, dify=dify)  # type: ignore[arg-type]
    data = await service.generate(
        GenerateRequest(
            conversation_id="conv_01K4Q8Z2X7M9B3N5P6R8T0V2W4",
            profile=ModelProfile.GENERAL,
            messages=[ChatMessage(role=MessageRole.USER, content="年假怎么申请？")],
            knowledge_base_ids=["kb_m2"],
        ),
        user_id=auth.user_id,
        auth=auth,
    )

    # 走的是 chat 路径（不是 /retrieve）
    assert dify.paths == ["/v1/chat-messages"]
    assert data.content == "年假需提前三个工作日申请。"
    assert data.usage.total_units == 21
    # 引用来自 chat 响应的 retriever_resources（与 R7 无关）
    assert len(data.citations) == 1
    assert data.citations[0].document_name == "员工手册.pdf"
    assert data.citations[0].base_id == "kb_m2"
