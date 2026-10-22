"""产物内容代理（阶段三 B19，`architecture-phase3.md` §3.5）。

**为什么不在 `/api/v1` 下**：阶段三端点表冻结 16 条（`PHASE3_DOMAIN_ENDPOINTS`），
下载是**内容代理**（无 JSON 信封、无业务语义），塞进 `/api/v1` 会让端点计数与
基线门禁失去意义（见 `app/core/artifact_token.py` 的说明）。

**鉴权靠令牌**：链接自带租户 + 产物 + 版本并经 HMAC 签名，因此不需要登录态
（浏览器直接点开下载、复制到聊天窗口都可用），但伪造令牌解不开别人的租户。
"""

from __future__ import annotations

from fastapi import APIRouter, Path
from fastapi.responses import Response

from app.core.artifact_token import parse_token
from app.core.config import Settings, get_settings
from app.db.repositories.artifact_repo import get_artifact_store

router = APIRouter(prefix="/files/artifacts", tags=["产物下载"])


@router.get(
    "/{token}",
    summary="下载产物（平台代理）",
    description="按下载令牌返回产物内容。令牌自带租户、产物与版本，过期的产物返回 404。",
    response_class=Response,
)
async def download_artifact(
    token: str = Path(..., description="下载令牌", min_length=8, max_length=256),
) -> Response:
    """下载产物内容。

    Args:
        token: 下载令牌。

    Returns:
        Response: 产物字节；令牌非法或产物不存在时返回 404 文本。

    Raises:
        None: 失败一律以 404 响应表达（下载链路上不应出现 JSON 错误信封）。
    """
    settings: Settings = get_settings()
    parsed = parse_token(token, settings)
    if parsed is None:
        return Response(status_code=404, content="链接无效或已过期".encode())
    tenant_id, artifact_id, version = parsed
    record = await get_artifact_store(settings).get(tenant_id, artifact_id)
    if record is None:
        return Response(status_code=404, content="产物不存在或已过期".encode())
    content = record.content
    if not content:
        content = record.inline_text.encode("utf-8")
    filename = record.name or record.output_key
    return Response(
        content=content,
        media_type=record.content_type or "application/octet-stream",
        headers={
            "Content-Disposition": _content_disposition(filename),
            "X-Artifact-Version": str(version),
        },
    )


def _content_disposition(filename: str) -> str:
    """构造 `Content-Disposition`（中文文件名必须走 RFC 5987 的 `filename*`）。

    **为什么必须这样写**（B26 e2e 实测暴露的 P0）：HTTP 头按 latin-1 编码，
    直接写 `filename="发布排期表.csv"` 会在响应序列化时抛
    `UnicodeEncodeError`，整条下载链路 500——而阶段三的产物名**几乎全是中文**
    （发布排期表 / 筛选评分表 / 录用通知），等于"所有产物都下不了"。
    ASCII 兜底名保证老客户端也能落地一个文件，`filename*` 保证现代客户端拿到中文名。

    Args:
        filename: 产物名称（可含中文）。

    Returns:
        str: 形如 `inline; filename="artifact.csv"; filename*=UTF-8''%E4%B8%AD.csv`。
    """
    from urllib.parse import quote

    fallback = filename.encode("ascii", "replace").decode("ascii").replace('"', "_")
    return f"inline; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}"


__all__: list[str] = ["router"]
