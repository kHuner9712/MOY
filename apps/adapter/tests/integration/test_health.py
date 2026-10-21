"""健康检查与全局约定契约测试（架构文档 3.0.1 / 3.0.2 / 10.2 D-07）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def test_healthz_needs_no_auth(client: TestClient) -> None:
    """`/healthz` 免鉴权（架构文档 3.0.2）。"""
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["message"] == "success"
    assert body["data"]["status"] == "ok"
    assert body["data"]["app"] == "officeai-adapter"


def test_readyz_needs_no_auth(client: TestClient) -> None:
    """`/readyz` 免鉴权，且返回逐项检查结果。"""
    response = client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["status"] == "ready"
    names = {check["name"] for check in body["data"]["checks"]}
    assert {"config", "profile_routes", "tool_registry"} <= names


def test_health_responses_never_expose_secrets(client: TestClient) -> None:
    """健康检查不得泄露连接串或密钥。"""
    for path in ("/healthz", "/readyz"):
        text = client.get(path).text.lower()
        for leaked in ("password", "secret", "token", "postgres://", "redis://", "amqp"):
            assert leaked not in text


def test_trace_id_is_returned_in_header(client: TestClient) -> None:
    """响应头回写 X-Trace-Id，便于全链路对账（架构文档 9.3）。"""
    response = client.get("/healthz")
    assert response.headers.get("X-Trace-Id")


def test_trace_id_is_propagated_from_upstream(client: TestClient) -> None:
    """上游传入的 trace_id 被透传而非重新生成。"""
    response = client.get("/healthz", headers={"X-Trace-Id": "upstream-trace-001"})
    assert response.headers["X-Trace-Id"] == "upstream-trace-001"


def test_upstream_trace_id_is_sanitized(client: TestClient) -> None:
    """上游 trace_id 中的非法字符被清理。"""
    response = client.get("/healthz", headers={"X-Trace-Id": "bad value!!<>;drop"})
    returned = response.headers["X-Trace-Id"]
    assert returned == "badvaluedrop"


def test_business_endpoint_requires_auth(client: TestClient) -> None:
    """业务端点缺少令牌返回 20002。"""
    response = client.get("/api/v1/model/catalog")
    assert response.status_code == 401
    body = response.json()
    assert body["code"] == 20002
    assert body["message"] == "登录状态已失效，请重新登录"
    assert body["data"] is None


def test_invalid_token_scheme_returns_20002(client: TestClient) -> None:
    """非 Bearer 认证方案返回 20002。"""
    response = client.get("/api/v1/model/catalog", headers={"Authorization": "Basic abc"})
    assert response.status_code == 401
    assert response.json()["code"] == 20002


def test_garbage_token_returns_20002(client: TestClient) -> None:
    """非法令牌返回 20002，且不泄露解析细节。"""
    response = client.get("/api/v1/model/catalog", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401
    body = response.json()
    assert body["code"] == 20002
    assert "jwt" not in body["message"].lower()
    assert "decode" not in body["message"].lower()


def test_unknown_route_returns_20004(client: TestClient) -> None:
    """未知路由返回统一 20004 响应。"""
    response = client.get("/api/v1/unknown-domain/endpoint")
    assert response.status_code == 404
    assert response.json()["code"] == 20004


def test_openapi_schema_is_generated(client: TestClient) -> None:
    """OpenAPI 文档可生成（T02 由此产出接口规范）。"""
    schema = client.get("/openapi.json").json()
    assert schema["info"]["version"] == "1.0.0"
    assert "/api/v1/model/generate" in schema["paths"]


def test_openapi_does_not_leak_base_layer_terms(client: TestClient) -> None:
    """对外文档中不得出现底座术语（架构文档 1.4 第 2 条）。"""
    text = client.get("/openapi.json").text.lower()
    for leaked in ("dify", "milvus", "litellm", "chat-messages", "datasets", "workflow_run"):
        assert leaked not in text


def test_exactly_30_business_endpoints(client: TestClient) -> None:
    """业务端点数量必须恰为 30（契约基线）。

    注意：同一路径上的多个方法在 OpenAPI 中合并为一个 path 项，
    因此这里按「路径 + 方法」组合计数。
    """
    schema = client.get("/openapi.json").json()
    operations = [
        (method.upper(), path)
        for path, methods in schema["paths"].items()
        if path.startswith("/api/v1")
        for method in methods
    ]
    assert len(operations) == 30


@pytest.mark.integration
@pytest.mark.contract
class TestEndpointRegistry:
    """30 个契约端点的注册与鉴权校验。"""

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("post", "/api/v1/model/generate"),
            ("post", "/api/v1/model/generate/stream"),
            ("get", "/api/v1/model/catalog"),
            ("get", "/api/v1/model/usage"),
            ("post", "/api/v1/knowledge/bases"),
            ("get", "/api/v1/knowledge/bases"),
            ("post", "/api/v1/knowledge/bases/kb_1/documents"),
            ("get", "/api/v1/knowledge/bases/kb_1/documents/doc_1"),
            ("post", "/api/v1/knowledge/bases/kb_1/documents/doc_1/reindex"),
            ("delete", "/api/v1/knowledge/bases/kb_1/documents/doc_1"),
            ("post", "/api/v1/knowledge/search"),
            ("post", "/api/v1/agent/tasks"),
            ("get", "/api/v1/agent/tasks"),
            ("get", "/api/v1/agent/tasks/task_1"),
            ("get", "/api/v1/agent/tasks/task_1/steps"),
            ("post", "/api/v1/agent/tasks/task_1/cancel"),
            ("get", "/api/v1/agent/tasks/task_1/events"),
            ("get", "/api/v1/tools/registry"),
            ("post", "/api/v1/tools/connections"),
            ("get", "/api/v1/tools/connections"),
            ("delete", "/api/v1/tools/connections/conn_1"),
            ("post", "/api/v1/tools/execute"),
            ("post", "/api/v1/tools/sync-jobs"),
            ("get", "/api/v1/tools/sync-jobs/job_1"),
            ("post", "/api/v1/identity/verify"),
            ("get", "/api/v1/identity/profile"),
            ("get", "/api/v1/identity/organizations/org_1/members"),
            ("post", "/api/v1/identity/organizations/org_1/invitations"),
            ("post", "/api/v1/identity/organizations/org_1/roles"),
            ("post", "/api/v1/identity/audit-logs"),
        ],
    )
    def test_endpoint_requires_auth(self, client: TestClient, method: str, path: str) -> None:
        """端点存在且强制鉴权：未带令牌时返回 20002。"""
        response = client.request(method.upper(), path)
        assert response.status_code == 401, f"{method.upper()} {path} 未受鉴权保护"
        assert response.json()["code"] == 20002

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("post", "/api/v1/model/generate"),
            ("post", "/api/v1/model/generate/stream"),
            ("get", "/api/v1/model/catalog"),
            ("get", "/api/v1/model/usage"),
            ("post", "/api/v1/knowledge/bases"),
            ("get", "/api/v1/knowledge/bases"),
            ("post", "/api/v1/knowledge/bases/{base_id}/documents"),
            ("get", "/api/v1/knowledge/bases/{base_id}/documents/{doc_id}"),
            ("post", "/api/v1/knowledge/bases/{base_id}/documents/{doc_id}/reindex"),
            ("delete", "/api/v1/knowledge/bases/{base_id}/documents/{doc_id}"),
            ("post", "/api/v1/knowledge/search"),
            ("post", "/api/v1/agent/tasks"),
            ("get", "/api/v1/agent/tasks"),
            ("get", "/api/v1/agent/tasks/{task_id}"),
            ("get", "/api/v1/agent/tasks/{task_id}/steps"),
            ("post", "/api/v1/agent/tasks/{task_id}/cancel"),
            ("get", "/api/v1/agent/tasks/{task_id}/events"),
            ("get", "/api/v1/tools/registry"),
            ("post", "/api/v1/tools/connections"),
            ("get", "/api/v1/tools/connections"),
            ("delete", "/api/v1/tools/connections/{conn_id}"),
            ("post", "/api/v1/tools/execute"),
            ("post", "/api/v1/tools/sync-jobs"),
            ("get", "/api/v1/tools/sync-jobs/{job_id}"),
            ("post", "/api/v1/identity/verify"),
            ("get", "/api/v1/identity/profile"),
            ("get", "/api/v1/identity/organizations/{org_id}/members"),
            ("post", "/api/v1/identity/organizations/{org_id}/invitations"),
            ("post", "/api/v1/identity/organizations/{org_id}/roles"),
            ("post", "/api/v1/identity/audit-logs"),
        ],
    )
    def test_endpoint_is_registered_in_schema(self, method: str, path: str) -> None:
        """30 个端点在 OpenAPI 中全部注册，且方法与路径完全一致。"""
        from app.main import create_app

        schema = create_app().openapi()
        operations = schema["paths"].get(path)
        assert operations is not None, f"{method.upper()} {path} 未注册"
        assert method in operations, f"{path} 缺少方法 {method}"
