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
from typing import TYPE_CHECKING

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import Response

from app.business.artifact_export import XLSX_CONTENT_TYPE, build_xlsx_export
from app.business.artifact_service import fetch_artifact
from app.core.artifact_token import parse_token
from app.core.config import Settings, get_request_settings
from app.core.errors import ApiError
from app.core.ratelimit import get_request_rate_limiter
from app.plugins.renderer import RENDER_CONTENT_TYPES
from app.services.office_preview import (
    OfficePreviewError,
    get_office_preview_converter,
)

if TYPE_CHECKING:  # 仅类型检查期引入：api 层不依赖 db（分层矩阵 R-API-DB）
    from app.db.repositories.artifact_repo import ArtifactRecord

router = APIRouter(prefix="/files/artifacts", tags=["产物下载"])

#: 单个「租户 + IP」每分钟下载次数上限（下载是低频动作，60 足够正常使用）
_DOWNLOAD_RATE_LIMIT: int = 60

#: 导出单独计数（避免"下载 + 导出"互相挤占配额，也便于按动作定位滥用）
_EXPORT_RATE_LIMIT: int = 30

#: 预览更贵（起 soffice 进程），配额与导出同档但单独计数
_PREVIEW_RATE_LIMIT: int = 30

#: 可预览的产物 MIME（pptx 刻意不在内：镜像不装 impress，仅下载）
_PREVIEWABLE_CONTENT_TYPES: frozenset[str] = frozenset(
    {RENDER_CONTENT_TYPES["docx"], RENDER_CONTENT_TYPES["xlsx"]}
)

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
    record, error = await _load_record(
        request, token, settings, rate_key="/files/artifacts", limit=_DOWNLOAD_RATE_LIMIT
    )
    if error is not None:
        return error
    assert record is not None
    claims = parse_token(token, settings)
    assert claims is not None
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
            # 适配层**自己**下发 nosniff 与 sandbox，不依赖网关继承：
            # nginx 的 `add_header` 一旦在某层出现，就不再继承上层同名头
            # （`lite.conf` 第 38-39 行的注释正是在讲这个坑）。`/files/` 是
            # 新增的 location，运维按惯例往里加安全头反而会把 server 级的
            # `nosniff` 顶掉——所以这里必须自带一份，不赌外面配对了。
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
        },
    )


@router.get(
    "/{token}/export",
    summary="按需导出产物为 Excel",
    description=(
        "把表格类产物（CSV 内容）按需转换为 xlsx，**不新增版本、不改任务状态**。"
        "令牌规则与下载一致；格式只支持 xlsx。"
    ),
    response_class=Response,
)
async def export_artifact(
    request: Request,
    token: str = Path(..., description="下载令牌", min_length=8, max_length=512),
    export_format: str = Query("xlsx", alias="format", description="导出格式（仅 xlsx）"),
) -> Response:
    """按需导出产物为 xlsx。

    Args:
        request: FastAPI 请求对象（取注入配置与限流器）。
        token: 下载令牌。
        export_format: 目标格式（`xlsx`）。

    Returns:
        Response: xlsx 字节；开关关闭/令牌非法/产物不存在返回 404，
        格式不支持返回 400，产物类型不支持返回 415，限流返回 429。
    """
    settings: Settings = get_request_settings(request)
    if not settings.artifact_xlsx_enabled:
        return Response(status_code=404, content=_INVALID_LINK_TEXT.encode())
    if export_format.strip().lower() != "xlsx":
        return Response(status_code=400, content="仅支持导出为 xlsx".encode())
    record, error = await _load_record(
        request, token, settings, rate_key="/files/artifacts:export", limit=_EXPORT_RATE_LIMIT
    )
    if error is not None:
        return error
    assert record is not None
    blob = build_xlsx_export(record)
    if blob is None:
        return Response(status_code=415, content="该产物不支持导出为 Excel".encode())
    return Response(
        content=blob.content,
        media_type=XLSX_CONTENT_TYPE,
        headers={
            "Content-Disposition": _content_disposition(blob.filename),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
        },
    )


