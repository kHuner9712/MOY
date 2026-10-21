"""健康检查与全局约定契约测试（架构文档 3.0.1 / 3.0.2 / 10.2 D-07）。"""

from __future__ import annotations

from typing import Any

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
    assert {"config", "profile_routes", "tool_registry", "db", "db_privilege"} <= names


def test_readyz_checks_is_array_contract() -> None:
    """`checks` 必须是**数组**（`ReadyData.checks: list[ReadyCheck]`），不得改成对象。

    阶段二新增 `db` / `db_privilege` 检查项时只能追加元素，否则破坏既有契约。
    """
    from app.schemas.common import ReadyData

    field = ReadyData.model_fields["checks"]
    assert isinstance(ReadyData(status="ready").checks, list)
    assert "list" in str(field.annotation)


def _readyz_settings(**overrides: object) -> Any:
    """构造 postgres 模式配置（凭据密钥必须合法，否则启动即失败）。"""
    import base64
    import os

    from app.core.config import Settings

    payload: dict[str, object] = {
        "env": "test",
        "deploy_mode": "private",
        "ratelimit_enabled": False,
        "business_store": "postgres",
        "database_url": "postgresql+asyncpg://officeai:pw@127.0.0.1:1/officeai",
        "credential_encryption_key": base64.b64encode(os.urandom(32)).decode("ascii"),
    }
    payload.update(overrides)
    return Settings(**payload)  # type: ignore[arg-type]


class _ScalarResult:
    """模拟 `Result.scalar()`。"""

    def __init__(self, value: Any) -> None:
        """初始化。"""
        self._value = value

    def scalar(self) -> Any:
        """返回标量。"""
        return self._value


class _Connection:
    """模拟连接：`pg_roles` 查询返回指定值，其余返回 1。"""

    def __init__(self, bypass_rls: bool) -> None:
        """初始化。"""
        self._bypass_rls = bypass_rls

    async def __aenter__(self) -> _Connection:
        """进入上下文。"""
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        """退出上下文。"""
        return False

    async def execute(self, statement: Any, parameters: Any = None) -> _ScalarResult:
        """执行语句。"""
        if "pg_roles" in str(statement):
            return _ScalarResult(self._bypass_rls)
        return _ScalarResult(1)


class _Engine:
    """模拟异步引擎。"""

    def __init__(self, bypass_rls: bool) -> None:
        """初始化。"""
        self._bypass_rls = bypass_rls

    def connect(self) -> _Connection:
        """返回连接（异步上下文管理器）。"""
        return _Connection(self._bypass_rls)


def test_readyz_not_ready_when_db_user_bypasses_rls() -> None:
    """连接账号带 BYPASSRLS（超级用户）→ `not_ready`，detail 为业务语言（G-25）。"""
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(_readyz_settings())
    with TestClient(app) as client:
        # 用替身引擎替换真实引擎（无需真实超级用户环境）
        app.state.db_engine = _Engine(bypass_rls=True)
        response = client.get("/readyz")

    body = response.json()
    assert response.status_code == 500
    assert body["data"]["status"] == "not_ready"
    checks = {check["name"]: check for check in body["data"]["checks"]}
    assert checks["db"]["ready"] is True
    assert checks["db_privilege"]["ready"] is False
    assert checks["db_privilege"]["detail"] == "数据库连接权限配置不正确"
    # 不得出现任何数据库内部术语
    for term in ("rolbypassrls", "bypassrls", "pg_roles", "superuser"):
        assert term not in response.text.lower()


def test_readyz_ready_when_db_user_is_restricted() -> None:
    """连接账号不带 BYPASSRLS → `ready`，detail 为「数据库连接权限正常」。"""
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(_readyz_settings())
    with TestClient(app) as client:
        app.state.db_engine = _Engine(bypass_rls=False)
        response = client.get("/readyz")

    body = response.json()
    assert response.status_code == 200
    assert body["data"]["status"] == "ready"
    checks = {check["name"]: check for check in body["data"]["checks"]}
    assert checks["db"]["ready"] is True
    assert checks["db_privilege"]["ready"] is True
    assert checks["db_privilege"]["detail"] == "数据库连接权限正常"


def test_readyz_db_checks_ready_in_memory_mode() -> None:
    """`business_store=memory` 时 `db` / `db_privilege` 均直接 ready 并注明内存模式。"""
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app

    app = create_app(Settings(env="test", deploy_mode="private", ratelimit_enabled=False))
    with TestClient(app) as client:
        body = client.get("/readyz").json()

    checks = {check["name"]: check for check in body["data"]["checks"]}
    assert checks["db"]["ready"] is True
    assert checks["db_privilege"]["ready"] is True
    assert "内存模式" in checks["db_privilege"]["detail"]


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
    """核心业务端点数量必须恰为 30（契约基线）。

    阶段二改造（D-09）：`/api/v1` 下同时存在「核心基线 30」与「新域基线 38」，
    因此这里按基线文件把新域端点剔除后再断言 30——核心基线仍然冻结，
    新域端点由 `docs/api/baseline-phase2-38.json` 逐批登记（见
    `tests/integration/test_endpoint_baseline.py` 的集合精确相等断言）。

    注意：同一路径上的多个方法在 OpenAPI 中合并为一个 path 项，
    因此这里按「路径 + 方法」组合计数。
    """
    import json
    import pathlib

    schema = client.get("/openapi.json").json()
    operations = {
        (method.upper(), path)
        for path, methods in schema["paths"].items()
        if path.startswith("/api/v1")
        for method in methods
    }
    baseline_path = (
        pathlib.Path(__file__).resolve().parents[4] / "docs" / "api" / "baseline-phase2-38.json"
    )
    phase2 = {
        (str(item["method"]).upper(), str(item["path"]))
        for item in json.loads(baseline_path.read_text(encoding="utf-8"))["endpoints"]
    }
    assert len(operations - phase2) == 30


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
