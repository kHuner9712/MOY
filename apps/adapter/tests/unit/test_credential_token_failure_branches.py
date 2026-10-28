"""QA-10（审计 2026-10-28）：凭据服务与下载令牌的**失败分支**单测。

审计发现以下分支从未被任何用例触碰（覆盖率空窗，属"错了也不知道"的高危区）：
- `RedisCredentialCache` 的四条降级路径（连接失败 / 读失败 / 写失败 / 删失败）
  与三条 JSON/结构防御分支；
- `InMemoryCredentialCache` 的过期淘汰与零 TTL 分支；
- `artifact_token.parse_token` 的密钥缺失分支与带合法签名的脏载荷分支。

修前基线：credential_service 68.16% / artifact_token 94.37%（仅本两模块口径）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

import pytest

from app.business.credential_service import (
    InMemoryCredentialCache,
    RedisCredentialCache,
    build_cache,
    ref_suffix,
)
from app.core.artifact_token import make_token, parse_token
from app.core.config import Settings

pytestmark = pytest.mark.unit


def _settings() -> Settings:
    """带 jwt_secret 的桩配置（令牌签发需要）。"""
    return Settings(env="test", jwt_secret="qa10-test-secret-0123456789abcdef")


# ---------------------------------------------------------------------------
# RedisCredentialCache 降级与防御分支（QA-10）
# ---------------------------------------------------------------------------


class _StubRedis:
    """可控失败的 redis 客户端替身。"""

    def __init__(
        self,
        *,
        get_error: Exception | None = None,
        get_value: str | None = None,
        set_error: Exception | None = None,
        delete_error: Exception | None = None,
    ) -> None:
        self._get_error = get_error
        self._get_value = get_value
        self._set_error = set_error
        self._delete_error = delete_error
        self.deleted: list[str] = []

    async def get(self, key: str) -> str | None:
        if self._get_error is not None:
            raise self._get_error
        return self._get_value

    async def set(self, key: str, value: str, ex: int) -> None:
        if self._set_error is not None:
            raise self._set_error

    async def delete(self, key: str) -> None:
        if self._delete_error is not None:
            raise self._delete_error
        self.deleted.append(key)


def _cache_with_stub(
    stub: _StubRedis, fallback: InMemoryCredentialCache | None = None
) -> tuple[RedisCredentialCache, InMemoryCredentialCache]:
    """构造挂了桩客户端的 Redis 缓存（bypass 惰性连接）。"""
    fb = fallback or InMemoryCredentialCache()
    cache = RedisCredentialCache("redis://stub:6379/0", fallback=fb)
    cache._client = stub  # noqa: SLF001 - 直连替身，绕过真实 redis
    cache._degraded = False  # noqa: SLF001
    return cache, fb


@pytest.mark.asyncio
async def test_redis_read_failure_falls_back_to_memory() -> None:
    """读缓存抛异常 → 记 warning 并回落内存缓存（缓存抖动按未命中处理）。"""
    fallback = InMemoryCredentialCache()
    await fallback.set("k", {"c": "x"}, 60)
    cache, _ = _cache_with_stub(_StubRedis(get_error=ConnectionError("boom")), fallback)

    assert await cache.get("k") == {"c": "x"}


@pytest.mark.asyncio
async def test_redis_corrupt_payload_returns_none() -> None:
    """缓存内容非合法 JSON / 非 dict → 按未命中处理，不抛异常。"""
    cache, _ = _cache_with_stub(_StubRedis(get_value="not-json{"))
    assert await cache.get("k") is None

    cache2, _ = _cache_with_stub(_StubRedis(get_value=json.dumps(["list"])))
    assert await cache2.get("k") is None


@pytest.mark.asyncio
async def test_redis_write_failure_falls_back_to_memory() -> None:
    """写缓存失败 → 回落内存缓存，主流程不受影响。"""
    stub = _StubRedis(set_error=ConnectionError("boom"))
    cache, fallback = _cache_with_stub(stub)

    await cache.set("k", {"c": "x"}, 60)

    assert await fallback.get("k") == {"c": "x"}


@pytest.mark.asyncio
async def test_redis_delete_failure_still_clears_fallback() -> None:
    """删缓存失败由 TTL 兜底，但内存兜底缓存必须照删。"""
    fallback = InMemoryCredentialCache()
    await fallback.set("k", {"c": "x"}, 60)
    cache, _ = _cache_with_stub(_StubRedis(delete_error=ConnectionError("boom")), fallback)

    await cache.delete("k")

    assert await fallback.get("k") is None


@pytest.mark.asyncio
async def test_degraded_cache_uses_memory_only() -> None:
    """连接失败降级后：读/写/删全部只走内存兜底，且不再重试连接。"""
    fallback = InMemoryCredentialCache()
    cache = RedisCredentialCache("redis://stub:6379/0", fallback=fallback)
    cache._degraded = True  # noqa: SLF001 - 直接置降级态

    await cache.set("k", {"c": "x"}, 60)
    assert await cache.get("k") == {"c": "x"}
    await cache.delete("k")
    assert await fallback.get("k") is None


@pytest.mark.asyncio
async def test_lazy_connect_failure_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    """首连失败 → 打降级标记并返回 None（后续请求不再尝试连接）。"""
    import redis.asyncio as redis_asyncio

    async def _boom(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("no redis")

    class _FakeFactory:
        @staticmethod
        def from_url(*args: Any, **kwargs: Any) -> Any:
            class _Client:
                async def ping(self) -> None:
                    await _boom()

            return _Client()

    monkeypatch.setattr(redis_asyncio, "from_url", _FakeFactory.from_url)

    cache = RedisCredentialCache("redis://stub:6379/0")
    assert await cache._get_client() is None  # noqa: SLF001
    assert cache._degraded is True  # noqa: SLF001
    # 降级后再次取用仍是 None（不重试）
    assert await cache._get_client() is None  # noqa: SLF001


def test_build_cache_backend_selection() -> None:
    """memory 后端 → 进程内缓存；redis 后端 → Redis 缓存。"""
    memory_settings = Settings(env="test", idempotency_backend="memory")
    assert isinstance(build_cache(memory_settings), InMemoryCredentialCache)

    redis_settings = Settings(
        env="test", idempotency_backend="redis", redis_url="redis://stub:6379/0"
    )
    assert isinstance(build_cache(redis_settings), RedisCredentialCache)


# ---------------------------------------------------------------------------
# InMemoryCredentialCache 过期/零 TTL 分支（QA-10）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_cache_expires_entry() -> None:
    """过期条目读取即淘汰并返回 None。"""
    cache = InMemoryCredentialCache()
    await cache.set("k", {"c": "x"}, 1)
    # 直接把过期时间拨到过去（避免真实等待）
    _expires_at, value = cache._entries["k"]  # noqa: SLF001
    cache._entries["k"] = (0.0, value)  # noqa: SLF001 - expires_at=0 必已过期

    assert await cache.get("k") is None
    assert "k" not in cache._entries  # noqa: SLF001


@pytest.mark.asyncio
async def test_memory_cache_zero_ttl_not_stored() -> None:
    """ttl<=0 表示不缓存。"""
    cache = InMemoryCredentialCache()
    await cache.set("k", {"c": "x"}, 0)

    assert await cache.get("k") is None
    assert "k" not in cache._entries  # noqa: SLF001


def test_ref_suffix_edge_cases() -> None:
    """空引用返回空串；常规取后 N 位。"""
    assert ref_suffix("") == ""
    assert ref_suffix("   ") == ""
    assert ref_suffix("credential_abcdef123456") == "123456"


# ---------------------------------------------------------------------------
# artifact_token 失败分支（QA-10）
# ---------------------------------------------------------------------------


def test_parse_token_missing_secret_returns_none() -> None:
    """服务端密钥缺失：一律按无效令牌处理，绝不「无签名放行」。"""
    settings = Settings(env="test", jwt_secret="")
    assert parse_token("whatever-token", settings) is None


def test_parse_token_valid_signature_dirty_payload_returns_none() -> None:
    """带**合法签名**但载荷是脏数据（非法 base64）→ None（修复盲签风险口径）。"""
    settings = _settings()
    secret = settings.jwt_secret.encode()

    dirty_payload = base64.urlsafe_b64encode(b"\xff\xfe not-utf8 \x00\x01").decode("ascii")
    # 令牌由 payload + "." + signature 组成；签名按模块同款算法伪造（合法但载荷脏）
    signature = hmac.new(secret, dirty_payload.encode("ascii"), hashlib.sha256).hexdigest()[:32]
    token = f"{dirty_payload}.{signature}"

    assert parse_token(token, settings) is None


def test_parse_token_valid_signature_bad_version_returns_none() -> None:
    """带合法签名但 version/expires 非整数 → None。"""
    settings = _settings()
    secret = settings.jwt_secret.encode()

    payload = base64.urlsafe_b64encode(b"tnt_1|art_1|not-a-number|9999999999").decode("ascii")
    signature = hmac.new(secret, payload.encode("ascii"), hashlib.sha256).hexdigest()[:32]
    token = f"{payload}.{signature}"

    assert parse_token(token, settings) is None


def test_make_and_parse_roundtrip_unchanged() -> None:
    """正常路径回归守护：签发→解析往返成功（确认新增失败分支没误伤主链路）。"""
    settings = _settings()
    token = make_token(tenant_id="tnt_1", artifact_id="art_1", version=2, settings=settings)
    parsed = parse_token(token, settings)

    assert parsed is not None
    assert parsed.tenant_id == "tnt_1"
    assert parsed.artifact_id == "art_1"
    assert parsed.version == 2
