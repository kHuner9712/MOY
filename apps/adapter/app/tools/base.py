"""工具连接器抽象基类与注册机制。

设计目标（架构文档 3.4.1 设计原则 + 第 4 章 Q1）：
「新增工具只加 `tools/`，不改接口」——所有工具类型共享同一套抽象，
新增工具无需触碰 `api/v1/tool.py` 的任何一行代码。

安全约束（架构文档 9.3）：连接器返回与接收的数据中**禁止**出现凭据原文，
凭据由连接管理加密存储，本层只持有引用 ID。

**能力声明规范（阶段二 B06）**：每个连接器必须在类上声明
`SUPPORTED_OPERATIONS`（取值与 `registry/tool_registry.yaml` 中该工具的
`operations[].operation` **严格一致**）。`ToolService` 构造时会逐个校验，
不一致即启动失败（`30004`）——避免「注册表说支持、实现却没写」的静默缺口在
用户点击时才暴露。

**部分成功语义（阶段二 B06）**：第三方批量操作常出现「整单成功、个别对象失败」。
此类结果用 `build_partial_result(...)` 构造：`status` 仍为 `success`，
失败明细放 `warnings`（业务语言），**不得**判整单失败（PRD P2-TL-02 第 4 条）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any, ClassVar, Final

from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger

logger = get_logger(__name__)

#: 默认工具超时（毫秒），架构文档 3.0.4
DEFAULT_TIMEOUT_MS: Final[int] = 10_000

#: 部分成功结果中的告警字段名（前端据此提示「已创建，N 位未成功」）
WARNINGS_KEY: Final[str] = "warnings"


def build_partial_result(**fields: Any) -> dict[str, Any]:
    """构造「部分成功」结果（整单成功 + 告警明细）。

    Args:
        **fields: 业务结果字段；`warnings` 为业务语言告警列表。

    Returns:
        dict[str, Any]: 含 `warnings` 的业务结果。
    """
    raw_warnings = fields.pop(WARNINGS_KEY, [])
    warnings = [str(item) for item in raw_warnings if str(item).strip()]
    payload: dict[str, Any] = {WARNINGS_KEY: warnings}
    payload.update(fields)
    return payload


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

    #: 本连接器支持的操作标识（**必须**与注册表 `operations[].operation` 一致）
    SUPPORTED_OPERATIONS: ClassVar[tuple[str, ...]] = ()

    #: 集成状态（D-19）：`demo` = 演示实现（桩实现，**不产生任何真实第三方调用**），
    #: `live` = 已接入真实第三方服务。
    #:
    #: 默认 `demo` 是**fail-honest** 取向：新增连接器在真正对接第三方之前，
    #: 无需声明即被前端标注为「演示」；只有完成真实对接并验证后才显式改成 `live`。
    #: 取反（默认 live）会让空壳实现默认伪装成可用能力，属于误导。
    INTEGRATION_STATUS: ClassVar[str] = "demo"

    @classmethod
    def declared_operations(cls) -> tuple[str, ...]:
        """取本连接器声明的能力清单。

        Returns:
            tuple[str, ...]: 操作标识元组。

        Raises:
            ApiError: 连接器未声明任何能力（30004 配置异常）。
        """
        if not cls.SUPPORTED_OPERATIONS:
            raise ApiError(
                ErrorCode.CONFIG_ERROR,
                internal_detail=f"connector {cls.__name__} declares no SUPPORTED_OPERATIONS",
            )
        return cls.SUPPORTED_OPERATIONS

    @classmethod
    def assert_registry_consistency(cls, registered: Sequence[str]) -> None:
        """校验连接器能力声明与注册表一致（启动期暴露配置缺口）。

        Args:
            registered: 注册表中该工具登记的操作标识序列。

        Raises:
            ApiError: 两侧能力集合不一致（30004）。
        """
        declared = set(cls.declared_operations())
        configured = {str(item) for item in registered if str(item).strip()}
        if declared == configured:
            return
        missing_in_code = sorted(configured - declared)
        missing_in_registry = sorted(declared - configured)
        raise ApiError(
            ErrorCode.CONFIG_ERROR,
            internal_detail=(
                f"connector {cls.connector_impl} operations mismatch "
                f"missing_in_code={missing_in_code} missing_in_registry={missing_in_registry}"
            ),
        )

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


__all__: list[str] = [
    "DEFAULT_TIMEOUT_MS",
    "WARNINGS_KEY",
    "ConnectorRegistry",
    "ToolConnector",
    "build_partial_result",
    "register_connector",
]
