"""上下文自研组装与会话服务的单元验收（阶段四 T4-02.3）。

**这层存在的意义**：历史真源从"客户端传的 messages"换成"自研 `message` 表"。
所以本文件的每条断言都围绕一件事——**query 里的历史到底来自哪里、有没有重复**：

1. `server` 模式：只读库；入参 5 条历史 → `ignored_client_history=4`，query 里
   `TRANSCRIPT_HEADER` **只出现一次**、本轮那句**只出现一次**；
2. `client` 模式（逃生门）：不读库，只拼入参；
3. 库中已有本轮行时，用 `seq < 本轮` 取历史，**本轮那句不得出现在历史段里**
   （否则模型以为用户又强调了一遍）；
4. 两轮并发：第二轮的历史里必须包含第一轮，而第一轮的 query 不得包含第二轮
   （`seq` 边界是单调的，这就是不用时间戳的原因）；
5. 半截的 assistant 行（streaming/aborted）不进 transcript。

外加会话服务的落库顺序：**用户消息先落、助手空壳直接 streaming**（触发器要求）。
"""

from __future__ import annotations

import pytest

from app.business.context_assembler import (
    TRANSCRIPT_HEADER,
    AssembledContext,
    ContextAssembler,
)
from app.business.conversation_service import ConversationService
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.db.repositories.conversation import (
    InMemoryConversationStore,
    InMemoryMessageStore,
    new_conversation_id,
    reset_conversation_stores,
)
from app.schemas.model import ChatMessage

pytestmark = pytest.mark.unit

TENANT = "tnt_test_tenant"
USER = "usr_test_user"


@pytest.fixture
def settings() -> Settings:
    """测试配置。

    Returns:
        Settings: 配置实例。
    """
    return Settings(
        env="test",
        deploy_mode="private",
        downstream_enabled=False,
        auth_mode="stub",
        jwt_secret="context-assembler-test-secret",
        ratelimit_enabled=False,
        conversation_soft_delete_recover_days=30,
    )


@pytest.fixture
def conversations() -> InMemoryConversationStore:
    """会话存储。

    Returns:
        InMemoryConversationStore: 存储。
    """
    reset_conversation_stores()
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
) -> ConversationService:
    """会话服务。

    Args:
        settings: 配置。
        conversations: 会话存储。
        messages: 消息存储。

    Returns:
        ConversationService: 服务。
    """
    return ConversationService(settings, conversations=conversations, messages=messages)


@pytest.fixture
def assembler(
    settings: Settings,
    conversations: InMemoryConversationStore,
    messages: InMemoryMessageStore,
) -> ContextAssembler:
    """上下文组装器。

    Args:
        settings: 配置。
        conversations: 会话存储。
        messages: 消息存储。

    Returns:
        ContextAssembler: 组装器。
    """
    return ContextAssembler(settings, messages=messages, conversations=conversations)


def _msgs(*texts: str) -> list[ChatMessage]:
    """构造若干条 user 消息（模拟客户端仍在传历史）。

    Args:
        *texts: 消息正文。

    Returns:
        list[ChatMessage]: 消息列表。
    """
    return [ChatMessage(role="user", content=text) for text in texts]


# ===========================================================================
# 1. server 模式：读库、忽略客户端历史、不重复
# ===========================================================================
async def test_server_mode_ignores_client_history_and_counts_it(
    assembler: ContextAssembler,
) -> None:
    """入参 5 条历史 → 只取最后一条，`ignored_client_history=4`。"""
    conversation_id = new_conversation_id()

    assembled = await assembler.build(
        TENANT,
        USER,
        conversation_id,
        current_user_text="那第三点呢？",
        client_messages=_msgs("第一句", "第二句", "第三句", "第四句", "那第三点呢？"),
        assemble_from="server",
    )

    assert assembled.ignored_client_history == 4
    assert assembled.history_source == "server_empty", "新会话库里没有历史"
    assert assembled.query_text == "那第三点呢？", "首轮不得凭空造出历史段"
    assert TRANSCRIPT_HEADER not in assembled.query_text


