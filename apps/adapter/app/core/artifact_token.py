"""产物下载令牌（阶段三 B19，`architecture-phase3.md` §3.5「不暴露存储地址」）。

产物下载走平台代理 `/files/artifacts/{token}`：

- **为什么用令牌而不是裸 ID**：下载地址会出现在前端 DOM、复制到聊天窗口、被截图。
  裸 `artifact_id` 等于把「知道 ID 就能下载」写进契约；令牌里带租户与版本，
  并用 HMAC 签名，伪造一个别人的租户 ID 也解不开。
- **为什么不放 `/api/v1` 下**：阶段三端点表冻结为 16 条（`PHASE3_DOMAIN_ENDPOINTS`），
  下载是**内容代理**不是业务端点（无业务语义、无 JSON 信封、不参与版本化契约），
  把它塞进 `/api/v1` 会让端点计数与基线门禁失去意义。

**阶段四收口（OA-REVIEW-002 / S9，2026-10-23）**：

1. 令牌载荷加入过期时间 `exp`（epoch 秒），`parse_token` 强制校验——此前的令牌
   **一旦泄漏即永久有效**，而产物里是 Offer 薪资、简历、合同草稿等最高敏数据。
2. 签名密钥为空时**拒绝签发**，不再回落硬编码 `_DEV_KEY`（那等于公开密钥）。
3. TTL 可配（`OA_ARTIFACT_TOKEN_TTL_SECONDS`，默认 900 秒），但封顶 24 小时——
   下载链接是「短期凭证」，不是资源标识；页面需要新链接时重新拉任务详情即可。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Final

from app.core.config import Settings

#: 令牌分隔符（`.` 在 URL 里安全，无需再编码）
_SEPARATOR: Final[str] = "."

#: 签名长度（HMAC-SHA256 截断到 16 hex，足够防伪造且地址不至于长得离谱）
_SIGNATURE_LEN: Final[int] = 16

#: 默认有效期（秒）：15 分钟。够用户从任务详情点开下载，又不给泄漏链接长期窗口。
DEFAULT_TTL_SECONDS: Final[int] = 900

#: 有效期上限（秒）：24 小时。防止配置成「事实上的永久令牌」。
MAX_TTL_SECONDS: Final[int] = 86_400


class ArtifactTokenError(RuntimeError):
    """令牌签发失败（原因不可回退到可预测密钥）。"""


@dataclass(frozen=True, slots=True)
class ArtifactToken:
    """解析后的下载令牌载荷。

    Attributes:
        tenant_id: 企业 ID。
        artifact_id: 产物 ID。
        version: 版本号。
        expires_at: 过期时间（UTC epoch 秒）。
    """

    tenant_id: str
    artifact_id: str
    version: int
    expires_at: int


def _secret(settings: Settings | None) -> bytes:
    """取签名密钥。

    **不允许兜底**：空密钥回落常量等于把签名算法与密钥一起公开。
    真实部署形态由 `validate_auth_startup` 在启动时保证非空；development/test
    若未配置则在此处显式失败（调用方应视为配置错误，而不是静默降级）。

    Args:
        settings: 配置实例（可为 None）。

    Returns:
        bytes: 密钥字节。

    Raises:
        ArtifactTokenError: `OA_JWT_SECRET` 为空。
    """
    secret = getattr(settings, "jwt_secret", "") if settings is not None else ""
    if not secret.strip():
        raise ArtifactTokenError(
            "产物下载令牌签名密钥未配置（OA_JWT_SECRET 为空）；拒绝签发可预测令牌"
        )
    return secret.encode("utf-8")


def make_token(
    *,
    tenant_id: str,
    artifact_id: str,
    version: int,
    settings: Settings | None = None,
    ttl_seconds: int | None = None,
    now: int | None = None,
) -> str:
    """生成下载令牌。

    Args:
        tenant_id: 企业 ID。
        artifact_id: 产物 ID。
        version: 版本号（旧版本也有各自的下载地址）。
        settings: 配置实例。
        ttl_seconds: 有效期（秒）；缺省取配置项，再缺省 `DEFAULT_TTL_SECONDS`。
        now: 当前时间戳（epoch 秒），仅供测试注入。

    Returns:
        str: 形如 `<base64>.<signature>` 的令牌。

    Raises:
        ArtifactTokenError: 签名密钥为空。
        ValueError: TTL 不在 `(0, MAX_TTL_SECONDS]` 区间。
    """
    effective_ttl = (
        ttl_seconds
        if ttl_seconds is not None
        else int(getattr(settings, "artifact_token_ttl_seconds", DEFAULT_TTL_SECONDS))
    )
    if effective_ttl <= 0 or effective_ttl > MAX_TTL_SECONDS:
        raise ValueError(
            f"artifact token ttl must be in (0, {MAX_TTL_SECONDS}], got {effective_ttl}"
        )
    expires_at = int(now if now is not None else time.time()) + effective_ttl
    raw = f"{tenant_id}|{artifact_id}|{max(1, int(version))}|{expires_at}".encode()
    payload = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    signature = hmac.new(_secret(settings), payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload}{_SEPARATOR}{signature[:_SIGNATURE_LEN]}"


def parse_token(
    token: str, settings: Settings | None = None, *, now: int | None = None
) -> ArtifactToken | None:
    """解析并校验下载令牌（含过期校验）。

    Args:
        token: 令牌字符串。
        settings: 配置实例。
        now: 当前时间戳（epoch 秒），仅供测试注入。

    Returns:
        ArtifactToken | None: 合法且未过期时返回载荷；否则返回 None。
    """
    if not token or _SEPARATOR not in token:
        return None
    payload, _, signature = token.rpartition(_SEPARATOR)
    try:
        key = _secret(settings)
    except ArtifactTokenError:
        # 服务端密钥缺失属于配置错误：一律按无效令牌处理，绝不「无签名放行」。
        return None
    expected = hmac.new(key, payload.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected[:_SIGNATURE_LEN], signature):
        return None
    padded = payload + "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError):  # pragma: no cover - 脏令牌
        return None
    parts = decoded.split("|")
    if len(parts) != 4:
        return None
    tenant_id, artifact_id, version_text, expires_text = parts
    try:
        version = int(version_text)
        expires_at = int(expires_text)
    except ValueError:  # pragma: no cover - 脏令牌
        return None
    current = int(now if now is not None else time.time())
    if expires_at <= current:
        return None
    return ArtifactToken(
        tenant_id=tenant_id,
        artifact_id=artifact_id,
        version=version,
        expires_at=expires_at,
    )


__all__: list[str] = [
    "DEFAULT_TTL_SECONDS",
    "MAX_TTL_SECONDS",
    "ArtifactToken",
    "ArtifactTokenError",
    "make_token",
    "parse_token",
]