@router.get(
    "/{token}/preview",
    summary="在线预览产物（Office → PDF）",
    description=(
        "用镜像内的 LibreOffice 离线把 docx/xlsx（或表格产物先转 xlsx）转成 PDF，"
        "以 `inline` 返回，供浏览器自带阅读器打开。需 `OA_OFFICE_PREVIEW_ENABLED`。"
    ),
    response_class=Response,
)
async def preview_artifact(
    request: Request,
    token: str = Path(..., description="下载令牌", min_length=8, max_length=512),
) -> Response:
    """在线预览产物（转成 PDF）。

    **响应头与下载链路刻意不同**（回归测试 `test_artifact_preview_headers.py` 锁定）：

    - `inline` + `application/pdf`：浏览器内打开，而不是落地文件；
    - `X-Frame-Options: SAMEORIGIN` + `frame-ancestors 'self'`：允许平台自身页面
      引用，同时挡住第三方站点嵌套；
    - **不带 `Content-Security-Policy: sandbox`**：PDF 阅读器在 Chromium 里是插件，
      sandbox 会禁掉插件与内嵌查看器，结果是"点了预览一片空白"。下载链路必须
      sandbox（内容是外部生成的，不许渲染），预览链路必须不 sandbox——两条链路
      语义不同，**不要"顺手对齐"**。

    Args:
        request: FastAPI 请求对象。
        token: 下载令牌。

    Returns:
        Response: PDF 字节；开关关闭 503、令牌非法 404、格式不支持 415、
        转换失败 502/504。
    """
    settings: Settings = get_request_settings(request)
    if not settings.office_preview_enabled:
        return Response(status_code=503, content="预览功能未开启，请下载后查看".encode())
    record, error = await _load_record(
        request, token, settings, rate_key="/files/artifacts:preview", limit=_PREVIEW_RATE_LIMIT
    )
    if error is not None:
        return error
    assert record is not None
    source, suffix = _preview_source(record)
    if source is None:
        return Response(status_code=415, content="该格式暂不支持在线预览，请下载后查看".encode())
    converter = get_office_preview_converter(request.app.state, settings)
    try:
        pdf = await converter.convert(source, suffix=suffix)
    except OfficePreviewError as exc:
        return Response(status_code=exc.status_code, content=exc.message.encode())
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": "inline",
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "SAMEORIGIN",
            # 只声明可被谁内嵌；**不含 sandbox**（见函数文档）
            "Content-Security-Policy": "frame-ancestors 'self'",
        },
    )


def _preview_source(record: ArtifactRecord) -> tuple[bytes | None, str]:
    """取预览输入字节与后缀（不可预览返回 `(None, "")`）。

    Args:
        record: 产物行。

    Returns:
        tuple[bytes | None, str]: `(源字节, 后缀)`。
    """
    if record.content_type in _PREVIEWABLE_CONTENT_TYPES and record.content:
        suffix = ".docx" if record.content_type == RENDER_CONTENT_TYPES["docx"] else ".xlsx"
        return record.content, suffix
    if record.type == "table":
        blob = build_xlsx_export(record)
        if blob is None:
            return None, ""
        return blob.content, ".xlsx"
    return None, ""


async def _load_record(
    request: Request,
    token: str,
    settings: Settings,
    *,
    rate_key: str,
    limit: int,
) -> tuple[ArtifactRecord | None, Response | None]:
    """解析令牌 → 限流 → 读产物（下载/导出/预览共用）。

    Args:
        request: FastAPI 请求对象。
        token: 下载令牌。
        settings: 注入配置。
        rate_key: 限流桶键（不同动作分开计数）。
        limit: 每分钟上限。

    Returns:
        tuple[ArtifactRecord | None, Response | None]: 产物行与错误响应；
        成功时错误响应为 None。
    """
    claims = parse_token(token, settings)
    if claims is None:
        return None, Response(status_code=404, content=_INVALID_LINK_TEXT.encode())

    limiter = get_request_rate_limiter(request)
    if limiter.enabled:
        client_ip = request.client.host if request.client else "unknown"
        try:
            await limiter.check(
                claims.tenant_id,
                client_ip,
                rate_key,
                limit=limit,
                window_seconds=60,
            )
        except ApiError:
            return None, Response(status_code=429, content="请求过于频繁，请稍后再试".encode())

    record = await fetch_artifact(settings, claims.tenant_id, claims.artifact_id)
    if record is None or _is_expired(record.expires_at):
        return None, Response(status_code=404, content=_INVALID_LINK_TEXT.encode())
    return record, None


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

    **为什么是 `attachment` 而不是 `inline`**（2026-11-02 审计 S-02）：产物是
    「下载」不是「预览」。`inline` 会让浏览器按声明的 MIME 就地渲染——一旦
    某条产物的 MIME 是 `text/html`，脚本就在**平台同源**下执行，可以读走
    localStorage 里的访问令牌。改为 `attachment` 后浏览器只落地文件不渲染，
    这是这条链路上最便宜、也最不可绕过的一道闸（比 MIME 白名单更靠前）。

    **为什么要清换行符**：`filename` 来自插件声明，不是完全可信输入。
    `\r` / `\n` 会截断响应头（header injection），必须替换掉。

    Args:
        filename: 产物名称（可含中文）。

    Returns:
        str: 形如 `attachment; filename="artifact.csv"; filename*=UTF-8''%E4%B8%AD.csv`。
    """
    from urllib.parse import quote

    sanitized = filename.replace("\r", " ").replace("\n", " ")
    fallback = sanitized.encode("ascii", "replace").decode("ascii").replace('"', "_")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(sanitized)}"


__all__: list[str] = ["router"]
