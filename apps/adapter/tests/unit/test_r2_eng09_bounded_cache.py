"""R2-ENG-09：进程内权限缓存必须有界（`OrderedDict` 自实现 LRU）。

## 这一组用例在防什么风险

修复前 `policy_service._cache` 与 `plugin_grant_service._cache` 是**无上限**的
普通 `dict`，键含「租户 × 用户」（插件授权还多一维插件）。长跑进程里它们只会
单调增长：TTL 只决定"什么时候重建快照"，**不决定"什么时候删条目"**（惰性清理
只在同键再次被访问时触发）。十万用户 × 单条数百字节到数 KB = 数十 MB 常驻，
最终被 OOMKilled，而 killer 的日志不会指出是权限缓存吃掉的。

因此这里钉三件事：

1. **上限生效**：写入超过 `max_entries` 时最旧的条目被淘汰（不会静默丢失最新）；
2. **是 LRU 不是 FIFO**：`get` 命中的条目要刷新为新，否则热点条目会被误淘汰；
3. **两个服务都真的换成了有界实现**：防止有人把 `_cache` 改回 `dict` 而测试仍绿
   （"容器类型"本身就是被审计的对象，必须断言）。

全部走内存仓储，不连库。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.business.plugin_grant_service import (
    GRANT_CACHE_MAX_ENTRIES,
    PluginGrantService,
)
from app.business.policy_service import (
    DEFAULT_PERMISSION_CACHE_MAX_ENTRIES,
    BoundedLRUCache,
    PolicyService,
)
from app.core.config import Settings
from app.core.security import AuthContext
from app.db.repositories.grant_repo import InMemoryGrantStore
from app.db.repositories.knowledge_base_repo import InMemoryKnowledgeBaseStore

pytestmark = pytest.mark.unit

_BASE_PARAMS: dict[str, Any] = {
    "env": "test",
    "deploy_mode": "saas",
    "business_store": "memory",
    "auth_mode": "stub",
    "jwt_secret": "bounded-cache-unit-secret",
    "downstream_enabled": False,
    "ratelimit_enabled": False,
    "background_jobs_enabled": False,
}


def _settings(**overrides: Any) -> Settings:
    """构造配置实例。

    Args:
        **overrides: 覆盖项。

    Returns:
        Settings: 配置。
    """
    params = dict(_BASE_PARAMS)
    params.update(overrides)
    return Settings(**params)  # type: ignore[arg-type]


# ===========================================================================
# 1) 纯缓存的淘汰语义
# ===========================================================================
def test_cache_evicts_oldest_entry_when_over_capacity() -> None:
    """超过上限时**最旧的**条目被淘汰，最新写入的一定还在（R2-ENG-09 核心断言）。"""
    cache: BoundedLRUCache[str, int] = BoundedLRUCache(max_entries=2)
    cache["a"] = 1
    cache["b"] = 2
    cache["c"] = 3  # 触发淘汰：a 是最旧的
    assert len(cache) == 2
    assert cache.get("a") is None, "最旧条目必须被淘汰"
    assert cache.get("b") == 2
    assert cache.get("c") == 3


def test_cache_get_refreshes_recency_not_fifo() -> None:
    """`get` 命中要刷新为新——否则热点条目会按写入顺序被误淘汰（是 LRU 不是 FIFO）。"""
    cache: BoundedLRUCache[str, int] = BoundedLRUCache(max_entries=2)
    cache["a"] = 1
    cache["b"] = 2
    assert cache.get("a") == 1  # a 变成最近使用
    cache["c"] = 3  # 应当淘汰 b（最久未使用），而不是 a
    assert cache.get("a") == 1, "刚被访问过的条目不得被淘汰"
    assert cache.get("b") is None, "最久未使用的条目才是淘汰对象"
    assert len(cache) == 2


def test_cache_capacity_is_at_least_one() -> None:
    """上限传入 0 时按 1 处理，不会退化成"永远空缓存"。"""
    cache: BoundedLRUCache[str, int] = BoundedLRUCache(max_entries=0)
    cache["a"] = 1
    assert len(cache) == 1
    assert cache.get("a") == 1


def test_cache_pop_and_clear() -> None:
    """`pop` 只删指定键，`clear` 全清；`__iter__` 返回快照，可在遍历中 `pop`。"""
    cache: BoundedLRUCache[str, int] = BoundedLRUCache(max_entries=8)
    cache["a"] = 1
    cache["b"] = 2
    assert cache.pop("a") == 1
    assert cache.pop("missing") is None
    for key in cache:  # 遍历快照，边遍历边删不得抛 RuntimeError
        cache.pop(key)
    assert len(cache) == 0
    cache["c"] = 3
    cache.clear()
    assert len(cache) == 0


# ===========================================================================
# 2) 两个服务真的换成了有界实现
# ===========================================================================
def test_policy_service_uses_bounded_cache() -> None:
    """`PolicyService._cache` 必须是有界 LRU，且默认上限 4096。"""
    service = PolicyService(
        settings=_settings(),
        bases=InMemoryKnowledgeBaseStore(),
        grants=InMemoryGrantStore(),
    )
    assert isinstance(service._cache, BoundedLRUCache)  # noqa: SLF001 - 断言容器类型本身
    assert DEFAULT_PERMISSION_CACHE_MAX_ENTRIES == 4096
    assert len(service._cache) == 0  # noqa: SLF001
    assert service._cache._max_entries == DEFAULT_PERMISSION_CACHE_MAX_ENTRIES  # noqa: SLF001


async def test_policy_service_snapshot_cache_respects_capacity() -> None:
    """连续 3 个用户的快照在容量 2 下只保留最后 2 条（最旧的被淘汰）。"""
    service = PolicyService(
        settings=_settings(),
        bases=InMemoryKnowledgeBaseStore(),
        grants=InMemoryGrantStore(),
        max_cache_entries=2,
    )
    for user_id in ("usr_1", "usr_2", "usr_3"):
        await service.snapshot(AuthContext(user_id=user_id, tenant_id="tnt_lru"))
    assert len(service._cache) == 2  # noqa: SLF001 - 断言容量上限
    assert ("tnt_lru", "usr_1") not in service._cache  # noqa: SLF001
    assert ("tnt_lru", "usr_2") in service._cache  # noqa: SLF001
    assert ("tnt_lru", "usr_3") in service._cache  # noqa: SLF001


class _StubPluginStore:
    """插件存储替身：只实现快照用到的 `get_grants` / `set_grants`。"""

    async def get_grants(self, tenant_id: str, plugin_id: str, user_id: str) -> set[str]:
        """返回固定的能力集合。

        Args:
            tenant_id: 企业 ID。
            plugin_id: 插件标识。
            user_id: 成员 ID。

        Returns:
            set[str]: 能力集合。
        """
        del tenant_id, plugin_id, user_id
        return {"personal.file.write"}

    async def set_grants(
        self, tenant_id: str, plugin_id: str, user_id: str, capabilities: set[str]
    ) -> set[str]:
        """原样返回写入集合。

        Args:
            tenant_id: 企业 ID。
            plugin_id: 插件标识。
            user_id: 成员 ID。
            capabilities: 目标集合。

        Returns:
            set[str]: 写入后的集合。
        """
        del tenant_id, plugin_id, user_id
        return set(capabilities)


def test_plugin_grant_service_uses_bounded_cache() -> None:
    """`PluginGrantService._cache` 必须是有界 LRU，上限与 policy 取同一值。"""
    service = PluginGrantService(settings=_settings(), store=_StubPluginStore())  # type: ignore[arg-type]
    assert isinstance(service._cache, BoundedLRUCache)  # noqa: SLF001
    assert GRANT_CACHE_MAX_ENTRIES == DEFAULT_PERMISSION_CACHE_MAX_ENTRIES == 4096
    assert service._cache._max_entries == GRANT_CACHE_MAX_ENTRIES  # noqa: SLF001


async def test_plugin_grant_snapshot_cache_respects_capacity() -> None:
    """连续 3 个成员的能力快照在容量 2 下只保留最后 2 条。"""
    service = PluginGrantService(
        settings=_settings(),
        store=_StubPluginStore(),
        max_cache_entries=2,  # type: ignore[arg-type]
    )
    for user_id in ("usr_1", "usr_2", "usr_3"):
        await service.snapshot("tnt_lru", "plg_x", user_id)
    assert len(service._cache) == 2  # noqa: SLF001
    assert ("tnt_lru", "plg_x", "usr_1") not in service._cache  # noqa: SLF001
    assert ("tnt_lru", "plg_x", "usr_3") in service._cache  # noqa: SLF001
