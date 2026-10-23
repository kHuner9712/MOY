"""档位路由（阶段四 T4-04.1，架构 §6.2 / §6.3 / F-8）。

**这是 F-8 的修复点**：此前 `ProfileRoute.resolve_app_key()` 直接读 `os.environ`，
而 4 档密钥早已是 `Settings` 的字段（`dify_code_app_key` 等）。两条读法并存导致：

1. **注入式配置失效**：`create_app(settings=...)` 注入的密钥到不了路由层，
   测试只能用环境变量模拟——本仓阶段二 QA BUG-01/02 已经为同类问题付过一次代价；
2. **"配了 4 档"无法断言**：契约测试只能间接看目录 `status`，看不出"4 个档位
   是否真的解析出 4 个**不同**的密钥"——而这正是 F-8 的行径：四档全落到同一个默认 key。

本模块是"档位 → 密钥/可用性"的**唯一**判定处：
`Settings` → 环境变量名映射 → `ProfileRoute.resolve_app_key(env=...)`。

**运行期可用性（B-3）**：`status` 不再取 yaml 的静态值，而是"该档能否解析出密钥"。
例外是桩模式（`downstream_enabled=false`）：那时根本没有真实密钥，也没有上游调用，
可用性沿用 yaml 声明——否则所有离线用例的目录都会变成全不可用，掩盖真正的配置缺陷。
"""

from __future__ import annotations

from typing import Final

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.registry.loader import ProfileRoute, ProfileRouteTable, get_profile_routes

logger = get_logger(__name__)

#: 「密钥齐备」的最低要求：主路由 + 降级链都要有值才算"配好"？（不，任一可用即可）
#: 这里登记的是**诊断**用的档位顺序（与 yaml 一致，便于日志比对）
PROFILE_ORDER: Final[tuple[str, ...]] = ("general", "code", "long_context", "reasoning")


def app_env_map(settings: Settings) -> dict[str, str]:
    """把 `Settings` 的 4 档密钥摊平成"环境变量名 → 值"映射。

    为什么保留环境变量名这一层：`profile_routes.yaml` 里登记的是**变量名**
    （密钥值不落配置文件），`ProfileRoute.resolve_app_key(env=...)` 接收的也是映射。
    这样"哪个档位对应哪个变量"仍然只由 yaml 决定，代码里没有第二份映射。

    Args:
        settings: 配置实例。

    Returns:
        dict[str, str]: 变量名到密钥值的映射（缺省为空串）。
    """
    return {
        "OA_DIFY_DEFAULT_APP_KEY": settings.dify_default_app_key,
        "OA_DIFY_CODE_APP_KEY": settings.dify_code_app_key,
        "OA_DIFY_LONG_CONTEXT_APP_KEY": settings.dify_long_context_app_key,
        "OA_DIFY_REASONING_APP_KEY": settings.dify_reasoning_app_key,
        "OA_DIFY_FALLBACK_APP_KEY": settings.dify_fallback_app_key,
        "OA_DIFY_AGENT_APP_KEY": settings.dify_agent_app_key,
    }


class ProfileRouter:
    """档位 → 路由 / 密钥 / 可用性。"""

    def __init__(
        self, settings: Settings | None = None, routes: ProfileRouteTable | None = None
    ) -> None:
        """初始化。

        Args:
            settings: 可选配置实例。
            routes: 可选路由表（缺省从注册表加载）。
        """
        self._settings = settings or get_settings()
        self._routes = routes or get_profile_routes()

    @property
    def settings(self) -> Settings:
        """配置实例。

        Returns:
            Settings: 配置。
        """
        return self._settings

    @property
    def routes(self) -> ProfileRouteTable:
        """路由表。

        Returns:
            ProfileRouteTable: 路由表。
        """
        return self._routes

    def route(self, profile: str) -> ProfileRoute | None:
        """取档位路由（不做可用性判定）。

        Args:
            profile: 档位值。

        Returns:
            ProfileRoute | None: 路由；未登记返回 None。
        """
        return self._routes.get(profile)

    def app_key(self, profile: str) -> str:
        """按"主路由 → 降级链"解析密钥。

        Args:
            profile: 档位值。

        Returns:
            str: 首个可用的密钥；未登记或都未配置时返回空串。
        """
        route = self.route(profile)
        if route is None:
            return ""
        return route.resolve_app_key(app_env_map(self._settings))

    def uses_fallback(self, profile: str) -> bool:
        """该档是否正在走降级链（主 key 缺失但降级 key 有值）。

        只用于可观测（启动日志与 `/readyz`）：运维需要知道"编程档其实跑在通用应用上"。

        Args:
            profile: 档位值。

        Returns:
            bool: 走降级链时返回 True。
        """
        route = self.route(profile)
        if route is None:
            return False
        env = app_env_map(self._settings)
        if env.get(route.default_app_env, ""):
            return False
        return any(env.get(name, "") for name in route.fallback_app_env)

    def is_available(self, profile: str) -> bool:
        """运行期可用性（B-3）。

        桩模式（`downstream_enabled=false`）沿用 yaml 声明：那时没有真实密钥、
        也没有上游调用，按密钥判可用性会让离线用例的目录全变 `unavailable`，
        反而掩盖真实配置缺陷。

        Args:
            profile: 档位值。

        Returns:
            bool: 可用返回 True。
        """
        route = self.route(profile)
        if route is None:
            return False
        if not self._settings.downstream_enabled:
            return route.status == "available"
        return bool(self.app_key(profile))

    def available_profiles(self) -> list[str]:
        """当前可用的档位列表（按 yaml 顺序）。

        Returns:
            list[str]: 档位值列表。
        """
        return [route.profile for route in self._routes.routes if self.is_available(route.profile)]

    def unavailable_profiles(self) -> list[str]:
        """当前不可用的档位列表（启动告警与 `/readyz` 用）。

        Returns:
            list[str]: 档位值列表。
        """
        return [
            route.profile for route in self._routes.routes if not self.is_available(route.profile)
        ]

    def log_routing(self) -> None:
        """打一条启动日志：4 档各自是否配置、是否走降级。

        **只打"有没有配"与"是否降级"，绝不打印密钥值**。
        """
        detail = {
            route.profile: {
                "configured": bool(self.app_key(route.profile)),
                "fallback": self.uses_fallback(route.profile),
                "available": self.is_available(route.profile),
            }
            for route in self._routes.routes
        }
        missing = self.unavailable_profiles()
        if missing:
            logger.warning("profile_routing_incomplete", profiles=detail, unavailable=missing)
            return
        logger.info("profile_routing_ready", profiles=detail)


__all__: list[str] = [
    "PROFILE_ORDER",
    "ProfileRouter",
    "app_env_map",
]
