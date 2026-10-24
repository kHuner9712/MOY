"""工具注册表集成状态回归（D-19：演示实现必须如实标注）。

**为什么单独立文件**：`integration_status` 是"诚不诚实"的字段，不是功能字段。
它最典型的退化方式是「配置/响应说 live、实现其实是桩」，而这种退化不会让任何
功能用例变红——用户照样能点、接口照样返回 200，只是"演示"二字消失了。
所以这里用两条互补断言把它钉死：

1. **对外契约**：`GET /api/v1/tools/registry` 返回的 5 个工具逐项为 `demo`；
2. **实现对齐**：每个工具响应里的值与它**连接器类**的声明一致——
   防止响应层被单独改掉（例如为了好看硬编码成 `live`）而实现并未对接。

运行在 `OA_BUSINESS_STORE=memory` 下，不依赖 PostgreSQL。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.registry.loader import get_tool_registry
from app.schemas.tool import IntegrationStatus
from app.tools.base import ConnectorRegistry

pytestmark = pytest.mark.integration

#: T1 注册表路径
REGISTRY_PATH = "/api/v1/tools/registry"

#: 当前登记在册的 5 款工具
EXPECTED_TOOL_TYPES = {"email", "github", "wecom", "feishu", "wechat_mp"}


def test_registry_marks_all_tools_as_demo(client: TestClient, auth_headers: dict[str, str]) -> None:
    """对外契约：5 个工具全部标注为演示实现。"""
    response = client.get(REGISTRY_PATH, headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0

    tools = body["data"]["tools"]
    assert {tool["tool_type"] for tool in tools} == EXPECTED_TOOL_TYPES
    for tool in tools:
        assert tool["integration_status"] == IntegrationStatus.DEMO.value, (
            f"工具 {tool['tool_type']} 未标注为演示实现：{tool['integration_status']!r}"
        )


def test_registry_status_matches_connector_declaration(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """实现对齐：响应里的集成状态与连接器类声明逐项一致。

    若某天某个连接器真的完成了对接，它会把 `INTEGRATION_STATUS` 改成 `live`，
    这条断言随即要求注册表响应同步变化——两边不可能只改一边。
    """
    response = client.get(REGISTRY_PATH, headers=auth_headers)
    tools = response.json()["data"]["tools"]

    registry = get_tool_registry()
    mismatched: list[str] = []
    for tool in tools:
        spec = registry.get(tool["tool_type"])
        assert spec is not None, f"注册表响应出现了未登记的工具：{tool['tool_type']}"
        connector_cls = ConnectorRegistry.get(spec.connector_impl)
        expected = (
            IntegrationStatus.DEMO.value
            if connector_cls is None
            else str(connector_cls.INTEGRATION_STATUS or IntegrationStatus.DEMO.value)
        )
        if tool["integration_status"] != expected:
            mismatched.append(
                f"{tool['tool_type']}: 响应={tool['integration_status']!r} 实现={expected!r}"
            )
    assert mismatched == [], f"集成状态与连接器声明不一致：{mismatched}"
