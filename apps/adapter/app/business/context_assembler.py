"""上下文自研组装（阶段四 T4-02.3，架构 §2.2 / §4.5 / §5.1）。

**为什么必须有这一层（F-1）**：现状是"把客户端传来的 `messages` 拼成一条 query"
（`model_service._build_downstream_payload`）。它有两个死穴：

1. **真源在客户端**：刷新页面即丢、换设备即断；用户改一下网络请求就能伪造历史，
   诱导模型"确认"一件从未发生的事（记忆投毒）；
2. **无处插入记忆**：T4-03 的跨会话记忆要注入到 query 里，而拼接逻辑埋在服务层、
   数据来源是请求体，没有可注入的缝。

本模块把"取数"与"拼接"收进来，只做两件事：

- `assemble_from=server`（**默认**）：历史**只读** `message` 表，条件 `seq < current.seq`
  ——用序号而不是时间戳，因为同一毫秒可能有多条，时间戳做边界会漏/重；
- `assemble_from=client`（逃生门）：只用入参拼 transcript，**不读库**。
  灰度期与既有用例用它，也为"底座侧会话仍有效"的客户保留一条退路（A-5）。

**一个容易做错的细节**：入参 `messages` 里可能有 5 条"历史"，但真正的历史已经在库里。
所以 server 模式下**只取最后一条**（且必须是 `role=user`），其余忽略并计数
（`ignored_client_history`）。不这么做的后果是同一句话在 query 里出现两次，
模型会把它当成"用户又强调了一遍"，回答语气随之改变。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.repositories.conversation_repo import (
    ConversationStore,
    MessageStore,
    get_conversation_store,
    get_message_store,
)
from app.schemas.model import ChatMessage

logger = get_logger(__name__)

#: 组装来源
AssembleSource = Literal["server", "client"]

#: 历史 transcript 的头（与阶段三的拼接文案保持一致，便于对比灰度结果）
TRANSCRIPT_HEADER: Final[str] = "以下是此前的对话记录："

#: 单条历史消息在 transcript 里的角色前缀
ROLE_LABELS: Final[dict[str, str]] = {"user": "用户", "assistant": "助手", "system": "系统"}


@dataclass(slots=True)
class AssembledContext:
    """组装结果。

    Attributes:
        query_text: 最终发给底座的单条 query（历史 + 本轮用户输入）。
        current_user_text: 本轮用户输入原文（单独保留，便于审计与幂等判断）。
        transcript_turns: 参与拼接的历史条数（0 表示首轮）。
        ignored_client_history: 被忽略的客户端历史条数（server 模式下 >0 说明客户端
            仍在传历史，可用于灰度观察，不报错）。
        history_source: `server|server_empty|client`——`server_empty` 表示服务端无历史
            （新会话首轮），三者都要能在日志里区分。
        conversation_id: 本次使用的会话 ID（服务端生成时是新的）。
    """

    query_text: str = ""
    current_user_text: str = ""
    transcript_turns: int = 0
    ignored_client_history: int = 0
    history_source: str = "server_empty"
    conversation_id: str = ""


class ContextAssembler:
    """把「历史 + 本轮输入（+ 记忆）」拼成一条 query。"""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        messages: MessageStore | None = None,
        conversations: ConversationStore | None = None,
    ) -> None:
        """初始化。

        Args:
            settings: 可选配置实例。
            messages: 消息存储（缺省按配置选实现）。
            conversations: 会话存储（缺省按配置选实现）。
        """
        self._settings = settings or get_settings()
        self._messages = messages or get_message_store(self._settings)
        self._conversations = conversations or get_conversation_store(self._settings)

    async def build(
        self,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
        *,
        current_user_text: str,
        client_messages: list[ChatMessage] | None = None,
        current_message_id: str = "",
        history_window: int = 20,
        profile: str = "",
        assemble_from: str = "server",
    ) -> AssembledContext:
        """组装本轮 query。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属人（**必须来自令牌**，不接受请求参数）。
            conversation_id: 会话 ID。
            current_user_text: 本轮用户输入（已落库那条的正文）。
            client_messages: 客户端传来的 messages（server 模式下只用最后一条）。
            current_message_id: 本轮用户消息 ID（用于取 `seq` 边界）。
            history_window: 携带的历史条数上限。
            profile: 能力档位（仅用于日志）。
            assemble_from: `server`（默认）/ `client`（逃生门）。

        Returns:
            AssembledContext: 组装结果。
        """
        text = (current_user_text or "").strip()
        ignored = 0
        if assemble_from == "client":
            transcript = self._transcript_from_client(client_messages or [], text)
            logger.info(
                "context_assembled",
                source="client",
                conversation_id=conversation_id,
                profile=profile,
                turns=len(client_messages or []),
            )
            return AssembledContext(
                query_text=transcript,
                current_user_text=text,
                transcript_turns=max(0, len(client_messages or []) - 1),
                ignored_client_history=0,
                history_source="client",
                conversation_id=conversation_id,
            )

        # server 模式：只取最后一条客户端消息，其余计数丢弃（见模块 docstring）
        if client_messages is not None:
            ignored = max(0, len(client_messages) - 1)

        history = await self._load_history(
            tenant_id,
            conversation_id,
            current_message_id=current_message_id,
            history_window=history_window,
        )
        query = self._compose(history, text)
        source = "server" if history else "server_empty"
        logger.info(
            "context_assembled",
            source=source,
            conversation_id=conversation_id,
            profile=profile,
            turns=len(history),
            ignored_client_history=ignored,
        )
        return AssembledContext(
            query_text=query,
            current_user_text=text,
            transcript_turns=len(history),
            ignored_client_history=ignored,
            history_source=source,
            conversation_id=conversation_id,
        )

    async def _load_history(
        self,
        tenant_id: str,
        conversation_id: str,
        *,
        current_message_id: str,
        history_window: int,
    ) -> list[tuple[str, str]]:
        """读服务端历史（`seq < 本轮`，**只取已定稿**的行）。

        Args:
            tenant_id: 企业 ID。
            conversation_id: 会话 ID。
            current_message_id: 本轮用户消息 ID。
            history_window: 条数上限。

        Returns:
            list[tuple[str, str]]: `(role, content)` 列表，时间正序。
        """
        if not conversation_id or history_window <= 0:
            return []
        before_seq: int | None = None
        if current_message_id:
            current = await self._messages.get(tenant_id, current_message_id)
            before_seq = current.seq if current is not None and current.seq > 0 else None
        rows = await self._messages.list_before(
            tenant_id, conversation_id, before_seq=before_seq, limit=history_window
        )
        # 只带**已定稿**的历史：半截的 assistant 行（streaming/aborted）混进 transcript
        # 会让模型把"没说完的话"当上下文，回答前后矛盾。
        settled = [row for row in rows if row.status == "settled"]
        return [(row.role, row.content) for row in reversed(settled)]

    def _compose(self, history: list[tuple[str, str]], current_text: str) -> str:
        """拼接最终 query（历史在前，本轮输入在最后且**只出现一次**）。

        Args:
            history: 历史 `(role, content)`（时间正序）。
            current_text: 本轮用户输入。

        Returns:
            str: 单条 query。
        """
        if not history:
            return current_text
        lines = [TRANSCRIPT_HEADER]
        for role, content in history:
            label = ROLE_LABELS.get(role, ROLE_LABELS["user"])
            lines.append(f"{label}：{content}")
        lines.append("")
        lines.append(f"用户：{current_text}")
        return "\n".join(lines)

    def _transcript_from_client(self, messages: list[ChatMessage], current_text: str) -> str:
        """逃生门：只用入参拼 transcript（不读库）。

        与 server 模式的**唯一**语义差异是数据来源；拼接形态保持一致，
        否则灰度对比时两个模式的回答没法比。

        Args:
            messages: 客户端消息。
            current_text: 本轮用户输入（为空时取 messages 最后一条）。

        Returns:
            str: 单条 query。
        """
        pairs: list[tuple[str, str]] = [(str(item.role), item.content) for item in messages]
        text = current_text or (pairs[-1][1] if pairs else "")
        if pairs and pairs[-1][0] == "user" and pairs[-1][1] == text:
            pairs = pairs[:-1]
        return self._compose(pairs, text)


#: 组装器单例
_assembler: ContextAssembler | None = None


def get_context_assembler(settings: Settings | None = None) -> ContextAssembler:
    """取组装器单例。

    Args:
        settings: 可选配置实例。

    Returns:
        ContextAssembler: 组装器。
    """
    global _assembler
    conf = settings or get_settings()
    if _assembler is None:
        _assembler = ContextAssembler(conf)
    return _assembler


def reset_context_assembler() -> None:
    """重置组装器单例（测试隔离用）。"""
    global _assembler
    _assembler = None


__all__: list[str] = [
    "TRANSCRIPT_HEADER",
    "AssembleSource",
    "AssembledContext",
    "ContextAssembler",
    "get_context_assembler",
    "reset_context_assembler",
]
