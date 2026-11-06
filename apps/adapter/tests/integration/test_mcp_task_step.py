"""插件 MCP 步骤测试（矩阵第 3 项）。

验证四件事：

1. 加载器对 `tool_type="mcp"` 的窄例外只放行白名单内的形态，**没有 confirm 必须拒**；
2. 执行桥接会再复核 `personal.tool.use`；
3. 真实 HTTP server 的成功结果能变成文本产物；
4. 外部失败（工具自报失败/服务停掉/参数超限）都只是 `ActionResult(failed)`，
   **不向上抛**，不会拖垮任务。
"""

from __future__ import annotations

import base64
import json
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.business.credential_service import CredentialResolver
from app.business.mcp_service import McpServerService
from app.core.config import Settings
from app.core.errors import ErrorCode
from app.db.repositories.credential_repo import InMemoryCredentialStore
from app.db.repositories.mcp_repo import InMemoryMcpServerStore, McpServerRecord
from app.plugins.actions import RunContext
from app.plugins.loader import STAGE_CONFIRM, STAGE_TOOL, PluginRegistry
from app.services.mcp_runner import McpRunner
from app.services.mcp_tool_bridge import McpToolBridge
from tests.fixtures import minimal_mcp_server

pytestmark = pytest.mark.integration

_TEST_KEY = base64.b64encode(b"0" * 32).decode()

_MANIFEST_PATH = (
    Path(__file__).resolve().parents[2] / "app" / "plugins" / "manifests" / "plg_content_ops.json"
)


def _manifest_with_mcp_step(*, with_confirm: bool = True) -> dict:
    """在真实插件声明上替换一步为 MCP 步骤。

    Args:
        with_confirm: 是否声明确认。

    Returns:
        dict: 声明字典。
    """
    payload = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    # 复用最后一步的 tool 步骤（保留检索步骤，否则 `personal.file.read` 的声明
    # 会因为缺少上传输入/检索步骤被判"导出文件声明不完整"）
    step = dict(payload["steps"][-1])
    step.pop("tool_alternatives", None)
    step.update(
        {
            "title": "调用外部工具",
            "action": "tool",
            "tool_type": "mcp",
            "operation": "demo.echo",
        }
    )
    if with_confirm:
        step["confirm"] = {"reason": "确认后会把内容交给外部工具处理"}
    else:
        step.pop("confirm", None)
    payload["steps"][-1] = step
    # 工具原语需要 `personal.tool.use`（其余能力保留，避免影响其他步骤）
    keys = {item["key"] for item in payload["capabilities"]}
    if "personal.tool.use" not in keys:
        payload["capabilities"].append({"key": "personal.tool.use", "required": True})
    return payload


def test_loader_accepts_mcp_step_with_confirm() -> None:
    """带 confirm 的 MCP 步骤可以加载。"""
    registry = PluginRegistry()
    assert registry.load_manifest(_manifest_with_mcp_step()) is None


def test_loader_rejects_mcp_step_without_confirm() -> None:
    """MCP 一律视为对外动作：没有 confirm 拒绝加载。"""
    registry = PluginRegistry()
    failure = registry.load_manifest(_manifest_with_mcp_step(with_confirm=False))
    assert failure is not None
    assert failure.stage == STAGE_CONFIRM


def test_loader_rejects_malformed_mcp_operation() -> None:
    """动作标识必须是 `<server>.<tool>`。"""
    payload = _manifest_with_mcp_step()
    payload["steps"][-1]["operation"] = "demo"
    registry = PluginRegistry()
    failure = registry.load_manifest(payload)
    assert failure is not None
    assert failure.stage == STAGE_TOOL


def _settings() -> Settings:
    """构造测试配置。

    Returns:
        Settings: 配置。
    """
    return Settings(
        env="test",
        business_store="memory",
        credential_encryption_key=_TEST_KEY,
        mcp_enabled=True,
        mcp_allow_stdio=True,
        mcp_stdio_allowed_commands=sys.executable,
        mcp_allow_insecure_http=True,
    )


def _service() -> McpServerService:
    """构造独立服务。

    Returns:
        McpServerService: 服务。
    """
    conf = _settings()
    return McpServerService(
        settings=conf,
        store=InMemoryMcpServerStore(),
        credentials=CredentialResolver(settings=conf, store=InMemoryCredentialStore()),
    )


