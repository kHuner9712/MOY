"""会话服务（阶段四 T4-02.3，架构 §4.5 / §5.1 / §9 数据落库顺序）。

**落库顺序是硬约束**（架构 §12 第 9 条）：**用户消息先 COMMIT，再调上游**。
反过来（先调上游、成功后再落库）会在上游超时/进程被杀时丢掉用户那句话——
用户看到"消息发出去了但历史里没有"，而这正是最容易被投诉的形态。

**助手消息的状态机**：`streaming → settled / aborted / failed`。
- 空壳**插入时就是 `streaming`**（不是 pending→streaming）：触发器只在
  `OLD.status='streaming'` 时允许改 `content`，见 `0008` 的 `message_guard_content`；
- 断连/超时在 `finally` 里落 `aborted` + `truncated=true`，**已收到的部分内容保留**
  （用户看到的与历史里存的必须一致）；
- 进程被杀留下的 `pending/streaming` 行由启动 reconciler 收口为 `aborted`。

**幂等**：`finalize_assistant_message` 允许重复调用（网络重试、断连补偿都会重复），
仓储层对同终态不做改动。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.db.repositories.conversation_repo import (
    ConversationRecord,
    ConversationStore,
    MessageRecord,
    MessageStore,
    get_conversation_store,
    get_message_store,
    new_conversation_id,
    new_message_id,
    recoverable_until,
)

logger = get_logger(__name__)

#: 标题自动生成时截取的字符数（决策 A-6：不额外调上游生成标题）
TITLE_PREFIX_LENGTH: int = 20

#: 孤儿消息判定阈值（启动 reconciler：超过该时长仍未定稿 → 收口为 aborted）
ORPHAN_AFTER_MINUTES: int = 10


@dataclass(slots=True)
class StartedGeneration:
    """一轮生成的两条消息句柄。

    Attributes:
        conversation: 会话行。
        user_message: 用户消息行（已落库并提交）。
        assistant_message: 助手空壳行（`status='streaming'`）。
        is_new_conversation: 本次是否新建了会话（用于响应里回 `conv_`）。
    """

    conversation: ConversationRecord
    user_message: MessageRecord
    assistant_message: MessageRecord
    is_new_conversation: bool = False


class ConversationService:
    """会话与消息的业务编排。"""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        conversations: ConversationStore | None = None,
        messages: MessageStore | None = None,
    ) -> None:
        """初始化。

        Args:
            settings: 可选配置实例。
            conversations: 会话存储。
            messages: 消息存储。
        """
        self._settings = settings or get_settings()
        self._conversations = conversations or get_conversation_store(self._settings)
        self._messages = messages or get_message_store(self._settings)

    async def ensure_conversation(
        self, tenant_id: str, user_id: str, conversation_id: str, *, title_hint: str = ""
    ) -> tuple[ConversationRecord, bool]:
        """取会话；不存在则新建（`conversation_id` 缺省时服务端生成）。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属人。
            conversation_id: 客户端给的会话 ID（可为空）。
            title_hint: 首条用户消息（用于生成标题）。

        Returns:
            tuple[ConversationRecord, bool]: `(会话, 是否新建)`。

        Raises:
            ApiError: 会话 ID 已存在但已软删或不属于本人（18001；不区分以免探测）。
        """
        if conversation_id:
            existing = await self._conversations.get(tenant_id, user_id, conversation_id)
            if existing is not None:
                return existing, False
            # ID 存在但已软删 / 不属于本人 → 18001，**绝不覆盖**
            # （覆盖等于把别人的会话内容清空，比"查不到"严重得多）
            other = await self._conversations.get_any(tenant_id, conversation_id)
            if other is not None:
                raise ApiError(
                    ErrorCode.CONVERSATION_NOT_FOUND,
                    internal_detail=(
                        f"conversation_id={conversation_id} user_id={user_id} "
                        f"deleted={other.deleted}"
                    ),
                )
            # **采纳老客户端的会话 ID**：阶段四之前 `conversation_id` 是必填的标签，
            # 老调用方会传自己生成的 ID（如 `conv_base`、前端本地 UUID）。
            # 若这里报 18001，等于把「必填→可选」这个"向后兼容变更"（架构 §4.3 B-1）
            # 做成破坏性变更。所以首次见到就按该 ID 建会话，之后照常复用。
            adopted = await self._conversations.create(
                ConversationRecord(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    title=(title_hint or "").strip()[:TITLE_PREFIX_LENGTH],
                )
            )
            return adopted, True
        created = await self._conversations.create(
            ConversationRecord(
                tenant_id=tenant_id,
                conversation_id=new_conversation_id(),
                user_id=user_id,
                title=(title_hint or "").strip()[:TITLE_PREFIX_LENGTH],
            )
        )
        return created, True

    async def start_generation(
        self, tenant_id: str, user_id: str, conversation_id: str, *, user_text: str
    ) -> StartedGeneration:
        """落用户消息 + 建助手空壳（**上游调用之前**完成）。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属人。
            conversation_id: 会话 ID（空则新建）。
            user_text: 本轮用户输入。

        Returns:
            StartedGeneration: 两条消息句柄与会话行。
        """
        conversation, is_new = await self.ensure_conversation(
            tenant_id, user_id, conversation_id, title_hint=user_text
        )
        user_message = await self._messages.append(
            MessageRecord(
                tenant_id=tenant_id,
                message_id=new_message_id(),
                conversation_id=conversation.conversation_id,
                user_id=user_id,
                role="user",
                content=user_text,
                status="settled",
            )
        )
        # 空壳**直接 streaming**：触发器只允许 streaming 行写正文（0008）
        assistant_message = await self._messages.append(
            MessageRecord(
                tenant_id=tenant_id,
                message_id=new_message_id(),
                conversation_id=conversation.conversation_id,
                user_id=user_id,
                role="assistant",
                content="",
                status="streaming",
            )
        )
        await self._conversations.bump(tenant_id, conversation.conversation_id, added=2)
        logger.info(
            "generation_messages_written",
            conversation_id=conversation.conversation_id,
            user_message_id=user_message.message_id,
            assistant_message_id=assistant_message.message_id,
            new_conversation=is_new,
        )
        return StartedGeneration(
            conversation=conversation,
            user_message=user_message,
            assistant_message=assistant_message,
            is_new_conversation=is_new,
        )

    async def finalize_assistant_message(
        self,
        tenant_id: str,
        message_id: str,
        *,
        content: str,
        status: str,
        error_reason: str = "",
        truncated: bool = False,
        prompt_units: int = 0,
        completion_units: int = 0,
    ) -> MessageRecord | None:
        """写助手消息终态（幂等）。

        Args:
            tenant_id: 企业 ID。
            message_id: 助手消息 ID。
            content: 终态正文（中断时是"已收到的部分"）。
            status: `settled|aborted|failed`。
            error_reason: 业务化失败原因（**不含码与堆栈**）。
            truncated: 是否截断。
            prompt_units: 输入额度。
            completion_units: 输出额度。

        Returns:
            MessageRecord | None: 更新后的行。
        """
        if status not in {"settled", "aborted", "failed"}:
            raise ApiError(
                ErrorCode.INTERNAL_ERROR,
                internal_detail=f"finalize 只接受终态，收到 {status}",
            )
        return await self._messages.finalize(
            tenant_id,
            message_id,
            content=content,
            status=status,
            error_reason=error_reason,
            truncated=truncated,
            prompt_units=prompt_units,
            completion_units=completion_units,
        )

    async def abort_orphans(self, tenant_id: str, *, now: datetime | None = None) -> int:
        """把滞留的 `pending/streaming` 消息收口为 `aborted`（启动 reconciler）。

        为什么必须收口：进程被杀/容器重启时，行会停在 streaming。若不处理，
        用户下次打开会话会看到一条永远"正在输入"的消息，而且它会混进上下文组装
        （`_load_history` 只取 settled，所以只是显示问题，但显示问题就是信任问题）。

        Args:
            tenant_id: 企业 ID。
            now: 当前时间（测试注入）。

        Returns:
            int: 收口的条数。
        """
        threshold = (now or datetime.now(UTC)) - timedelta(minutes=ORPHAN_AFTER_MINUTES)
        orphans = await self._messages.list_orphans(tenant_id, older_than=threshold)
        closed = 0
        for record in orphans:
            await self._messages.finalize(
                tenant_id,
                record.message_id,
                content=record.content,
                status="aborted",
                error_reason="回答中断，可重新发送",
                truncated=True,
            )
            closed += 1
        if closed:
            logger.warning("message_orphans_closed", tenant_id=tenant_id, count=closed)
        return closed

    async def soft_delete(
        self, tenant_id: str, user_id: str, conversation_id: str
    ) -> tuple[ConversationRecord, str]:
        """软删会话并给出可恢复截止时间。

        Args:
            tenant_id: 企业 ID。
            user_id: 归属人。
            conversation_id: 会话 ID。

        Returns:
            tuple[ConversationRecord, str]: `(会话行, recoverable_until)`。

        Raises:
            ApiError: 会话不存在或不属于本人（18001）。
        """
        deleted = await self._conversations.soft_delete(tenant_id, user_id, conversation_id)
        if deleted is None:
            raise ApiError(
                ErrorCode.CONVERSATION_NOT_FOUND,
                internal_detail=f"conversation_id={conversation_id} user_id={user_id}",
            )
        return deleted, recoverable_until(deleted.deleted_at, self._settings)


#: 会话服务单例
_conversation_service: ConversationService | None = None


def get_conversation_service(settings: Settings | None = None) -> ConversationService:
    """取会话服务单例。

    Args:
        settings: 可选配置实例。

    Returns:
        ConversationService: 服务。
    """
    global _conversation_service
    conf = settings or get_settings()
    if _conversation_service is None:
        _conversation_service = ConversationService(conf)
    return _conversation_service


def reset_conversation_service() -> None:
    """重置会话服务单例（测试隔离用）。"""
    global _conversation_service
    _conversation_service = None


__all__: list[str] = [
    "ORPHAN_AFTER_MINUTES",
    "TITLE_PREFIX_LENGTH",
    "ConversationService",
    "StartedGeneration",
    "get_conversation_service",
    "reset_conversation_service",
]
