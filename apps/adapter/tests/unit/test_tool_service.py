"""工具服务与连接器单元测试（架构文档 3.4 / 第 4 章 Q1 数据驱动模式）。"""

from __future__ import annotations

import pytest

from app.clients.keycloak_client import KeycloakClient
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.security import AuthContext
from app.schemas.tool import (
    ConnectionStatus,
    CreateConnectionRequest,
    CreateSyncJobRequest,
    ExecuteRequest,
    IntegrationStatus,
    ListConnectionsQuery,
    ToolCategory,
    ToolStatus,
)
from app.services.identity_service import IdentityService
from app.services.tool_service import (
    ToolService,
    new_connection_id,
    new_execution_id,
    new_job_id,
)
from app.tools.base import ConnectorRegistry, ToolConnector
from app.tools.email_connector import EmailConnector
from app.tools.github_connector import GitHubConnector

pytestmark = pytest.mark.unit

_AUTH = AuthContext(user_id="usr_1", tenant_id="tnt_1")


def _service() -> ToolService:
    """构造工具服务实例。

    Returns:
        ToolService: 服务实例。
    """
    return ToolService(settings=Settings(downstream_enabled=False))


def test_id_prefixes_follow_spec() -> None:
    """资源 ID 前缀符合架构文档 9.1。"""
    assert new_connection_id().startswith("conn_")
    assert new_execution_id().startswith("exec_")
    assert new_job_id().startswith("job_")


def test_connectors_are_registered() -> None:
    """两个示例连接器均已注册。"""
    registry = ConnectorRegistry.all()
    assert registry["email_connector"] is EmailConnector
    assert registry["github_connector"] is GitHubConnector


def test_registry_returns_tool_items() -> None:
    """T1 注册表返回全部工具类型与能力清单。"""
    data = _service().list_registry(_AUTH)
    assert {tool.tool_type for tool in data.tools} == {
        "email",
        "github",
        "wecom",
        "feishu",
        "wechat_mp",
    }
    email = next(tool for tool in data.tools if tool.tool_type == "email")
    assert email.display_name == "邮箱"
    assert email.category == ToolCategory.COMMUNICATION
    assert email.status == ToolStatus.AVAILABLE
    assert email.timeout_ms == 10_000
    assert {op.operation for op in email.operations} == {"send_message", "list_messages", "sync"}


def test_registry_is_self_describing_for_frontend() -> None:
    """注册表自带展示名与参数描述，前端无需硬编码（架构文档 4.1）。"""
    data = _service().list_registry(_AUTH)
    for tool in data.tools:
        assert tool.display_name
        for op in tool.operations:
            assert op.display_name
            assert op.params_schema.get("type") == "object"


def test_registry_marks_every_tool_as_demo_integration() -> None:
    """D-19：当前 5 个连接器全是桩实现，注册表必须**逐项**标注为演示。

    这条断言是"诚实标注"的护栏：任何一个工具被改成 `live` 而没有真实对接，
    都会在这里被拦下（同时提醒同步更新前端「演示」徽标的判据）。
    """
    data = _service().list_registry(_AUTH)
    assert data.tools, "注册表不得为空"
    for tool in data.tools:
        assert tool.integration_status == IntegrationStatus.DEMO, (
            f"工具 {tool.tool_type} 未标注为演示实现"
        )


def test_integration_status_comes_from_connector_declaration() -> None:
    """集成状态的取值来源是连接器类声明，而不是注册表 YAML。

    校验口径：`ToolConnector.INTEGRATION_STATUS` 的默认值必须是 `demo`
    （fail-honest：新增连接器在真正对接前不会被误标为已接入）。
    """
    assert ToolConnector.INTEGRATION_STATUS == IntegrationStatus.DEMO.value
    for connector_cls in ConnectorRegistry.all().values():
        assert getattr(connector_cls, "INTEGRATION_STATUS", "") == IntegrationStatus.DEMO.value, (
            f"连接器 {connector_cls.__name__} 未声明演示实现"
        )


def test_unknown_tool_type_returns_13001() -> None:
    """未注册工具类型返回 13001。"""
    service = _service()
    with pytest.raises(ApiError) as exc_info:
        service._get_spec("unknown_tool")  # noqa: SLF001
    assert exc_info.value.code == ErrorCode.TOOL_TYPE_UNSUPPORTED


