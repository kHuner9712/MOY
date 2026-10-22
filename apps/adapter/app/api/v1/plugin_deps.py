"""插件域依赖装配（阶段三 B17，`architecture-phase3.md` §3.1 / D-16）。

这个文件只有一个职责：**把「插件层需要的门面」与「平台层的实现」接起来**，
从而让 `app/plugins/**` 永远不需要 `import app.services.*`：

- `get_plugin_registry()`：声明注册表（进程内单例，首次调用即加载并校验）；
- `get_plugins_service()`：目录/安装服务，按请求生效的配置构造；
- `build_action_gateway()`：`ActionGateway` 的实现注入点（B19 提供实现）。

**为什么 ActionGateway 用延迟导入**：实现类 `services/action_gateway.py` 依赖 M1/R7/T5
三个既有服务，而 `plugins/**` 不能反向依赖它们。若在模块顶层导入，会让
"插件层不碰 services" 这条分层铁律在 import 图上也破功（循环 + 越层同时出现）。
延迟到调用点导入，依赖方向仍是 `api → plugins → business → db`。
"""

from __future__ import annotations

from fastapi import Request

from app.business.plugin_grant_service import (
    PluginGrantService,
    get_plugin_grant_service,
)
from app.business.plugin_service import PluginService, get_plugin_service
from app.core.config import Settings, get_request_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.plugins.actions import ActionGateway
from app.plugins.loader import PluginRegistry, get_registry

logger = get_logger(__name__)


def get_plugin_registry() -> PluginRegistry:
    """获取声明注册表（失败时抛 `30004`，启动期失败好过运行期带病）。

    Returns:
        PluginRegistry: 注册表单例。

    Raises:
        ApiError: 存在无法加载的声明（30004）。
    """
    return get_registry()


def get_plugins_service(request: Request) -> PluginService:
    """按请求生效配置构造插件服务。

    单例缓存与配置解耦：`PluginService` 内部持有的 store/registry/plans 都由
    进程级单例提供，因此这里直接返回单例即可；`get_request_settings` 仅用于
    记录当前请求生效的环境（app.state.settings），避免测试注入的配置被忽略。

    Args:
        request: FastAPI 请求对象。

    Returns:
        PluginService: 插件服务。
    """
    settings: Settings = get_request_settings(request)
    service = get_plugin_service(settings)
    # 授权服务必须与 store 指向同一份数据（否则"刚授的权"读不到）
    del settings
    return service


def get_grants_service(request: Request) -> PluginGrantService:
    """按请求生效配置构造授权服务。

    Args:
        request: FastAPI 请求对象。

    Returns:
        PluginGrantService: 授权服务。
    """
    return get_plugin_grant_service(get_request_settings(request))


def build_action_gateway(settings: Settings | None = None) -> ActionGateway:
    """构造执行原语门面（实现由 B19 提供，这里只做延迟装配）。

    Args:
        settings: 可选配置实例。

    Returns:
        ActionGateway: 原语门面实现。

    Raises:
        ApiError: 实现尚未装配（30004）——B17 阶段只读接口不需要它，
            任务执行（B19）才会调用。
    """
    try:
        from app.services.action_gateway import ServicesActionGateway
    except ImportError as exc:  # pragma: no cover - B19 之前不可能被调用
        logger.error("action_gateway_not_ready")
        raise ApiError(
            ErrorCode.CONFIG_ERROR, internal_detail=f"action gateway unavailable: {exc}"
        ) from exc
    gateway: ActionGateway = ServicesActionGateway(settings)
    return gateway


__all__: list[str] = [
    "build_action_gateway",
    "get_grants_service",
    "get_plugin_registry",
    "get_plugins_service",
]