async def _register(service: McpServerService, endpoint: str) -> McpServerRecord:
    """登记测试 server。

    Args:
        service: 服务。
        endpoint: 地址。

    Returns:
        McpServerRecord: 登记。
    """
    return await service.register_server(
        "tenant-a",
        {
            "name": "demo",
            "transport": "http",
            "endpoint": endpoint,
            "allowed_tools": ["echo", "add", "fail"],
            "enabled": True,
        },
    )


@pytest.fixture
async def bridge() -> Iterator[tuple[McpToolBridge, str, str]]:
    """启动真实 server 并登记。

    Yields:
        tuple[McpToolBridge, str, str]: `(bridge, tenant_id, server_name)`。
    """
    server, endpoint = minimal_mcp_server.start_http_server()
    service = _service()
    record = await _register(service, endpoint)
    try:
        yield McpToolBridge(runner=McpRunner(service=service)), "tenant-a", record.name
    finally:
        minimal_mcp_server.stop_http_server(server)


def _ctx(**overrides: object) -> RunContext:
    """构造执行上下文。

    Args:
        **overrides: 覆盖项。

    Returns:
        RunContext: 上下文。
    """
    values: dict[str, object] = {
        "tenant_id": "tenant-a",
        "granted": frozenset({"personal.tool.use"}),
        "inputs": {"text": "hello"},
    }
    values.update(overrides)
    return RunContext(**values)  # type: ignore[arg-type]


async def test_bridge_success_returns_text_artifact(
    bridge: tuple[McpToolBridge, str, str],
) -> None:
    """真实调用成功 → 文本产物。"""
    tool_bridge, tenant_id, server_name = bridge
    result = await tool_bridge.execute(
        _ctx(tenant_id=tenant_id),
        {
            "tool_type": "mcp",
            "operation": f"{server_name}.echo",
            "produces": ["result"],
            "inputs": {"text": "hello-mcp"},
        },
    )
    assert result.status == "ok"
    assert result.artifacts[0].inline_text == "hello-mcp"


async def test_bridge_requires_capability(
    bridge: tuple[McpToolBridge, str, str],
) -> None:
    """没有 `personal.tool.use` → 失败（不调用外部）。"""
    tool_bridge, tenant_id, server_name = bridge
    result = await tool_bridge.execute(
        _ctx(tenant_id=tenant_id, granted=frozenset()),
        {"tool_type": "mcp", "operation": f"{server_name}.echo", "inputs": {"text": "x"}},
    )
    assert result.status == "failed"
    assert result.error_code == ErrorCode.CAPABILITY_NOT_GRANTED


async def test_bridge_tool_error_is_isolated(
    bridge: tuple[McpToolBridge, str, str],
) -> None:
    """工具自报失败 → failed（不抛异常）。"""
    tool_bridge, tenant_id, server_name = bridge
    result = await tool_bridge.execute(
        _ctx(tenant_id=tenant_id),
        {"tool_type": "mcp", "operation": f"{server_name}.fail", "inputs": {}},
    )
    assert result.status == "failed"
    assert result.retryable is False


async def test_bridge_oversized_arguments_rejected(
    bridge: tuple[McpToolBridge, str, str],
) -> None:
    """参数超过 32KB → 本地拒绝，不交给外部。"""
    tool_bridge, tenant_id, server_name = bridge
    result = await tool_bridge.execute(
        _ctx(tenant_id=tenant_id),
        {
            "tool_type": "mcp",
            "operation": f"{server_name}.echo",
            "inputs": {"text": "x" * 40_000},
        },
    )
    assert result.status == "failed"
    assert result.error_code == ErrorCode.PARAM_INVALID


async def test_bridge_connection_failure_is_isolated() -> None:
    """服务停掉后是 failed，不是异常。"""
    server, endpoint = minimal_mcp_server.start_http_server()
    service = _service()
    record = await _register(service, endpoint)
    minimal_mcp_server.stop_http_server(server)
    tool_bridge = McpToolBridge(runner=McpRunner(service=service))
    result = await tool_bridge.execute(
        _ctx(),
        {"tool_type": "mcp", "operation": f"{record.name}.echo", "inputs": {"text": "x"}},
    )
    assert result.status == "failed"
    assert result.error_code in {
        ErrorCode.TOOL_UNRESPONSIVE,
        ErrorCode.TOOL_THIRD_PARTY_ERROR,
    }
