"""凭据加解密（阶段二 D-10，架构文档 2.3 结论第 1 条）。

安全口径（逐条落实）：

1. **算法**：AES-256-GCM（`cryptography` 的 `AESGCM`），12 字节随机 nonce，
   密文含 16 字节认证 tag；同一明文每次加密结果不同（nonce 随机）。
2. **密钥来源**：`OA_CREDENTIAL_ENCRYPTION_KEY`（base64 编码的 32 字节）；
   轮换期旧密钥 `OA_CREDENTIAL_ENCRYPTION_KEYS_LEGACY`（格式 `1:base64,2:base64`），
   解密时按行内 `key_version` 选密钥，**加密永远用新密钥**。
3. **密钥缺失不静默降级**：真实部署形态（`business_store=postgres` 或
   `env=production`）启动即失败（30004）；development/test 允许缺失，
   但一旦需要加解密仍立即失败，并给出可操作文案（含生成命令）。
4. **明文只在进程内存存活一个请求的生命周期**：不落日志、不进响应、不进缓存。

密钥轮换步骤（写入运维手册）：新增 `OA_CREDENTIAL_ENCRYPTION_KEY` 为新密钥，
把旧密钥挪到 `OA_CREDENTIAL_ENCRYPTION_KEYS_LEGACY`（沿用其原 `key_version`），
重启后新写入用新密钥、旧密文仍可解；全部重加密完成后再移除旧密钥。
"""

from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger

logger = get_logger(__name__)

#: 密钥长度（AES-256）
KEY_LENGTH_BYTES: Final[int] = 32

#: GCM nonce 长度（NIST SP 800-38D 推荐 12 字节）
NONCE_LENGTH_BYTES: Final[int] = 12

#: 当前主密钥版本号（首次配置即 1）
PRIMARY_KEY_VERSION: Final[int] = 1

#: 生成密钥的可操作提示（出现在启动失败文案与运维文档中）
KEY_GENERATION_HINT: Final[str] = (
    'python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"'
)


class CredentialDecryptError(Exception):
    """凭据解密失败（缺失密钥/版本不存在/认证标签校验失败）。

    该异常**只允许**在 `business/credential_service.py` 内部被捕获并转换为
    `11007`；任何情况下都不得转换为「使用全局凭据」的回退路径。
    """


@dataclass(frozen=True, slots=True)
class EncryptedSecret:
    """加密结果（三要素整体落库，缺一不可解）。

    Attributes:
        cipher_text: 密文（含 16 字节 GCM 认证 tag）。
        nonce: 12 字节随机 nonce。
        key_version: 加密所用密钥版本号。
    """

    cipher_text: bytes
    nonce: bytes
    key_version: int


def decode_key(raw: str, *, source: str) -> bytes:
    """解码 base64 密钥并校验长度。

    Args:
        raw: base64 编码的密钥。
        source: 配置项名称（仅用于错误文案定位）。

    Returns:
        bytes: 32 字节密钥。

    Raises:
        ApiError: 非法 base64 或长度不足（30004）。
    """
    try:
        decoded = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ApiError(
            ErrorCode.CONFIG_ERROR,
            internal_detail=(f"{source} 不是合法的 base64；请用 {KEY_GENERATION_HINT} 重新生成"),
        ) from exc
    if len(decoded) != KEY_LENGTH_BYTES:
        raise ApiError(
            ErrorCode.CONFIG_ERROR,
            internal_detail=(
                f"{source} 解码后必须为 {KEY_LENGTH_BYTES} 字节，实际 {len(decoded)} 字节；"
                f"请用 {KEY_GENERATION_HINT} 重新生成"
            ),
        )
    return decoded


def parse_legacy_keys(raw: str) -> dict[int, bytes]:
    """解析轮换期旧密钥串。

    Args:
        raw: 形如 `1:base64key,2:base64key` 的配置值（允许为空）。

    Returns:
        dict[int, bytes]: 版本号 → 密钥。

    Raises:
        ApiError: 格式非法（30004）。
    """
    keys: dict[int, bytes] = {}
    for item in (raw or "").split(","):
        entry = item.strip()
        if not entry:
            continue
        version_text, separator, encoded = entry.partition(":")
        if not separator or not version_text.strip().isdigit():
            raise ApiError(
                ErrorCode.CONFIG_ERROR,
                internal_detail=(
                    "OA_CREDENTIAL_ENCRYPTION_KEYS_LEGACY 格式必须为 '1:base64key,2:base64key'"
                ),
            )
        keys[int(version_text)] = decode_key(
            encoded.strip(), source="OA_CREDENTIAL_ENCRYPTION_KEYS_LEGACY"
        )
    return keys


