"""产物下载令牌的过期与密钥约束（S9，2026-10-23）。

**这些用例存在的理由**：旧实现的令牌载荷是 `tenant|artifact|version`，
没有时间戳，`parse_token` 也不可能校验过期——链接一旦泄漏就**永久可下载**。
产物里是 Offer 薪资、简历、合同草稿，这不是「体验问题」，是数据泄露面。
"""

from __future__ import annotations

import pytest

from app.core.artifact_token import (
    DEFAULT_TTL_SECONDS,
    MAX_TTL_SECONDS,
    ArtifactTokenError,
    make_token,
    parse_token,
)
from app.core.config import Settings

SECRET = "artifact-token-unit-test-secret-32b"


def _settings(**overrides: object) -> Settings:
    """构造测试配置。

    Args:
        **overrides: 需要覆盖的字段。

    Returns:
        Settings: 配置实例。
    """
    payload: dict[str, object] = {"env": "test", "jwt_secret": SECRET}
    payload.update(overrides)
    return Settings(**payload)  # type: ignore[arg-type]


def test_roundtrip_carries_claims() -> None:
    """签发 → 解析应还原租户、产物、版本与过期时间。"""
    token = make_token(
        tenant_id="tnt_a", artifact_id="art_1", version=3, settings=_settings(), now=1_000
    )
    claims = parse_token(token, _settings(), now=1_001)
    assert claims is not None
    assert (claims.tenant_id, claims.artifact_id, claims.version) == ("tnt_a", "art_1", 3)
    assert claims.expires_at == 1_000 + DEFAULT_TTL_SECONDS


def test_expired_token_is_rejected() -> None:
    """到达过期时刻即失效（边界取 `<=`，不给「刚好一秒」留口子）。"""
    token = make_token(
        tenant_id="tnt_a", artifact_id="art_1", version=1, settings=_settings(), now=1_000
    )
    assert parse_token(token, _settings(), now=1_000 + DEFAULT_TTL_SECONDS - 1) is not None
    assert parse_token(token, _settings(), now=1_000 + DEFAULT_TTL_SECONDS) is None


def test_tampered_token_is_rejected() -> None:
    """改一个字符就应失效（HMAC 校验）。"""
    token = make_token(
        tenant_id="tnt_a", artifact_id="art_1", version=1, settings=_settings(), now=1_000
    )
    payload, _, signature = token.rpartition(".")
    tampered = f"{payload[:-1]}X.{signature}"
    assert parse_token(tampered, _settings(), now=1_000) is None


def test_empty_secret_refuses_to_sign() -> None:
    """密钥为空时拒绝签发，绝不回落到硬编码常量。"""
    with pytest.raises(ArtifactTokenError):
        make_token(
            tenant_id="tnt_a",
            artifact_id="art_1",
            version=1,
            settings=_settings(jwt_secret=""),
        )


def test_empty_secret_never_parses() -> None:
    """服务端密钥缺失时一律按无效令牌处理（不「无签名放行」）。"""
    token = make_token(
        tenant_id="tnt_a", artifact_id="art_1", version=1, settings=_settings(), now=1_000
    )
    assert parse_token(token, _settings(jwt_secret=""), now=1_000) is None


def test_ttl_must_be_positive_and_bounded() -> None:
    """TTL 必须落在 (0, 24h]，避免配置成事实上的永久令牌。"""
    for bad in (0, -1, MAX_TTL_SECONDS + 1):
        with pytest.raises(ValueError):
            make_token(
                tenant_id="tnt_a",
                artifact_id="art_1",
                version=1,
                settings=_settings(),
                ttl_seconds=bad,
            )


def test_ttl_can_come_from_settings() -> None:
    """配置项 `artifact_token_ttl_seconds` 生效。"""
    token = make_token(
        tenant_id="tnt_a",
        artifact_id="art_1",
        version=1,
        settings=_settings(artifact_token_ttl_seconds=120),
        now=2_000,
    )
    claims = parse_token(token, _settings(), now=2_119)
    assert claims is not None
    assert claims.expires_at == 2_120
    assert parse_token(token, _settings(), now=2_120) is None
