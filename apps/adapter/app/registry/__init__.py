"""注册表包：数据驱动的档位路由表与工具类型注册表。

设计意图（架构文档 6.2）：`registry/*.yaml` 是「改配置不改代码」的落点。
本包只负责加载与校验 YAML，不含任何业务逻辑，也不出现底座字样。
"""

from app.registry.loader import (
    ProfileRoute,
    ProfileRouteTable,
    ToolRegistry,
    ToolSpecModel,
    get_profile_routes,
    get_tool_registry,
)

__all__ = [
    "ProfileRoute",
    "ProfileRouteTable",
    "ToolRegistry",
    "ToolSpecModel",
    "get_profile_routes",
    "get_tool_registry",
]