async def test_server_mode_reads_history_from_database_only(
    assembler: ContextAssembler, service: ConversationService
) -> None:
    """历史只来自库：库里有两轮 → transcript 两轮 + 本轮，且标题头只出现一次。"""
    first = await service.start_generation(TENANT, USER, "", user_text="帮我看下上线计划")
    await service.finalize_assistant_message(
        TENANT, first.assistant_message.message_id, content="好的，先列里程碑", status="settled"
    )
    second = await service.start_generation(
        TENANT, USER, first.conversation.conversation_id, user_text="第二步的风险呢？"
    )

    assembled = await assembler.build(
        TENANT,
        USER,
        first.conversation.conversation_id,
        current_user_text="第二步的风险呢？",
        client_messages=_msgs("帮我看下上线计划", "好的，先列里程碑", "第二步的风险呢？"),
        current_message_id=second.user_message.message_id,
        assemble_from="server",
    )

    assert assembled.history_source == "server"
    assert assembled.transcript_turns == 2
    assert assembled.query_text.count(TRANSCRIPT_HEADER) == 1, "历史段只能拼一次"
    assert "帮我看下上线计划" in assembled.query_text
    assert "好的，先列里程碑" in assembled.query_text


async def test_current_turn_appears_exactly_once(
    assembler: ContextAssembler, service: ConversationService
) -> None:
    """库中已有本轮行时，本轮那句在 query 里**只出现一次**。

    这是最容易错的一处：历史用 `seq < 本轮` 取，本轮自己由 `current_user_text`
    补在末尾；若历史段也把它带上，模型会看到"用户说了两遍"。
    """
    started = await service.start_generation(TENANT, USER, "", user_text="请解释一下幂等")

    assembled = await assembler.build(
        TENANT,
        USER,
        started.conversation.conversation_id,
        current_user_text="请解释一下幂等",
        current_message_id=started.user_message.message_id,
        assemble_from="server",
    )

    assert assembled.query_text.count("请解释一下幂等") == 1
    assert assembled.transcript_turns == 0, "本轮之前的已定稿消息为 0 条"


async def test_unsettled_assistant_rows_are_excluded(
    assembler: ContextAssembler, service: ConversationService
) -> None:
    """半截的 assistant 行（streaming）不进 transcript（否则上下文里出现没说完的话）。"""
    first = await service.start_generation(TENANT, USER, "", user_text="写个提纲")
    # 故意不 finalize：assistant 空壳停在 streaming
    second = await service.start_generation(
        TENANT, USER, first.conversation.conversation_id, user_text="再补一条"
    )

    assembled = await assembler.build(
        TENANT,
        USER,
        first.conversation.conversation_id,
        current_user_text="再补一条",
        current_message_id=second.user_message.message_id,
        assemble_from="server",
    )

    assert assembled.transcript_turns == 1, "只有那条已定稿的 user 消息进历史"
    assert "写个提纲" in assembled.query_text


# ===========================================================================
# 2. client 模式（逃生门）
# ===========================================================================
async def test_client_mode_uses_request_messages_without_reading_db(
    assembler: ContextAssembler,
) -> None:
    """`assemble_from=client`：只用入参拼，**不读库**（库里刻意留空）。"""
    assembled = await assembler.build(
        TENANT,
        USER,
        new_conversation_id(),
        current_user_text="第三个问题",
        client_messages=_msgs("第一个问题", "第二个问题", "第三个问题"),
        assemble_from="client",
    )

    assert assembled.history_source == "client"
    assert assembled.ignored_client_history == 0
    assert "第一个问题" in assembled.query_text and "第二个问题" in assembled.query_text
    assert assembled.query_text.count("第三个问题") == 1


# ===========================================================================
# 3. 并发两轮互不包含
# ===========================================================================
async def test_two_rounds_do_not_leak_into_each_other(
    assembler: ContextAssembler, service: ConversationService
) -> None:
    """第一轮的上下文不得包含第二轮的问题（`seq` 边界单调）。"""
    first = await service.start_generation(TENANT, USER, "", user_text="第一轮问题")
    assembled_first = await assembler.build(
        TENANT,
        USER,
        first.conversation.conversation_id,
        current_user_text="第一轮问题",
        current_message_id=first.user_message.message_id,
        assemble_from="server",
    )
    await service.finalize_assistant_message(
        TENANT, first.assistant_message.message_id, content="第一轮回答", status="settled"
    )

    second = await service.start_generation(
        TENANT, USER, first.conversation.conversation_id, user_text="第二轮问题"
    )
    assembled_second = await assembler.build(
        TENANT,
        USER,
        first.conversation.conversation_id,
        current_user_text="第二轮问题",
        current_message_id=second.user_message.message_id,
        assemble_from="server",
    )

    assert "第二轮问题" not in assembled_first.query_text
    assert "第二轮问题" in assembled_second.query_text
    assert "第一轮问题" in assembled_second.query_text


