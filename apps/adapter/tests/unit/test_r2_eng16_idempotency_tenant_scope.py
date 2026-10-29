"""L-16：幂等存储键必须带租户命名空间（`oa:idem:{tenant_id}:{key}`）。

## 这一组用例在防什么风险

`Idempotency-Key` 是**客户端生成**的 UUID，服务端无法假设它全局唯一：弱随机源、
批量灌数据的脚本、从同一套模板复制出来的客户端都会撞键。修复前存储键只有
`oa:idem:{key}`（内存实现更是裸 key），于是同一个 UUID 在不同租户之间互相可见：

- **跨租户结果回放**：租户 B 的写请求命中租户 A 缓存的响应体并原样返回——
  一次真实的越权读，却被 `code=20007`（"重复请求"）包装成正常成功；
- **跨租户误冲突**：即使指纹不同（指纹含 tenant），`peek` 命中后也只会抛
  `20005`，让租户 B 的合法请求永久失败，直到 24h TTL 到期。

指纹**已经**含租户（架构 3.0.5），缺的是存储键那一侧。这里钉住：
租户隔离在**内存实现与 Redis 实现上语义一致**，且 `tenant_id` 缺省时
行为与修复前逐字节一致（不破坏既有 33 条用例与单测直连存储层的用法）。
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.idempotency import (
    IdempotencyManager,
    InMemoryIdempotencyStore,
    RedisIdempotencyStore,
    build_fingerprint,
    build_storage_key,
)

pytestmark = pytest.mark.unit

_KEY = "11111111-2222-4333-8444-555555555555"
_TENANT_A = "tnt_alpha"
_TENANT_B = "tnt_beta"
_FP_A = build_fingerprint("POST", "/api/v1/model/generate", _TENANT_A, "usr_1")
_FP_B = build_fingerprint("POST", "/api/v1/model/generate", _TENANT_B, "usr_1")


class _RecordingRedis:
    """记录写入键名的 Redis 替身（用于断言最终键的命名空间形态）。"""

    def __init__(self) -> None:
        """初始化内存字典。"""
        self.store: dict[str, str] = {}

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
        return True

    async def delete(self, key: str) -> int:
        """删除键。

        Args:
            key: 键名。

        Returns:
            int: 删除数量。
        """
        return 1 if self.store.pop(key, None) is not None else 0


def _manager() -> IdempotencyManager:
    """构造使用进程内存储的管理器。

    Returns:
        IdempotencyManager: 管理器。
    """
    return IdempotencyManager(store=InMemoryIdempotencyStore(), settings=Settings())


# ===========================================================================
# 键构造
# ===========================================================================
def test_build_storage_key_prefixes_tenant() -> None:
    """带租户时拼成 `{tenant}:{key}`。"""
    assert build_storage_key(_KEY, _TENANT_A) == f"{_TENANT_A}:{_KEY}"


def test_build_storage_key_without_tenant_is_unchanged() -> None:
    """不传租户（或空串/纯空白）时原样返回既有键，保持向后兼容。"""
    assert build_storage_key(_KEY) == _KEY
    assert build_storage_key(_KEY, "") == _KEY
    assert build_storage_key(_KEY, "   ") == _KEY
    assert build_storage_key(f"  {_KEY}  ", "  ") == _KEY, "首尾空白始终要归一化"


# ===========================================================================
# 内存实现：同键不同租户互不可见
# ===========================================================================
async def test_same_key_in_two_tenants_does_not_conflict() -> None:
    """同一个 UUID 在两个租户下都能占用成功（修复前这里会抛 20005）。"""
    manager = _manager()
    assert await manager.check_or_set(_KEY, _FP_A, _TENANT_A) is True
    assert await manager.check_or_set(_KEY, _FP_B, _TENANT_B) is True


async def test_same_key_in_two_tenants_does_not_replay_across_tenants() -> None:
    """租户 A 的结果不得被租户 B 回放（修复前这里是真实的越权读）。"""
    manager = _manager()
    await manager.check_or_set(_KEY, _FP_A, _TENANT_A)
    await manager.store_result(_KEY, {"owner": "tenant_a"}, _TENANT_A)
    assert await manager.get_result(_KEY, _TENANT_A) == {"owner": "tenant_a"}
    assert await manager.get_result(_KEY, _TENANT_B) is None, "跨租户不得读到别人的结果"


async def test_execute_replays_only_within_same_tenant() -> None:
    """`execute` 的回放严格按租户隔离。"""
    manager = _manager()
    calls: list[str] = []

    async def factory_a() -> dict[str, str]:
        calls.append("a")
        return {"tenant": "a"}

    async def factory_b() -> dict[str, str]:
        calls.append("b")
        return {"tenant": "b"}

    first, replayed_a = await manager.execute(_KEY, _FP_A, factory_a, tenant_id=_TENANT_A)
    second, replayed_b = await manager.execute(_KEY, _FP_B, factory_b, tenant_id=_TENANT_B)
    assert first == {"tenant": "a"}
    assert second == {"tenant": "b"}
    assert replayed_a is False
    assert replayed_b is False, "另一租户的首次请求不得被判为重复"
    assert calls == ["a", "b"]
    # 同租户内再次请求才是重复
    again, replayed = await manager.execute(_KEY, _FP_A, factory_a, tenant_id=_TENANT_A)
    assert again == {"tenant": "a"}
    assert replayed is True
    assert calls == ["a", "b"], "重复请求不得再执行一次业务逻辑"


async def test_release_is_tenant_scoped() -> None:
    """释放只影响本租户的键，另一租户的占用仍在。"""
    manager = _manager()
    await manager.check_or_set(_KEY, _FP_A, _TENANT_A)
    await manager.check_or_set(_KEY, _FP_B, _TENANT_B)
    await manager.release(_KEY, _TENANT_A)
    assert await manager.check_or_set(_KEY, _FP_A, _TENANT_A) is True, "本租户已释放，可重试"
    assert await manager.check_or_set(_KEY, _FP_B, _TENANT_B) is False, "另一租户不受影响"


# ===========================================================================
# Redis 实现：最终键必须是 `oa:idem:{tenant}:{key}`
# ===========================================================================
async def test_redis_key_carries_tenant_namespace() -> None:
    """Redis 侧最终键形如 `oa:idem:{tenant_id}:{key}`（不含租户维度的老键不再出现）。"""
    client = _RecordingRedis()
    store = RedisIdempotencyStore(client)  # type: ignore[arg-type]
    assert await store.acquire(build_storage_key(_KEY, _TENANT_A), _FP_A, 60) is True
    assert list(client.store) == [f"oa:idem:{_TENANT_A}:{_KEY}"]


async def test_redis_manager_isolates_tenants() -> None:
    """经管理器写入 Redis 时，两个租户各占一个键、互不覆盖。"""
    client = _RecordingRedis()
    manager = IdempotencyManager(
        store=RedisIdempotencyStore(client),  # type: ignore[arg-type]
        settings=Settings(),
    )
    await manager.execute(_KEY, _FP_A, lambda: _result({"n": 1}), tenant_id=_TENANT_A)
    await manager.execute(_KEY, _FP_B, lambda: _result({"n": 2}), tenant_id=_TENANT_B)
    assert sorted(client.store) == sorted(
        [f"oa:idem:{_TENANT_A}:{_KEY}", f"oa:idem:{_TENANT_B}:{_KEY}"]
    )
    assert await manager.get_result(_KEY, _TENANT_A) == {"n": 1}
    assert await manager.get_result(_KEY, _TENANT_B) == {"n": 2}


async def _result(payload: dict[str, int]) -> dict[str, int]:
    """异步返回固定结果（`execute` 要求无参异步可调用对象）。

    Args:
        payload: 返回值。

    Returns:
        dict[str, int]: 原样返回。
    """
    return payload