async def test_create_connection_then_execute() -> None:
    """建立连接后可执行工具操作（T2 → T5 链路）。"""
    service = _service()
    conn = await service.create_connection(
        CreateConnectionRequest(tool_type="email", auth_code="code-123"), _AUTH
    )
    assert conn.status == ConnectionStatus.CONNECTED
    assert conn.connection_id.startswith("conn_")

    result = await service.execute(
        ExecuteRequest(
            tool_type="email",
            operation="send_message",
            params={"to": ["a@example.com"], "subject": "面试邀约", "body": "内容"},
        ),
        _AUTH,
    )
    assert result.status.value == "success"
    assert result.error is None
    assert result.execution_id.startswith("exec_")
    assert result.result is not None
    assert result.result["recipient_count"] == 1


async def test_execute_without_connection_returns_13002() -> None:
    """未连接工具执行返回 13002。"""
    with pytest.raises(ApiError) as exc_info:
        await _service().execute(
            ExecuteRequest(tool_type="email", operation="send_message", params={"to": ["a@b.com"]}),
            _AUTH,
        )
    assert exc_info.value.code == ErrorCode.TOOL_NOT_CONNECTED


async def test_execute_unsupported_operation_returns_13005() -> None:
    """操作不在能力清单内返回 13005。"""
    service = _service()
    await service.create_connection(
        CreateConnectionRequest(tool_type="email", auth_code="code-123"), _AUTH
    )
    with pytest.raises(ApiError) as exc_info:
        await service.execute(
            ExecuteRequest(tool_type="email", operation="delete_everything", params={}), _AUTH
        )
    assert exc_info.value.code == ErrorCode.TOOL_OPERATION_UNSUPPORTED


async def test_execute_email_requires_recipients() -> None:
    """邮箱发送缺少收件人返回 20001。"""
    service = _service()
    await service.create_connection(
        CreateConnectionRequest(tool_type="email", auth_code="code-123"), _AUTH
    )
    with pytest.raises(ApiError) as exc_info:
        await service.execute(
            ExecuteRequest(tool_type="email", operation="send_message", params={}), _AUTH
        )
    assert exc_info.value.code == ErrorCode.PARAM_INVALID


async def test_execute_github_requires_core_fields() -> None:
    """代码仓库操作缺少必填字段返回 20001。"""
    service = _service()
    await service.create_connection(
        CreateConnectionRequest(tool_type="github", auth_code="code-456"), _AUTH
    )
    with pytest.raises(ApiError) as exc_info:
        await service.execute(
            ExecuteRequest(
                tool_type="github", operation="create_pull_request", params={"owner": "x"}
            ),
            _AUTH,
        )
    assert exc_info.value.code == ErrorCode.PARAM_INVALID


async def test_execute_github_create_pull_request() -> None:
    """代码仓库创建合并请求返回业务化结果。"""
    service = _service()
    await service.create_connection(
        CreateConnectionRequest(tool_type="github", auth_code="code-456"), _AUTH
    )
    result = await service.execute(
        ExecuteRequest(
            tool_type="github",
            operation="create_pull_request",
            params={
                "owner": "org",
                "repo": "repo",
                "title": "feat: 新能力",
                "head": "feature/x",
                "base": "main",
            },
        ),
        _AUTH,
    )
    assert result.result is not None
    assert result.result["title"] == "feat: 新能力"


async def test_connect_with_empty_code_returns_13003() -> None:
    """空授权码返回 13003。"""
    connector = EmailConnector()
    with pytest.raises(ApiError) as exc_info:
        await connector.connect(auth_code="", redirect_uri="", tenant_id="tnt_1", user_id="usr_1")
    assert exc_info.value.code == ErrorCode.TOOL_AUTH_EXPIRED


async def test_disconnect_without_connection_id_returns_13002() -> None:
    """空连接 ID 断开返回 13002。"""
    connector = GitHubConnector()
    with pytest.raises(ApiError) as exc_info:
        await connector.disconnect(connection_id="", tenant_id="tnt_1", user_id="usr_1")
    assert exc_info.value.code == ErrorCode.TOOL_NOT_CONNECTED


async def test_github_sync_is_unsupported() -> None:
    """代码仓库工具不支持数据同步，返回 13005。"""
    connector = GitHubConnector()
    with pytest.raises(ApiError) as exc_info:
        await connector.sync(
            connection_id="conn_1",
            scope="all",
            params={},
            timeout_ms=10_000,
            tenant_id="tnt_1",
            user_id="usr_1",
        )
    assert exc_info.value.code == ErrorCode.TOOL_OPERATION_UNSUPPORTED


