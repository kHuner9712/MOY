"""产物下载令牌（阶段三 B19，`architecture-phase3.md` §3.5「不暴露存储地址」）。

产物下载走平台代理 `/files/artifacts/{token}`：

- **为什么用令牌而不是裸 ID**：下载地址会出现在前端 DOM、复制到聊天窗口、被截图。
  裸 `artifact_id` 等于把「知道 ID 就能下载」写进契约；令牌里带租户与版本，
  并用 HMAC 签名，伪造一个别人的租户 ID 也解不开。
- **为什么不放 `/api/v1` 下**：阶段三端点表冻结为 16 条（`PHASE3_DOMAIN_ENDPOINTS`），
  下载是**内容代理**不是业务端点（无业务语义、无 JSON 信封、不参与版本化契约），
  把它塞进 `/api/v1` 会让端点计数与基线门禁失去意义。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Final

from app.core.config import Settings

#: 令牌分隔符（`.` 在 URL 里安全，无需再编码）
_SEPARATOR: Final[str] = "."
#: 签名长度（HMAC-SHA256 截断到 16 hex，足够防伪造且地址不至于长得离谱）
_SIGNATURE_LEN: Final[int] = 16
#: 无密钥时的开发兜底（生产必须配 `jwt_secret`；缺省行为在告警里体现）
_DEV_KEY: Final[str] = "officeai-artifact-download-dev"


def _key(settings: Settings | None) -> bytes:
    """取签名密钥。

    Args:
        settings: 配置实例（可为 None）。

    Returns:
        bytes: 密钥字节。
    """
    secret = getattr(settings, "jwt_secret", "") if settings is not None else ""
    return (secret or _DEV_KEY).encode("utf-8")


def make_token(
    *, tenant_id: str, artifact_id: str, version: int, settings: Settings | None = None
) -> str:
    """生成下载令牌。

    Args:
        tenant_id: 企业 ID。
        artifact_id: 产物 ID。
        version: 版本号（旧版本也有各自的下载地址）。
        settings: 配置实例。

    Returns:
        str: 形如 `<base64>.<signature>` 的令牌。
    """
    raw = f"{tenant_id}|{artifact_id}|{max(1, int(version))}".encode()
    payload = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    signature = hmac.new(_key(settings), payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload}{_SEPARATOR}{signature[:_SIGNATURE_LEN]}"


def parse_token(token: str, settings: Settings | None = None) -> tuple[str, str, int] | None:
    """解析并校验下载令牌。

    Args:
        token: 令牌字符串。
        settings: 配置实例。

    Returns:
        tuple[str, str, int] | None: `(tenant_id, artifact_id, version)`；非法返回 None。
    """
    if not token or _SEPARATOR not in token:
        return None
    payload, _, signature = token.rpartition(_SEPARATOR)
    expected = hmac.new(_key(settings), payload.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected[:_SIGNATURE_LEN], signature):
        return None
    padded = payload + "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError):  # pragma: no cover - 脏令牌
        return None
    parts = decoded.split("|")
    if len(parts) != 3:
        return None
    tenant_id, artifact_id, version_text = parts
    try:
        version = int(version_text)
    except ValueError:  # pragma: no cover - 脏令牌
        return None
    return tenant_id, artifact_id, version


__all__: list[str] = ["make_token", "parse_token"]