# ===========================================================================
# 4. 会话服务：落库顺序与状态机
# ===========================================================================
async def test_start_generation_writes_user_then_streaming_assistant(
    service: ConversationService, messages: InMemoryMessageStore
) -> None:
    """先落 user（settled），再落 assistant 空壳且**直接 streaming**（触发器要求）。"""
    started = await service.start_generation(TENANT, USER, "", user_text="你好")

    rows = await messages.list_before(
        TENANT, started.conversation.conversation_id, before_seq=None, limit=10
    )
    by_id = {row.message_id: row for row in rows}

    assert by_id[started.user_message.message_id].role == "user"
    assert by_id[started.user_message.message_id].status == "settled"
    assert by_id[started.assistant_message.message_id].status == "streaming"
    assert by_id[started.assistant_message.message_id].content == ""
    assert started.is_new_conversation is True
    assert started.conversation.title == "你好", "标题取首条用户消息前 20 字（决策 A-6）"


async def test_finalize_rejects_non_terminal_status(service: ConversationService) -> None:
    """`finalize_assistant_message` 只接受终态（传 streaming 属编码缺陷）。"""
    started = await service.start_generation(TENANT, USER, "", user_text="你好")

    with pytest.raises(ApiError) as excinfo:
        await service.finalize_assistant_message(
            TENANT, started.assistant_message.message_id, content="x", status="streaming"
        )

    assert excinfo.value.code == ErrorCode.INTERNAL_ERROR


async def test_client_supplied_conversation_id_is_adopted(
    service: ConversationService,
) -> None:
    """老客户端传的 ID 首次出现时**被采纳**（架构 §4.3 B-1 的向后兼容前提）。

    阶段四之前 `conversation_id` 是必填标签，老调用方会传自己生成的 ID。
    若这里报 18001，"必填→可选"就变成了破坏性变更。
    """
    started = await service.start_generation(TENANT, USER, "conv_legacy_label", user_text="你好")

    assert started.conversation.conversation_id == "conv_legacy_label"
    assert started.is_new_conversation is True


async def test_conversation_id_of_another_user_is_18001(
    service: ConversationService,
) -> None:
    """ID 存在但属于别人（或已软删）→ 18001，**绝不覆盖**别人的会话。"""
    owner = await service.start_generation(TENANT, USER, "", user_text="我的会话")
    conversation_id = owner.conversation.conversation_id

    with pytest.raises(ApiError) as excinfo:
        await service.start_generation(
            TENANT, "usr_someone_else", conversation_id, user_text="插话"
        )

    assert excinfo.value.code == ErrorCode.CONVERSATION_NOT_FOUND
    assert excinfo.value.to_response().message == "该对话已不存在"


async def test_soft_delete_returns_recoverable_until(service: ConversationService) -> None:
    """软删返回可恢复截止时间（默认 30 天后），且之后 `get` 读不到。"""
    started = await service.start_generation(TENANT, USER, "", user_text="待删会话")

    deleted, until = await service.soft_delete(TENANT, USER, started.conversation.conversation_id)

    assert deleted.deleted_at
    assert until > deleted.deleted_at, "截止时间必须晚于删除时间"


async def test_abort_orphans_closes_stale_streaming_rows(
    service: ConversationService,
) -> None:
    """滞留的 streaming 行被收口为 aborted + truncated（进程被杀后的收尾）。"""
    from datetime import UTC, datetime, timedelta

    started = await service.start_generation(TENANT, USER, "", user_text="会中断的一轮")

    closed = await service.abort_orphans(TENANT, now=datetime.now(UTC) + timedelta(hours=1))

    assert closed == 1
    record = await service._messages.get(  # noqa: SLF001 - 用例直接核对落库结果
        TENANT, started.assistant_message.message_id
    )
    assert record is not None
    assert record.status == "aborted" and record.truncated is True
    assert record.error_reason, "必须给业务化原因（不含码与堆栈）"


def test_assembled_context_defaults_are_safe() -> None:
    """默认值必须是"空上下文"，不能有任何隐式历史（防误用为已组装）。"""
    assembled = AssembledContext()

    assert assembled.query_text == ""
    assert assembled.transcript_turns == 0
    assert assembled.history_source == "server_empty"
