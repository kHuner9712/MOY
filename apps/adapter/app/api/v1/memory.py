"""记忆端点 MM1-MM4（阶段四 T4-03.5，架构 §4.2 / PRD §4.4）。

| # | 端点 | 方法 | 用途 |
| --- | --- | --- | --- |
| MM1 | `/api/v1/model/memories` | GET | 列表（类别筛选、分页，同包返回 `enabled`） |
| MM2 | `/api/v1/model/memories/{memory_id}` | DELETE | 删单条（**硬删**，含向量行） |
| MM3 | `/api/v1/model/memories` | DELETE | 按类删 / 全删 |
| MM4 | `/api/v1/model/memories/settings` | PUT | 开关（全局 + 分类） |

**四条纪律**：

1. **不接受 `user_id`**：身份只来自令牌 `sub`（少这一条就是"替别人删记忆"）；
2. **删除是硬删**：`user_memory` 行与 `memory_embedding` 行同一事务删除，
   否则隐私政策里"删除后不再使用"这句话就不成立（PRD §4.4 第 2 条不允许项）；
3. **删不存在的记忆 → 18002**（`这条信息已不存在`），不静默 200——
   静默成功会让用户以为"删掉了别人的"或"删掉了但其实还在"；
4. **不新增"手动立即整理"端点**：端点预算固定 10 个（PRD §0.3），
   手动触发列 V1.1；本条已在 `docs/phase4-batch-log.md` 披露。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query, Request

from app.business.memory_service import MemoryService, get_memory_service
from app.core.errors import ApiError, ErrorCode, error_response
from app.core.idempotency import IdempotencyManager, get_idempotency_manager
from app.core.security import RequestContext, get_request_context, run_idempotent
from app.db.repositories.memory_repo import MEMORY_CATEGORIES, MemoryRecord
from app.schemas.common import ApiResponse, PageMeta
from app.schemas.memory import (
    DEFAULT_MEMORY_PAGE_SIZE,
    MAX_MEMORY_PAGE_SIZE,
    MemoryBatchDeleteData,
    MemoryDeleteData,
    MemoryItem,
    MemoryPageData,
    MemorySettingsData,
    MemorySettingsRequest,
)

router = APIRouter(prefix="/model", tags=["我的记忆"])

#: 记忆 ID 的最大长度（`mem_` + ULID，与 `new_memory_id()` 对齐后留足余量）
_MAX_ID_LENGTH: int = 128


def _service(request: Request) -> MemoryService:
    """取本次请求生效的记忆服务（配置从 `app.state` 注入）。

    Args:
        request: FastAPI 请求对象。

    Returns:
        MemoryService: 记忆服务。
    """
    from app.core.config import get_request_settings

    return get_memory_service(get_request_settings(request))


def _to_item(record: MemoryRecord) -> MemoryItem:
    """记忆行 → 列表项。

    Args:
        record: 记忆行。

    Returns:
        MemoryItem: 列表项。
    """
    return MemoryItem(
        memory_id=record.memory_id,
        category=record.category,
        content=record.content,
        confidence=record.confidence,
        source_conversation_id=record.source_conversation_id,
        updated_at=record.updated_at,
        superseded_by=record.superseded_by,
    )


@router.get(
    "/memories",
    response_model=ApiResponse[MemoryPageData],
    summary="MM1 我的记忆列表",
    description=(
        "按更新时间倒序列出「我在对话中了解到的关于你的信息」。"
        "`category` 可按类别筛选；响应同包返回 `enabled`，"
        "便于前端区分「已关闭」与「还没记到东西」两种空态。"
    ),
)
async def list_memories(
    request: Request,
    category: str = Query(default="", description="类别筛选（空=全部）"),
    page: int = Query(default=1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(
        default=DEFAULT_MEMORY_PAGE_SIZE, ge=1, le=MAX_MEMORY_PAGE_SIZE, description="每页条数"
    ),
    context: RequestContext = Depends(get_request_context),
) -> ApiResponse[MemoryPageData]:
    """MM1 记忆列表。

    Args:
        request: FastAPI 请求对象。
        category: 类别筛选。
        page: 页码。
        page_size: 每页条数。
        context: 请求上下文（租户与用户来自令牌）。

    Returns:
        ApiResponse[MemoryPageData]: 分页记忆列表与开关状态。

    Raises:
        ApiError: 类别非法或分页参数越界（20001）。
    """
    if category and category not in MEMORY_CATEGORIES:
        raise ApiError(
            ErrorCode.PARAM_INVALID,
            internal_detail=f"unknown memory category: {category}",
        )
    service = _service(request)
    rows, total, setting = await service.list_memories(
        context.auth.tenant_id,
        context.auth.user_id,
        category=category,
        page=page,
        page_size=page_size,
    )
    total_pages = (total + page_size - 1) // page_size if page_size else 0
    data = MemoryPageData(
        items=[_to_item(row) for row in rows],
        meta=PageMeta(page=page, page_size=page_size, total=total, total_pages=total_pages),
        enabled=setting.enabled,
        category_switches=dict(setting.category_switches),
    )
    return ApiResponse[MemoryPageData].ok(data)


@router.delete(
    "/memories/{memory_id}",
    response_model=ApiResponse[MemoryDeleteData],
    summary="MM2 删除单条记忆",
    description="硬删除该条信息（含其派生数据）；删除后我不再在任何回答里使用它。",
)
async def delete_memory(
    request: Request,
    memory_id: str = Path(..., min_length=1, max_length=_MAX_ID_LENGTH),
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
) -> ApiResponse[MemoryDeleteData]:
    """MM2 删除单条记忆。

    Args:
        request: FastAPI 请求对象。
        memory_id: 记忆 ID。
        context: 请求上下文。
        manager: 幂等管理器。

    Returns:
        ApiResponse[MemoryDeleteData]: 删除结果。

    Raises:
        ApiError: 记忆不存在或不属于本人（18002）。
    """
    service = _service(request)
    tenant_id = context.auth.tenant_id
    user_id = context.auth.user_id

    async def factory() -> MemoryDeleteData:
        deleted = await service.delete_memory(tenant_id, user_id, memory_id)
        if not deleted:
            raise ApiError(
                ErrorCode.MEMORY_NOT_FOUND,
                internal_detail=f"memory_id={memory_id} user_id={user_id}",
            )
        return MemoryDeleteData(memory_id=memory_id, deleted=True)

    result, replayed = await run_idempotent(request, context, factory, manager)
    if replayed:
        return ApiResponse[MemoryDeleteData].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message=error_response(ErrorCode.IDEMPOTENT_DUPLICATE).message,
            data=result,
        )
    return ApiResponse[MemoryDeleteData].ok(result)


@router.delete(
    "/memories",
    response_model=ApiResponse[MemoryBatchDeleteData],
    summary="MM3 按类别删除 / 清空记忆",
    description=(
        "传 `category` 删除该类别下全部信息；不传则清空我的全部记忆。"
        "对话记录不受影响。删除后这些信息不再被使用。"
    ),
)
async def delete_memories(
    request: Request,
    category: str = Query(default="", description="类别（空=全部）"),
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
) -> ApiResponse[MemoryBatchDeleteData]:
    """MM3 按类别删除 / 清空。

    Args:
        request: FastAPI 请求对象。
        category: 类别（空表示全部）。
        context: 请求上下文。
        manager: 幂等管理器。

    Returns:
        ApiResponse[MemoryBatchDeleteData]: 删除条数。

    Raises:
        ApiError: 类别非法（20001）。
    """
    if category and category not in MEMORY_CATEGORIES:
        raise ApiError(
            ErrorCode.PARAM_INVALID,
            internal_detail=f"unknown memory category: {category}",
        )
    service = _service(request)
    tenant_id = context.auth.tenant_id
    user_id = context.auth.user_id

    async def factory() -> MemoryBatchDeleteData:
        removed = await service.delete_memories(tenant_id, user_id, category=category)
        return MemoryBatchDeleteData(deleted_count=removed, category=category)

    result, replayed = await run_idempotent(request, context, factory, manager)
    if replayed:
        return ApiResponse[MemoryBatchDeleteData].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message=error_response(ErrorCode.IDEMPOTENT_DUPLICATE).message,
            data=result,
        )
    return ApiResponse[MemoryBatchDeleteData].ok(result)


@router.put(
    "/memories/settings",
    response_model=ApiResponse[MemorySettingsData],
    summary="MM4 记忆开关",
    description=(
        "关闭后我不再从新对话里记东西；已记住的仍可在这里删除。"
        "分类开关只影响该类别（关闭后该类不再被记录、也不再被使用，已有条目保留）。"
    ),
)
async def update_memory_settings(
    request: Request,
    body: MemorySettingsRequest,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
) -> ApiResponse[MemorySettingsData]:
    """MM4 更新记忆开关。

    Args:
        request: FastAPI 请求对象。
        body: 开关请求。
        context: 请求上下文。
        manager: 幂等管理器。

    Returns:
        ApiResponse[MemorySettingsData]: 更新后的开关。

    Raises:
        ApiError: 两项都没传（20001）或类别非法（20001）。
    """
    if body.enabled is None and body.category_switches is None:
        raise ApiError(
            ErrorCode.PARAM_INVALID,
            message="请至少开启或关闭一项设置",
            internal_detail="empty settings payload",
        )
    service = _service(request)
    tenant_id = context.auth.tenant_id
    user_id = context.auth.user_id

    async def factory() -> MemorySettingsData:
        saved = await service.update_setting(
            tenant_id,
            user_id,
            enabled=body.enabled,
            category_switches=body.category_switches,
        )
        return MemorySettingsData(
            enabled=saved.enabled,
            category_switches=dict(saved.category_switches),
            updated_at=saved.updated_at,
        )

    result, replayed = await run_idempotent(request, context, factory, manager)
    if replayed:
        return ApiResponse[MemorySettingsData].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message=error_response(ErrorCode.IDEMPOTENT_DUPLICATE).message,
            data=result,
        )
    return ApiResponse[MemorySettingsData].ok(result)


__all__: list[str] = ["router"]
