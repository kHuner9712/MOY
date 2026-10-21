"""凭据加解密测试（阶段二 B03，架构文档 2.3 结论第 1 条）。

覆盖点：

1. AES-256-GCM 往返、随机 nonce（同明文两次密文不同）、12 字节 nonce；
2. 认证标签（tag）校验：篡改密文/错版本必须抛 `CredentialDecryptError`；
3. 密钥缺失/格式非法 → `30004`，且**文案可操作**（含生成命令）；
4. 密钥轮换：旧密钥挪到 LEGACY 后旧密文仍可解，新写入使用新版本号；
5. 启动校验：真实部署形态必须配密钥，development/test 允许缺失。
"""

from __future__ import annotations

import base64
import os

import pytest

from app.core.config import Settings
from app.core.crypto import (
    KEY_GENERATION_HINT,
    KEY_LENGTH_BYTES,
    NONCE_LENGTH_BYTES,
    CredentialCipher,
    CredentialDecryptError,
    build_keyring,
    decode_key,
    parse_legacy_keys,
    validate_startup,
)
from app.core.errors import ApiError, ErrorCode

pytestmark = pytest.mark.unit


def make_key() -> str:
    """生成一个合法的 base64 密钥。

    Returns:
        str: 32 字节随机密钥的 base64。
    """
    return base64.b64encode(os.urandom(KEY_LENGTH_BYTES)).decode("ascii")


def make_settings(**overrides: object) -> Settings:
    """构造带密钥的测试配置。

    Args:
        **overrides: 覆盖字段。

    Returns:
        Settings: 配置实例。
    """
    payload: dict[str, object] = {
        "env": "test",
        "credential_encryption_key": make_key(),
    }
    payload.update(overrides)
    return Settings(**payload)  # type: ignore[arg-type]


def test_encrypt_decrypt_roundtrip() -> None:
    """加解密往返得到原文。"""
    cipher = CredentialCipher.from_settings(make_settings())
    secret = cipher.encrypt("app-abcdef123456")
    assert cipher.decrypt(secret.cipher_text, secret.nonce, secret.key_version) == (
        "app-abcdef123456"
    )


def test_nonce_is_random_per_encryption() -> None:
    """同一明文两次加密必须产生不同 nonce 与不同密文（GCM 安全前提）。"""
    cipher = CredentialCipher.from_settings(make_settings())
    first = cipher.encrypt("same-plaintext")
    second = cipher.encrypt("same-plaintext")
    assert first.nonce != second.nonce
    assert first.cipher_text != second.cipher_text
    assert len(first.nonce) == NONCE_LENGTH_BYTES


def test_cipher_text_is_not_plaintext() -> None:
    """密文中不得出现明文片段。"""
    cipher = CredentialCipher.from_settings(make_settings())
    plaintext = "app-super-secret-key"
    secret = cipher.encrypt(plaintext)
    assert plaintext.encode() not in secret.cipher_text


def test_primary_key_version_starts_at_one() -> None:
    """未配置旧密钥时主密钥版本为 1。"""
    keyring, version = build_keyring(make_settings())
    assert version == 1
    assert keyring.keys() == {1}


def test_tampered_cipher_text_raises_decrypt_error() -> None:
    """篡改密文（认证 tag 校验失败）必须抛 CredentialDecryptError。"""
    cipher = CredentialCipher.from_settings(make_settings())
    secret = cipher.encrypt("app-key")
    tampered = bytes([secret.cipher_text[0] ^ 0xFF]) + secret.cipher_text[1:]
    with pytest.raises(CredentialDecryptError):
        cipher.decrypt(tampered, secret.nonce, secret.key_version)


def test_unknown_key_version_raises_decrypt_error() -> None:
    """密文的 key_version 不在密钥环中 → 解密失败（fail-closed）。"""
    cipher = CredentialCipher.from_settings(make_settings())
    secret = cipher.encrypt("app-key")
    with pytest.raises(CredentialDecryptError):
        cipher.decrypt(secret.cipher_text, secret.nonce, 99)


