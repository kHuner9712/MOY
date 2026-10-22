"""M1/M2 消息落库的端到端验收（阶段四 T4-02.3，架构 §5.1 / §5.2）。

三条判据都是"用户会立刻看到"的：

1. **一轮对话 = 2 行**（user 一条 + assistant 一条）——少一条意味着刷新后历史缺一半；
2. **上游抛错时**：user 行仍在（用户那句话不能丢）、assistant 落 `failed`、
   `error_reason` 是业务语言（**不含错误码与堆栈**，那是泄漏面）；
3. **不带 `conversation_id`**：响应里回 `conv_`，且第二轮带上它时历史能接上。

另外覆盖流式中断：断开后 assistant 落 `aborted` + `truncated=true`，
**已收到的部分内容保留**（用户看到的与历史里存的必须一致）。
"""

from __future__ import annotations

import pytest

from app.business.context_assembler import reset_context_assembler
from app.business.conversation_service import reset_conversation_service
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.security import AuthContext
from app.db.repositories.conversation_repo import (
    InMemoryConversationStore,
    InMemoryMessageStore,
    reset_conversation_stores,
)
from app.schemas.model import ChatMessage, GenerateRequest, ModelProfile
from app.services.model_service import ModelService

pytestmark = pytest.mark.integration

TENANT = "tnt_test_tenant"
USER = "usr_test_user"


@pytest.fixture
def settings() -> Settings:
    """桩模式配置（不产生真实下游请求）。

    Returns:
        Settings: 配置实例。
    """
    reset_conversation_stores()
    reset_context_assembler()
    reset_conversation_service()
    return Settings(
        env="test",
        deploy_mode="private",
        downstream_enabled=False,
        auth_mode="stub",
        jwt_secret="message-persistence-test-secret",
        ratelimit_enabled=False,
    )


@pytest.fixture
def conversations() -> InMemoryConversationStore:
    """会话存储。

    Returns:
        InMemoryConversationStore: 存储。
    """
    return InMemoryConversationStore()


@pytest.fixture
def messages() -> InMemoryMessageStore:
    """消息存储。

    Returns:
        InMemoryMessageStore: 存储。
    """
    return InMemoryMessageStore()


@pytest.fixture
def service(
    settings: Settings,
    conversations: InMemoryConversationStore,
    messages: InMemoryMessageStore,
) -> ModelService:
    """模型服务（会话/组装器按注入的存储构造）。

    Args:
        settings: 配置。
        conversations: 会话存储。
        messages: 消息存储。

    Returns:
        ModelService: 服务。
    """
    from app.business.context_assembler import ContextAssembler
    from app.business.conversation_service import ConversationService

    return ModelService(
        settings,
        conversations=ConversationService(settings, conversations=conversations, messages=messages),
        assembler=ContextAssembler(settings, messages=messages, conversations=conversations),
    )


def _auth(user_id: str = USER) -> AuthContext:
    """构造鉴权上下文（租户与用户都**来自令牌**，不是请求参数）。

    Args:
        user_id: 终端用户 ID。

    Returns:
        AuthContext: 鉴权上下文。
    """
    return AuthContext(user_id=user_id, tenant_id=TENANT)


def _request(conversation_id: str = "", *texts: str) -> GenerateRequest:
    """构造 M1 请求。

    Args:
        conversation_id: 会话 ID（空则服务端生成）。
        *texts: 消息正文（依次为 user 消息）。

    Returns:
        GenerateRequest: 请求。
    """
    return GenerateRequest(
        conversation_id=conversation_id,
        profile=ModelProfile.GENERAL,
        messages=[ChatMessage(role="user", content=text) for text in texts],
    )


async def _rows(messages: InMemoryMessageStore, conversation_id: str) -> list:
    """取某会话的全部消息（时间正序）。

    Args:
        messages: 消息存储。
        conversation_id: 会话 ID。

    Returns:
        list: 消息列表（正序）。
    """
    rows = await messages.list_before(TENANT, conversation_id, before_seq=None, limit=100)
    return list(reversed(rows))


# ===========================================================================
# 1. 一轮 = 2 行
# ===========================================================================
async def test_one_round_persists_two_messages(
    service: ModelService, messages: InMemoryMessageStore
) -> None:
    """一轮对话后 `count(*) == 2`，且 user 是 settled、assistant 也落定。"""
    data = await service.generate(_request("", "帮我梳理上线计划"), user_id=USER, auth=_auth())

    rows = await _rows(messages, data.conversation_id)

    assert len(rows) == 2, f"一轮应该恰好 2 行，实际 {[(r.role, r.status) for r in rows]}"
    assert [row.role for row in rows] == ["user", "assistant"]
    assert rows[0].content == "帮我梳理上线计划"
    assert rows[1].status == "settled"
    assert rows[1].content, "助手正文必须落库（否则刷新后是空白）"


