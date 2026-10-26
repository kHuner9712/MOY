"""会话端点 MC1-MC4（阶段四 T4-02.5，架构 §4.2 / §4.4）。

| # | 端点 | 方法 | 用途 |
| --- | --- | --- | --- |
| MC1 | `/api/v1/model/conversations` | GET | 会话列表（分页、归档筛选） |
| MC2 | `/api/v1/model/conversations/{conversation_id}/messages` | GET | 历史消息（游标分页） |
| MC3 | `/api/v1/model/conversations/{conversation_id}` | DELETE | 软删（30 天可恢复） |
| MC4 | `/api/v1/model/conversations/{conversation_id}` | PATCH | 改标题 / 归档 |

三条纪律（与既有 30 端点一致）：

1. **不接受 `user_id`**：身份只来自令牌 `sub`；少这一条就等于允许"替别人翻历史"；
2. **游标而不是 offset**：`before=<cursor>` 取 `seq` 更早的消息。删行/并发写入下
   offset 会重复或漏行，而对话恰好在流式期间频繁写入；
3. **读/删"确实不存在"的会话 → 18001**；"不存在但可以建"只发生在 M1/M2
   （那里走 upsert，见 `ConversationService.ensure_conversation` 的注释）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query, Request

from app.business.conversation_service import ConversationService, get_conversation_service
from app.core.errors import ApiError, ErrorCode, error_response
from app.core.idempotency import IdempotencyManager, get_idempotency_manager
from app.core.security import RequestContext, get_request_context, run_idempotent
from app.db.repositories.conversation import (
    ConversationRecord,
    ConversationStore,
    MessageStore,
    decode_cursor,
    encode_cursor,
    get_conversation_store,
    get_message_store,
)
from app.schemas.common import ApiResponse, PageMeta
from app.schemas.conversation import (
    DEFAULT_MESSAGE_LIMIT,
    MAX_MESSAGE_LIMIT,
    MAX_PAGE_SIZE,
    ConversationDeleteData,
    ConversationItem,
    ConversationPageData,
    ConversationUpdateData,
    ConversationUpdateRequest,
    MessageItem,
    MessagePageData,
)

router = APIRouter(prefix="/model", tags=["会话与历史"])

#: 会话 ID 的最大长度（与 `GenerateRequest.conversation_id` 一致）
_MAX_ID_LENGTH: int = 128


def _stores(request: Request) -> tuple[ConversationService, ConversationStore, MessageStore]:
    """取本次请求生效的服务与存储（配置从 `app.state` 注入）。

    Args:
        request: FastAPI 请求对象。

    Returns:
        tuple: `(会话服务, 会话存储, 消息存储)`。
    """
    from app.core.config import get_request_settings

    settings = get_request_settings(request)
    return (
        get_conversation_service(settings),
        get_conversation_store(settings),
        get_message_store(settings),
    )


def _to_item(record: ConversationRecord) -> ConversationItem:
    """会话行 → 列表项。

    Args:
        record: 会话行。

    Returns:
        ConversationItem: 列表项。
    """
    return ConversationItem(
        conversation_id=record.conversation_id,
        title=record.title,
        last_message_at=record.last_message_at,
        message_count=record.message_count,
        archived=record.archived,
    )


@router.get(
    "/conversations",
    response_model=ApiResponse[ConversationPageData],
    summary="MC1 会话列表",
    description=(
        "按最近消息时间倒序列出本人在当前企业的会话。"
        "`archived` 切换归档视图，`include_deleted` 用于回收站（软删 30 天内可恢复）。"
    ),
)
async def list_conversations(
    request: Request,
    page: int = Query(default=1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE, description="每页条数"),
    archived: bool = Query(default=False, description="只看归档会话"),
    include_deleted: bool = Query(default=False, description="包含已软删（回收站视图）"),
    context: RequestContext = Depends(get_request_context),
) -> ApiResponse[ConversationPageData]:
    """MC1 会话列表。

    Args:
        request: FastAPI 请求对象。
        page: 页码。
        page_size: 每页条数。
        archived: 是否只看归档。
        include_deleted: 是否包含已软删。
        context: 请求上下文（租户与用户来自令牌）。

    Returns:
        ApiResponse[ConversationPageData]: 分页会话列表。
    """
    _service, conversations, _messages = _stores(request)
    rows, total = await conversations.list_page(
        context.auth.tenant_id,
        context.auth.user_id,
        page=page,
        page_size=page_size,
        archived=archived,
        include_deleted=include_deleted,
    )
    total_pages = (total + page_size - 1) // page_size if page_size else 0
    data = ConversationPageData(
        items=[_to_item(row) for row in rows],
        meta=PageMeta(page=page, page_size=page_size, total=total, total_pages=total_pages),
    )
    return ApiResponse[ConversationPageData].ok(data)


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=ApiResponse[MessagePageData],
    summary="MC2 历史消息",
    description=(
        "按 `seq` 游标取更早的消息（**不用 offset**：删行会重复或漏行）。"
        "`before` 传上一页返回的 `next_cursor`，返回按时间正序，便于直接渲染。"
    ),
)
async def list_messages(
    request: Request,
    conversation_id: str = Path(..., min_length=1, max_length=_MAX_ID_LENGTH),
    before: str = Query(default="", description="游标（上一页的 next_cursor）"),
    limit: int = Query(
        default=DEFAULT_MESSAGE_LIMIT, ge=1, le=MAX_MESSAGE_LIMIT, description="本页条数"
    ),
    context: RequestContext = Depends(get_request_context),
) -> ApiResponse[MessagePageData]:
    """MC2 历史消息。

    Args:
        request: FastAPI 请求对象。
        conversation_id: 会话 ID。
        before: 游标。
        limit: 本页条数。
        context: 请求上下文。

    Returns:
        ApiResponse[MessagePageData]: 消息分页。

    Raises:
        ApiError: 游标非法（20001）或会话不存在/不属于本人（18001）。
    """
    _service, conversations, messages = _stores(request)

    before_seq: int | None = None
    if before:
        before_seq = decode_cursor(before)
        if before_seq is None:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message="翻页参数无效，请刷新后重试",
                internal_detail=f"invalid cursor: {before[:32]}",
            )

    conversation = await conversations.get(
        context.auth.tenant_id, context.auth.user_id, conversation_id
    )
    if conversation is None:
        raise ApiError(
            ErrorCode.CONVERSATION_NOT_FOUND,
            internal_detail=f"conversation_id={conversation_id} user_id={context.auth.user_id}",
        )

    # 多取一条用于判断"还有没有更早的"——比额外 count 查询便宜，且不受并发删除影响
    rows = await messages.list_before(
        context.auth.tenant_id,
        conversation_id,
        before_seq=before_seq,
        limit=limit + 1,
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = encode_cursor(page[-1].seq) if has_more and page else ""

    data = MessagePageData(
        items=[
            MessageItem(
                message_id=row.message_id,
                role=row.role,
                content=row.content,
                status=row.status,
                created_at=row.created_at,
                truncated=row.truncated,
            )
            for row in reversed(page)  # 存储返回降序，接口按时间正序下发
        ],
        next_cursor=next_cursor,
    )
    return ApiResponse[MessagePageData].ok(data)


@router.delete(
    "/conversations/{conversation_id}",
    response_model=ApiResponse[ConversationDeleteData],
    summary="MC3 删除会话（软删）",
    description=(
        "软删：行保留 30 天（`conversation_soft_delete_recover_days`），"
        "响应回 `recoverable_until`。幂等：重复删除返回同一个截止时间。"
    ),
)
async def delete_conversation(
    request: Request,
    conversation_id: str = Path(..., min_length=1, max_length=_MAX_ID_LENGTH),
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
) -> ApiResponse[ConversationDeleteData]:
    """MC3 删除会话。

    Args:
        request: FastAPI 请求对象。
        conversation_id: 会话 ID。
        context: 请求上下文。
        manager: 幂等管理器。

    Returns:
        ApiResponse[ConversationDeleteData]: 删除结果。

    Raises:
        ApiError: 会话不存在/不属于本人（18001）。
    """
    service, _conversations, _messages = _stores(request)
    tenant_id = context.auth.tenant_id
    user_id = context.auth.user_id

    async def factory() -> ConversationDeleteData:
        deleted, until = await service.soft_delete(tenant_id, user_id, conversation_id)
        return ConversationDeleteData(
            conversation_id=deleted.conversation_id,
            deleted_at=deleted.deleted_at,
            recoverable_until=until,
        )

    result, replayed = await run_idempotent(request, context, factory, manager)
    if replayed:
        return ApiResponse[ConversationDeleteData].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message=error_response(ErrorCode.IDEMPOTENT_DUPLICATE).message,
            data=result,
        )
    return ApiResponse[ConversationDeleteData].ok(result)


@router.patch(
    "/conversations/{conversation_id}",
    response_model=ApiResponse[ConversationUpdateData],
    summary="MC4 更新会话（标题 / 归档）",
    description="只改标题或归档状态；两个字段都可选，至少传一个。",
)
async def update_conversation(
    request: Request,
    body: ConversationUpdateRequest,
    conversation_id: str = Path(..., min_length=1, max_length=_MAX_ID_LENGTH),
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
) -> ApiResponse[ConversationUpdateData]:
    """MC4 更新会话。

    Args:
        request: FastAPI 请求对象。
        body: 更新请求。
        conversation_id: 会话 ID。
        context: 请求上下文。
        manager: 幂等管理器。

    Returns:
        ApiResponse[ConversationUpdateData]: 更新后的会话。

    Raises:
        ApiError: 两个字段都没传（20001）或会话不存在（18001）。
    """
    if body.title is None and body.archived is None:
        raise ApiError(
            ErrorCode.PARAM_INVALID,
            message="请至少填写标题或归档状态",
            internal_detail="empty update payload",
        )
    _service, conversations, _messages = _stores(request)
    tenant_id = context.auth.tenant_id
    user_id = context.auth.user_id

    async def factory() -> ConversationUpdateData:
        updated = await conversations.update(
            tenant_id, user_id, conversation_id, title=body.title, archived=body.archived
        )
        if updated is None:
            raise ApiError(
                ErrorCode.CONVERSATION_NOT_FOUND,
                internal_detail=f"conversation_id={conversation_id} user_id={user_id}",
            )
        return ConversationUpdateData(
            conversation_id=updated.conversation_id,
            title=updated.title,
            archived=updated.archived,
            updated_at=updated.updated_at,
        )

    result, replayed = await run_idempotent(request, context, factory, manager)
    if replayed:
        return ApiResponse[ConversationUpdateData].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message=error_response(ErrorCode.IDEMPOTENT_DUPLICATE).message,
            data=result,
        )
    return ApiResponse[ConversationUpdateData].ok(result)


__all__: list[str] = ["router"]
