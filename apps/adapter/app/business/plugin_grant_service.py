"""插件授权服务（阶段三 B17，`architecture-phase3.md` §2.5 / §3.6）。

职责只有三件，且都围绕「授权是数据边界」这一件事：

1. `snapshot()`：取某成员在某插件上的**有效**能力集合（撤销后立即消失）；
2. `grant()`：安装时写入授予集合（同时把没勾的能力记为撤销，保留审计痕迹）；
3. `assert_granted()`：**每步入口复检**（5s 缓存）——撤销授权必须在下一步内生效，
   而不是"下次任务才生效"（§2.5 撤销即时性）。

**为什么不用角色权限点**（PRD R4）：插件授权粒度是「成员 × 插件 × 能力」，
与角色正交。若塞进 `role_permission`，要么给每个插件加一批权限点（20 个上限立刻破），
要么让"读知识库"这种能力跨插件互相影响——两者都错。所以单独一张 `plugin_grant`。

**缓存口径**：本阶段用进程内 5 秒 TTL（与阶段二 `policy_service` 同体例）。
多副本下退化为"每副本各自 5 秒"，仍满足"撤销最多 1 步内生效"，不引入新的分布式依赖。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Final

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.db.repositories.plugin import PluginStore, get_plugin_store

logger = get_logger(__name__)

#: 授权快照缓存 TTL（秒）——与阶段二 `policy_service` 保持一致
GRANT_CACHE_TTL_SECONDS: Final[float] = 5.0


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    """授权快照缓存项。

    Attributes:
        expires_at: 过期时刻（`time.monotonic()`）。
        capabilities: 能力集合。
    """

    expires_at: float
    capabilities: frozenset[str]


class PluginGrantService:
    """插件授权服务（安装写入 + 运行期复检的唯一出口）。

    Attributes:
        _settings: 配置实例。
        _store: 插件存储。
        _cache: 进程内快照缓存（`(tenant, plugin, user) → (过期时刻, 能力集合)`）。
    """

    def __init__(self, settings: Settings | None = None, store: PluginStore | None = None) -> None:
        """初始化服务。

        Args:
            settings: 可选配置实例。
            store: 可选插件存储（测试注入）。
        """
        self._settings = settings or get_settings()
        self._store: PluginStore = store or get_plugin_store(self._settings)
        self._cache: dict[tuple[str, str, str], _CacheEntry] = {}

    @property
    def store(self) -> PluginStore:
        """当前插件存储（测试可据此断言）。

        Returns:
            PluginStore: 插件存储。
        """
        return self._store

    async def snapshot(self, tenant_id: str, plugin_id: str, user_id: str) -> frozenset[str]:
        """取有效能力集合（命中 5s 缓存则直接用）。

        Args:
            tenant_id: 企业 ID。
            plugin_id: 插件标识。
            user_id: 成员 ID。

        Returns:
            frozenset[str]: 有效能力集合。
        """
        key = (tenant_id, plugin_id, user_id)
        cached = self._cache.get(key)
        now = time.monotonic()
        if cached is not None and cached.expires_at > now:
            return cached.capabilities
        capabilities = frozenset(await self._store.get_grants(tenant_id, plugin_id, user_id))
        self._cache[key] = _CacheEntry(
            expires_at=now + GRANT_CACHE_TTL_SECONDS, capabilities=capabilities
        )
        return capabilities

    async def grant(
        self,
        *,
        tenant_id: str,
        plugin_id: str,
        user_id: str,
        capabilities: set[str],
    ) -> frozenset[str]:
        """覆盖写入成员能力并让缓存立即失效。

        Args:
            tenant_id: 企业 ID。
            plugin_id: 插件标识。
            user_id: 成员 ID。
            capabilities: 目标有效集合。

        Returns:
            frozenset[str]: 写入后的有效集合。
        """
        stored = await self._store.set_grants(tenant_id, plugin_id, user_id, capabilities)
        self.invalidate(tenant_id, plugin_id, user_id)
        logger.info(
            "plugin_grants_updated",
            tenant_id=tenant_id,
            plugin_id=plugin_id,
            user_id=user_id,
            granted=sorted(stored),
        )
        return frozenset(stored)

    async def assert_granted(
        self, *, tenant_id: str, plugin_id: str, user_id: str, required: set[str]
    ) -> None:
        """校验必需能力仍在（每步入口复检；缺失 → `16005`）。

        Args:
            tenant_id: 企业 ID。
            plugin_id: 插件标识。
            user_id: 成员 ID。
            required: 本次执行必需的权限集合。

        Raises:
            ApiError: 缺少能力（16005），`data.missing_capabilities` 由端点层填充。
        """
        granted = await self.snapshot(tenant_id, plugin_id, user_id)
        missing = sorted(required - granted)
        if missing:
            logger.warning(
                "plugin_grant_missing",
                tenant_id=tenant_id,
                plugin_id=plugin_id,
                user_id=user_id,
                missing=missing,
            )
            raise ApiError(
                ErrorCode.CAPABILITY_NOT_GRANTED,
                internal_detail=f"missing capabilities: {', '.join(missing)}",
            )

    def invalidate(self, tenant_id: str, plugin_id: str, user_id: str = "") -> None:
        """让缓存失效（授权变更 / 停用 / 卸载后调用）。

        Args:
            tenant_id: 企业 ID。
            plugin_id: 插件标识。
            user_id: 成员 ID；空串表示该插件下所有成员。
        """
        if user_id:
            self._cache.pop((tenant_id, plugin_id, user_id), None)
            return
        for key in [item for item in self._cache if item[0] == tenant_id and item[1] == plugin_id]:
            self._cache.pop(key, None)

    def clear(self) -> None:
        """清空缓存（测试隔离用）。"""
        self._cache.clear()


# ===========================================================================
# 进程级单例
# ===========================================================================
_service: PluginGrantService | None = None


def get_plugin_grant_service(settings: Settings | None = None) -> PluginGrantService:
    """获取插件授权服务单例。

    Args:
        settings: 可选配置实例。

    Returns:
        PluginGrantService: 进程内唯一实例。
    """
    global _service
    if _service is None:
        _service = PluginGrantService(settings)
    return _service


def reset_plugin_grant_service() -> None:
    """重置插件授权服务单例（测试隔离用）。"""
    global _service
    _service = None


__all__: list[str] = [
    "GRANT_CACHE_TTL_SECONDS",
    "PluginGrantService",
    "get_plugin_grant_service",
    "reset_plugin_grant_service",
]