# ===========================================================================
# 2. 上游抛错：user 行在 + assistant failed + 文案无码
# ===========================================================================
async def test_upstream_failure_keeps_user_row_and_marks_assistant_failed(
    settings: Settings,
    conversations: InMemoryConversationStore,
    messages: InMemoryMessageStore,
) -> None:
    """上游失败：user 行保留、assistant 落 failed、`error_reason` 不含码与堆栈。"""
    from app.business.context_assembler import ContextAssembler
    from app.business.conversation_service import ConversationService

    broken = Settings(
        env="test",
        deploy_mode="private",
        downstream_enabled=True,  # 走真实下游分支
        auth_mode="stub",
        jwt_secret="message-persistence-test-secret",
        ratelimit_enabled=False,
        dify_base_url="http://127.0.0.1:1",  # 必然连接失败
        dify_max_retries=0,
        dify_connect_timeout_seconds=0.05,
    )
    service = ModelService(
        broken,
        conversations=ConversationService(broken, conversations=conversations, messages=messages),
        assembler=ContextAssembler(broken, messages=messages, conversations=conversations),
    )

    with pytest.raises(ApiError):
        await service.generate(_request("", "这句话不能丢"), user_id=USER, auth=_auth())

    # 会话是服务端建的，从存储里找出来
    all_rows = [
        row
        for (tid, _cid), row in conversations._items.items()  # noqa: SLF001 - 用例直接核对
        if tid == TENANT
    ]
    assert len(all_rows) == 1
    rows = await _rows(messages, all_rows[0].conversation_id)

    assert [row.role for row in rows] == ["user", "assistant"], "用户那句话必须先落库"
    assert rows[0].content == "这句话不能丢"
    assert rows[1].status == "failed"
    assert rows[1].error_reason, "失败必须给业务化原因"
    assert not any(ch.isdigit() for ch in rows[1].error_reason), (
        f"error_reason 不得含错误码：{rows[1].error_reason}"
    )
    assert "Traceback" not in rows[1].error_reason


# ===========================================================================
# 3. 不给 conversation_id → 服务端生成并在响应里回填
# ===========================================================================
async def test_missing_conversation_id_is_generated_and_returned(
    service: ModelService,
) -> None:
    """不带 `conversation_id` → 响应含 `conv_`，且第二次带上它历史能接上。"""
    first = await service.generate(_request("", "第一个问题"), user_id=USER, auth=_auth())

    assert first.conversation_id.startswith("conv_")
    assert first.history_source == "server_empty", "首轮服务端无历史"

    second = await service.generate(
        _request(first.conversation_id, "第二个问题"), user_id=USER, auth=_auth()
    )

    assert second.conversation_id == first.conversation_id, "同一会话不得换 ID"
    assert second.history_source == "server", "第二轮必须读到服务端历史"
    assert second.ignored_client_history == 0


# ===========================================================================
# 4. 流式：正常收尾 settled / 中断 aborted + 部分内容保留
# ===========================================================================
async def test_stream_completes_and_persists_settled(
    service: ModelService, messages: InMemoryMessageStore
) -> None:
    """M2 桩模式跑完：assistant 落 settled 且正文非空。"""
    frames: list[str] = []
    conversation_id = ""
    async for frame in service.generate_stream(
        _request("", "流式问一句"), user_id=USER, auth=_auth()
    ):
        frames.append(frame)
        if frame.startswith("event: start"):
            import json

            payload = json.loads(frame.partition("data: ")[2])
            conversation_id = payload.get("conversation_id", "")

    assert conversation_id.startswith("conv_")
    rows = await _rows(messages, conversation_id)
    assert [row.role for row in rows] == ["user", "assistant"]
    assert rows[1].status == "settled"
    assert rows[1].content, "流式正文必须落库"


async def test_stream_interruption_marks_aborted_with_partial_content(
    settings: Settings,
    conversations: InMemoryConversationStore,
    messages: InMemoryMessageStore,
) -> None:
    """流式中途断开：assistant 落 `aborted` + `truncated=true`，**部分内容保留**。"""
    from app.business.context_assembler import ContextAssembler
    from app.business.conversation_service import ConversationService

    service = ModelService(
        settings,
        conversations=ConversationService(settings, conversations=conversations, messages=messages),
        assembler=ContextAssembler(settings, messages=messages, conversations=conversations),
    )

    delivered: list[str] = []
    stream = service.generate_stream(_request("", "会被打断的一轮"), user_id=USER, auth=_auth())
    async for frame in stream:
        delivered.append(frame)
        if frame.startswith("event: delta"):
            break  # 客户端断开：只收了第一块就 quit，触发 finally 收口
    await stream.aclose()

    all_rows = [
        row
        for (tid, _cid), row in conversations._items.items()  # noqa: SLF001
        if tid == TENANT
    ]
    rows = await _rows(messages, all_rows[0].conversation_id)

    assert [row.role for row in rows] == ["user", "assistant"]
    assert rows[1].status == "aborted"
    assert rows[1].truncated is True
    assert rows[1].content, "已收到的部分内容必须保留（不能变成空白）"
    assert rows[1].error_reason, "中断也要给业务化原因"


# ===========================================================================
# 5. 组装模式：client 是逃生门，行为与 server 不同
# ===========================================================================
async def test_client_mode_keeps_legacy_behavior(
    service: ModelService, messages: InMemoryMessageStore
) -> None:
    """`assemble_from=client` 时仍按入参拼历史（灰度与既有调用方的逃生门）。"""
    request = _request("", "第一句", "第二句", "第三句")
    request.context.assemble_from = "client"

    data = await service.generate(request, user_id=USER, auth=_auth())

    assert data.history_source == "client"
    assert data.ignored_client_history == 0
    rows = await _rows(messages, data.conversation_id)
    assert len(rows) == 2, "落库不受组装模式影响：仍然是一轮 2 行"


async def test_unknown_conversation_of_other_user_is_rejected(
    service: ModelService,
) -> None:
    """别人的会话 ID → 18001（不覆盖、不泄漏内容）。"""
    mine = await service.generate(_request("", "我的问题"), user_id=USER, auth=_auth())

    with pytest.raises(ApiError) as excinfo:
        await service.generate(
            _request(mine.conversation_id, "插话"),
            user_id="usr_other",
            auth=_auth("usr_other"),
        )

    assert excinfo.value.code == ErrorCode.CONVERSATION_NOT_FOUND
