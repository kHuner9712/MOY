"""幂等键管理单元测试（架构文档 3.0.4 幂等条）。"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.idempotency import (
    IdempotencyManager,
    InMemoryIdempotencyStore,
    RedisIdempotencyStore,
    build_fingerprint,
    hash_request_body,
)

pytestmark = pytest.mark.unit

_KEY = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
_FP = build_fingerprint("POST", "/api/v1/model/generate", "tnt_1", "usr_1")


class _FakeRedis:
    """最小 Redis 替身，用于验证 RedisIdempotencyStore 的调用契约。"""

    def __init__(self) -> None:
        """初始化内存字典。"""
        self.store: dict[str, str] = {}
        self.expiry: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        """读取键值。

        Args:
            key: 键名。

        Returns:
            str | None: 键值。
        """
        return self.store.get(key)

    async def set(self, key: str, value: str, **kwargs: object) -> bool | None:
        """写入键值。

        Args:
            key: 键名。
            value: 键值。
            **kwargs: `nx` / `ex` 选项。

        Returns:
            bool | None: `nx` 模式下返回是否写入成功。
        """
        if kwargs.get("nx") and key in self.store:
            return None
        self.store[key] = value
        ttl = kwargs.get("ex")
        if isinstance(ttl, int):
            self.expiry[key] = ttl
        return True

    async def delete(self, key: str) -> int:
        """删除键。

        Args:
            key: 键名。

        Returns:
            int: 删除数量。
        """
        return 1 if self.store.pop(key, None) is not None else 0


async def test_first_call_acquires_key(manager: IdempotencyManager) -> None:
    """首次请求占用幂等键成功。"""
    assert await manager.check_or_set(_KEY, _FP) is True


async def test_duplicate_call_is_rejected(manager: IdempotencyManager) -> None:
    """重复请求不得再次占用幂等键。"""
    await manager.check_or_set(_KEY, _FP)
    assert await manager.check_or_set(_KEY, _FP) is False


async def test_empty_key_raises_param_invalid(manager: IdempotencyManager) -> None:
    """缺少幂等键时返回 20001。"""
    with pytest.raises(ApiError) as exc_info:
        await manager.check_or_set("", _FP)
    assert exc_info.value.code == ErrorCode.PARAM_INVALID


async def test_store_and_get_result(manager: IdempotencyManager) -> None:
    """首次结果可被缓存并回放。"""
    await manager.check_or_set(_KEY, _FP)
    assert await manager.get_result(_KEY) is None
    await manager.store_result(_KEY, {"base_id": "kb_1"})
    assert await manager.get_result(_KEY) == {"base_id": "kb_1"}


async def test_release_allows_retry(manager: IdempotencyManager) -> None:
    """释放后允许客户端重试。"""
    await manager.check_or_set(_KEY, _FP)
    await manager.release(_KEY)
    assert await manager.check_or_set(_KEY, _FP) is True


async def test_execute_returns_result_on_first_call(manager: IdempotencyManager) -> None:
    """首次执行返回业务结果且非重复。"""
    calls: list[int] = []

    async def factory() -> dict[str, int]:
        calls.append(1)
        return {"n": len(calls)}

    result, replayed = await manager.execute(_KEY, _FP, factory)
    assert result == {"n": 1}
    assert replayed is False


async def test_execute_replays_cached_result(manager: IdempotencyManager) -> None:
    """重复执行回放首次结果且不重复调用业务逻辑。"""
    calls: list[int] = []

    async def factory() -> dict[str, int]:
        calls.append(1)
        return {"n": len(calls)}

    first, _ = await manager.execute(_KEY, _FP, factory)
    second, replayed = await manager.execute(_KEY, _FP, factory)
    assert replayed is True
    assert first == second
    assert len(calls) == 1


async def test_execute_releases_key_on_failure(manager: IdempotencyManager) -> None:
    """执行失败必须释放幂等键，允许客户端重试。"""

    async def failing() -> None:
        raise ApiError(ErrorCode.MODEL_GENERATE_FAILED)

    with pytest.raises(ApiError):
        await manager.execute(_KEY, _FP, failing)
    assert await manager.check_or_set(_KEY, _FP) is True


async def test_execute_releases_key_on_unexpected_error(manager: IdempotencyManager) -> None:
    """未预期异常同样释放幂等键。"""

    async def boom() -> None:
        raise RuntimeError("unexpected")

    with pytest.raises(RuntimeError):
        await manager.execute(_KEY, _FP, boom)
    assert await manager.check_or_set(_KEY, _FP) is True


async def test_manager_ttl_comes_from_settings() -> None:
    """TTL 取自配置（架构文档默认 24 小时）。"""
    manager = IdempotencyManager(settings=Settings(idempotency_ttl_seconds=3600))
    assert manager.ttl_seconds == 3600
    assert Settings().idempotency_ttl_seconds == 86_400


async def test_in_memory_store_expires_entry() -> None:
    """超期条目被惰性清理。"""
    store = InMemoryIdempotencyStore()
    assert await store.acquire(_KEY, _FP, 60) is True
    # 人为推进时间，模拟超过 TTL
    store._entries[_KEY].created_at -= 90_000  # noqa: SLF001 - 测试需要构造超期场景
    assert await store.get(_KEY) is None


async def test_redis_store_acquire_is_atomic() -> None:
    """Redis 后端基于 SET NX 实现原子占用。"""
    store = RedisIdempotencyStore(_FakeRedis())  # type: ignore[arg-type]
    assert await store.acquire(_KEY, _FP, 60) is True
    assert await store.acquire(_KEY, _FP, 60) is False


async def test_redis_store_roundtrip() -> None:
    """Redis 后端结果写入后可回放。"""
    store = RedisIdempotencyStore(_FakeRedis())  # type: ignore[arg-type]
    await store.acquire(_KEY, _FP, 60)
    assert await store.get(_KEY) is None
    await store.store_result(_KEY, {"ok": True}, 60)
    assert await store.get(_KEY) == {"ok": True}


async def test_redis_store_release() -> None:
    """Redis 后端释放后可重新占用。"""
    store = RedisIdempotencyStore(_FakeRedis())  # type: ignore[arg-type]
    await store.acquire(_KEY, _FP, 60)
    await store.release(_KEY)
    assert await store.acquire(_KEY, _FP, 60) is True


async def test_same_key_different_request_returns_conflict(manager: IdempotencyManager) -> None:
    """同一幂等键复用于不同请求时返回 20005，避免结果交叉回放。"""
    other_fp = build_fingerprint("POST", "/api/v1/knowledge/bases", "tnt_1", "usr_1")
    assert await manager.check_or_set(_KEY, _FP) is True
    with pytest.raises(ApiError) as exc_info:
        await manager.check_or_set(_KEY, other_fp)
    assert exc_info.value.code == ErrorCode.CONFLICT


async def test_conflict_detected_after_result_stored(manager: IdempotencyManager) -> None:
    """结果回放后仍能识别键复用冲突。"""
    other_fp = build_fingerprint("DELETE", "/api/v1/tools/connections/conn_1", "tnt_1", "usr_1")
    await manager.check_or_set(_KEY, _FP)
    await manager.store_result(_KEY, {"ok": True})
    with pytest.raises(ApiError) as exc_info:
        await manager.check_or_set(_KEY, other_fp)
    assert exc_info.value.code == ErrorCode.CONFLICT


async def test_release_clears_conflict(manager: IdempotencyManager) -> None:
    """释放后同一键可用于其他请求。"""
    other_fp = build_fingerprint("POST", "/api/v1/knowledge/bases", "tnt_1", "usr_1")
    await manager.check_or_set(_KEY, _FP)
    await manager.release(_KEY)
    assert await manager.check_or_set(_KEY, other_fp) is True


async def test_redis_peek_returns_fingerprint() -> None:
    """Redis 后端可读取已占用指纹。"""
    store = RedisIdempotencyStore(_FakeRedis())  # type: ignore[arg-type]
    assert await store.peek(_KEY) is None
    await store.acquire(_KEY, _FP, 60)
    assert await store.peek(_KEY) == _FP


async def test_redis_store_result_preserves_fingerprint() -> None:
    """Redis 后端回写结果后指纹不丢失。"""
    store = RedisIdempotencyStore(_FakeRedis())  # type: ignore[arg-type]
    await store.acquire(_KEY, _FP, 60)
    await store.store_result(_KEY, {"ok": True}, 60)
    assert await store.peek(_KEY) == _FP
    assert await store.get(_KEY) == {"ok": True}


async def test_execute_detects_key_reuse_conflict(manager: IdempotencyManager) -> None:
    """execute 路径同样识别键复用冲突。"""
    other_fp = build_fingerprint("POST", "/api/v1/knowledge/search", "tnt_1", "usr_1")

    async def factory() -> dict[str, int]:
        return {"n": 1}

    await manager.execute(_KEY, _FP, factory)
    with pytest.raises(ApiError) as exc_info:
        await manager.execute(_KEY, other_fp, factory)
    assert exc_info.value.code == ErrorCode.CONFLICT


def test_build_fingerprint_is_stable() -> None:
    """请求指纹构造稳定且含四元组信息。"""
    fingerprint = build_fingerprint("post", "/x", "tnt_1", "usr_1")
    assert len(fingerprint) == 64, "指纹应为 SHA-256 十六进制摘要"
    assert fingerprint == build_fingerprint("POST", "/x", "tnt_1", "usr_1")


def test_build_fingerprint_matches_architecture_spec() -> None:
    """指纹严格符合架构文档 3.0.5 的实现强制口径。

    口径：`SHA256(method + "\n" + path + "\n" + tenant_id + "\n" + user_id + "\n" + body_sha256)`
    """
    import hashlib

    body_sha = hash_request_body('{"name":"知识库 A"}')
    expected = hashlib.sha256(
        "\n".join(["POST", "/api/v1/knowledge/bases", "tnt_1", "usr_1", body_sha]).encode("utf-8")
    ).hexdigest()
    actual = build_fingerprint("POST", "/api/v1/knowledge/bases", "tnt_1", "usr_1", body_sha)
    assert actual == expected, "指纹必须与架构文档 3.0.5 口径逐字节一致"


def test_build_fingerprint_uses_empty_body_fixed_digest() -> None:
    """请求体为空时摘要取固定值，不得跳过该项（架构文档 3.0.5）。"""
    import hashlib

    empty_sha = hashlib.sha256(b"").hexdigest()
    expected = hashlib.sha256(
        "\n".join(["POST", "/x", "tnt_1", "usr_1", empty_sha]).encode("utf-8")
    ).hexdigest()
    assert build_fingerprint("POST", "/x", "tnt_1", "usr_1") == expected
    assert build_fingerprint("POST", "/x", "tnt_1", "usr_1", "") == expected


def test_build_fingerprint_includes_body_hash() -> None:
    """指纹必须包含请求体摘要（QA BUG-05：否则同键不同体被静默回放）。"""
    same = build_fingerprint("POST", "/x", "tnt_1", "usr_1", "hash-abc")
    different = build_fingerprint("POST", "/x", "tnt_1", "usr_1", "hash-xyz")
    assert same != different, "不同请求体必须产生不同指纹"


def test_hash_request_body_is_canonical() -> None:
    """请求体摘要按规范化 JSON 计算：键序与空白不影响结果。"""
    first = hash_request_body('{"name":"知识库 A","visibility":"organization"}')
    second = hash_request_body('{"visibility":"organization","name":"知识库 A"}')
    third = hash_request_body('{"visibility": "organization", "name": "知识库 A"}')
    assert first == second == third, "键序与空白差异必须归一化"


def test_hash_request_body_differs_for_different_content() -> None:
    """不同请求体必须产生不同摘要。"""
    assert hash_request_body('{"name":"库A"}') != hash_request_body('{"name":"库B"}')


def test_hash_request_body_hides_content() -> None:
    """摘要不泄露原文。"""
    digest = hash_request_body('{"name":"知识库 A"}')
    assert "知识库" not in digest
    assert len(digest) == 64, "应为 sha256 十六进制摘要"


def test_hash_request_body_handles_empty() -> None:
    """空请求体返回空体固定摘要（不得跳过该项）。"""
    import hashlib

    fixed = hashlib.sha256(b"").hexdigest()
    assert hash_request_body(None) == fixed
    assert hash_request_body(b"") == fixed
    assert hash_request_body("") == fixed
