"""工具连接器抽象基类与注册机制。

设计目标（架构文档 3.4.1 设计原则 + 第 4 章 Q1）：
「新增工具只加 `tools/`，不改接口」——所有工具类型共享同一套抽象，
新增工具无需触碰 `api/v1/tool.py` 的任何一行代码。

安全约束（架构文档 9.3）：连接器返回与接收的数据中**禁止**出现凭据原文，
凭据由连接管理加密存储，本层只持有引用 ID。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar, Final

from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger

logger = get_logger(__name__)

#: 默认工具超时（毫秒），架构文档 3.0.4
DEFAULT_TIMEOUT_MS: Final[int] = 10_000


class ToolConnector(ABC):
    """工具连接器抽象基类。

    子类必须声明 `tool_type` 与 `connector_impl`，并实现全部抽象方法。
    """

    #: 工具类型标识（与注册表 `tool_type` 一致）
    tool_type: ClassVar[str] = ""

    #: 连接器实现标识（与注册表 `connector_impl` 一致）
    connector_impl: ClassVar[str] = ""

    #: 业务化展示名（禁止出现第三方产品原始名称之外的实现细节）
    display_name: ClassVar[str] = ""

    @abstractmethod
    async def connect(
        self,
        *,
        auth_code: str,
        redirect_uri: str,
        tenant_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        """发起工具授权连接。

        Args:
            auth_code: 授权码或令牌（严禁写入日志）。
            redirect_uri: 授权回调地址。
            tenant_id: 租户 ID。
            user_id: 用户 ID。

        Returns:
            dict[str, Any]: 连接结果，含 `connection_id` 与 `expires_at`。

        Raises:
            ApiError: 授权失败（13003 授权过期）或第三方异常（13006）。
        """
        raise NotImplementedError

    @abstractmethod
    async def disconnect(self, *, connection_id: str, tenant_id: str, user_id: str) -> None:
        """断开工具连接。

        Args:
            connection_id: 连接 ID。
            tenant_id: 租户 ID。
            user_id: 用户 ID。

        Raises:
            ApiError: 连接不存在（13002）。
        """
        raise NotImplementedError

    @abstractmethod
    async def execute(
        self,
        *,
        connection_id: str,
        operation: str,
        params: dict[str, Any],
        timeout_ms: int,
        tenant_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        """执行工具操作。

        Args:
            connection_id: 连接 ID。
            operation: 操作标识。
            params: 操作参数。
            timeout_ms: 超时声明（来自注册表）。
            tenant_id: 租户 ID。
            user_id: 用户 ID。

        Returns:
            dict[str, Any]: 业务化执行结果。

        Raises:
            ApiError: 操作不支持（13005）、工具无响应（13004）、第三方异常（13006）。
        """
        raise NotImplementedError

    @abstractmethod
    async def sync(
        self,
        *,
        connection_id: str,
        scope: str,
        params: dict[str, Any],
        timeout_ms: int,
        tenant_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        """创建数据同步任务。

        Args:
            connection_id: 连接 ID。
            scope: 同步范围。
            params: 同步参数。
            timeout_ms: 超时声明（来自注册表）。
            tenant_id: 租户 ID。
            user_id: 用户 ID。

        Returns:
            dict[str, Any]: 同步任务初始信息，含 `total_count`。

        Raises:
            ApiError: 操作不支持（13005）或第三方异常（13006）。
        """
        raise NotImplementedError

    def validate_operation(self, operation: str, allowed: tuple[str, ...]) -> None:
        """校验操作是否在能力清单内。

        Args:
            operation: 操作标识。
            allowed: 允许的操作标识元组（来自注册表）。

        Raises:
            ApiError: 操作不在能力清单（13005）。
        """
        if operation not in allowed:
            raise ApiError(
                ErrorCode.TOOL_OPERATION_UNSUPPORTED,
                internal_detail=f"tool_type={self.tool_type} operation={operation}",
            )


class ConnectorRegistry:
    """连接器注册表：`connector_impl` 标识 → 连接器类。"""

    _connectors: ClassVar[dict[str, type[ToolConnector]]] = {}

    @classmethod
    def register(cls, connector_cls: type[ToolConnector]) -> type[ToolConnector]:
        """注册连接器类（可作为装饰器使用）。

        Args:
            connector_cls: 连接器类，必须声明 `connector_impl`。

        Returns:
            type[ToolConnector]: 原类，便于装饰器写法。

        Raises:
            ValueError: `connector_impl` 为空或重复注册。
        """
        impl = getattr(connector_cls, "connector_impl", "")
        if not impl:
            raise ValueError(f"{connector_cls.__name__} 未声明 connector_impl")
        if impl in cls._connectors:
            raise ValueError(f"connector_impl 重复注册: {impl}")
        cls._connectors[impl] = connector_cls
        logger.debug("tool_connector_registered", connector_impl=impl)
        return connector_cls

    @classmethod
    def get(cls, connector_impl: str) -> type[ToolConnector] | None:
        """按实现标识查询连接器类。

        Args:
            connector_impl: 连接器实现标识。

        Returns:
            type[ToolConnector] | None: 连接器类；未注册返回 `None`。
        """
        return cls._connectors.get(connector_impl)

    @classmethod
    def all(cls) -> dict[str, type[ToolConnector]]:
        """全部已注册连接器。

        Returns:
            dict[str, type[ToolConnector]]: 实现标识到连接器类的映射副本。
        """
        return dict(cls._connectors)

    @classmethod
    def reset(cls) -> None:
        """清空注册表，供测试隔离用例。"""
        cls._connectors.clear()


def register_connector(connector_cls: type[ToolConnector]) -> type[ToolConnector]:
    """注册连接器（装饰器）。

    Args:
        connector_cls: 连接器类。

    Returns:
        type[ToolConnector]: 原类。
    """
    return ConnectorRegistry.register(connector_cls)