async def test_create_sync_job_and_query() -> None:
    """创建同步任务后可查询进度（T6 → T7 链路）。"""
    service = _service()
    await service.create_connection(
        CreateConnectionRequest(tool_type="email", auth_code="code-123"), _AUTH
    )
    job = await service.create_sync_job(
        CreateSyncJobRequest(tool_type="email", operation="sync", scope="inbox"), _AUTH
    )
    assert job.job_id.startswith("job_")
    detail = service.get_sync_job(job.job_id, _AUTH)
    assert detail.job_id == job.job_id
    assert detail.tool_type == "email"


async def test_get_sync_job_unknown_returns_20004() -> None:
    """未知同步任务返回 20004。"""
    with pytest.raises(ApiError) as exc_info:
        _service().get_sync_job("job_missing", _AUTH)
    assert exc_info.value.code == ErrorCode.RESOURCE_NOT_FOUND


async def test_delete_connection_removes_from_list() -> None:
    """断开连接后列表不再包含该连接。"""
    service = _service()
    conn = await service.create_connection(
        CreateConnectionRequest(tool_type="email", auth_code="code-123"), _AUTH
    )
    assert len((await service.list_connections(ListConnectionsQuery(), _AUTH)).items) == 1
    deleted = await service.delete_connection(conn.connection_id, _AUTH)
    assert deleted == conn.connection_id
    assert len((await service.list_connections(ListConnectionsQuery(), _AUTH)).items) == 0


async def test_delete_unknown_connection_returns_13002() -> None:
    """断开未知连接返回 13002。"""
    with pytest.raises(ApiError) as exc_info:
        await _service().delete_connection("conn_missing", _AUTH)
    assert exc_info.value.code == ErrorCode.TOOL_NOT_CONNECTED


def test_connector_rejects_duplicate_registration() -> None:
    """重复注册同一 connector_impl 必须失败。"""
    with pytest.raises(ValueError, match="重复注册"):
        ConnectorRegistry.register(EmailConnector)


def test_connector_rejects_missing_impl() -> None:
    """未声明 connector_impl 的连接器必须注册失败。"""

    class _Bad(ToolConnector):
        async def connect(self, **kwargs: object) -> dict[str, object]:
            return {}

        async def disconnect(self, **kwargs: object) -> None:
            return None

        async def execute(self, **kwargs: object) -> dict[str, object]:
            return {}

        async def sync(self, **kwargs: object) -> dict[str, object]:
            return {}

    with pytest.raises(ValueError, match="connector_impl"):
        ConnectorRegistry.register(_Bad)


def test_tool_endpoint_has_no_tool_type_branch() -> None:
    """api/v1/tool.py 不得出现按工具类型分支的硬编码（架构文档第 4 章）。

    校验方式：剥离注释与文档字符串后，源码中不得出现按 `tool_type` 取值的条件判断。
    """
    import ast
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[2] / "app" / "api" / "v1" / "tool.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))

    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        is_tool_type_attr = isinstance(left, ast.Attribute) and left.attr == "tool_type"
        is_tool_type_name = isinstance(left, ast.Name) and left.id == "tool_type"
        if is_tool_type_attr or is_tool_type_name:
            offenders.append(ast.dump(node)[:80])
    assert offenders == []


def test_missing_connector_impl_raises_config_error() -> None:
    """注册条目指向未实现连接器时返回 30004。"""
    from app.registry.loader import ToolRegistry, ToolSpecModel

    registry = ToolRegistry(
        version=1,
        tools=(
            ToolSpecModel(
                tool_type="ghost",
                display_name="幽灵工具",
                category="data",
                auth_type="token",
                status="available",
                timeout_ms=1000,
                connector_impl="ghost_connector",
                operations=({"operation": "noop"},),
            ),
        ),
    )
    service = ToolService(settings=Settings(), registry=registry)
    with pytest.raises(ApiError) as exc_info:
        service._get_connector(registry.tools[0])  # noqa: SLF001
    assert exc_info.value.code == ErrorCode.CONFIG_ERROR


def test_identity_service_accepts_injected_client() -> None:
    """身份服务可注入自定义客户端（T02 联调使用）。"""
    client = KeycloakClient("http://example.invalid", "officeai")
    service = IdentityService(settings=Settings(), keycloak=client)
    assert service._keycloak is client  # noqa: SLF001 - 验证依赖注入