def test_decrypt_with_another_key_fails() -> None:
    """换用另一把密钥无法解密（防止密钥错配后静默读出垃圾数据）。"""
    first = CredentialCipher.from_settings(make_settings())
    secret = first.encrypt("app-key")
    second = CredentialCipher.from_settings(make_settings())
    with pytest.raises(CredentialDecryptError):
        second.decrypt(secret.cipher_text, secret.nonce, secret.key_version)


def test_missing_key_raises_actionable_config_error() -> None:
    """密钥缺失 → 30004，且文案含配置项名与生成命令（可操作）。"""
    with pytest.raises(ApiError) as excinfo:
        CredentialCipher.from_settings(Settings(env="test", credential_encryption_key=""))
    assert excinfo.value.code == ErrorCode.CONFIG_ERROR
    detail = excinfo.value.internal_detail or ""
    assert "OA_CREDENTIAL_ENCRYPTION_KEY" in detail
    assert KEY_GENERATION_HINT in detail


def test_invalid_base64_key_rejected() -> None:
    """非 base64 密钥 → 30004。"""
    with pytest.raises(ApiError) as excinfo:
        decode_key("not-base64!!", source="OA_CREDENTIAL_ENCRYPTION_KEY")
    assert excinfo.value.code == ErrorCode.CONFIG_ERROR


def test_wrong_length_key_rejected() -> None:
    """长度不是 32 字节 → 30004（避免把短密钥当 AES-256 用）。"""
    short = base64.b64encode(b"too-short").decode("ascii")
    with pytest.raises(ApiError) as excinfo:
        decode_key(short, source="OA_CREDENTIAL_ENCRYPTION_KEY")
    assert excinfo.value.code == ErrorCode.CONFIG_ERROR
    assert "32" in (excinfo.value.internal_detail or "")


def test_legacy_key_format_rejected() -> None:
    """旧密钥格式必须为 `版本:base64`。"""
    with pytest.raises(ApiError):
        parse_legacy_keys("no-version-prefix")


def test_key_rotation_keeps_old_ciphertext_readable() -> None:
    """轮换：旧密钥进 LEGACY 后旧密文仍可解，新加密使用新版本号。"""
    old_key = make_key()
    before = CredentialCipher.from_settings(Settings(env="test", credential_encryption_key=old_key))
    legacy_secret = before.encrypt("old-app-key")
    assert legacy_secret.key_version == 1

    new_key = make_key()
    after = CredentialCipher.from_settings(
        Settings(
            env="test",
            credential_encryption_key=new_key,
            credential_encryption_keys_legacy=f"1:{old_key}",
        )
    )
    # 旧密文仍可解（按行内 key_version 选密钥）
    assert (
        after.decrypt(legacy_secret.cipher_text, legacy_secret.nonce, legacy_secret.key_version)
        == "old-app-key"
    )
    # 新写入使用新版本号
    fresh = after.encrypt("new-app-key")
    assert fresh.key_version == 2
    assert after.decrypt(fresh.cipher_text, fresh.nonce, fresh.key_version) == "new-app-key"


def test_validate_startup_requires_key_in_real_deployment() -> None:
    """`business_store=postgres` 时缺少密钥 → 启动即失败。"""
    with pytest.raises(ApiError) as excinfo:
        validate_startup(
            Settings(
                env="test",
                business_store="postgres",
                database_url="postgresql://officeai:pw@db:5432/officeai",
                credential_encryption_key="",
            )
        )
    assert excinfo.value.code == ErrorCode.CONFIG_ERROR


def test_validate_startup_allows_missing_key_in_dev() -> None:
    """development/test + memory 后端允许缺失密钥（离线单测与演示）。"""
    validate_startup(Settings(env="test", business_store="memory", credential_encryption_key=""))


def test_validate_startup_rejects_bad_key_when_required() -> None:
    """必须配密钥时，密钥非法同样启动即失败。"""
    with pytest.raises(ApiError):
        validate_startup(Settings(env="production", credential_encryption_key="bad-key!!"))
