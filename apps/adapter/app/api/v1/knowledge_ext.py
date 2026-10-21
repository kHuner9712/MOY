"""知识库扩展端点（阶段二 KB1-KB8，架构文档 §4 / 2.2 第 2 条）。

本文件在 B03 阶段只落地 **KB8 凭据绑定**；KB1-KB7 由 B04/B05 追加到同一 `router`，
路径前缀固定 `/api/v1/knowledge`（与既有 RAG 域同前缀，不新增版本）。

KB8 设计要点（D-10 安全红线）：

1. **权限**：`knowledge:base:update`（走统一判权出口 `require_permission`，
   与 U1 `POST /api/v1/identity/verify` 同一套规则）；
2. **幂等**：写接口必须携带 `Idempotency-Key`；幂等指纹含请求体摘要
   （`run_idempotent` 自动读取 body digest），因此「同 path 不同 body」不会被
   误判为重复请求——这是 KB8 最易漏的一项（漏了就是「用户看到成功但凭据没换」）；
3. **响应/日志零明文**：响应只有 `base_id / bound / updated_at`，
   日志只允许出现 `base_id` 与 `credential_ref` 后 6 位；
4. **换绑即生效**：绑定成功后由 `CredentialResolver.bind()` 删除密文缓存与知识库缓存，
   下一个请求重新解析，无需重启。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, Field

from app.business.credential_service import get_credential_resolver, ref_suffix
from app.core.config import get_request_settings
from app.core.errors import ErrorCode, error_response
from app.core.idempotency import IdempotencyManager, get_idempotency_manager, hash_request_body
from app.core.logging import get_logger
from app.core.security import (
    RequestContext,
    get_request_context,
    require_permission,
    run_idempotent,
)
from app.schemas.common import ApiResponse

logger = get_logger(__name__)

#: KB8 所需权限点（前端 `PERM.KB_UPDATE`）
KB_UPDATE_PERMISSION = "knowledge:base:update"

router = APIRouter(prefix="/knowledge", tags=["RAG 服务"])


class CredentialBindingRequest(BaseModel):
    """KB8 请求体。

    Attributes:
        credential_ref: 凭据引用，形如 `cred_01K4Q8Z2X7M9B3N5P6R8T0V2W4`。
            明文密钥**永不**出现在本接口的请求/响应中。
    """

    credential_ref: str = Field(
        min_length=4,
        max_length=128,
        description="凭据引用（形如 cred_01K...），由运营侧预先入库",
    )


class CredentialBindingData(BaseModel):
    """KB8 响应数据。

    Attributes:
        base_id: 知识库 ID。
        bound: 是否已完成绑定（固定 True）。
        updated_at: 绑定更新时间（ISO 8601）。
    """

    base_id: str = Field(description="知识库 ID")
    bound: bool = Field(default=True, description="是否已绑定")
    updated_at: str = Field(default="", description="绑定更新时间，ISO 8601")


@router.put(
    "/bases/{base_id}/credential-binding",
    response_model=ApiResponse[CredentialBindingData],
    summary="KB8 更新知识库凭据绑定",
    description=(
        "为知识库绑定（或换绑）下游访问凭据。换绑后下一个请求即生效，无需重启服务。"
        "写接口，需携带 Idempotency-Key 请求头；需 knowledge:base:update 权限。"
    ),
)
async def update_credential_binding(
    request: Request,
    body: CredentialBindingRequest,
    context: RequestContext = Depends(get_request_context),
    manager: IdempotencyManager = Depends(get_idempotency_manager),
    base_id: str = Path(..., description="知识库 ID", min_length=3, max_length=128),
) -> ApiResponse[CredentialBindingData]:
    """KB8 更新知识库凭据绑定。

    Args:
        request: FastAPI 请求对象。
        body: 绑定请求。
        context: 请求上下文。
        manager: 幂等管理器。
        base_id: 知识库 ID。

    Returns:
        ApiResponse[CredentialBindingData]: 绑定结果（不含任何密钥信息）。

    Raises:
        ApiError: 权限不足（20003）、凭据不可用（11007）、知识库不存在（11001）。
    """
    require_permission(context.auth, KB_UPDATE_PERMISSION)
    settings = get_request_settings(request)
    resolver = get_credential_resolver(settings)
    credential_ref = body.credential_ref.strip()

    async def factory() -> CredentialBindingData:
        updated_at = await resolver.bind(base_id, credential_ref, tenant_id=context.tenant_id)
        logger.info(
            "credential_binding_requested",
            base_id=base_id,
            credential_ref_suffix=ref_suffix(credential_ref),
            tenant_id=context.tenant_id,
        )
        return CredentialBindingData(base_id=base_id, bound=True, updated_at=updated_at)

    # 幂等指纹必须含请求体摘要（架构文档 3.0.5）：KB8 是「同 path 不同 body」的
    # 典型接口，指纹漏掉 body_digest 就会出现「用户看到成功但凭据没换」。
    digest = hash_request_body(body.model_dump_json())
    result, replayed = await run_idempotent(request, context, factory, manager, body_digest=digest)
    if replayed:
        return ApiResponse[CredentialBindingData].fail(
            code=int(ErrorCode.IDEMPOTENT_DUPLICATE),
            message=error_response(ErrorCode.IDEMPOTENT_DUPLICATE).message,
            data=result,
        )
    return ApiResponse[CredentialBindingData].ok(result)


__all__ = [
    "KB_UPDATE_PERMISSION",
    "CredentialBindingData",
    "CredentialBindingRequest",
    "router",
    "update_credential_binding",
]