def build_keyring(settings: Settings) -> tuple[dict[int, bytes], int]:
    """构造密钥环。

    **版本号口径**（轮换的关键）：主密钥版本 = 旧密钥最大版本 + 1；
    无旧密钥时为 `1`。这样「把旧密钥挪到 LEGACY + 配置新主密钥」即可完成轮换：
    旧密文按其 `key_version` 命中原密钥继续可解，新写入用新版本号。

    Args:
        settings: 配置实例。

    Returns:
        tuple[dict[int, bytes], int]: `(版本 → 密钥, 主密钥版本)`。

    Raises:
        ApiError: 主密钥缺失或非法（30004，含可操作文案）。
    """
    primary_raw = (settings.credential_encryption_key or "").strip()
    legacy = parse_legacy_keys(settings.credential_encryption_keys_legacy)

    if not primary_raw:
        raise ApiError(
            ErrorCode.CONFIG_ERROR,
            internal_detail=(
                "缺少 OA_CREDENTIAL_ENCRYPTION_KEY（32 字节密钥的 base64）。"
                f"生成命令：{KEY_GENERATION_HINT}；"
                "写入 deploy/.env 后重启服务。已绑定的知识库在密钥缺失时不允许静默降级。"
            ),
        )

    primary = decode_key(primary_raw, source="OA_CREDENTIAL_ENCRYPTION_KEY")
    primary_version = max(legacy) + 1 if legacy else PRIMARY_KEY_VERSION
    keyring: dict[int, bytes] = dict(legacy)
    keyring[primary_version] = primary
    return keyring, primary_version


class CredentialCipher:
    """AES-256-GCM 凭据加解密器。

    Attributes:
        _primary_version: 加密时使用的密钥版本。
        _ciphers: 版本号 → AESGCM 实例。
    """

    def __init__(
        self, keyring: dict[int, bytes], primary_version: int = PRIMARY_KEY_VERSION
    ) -> None:
        """初始化加解密器。

        Args:
            keyring: 版本号 → 32 字节密钥。
            primary_version: 加密使用的密钥版本。

        Raises:
            ApiError: 主密钥版本不在密钥环中（30004）。
        """
        if primary_version not in keyring:
            raise ApiError(
                ErrorCode.CONFIG_ERROR,
                internal_detail=f"主密钥版本 {primary_version} 不在密钥环中",
            )
        self._primary_version = primary_version
        self._ciphers: dict[int, AESGCM] = {
            version: AESGCM(material) for version, material in keyring.items()
        }

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> CredentialCipher:
        """按配置构造加解密器。

        Args:
            settings: 可选配置实例。

        Returns:
            CredentialCipher: 加解密器。

        Raises:
            ApiError: 密钥缺失或非法（30004）。
        """
        conf = settings or get_settings()
        keyring, primary_version = build_keyring(conf)
        return cls(keyring, primary_version)

    @property
    def primary_key_version(self) -> int:
        """加密使用的密钥版本。"""
        return self._primary_version

    @property
    def known_versions(self) -> tuple[int, ...]:
        """密钥环中可解密的版本号（升序）。"""
        return tuple(sorted(self._ciphers))

    def encrypt(self, plaintext: str) -> EncryptedSecret:
        """加密明文。

        Args:
            plaintext: 待加密明文（如知识库访问密钥）。

        Returns:
            EncryptedSecret: 密文 / nonce / 密钥版本。

        Raises:
            ValueError: 明文为空（空凭据没有意义，属调用方缺陷）。
        """
        if not plaintext:
            raise ValueError("credential plaintext must not be empty")
        nonce = os.urandom(NONCE_LENGTH_BYTES)
        cipher_text = self._ciphers[self._primary_version].encrypt(
            nonce, plaintext.encode("utf-8"), None
        )
        return EncryptedSecret(
            cipher_text=cipher_text, nonce=nonce, key_version=self._primary_version
        )

    def decrypt(self, cipher_text: bytes, nonce: bytes, key_version: int) -> str:
        """解密。

        Args:
            cipher_text: 密文（含认证 tag）。
            nonce: 加密时的 nonce。
            key_version: 加密时的密钥版本。

        Returns:
            str: 明文。

        Raises:
            CredentialDecryptError: 版本不存在、认证失败或密文损坏。
        """
        cipher = self._ciphers.get(key_version)
        if cipher is None:
            raise CredentialDecryptError(f"unknown key_version={key_version}")
        try:
            plaintext = cipher.decrypt(nonce, cipher_text, None)
        except InvalidTag as exc:
            raise CredentialDecryptError("gcm authentication failed") from exc
        except ValueError as exc:  # pragma: no cover - nonce 长度异常等防御分支
            raise CredentialDecryptError("malformed ciphertext") from exc
        return plaintext.decode("utf-8")


def validate_startup(settings: Settings | None = None) -> None:
    """启动期密钥校验。

    仅在真实部署形态（`settings.credential_key_required`）下强制：
    密钥缺失/非法直接抛 `30004`，让「配置漏填」在启动时暴露，
    而不是等到第一个用户点知识库时才 11007。

    Args:
        settings: 可选配置实例。

    Raises:
        ApiError: 必须配置但缺失/非法（30004）。
    """
    conf = settings or get_settings()
    if not conf.credential_key_required:
        logger.debug("credential_key_check_skipped", env=conf.env, store=conf.business_store)
        return
    CredentialCipher.from_settings(conf)
    logger.info("credential_key_validated", env=conf.env, store=conf.business_store)


__all__ = [
    "KEY_GENERATION_HINT",
    "KEY_LENGTH_BYTES",
    "NONCE_LENGTH_BYTES",
    "PRIMARY_KEY_VERSION",
    "CredentialCipher",
    "CredentialDecryptError",
    "EncryptedSecret",
    "build_keyring",
    "decode_key",
    "parse_legacy_keys",
    "validate_startup",
]
