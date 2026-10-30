"""产物内容代理（阶段三 B19，`architecture-phase3.md` §3.5）。

**为什么不在 `/api/v1` 下**：阶段三端点表冻结 16 条（`PHASE3_DOMAIN_ENDPOINTS`），
下载是**内容代理**（无 JSON 信封、无业务语义），塞进 `/api/v1` 会让端点计数与
基线门禁失去意义（见 `app/core/artifact_token.py` 的说明）。

**鉴权靠令牌**：链接自带租户 + 产物 + 版本 + 过期时间并经 HMAC 签名，因此不需要
登录态（浏览器直接点开下载、复制到聊天窗口都可用），但伪造令牌解不开别人的租户，
且链接到期即失效（S9，2026-10-23）。另按「租户 + 客户端 IP」限流，防止令牌被
拿到后批量拉取产物。
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Path, Request
from fastapi.responses import Response

from app.business.artifact_service import fetch_artifact
from app.core.artifact_token import parse_token
from app.core.config import Settings, get_request_settings
from app.core.errors import ApiError
from app.core.ratelimit import get_request_rate_limiter

router = APIRouter(prefix="/files/artifacts", tags=["产物下载"])

#: 单个「租户 + IP」每分钟下载次数上限（下载是低频动作，60 足够正常使用）
_DOWNLOAD_RATE_LIMIT: int = 60

#: 统一的失效文案（不区分「令牌伪造 / 过期 / 产物不存在」，避免探测）
_INVALID_LINK_TEXT: str = "链接无效或已过期"


@router.get(
    "/{token}",
    summary="下载产物（平台代理）",
    description="按下载令牌返回产物内容。令牌自带租户、产物、版本与过期时间，过期后返回 404。",
    response_class=Response,
)
async def download_artifact(
    request: Request,
    token: str = Path(..., description="下载令牌", min_length=8, max_length=512),
) -> Response:
    """下载产物内容。

    Args:
        request: FastAPI 请求对象（取注入配置与限流器）。
        token: 下载令牌。

    Returns:
        Response: 产物字节；令牌非法、过期或产物不存在时返回 404 文本；
        触发限流时返回 429 文本。

    Raises:
        None: 失败一律以文本响应表达（下载链路上不应出现 JSON 错误信封）。
    """
    settings: Settings = get_request_settings(request)
    claims = parse_token(token, settings)
    if claims is None:
        return Response(status_code=404, content=_INVALID_LINK_TEXT.encode())

    limiter = get_request_rate_limiter(request)
    if limiter.enabled:
        client_ip = request.client.host if request.client else "unknown"
        try:
            await limiter.check(
                claims.tenant_id,
                client_ip,
                "/files/artifacts",
                limit=_DOWNLOAD_RATE_LIMIT,
                window_seconds=60,
            )
        except ApiError:
            return Response(status_code=429, content="请求过于频繁，请稍后再试".encode())

    record = await fetch_artifact(settings, claims.tenant_id, claims.artifact_id)
    if record is None or _is_expired(record.expires_at):
        return Response(status_code=404, content=_INVALID_LINK_TEXT.encode())
    content = record.content
    if not content:
        content = record.inline_text.encode("utf-8")
    filename = record.name or record.output_key
    return Response(
        content=content,
        media_type=record.content_type or "application/octet-stream",
        headers={
            "Content-Disposition": _content_disposition(filename),
            "X-Artifact-Version": str(claims.version),
            "Cache-Control": "private, no-store",
        },
    )


def _is_expired(expires_at: str) -> bool:
    """判断产物留存期是否已过。

    `expires_at` 由仓储层写成 ISO 8601（UTC，`Z` 结尾）；空值视为「未设置」
    （内存后端的历史记录），不拦截。解析失败同样不拦截——留存期是业务约束，
    不应该因为格式异常让所有下载 500。

    Args:
        expires_at: 过期时间字符串。

    Returns:
        bool: 已过期返回 True。
    """
    text = (expires_at or "").strip()
    if not text:
        return False
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed <= datetime.now(UTC)


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
