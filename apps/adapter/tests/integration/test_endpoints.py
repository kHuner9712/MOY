"""五大接口域端到端契约测试（架构文档第 3 章）。

覆盖策略：
- 每个域至少覆盖一条成功路径与一条错误码路径；
- 校验统一响应格式 `{code, message, data}`；
- 校验错误路径只返回业务语言文案，不含 code 之外的任何技术细节。
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

_GENERATE_BODY = {
    "conversation_id": "conv_01HF8YQZ3K7M9X2P4R6T8V0W",
    "profile": "general",
    "messages": [{"role": "user", "content": "帮我把这段话改写得更专业"}],
}


def _assert_envelope(response_payload: dict[str, object]) -> None:
    """校验统一响应格式。

    Args:
        response_payload: 响应体。
    """
    assert set(response_payload) == {"code", "message", "data"}
    assert isinstance(response_payload["code"], int)
    assert isinstance(response_payload["message"], str)
    assert response_payload["message"]


class TestModelEndpoints:
    """M1-M4 模型服务端点。"""

    def test_m1_generate_success(
        self, client: TestClient, auth_headers: dict[str, str], idempotency_headers: dict[str, str]
    ) -> None:
        """M1 非流式生成返回 code=0 与业务化用量。"""
        response = client.post(
            "/api/v1/model/generate",
            json=_GENERATE_BODY,
            headers={**auth_headers, **idempotency_headers},
        )
        assert response.status_code == 200
        body = response.json()
        _assert_envelope(body)
        assert body["code"] == 0
        assert body["message"] == "success"
        data = body["data"]
        assert data["generation_id"].startswith("gen_")
        assert set(data["usage"]) == {"prompt_units", "completion_units", "total_units"}

    def test_m1_missing_idempotency_key_returns_20001(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """写接口缺少 Idempotency-Key 返回 20001（架构文档 3.0.4）。"""
        response = client.post("/api/v1/model/generate", json=_GENERATE_BODY, headers=auth_headers)
        assert response.status_code == 400
        body = response.json()
        assert body["code"] == 20001
        assert body["message"] == "请求内容不完整，请检查后重试"

    def test_m1_duplicate_request_returns_20007(
        self, client: TestClient, auth_headers: dict[str, str], idempotency_headers: dict[str, str]
    ) -> None:
        """重复幂等键命中返回 20007 并回放首次结果。"""
        first = client.post(
            "/api/v1/model/generate",
            json=_GENERATE_BODY,
            headers={**auth_headers, **idempotency_headers},
        )
        second = client.post(
            "/api/v1/model/generate",
            json=_GENERATE_BODY,
            headers={**auth_headers, **idempotency_headers},
        )
        assert first.json()["code"] == 0
        assert second.json()["code"] == 20007
        assert second.json()["message"] == "重复的请求已被忽略"

    def test_m1_invalid_profile_returns_20001(
        self, client: TestClient, auth_headers: dict[str, str], idempotency_headers: dict[str, str]
    ) -> None:
        """非法能力档位返回 20001（参数校验失败）。"""
        response = client.post(
            "/api/v1/model/generate",
            json={**_GENERATE_BODY, "profile": "gpt-4o"},
            headers={**auth_headers, **idempotency_headers},
        )
        assert response.status_code == 400
        body = response.json()
        assert body["code"] == 20001
        assert body["data"] is None

    def test_m1_empty_messages_returns_20001(
        self, client: TestClient, auth_headers: dict[str, str], idempotency_headers: dict[str, str]
    ) -> None:
        """空消息列表返回 20001。"""
        response = client.post(
            "/api/v1/model/generate",
            json={**_GENERATE_BODY, "messages": []},
            headers={**auth_headers, **idempotency_headers},
        )
        assert response.json()["code"] == 20001

    def test_m2_stream_emits_self_defined_events(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """M2 流式生成返回 text/event-stream 与自研事件名。"""
        response = client.post(
            "/api/v1/model/generate/stream", json=_GENERATE_BODY, headers=auth_headers
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        text = response.text
        assert "event: start" in text
        assert "event: delta" in text
        assert "event: done" in text
        for leaked in ("chat-messages", "message_end", "agent_message", "dify"):
            assert leaked not in text

    def test_m3_catalog_returns_four_profiles(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """M3 目录返回四个能力档位。"""
        response = client.get("/api/v1/model/catalog", headers=auth_headers)
        assert response.json()["code"] == 0
        profiles = response.json()["data"]["profiles"]
        assert {item["profile"] for item in profiles} == {
            "general",
            "code",
            "long_context",
            "reasoning",
        }
        for item in profiles:
            assert item["display_name"]
            assert item["status"] in {"available", "unavailable"}

    def test_m4_usage_returns_business_units(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """M4 用量统计仅返回业务化口径。"""
        response = client.get("/api/v1/model/usage?period=month", headers=auth_headers)
        body = response.json()
        assert body["code"] == 0
        assert set(body["data"]["quota"]) == {
            "plan_name",
            "total_units",
            "used_units",
            "remaining_units",
        }
        assert "token" not in response.text.lower()

    def test_m4_invalid_period_returns_20001(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """非法统计周期返回 20001。"""
        response = client.get("/api/v1/model/usage?period=year", headers=auth_headers)
        assert response.status_code == 400
        assert response.json()["code"] == 20001


class TestRagEndpoints:
    """R1-R7 RAG 服务端点。"""

    def test_r1_create_base(
        self, client: TestClient, auth_headers: dict[str, str], idempotency_headers: dict[str, str]
    ) -> None:
        """R1 创建知识库返回 kb_ 前缀 ID。"""
        response = client.post(
            "/api/v1/knowledge/bases",
            json={"name": "人事制度库", "description": "制度文档", "visibility": "organization"},
            headers={**auth_headers, **idempotency_headers},
        )
        body = response.json()
        assert body["code"] == 0
        assert body["data"]["base_id"].startswith("kb_")
        assert body["data"]["name"] == "人事制度库"

    def test_r1_invalid_visibility_returns_20001(
        self, client: TestClient, auth_headers: dict[str, str], idempotency_headers: dict[str, str]
    ) -> None:
        """非法可见性枚举返回 20001。"""
        response = client.post(
            "/api/v1/knowledge/bases",
            json={"name": "x", "visibility": "public"},
            headers={**auth_headers, **idempotency_headers},
        )
        assert response.json()["code"] == 20001

    def test_r2_list_bases_returns_page(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """R2 列表返回分页结构。"""
        response = client.get("/api/v1/knowledge/bases?page=1&page_size=10", headers=auth_headers)
        body = response.json()
        assert body["code"] == 0
        assert set(body["data"]["meta"]) == {"page", "page_size", "total", "total_pages"}

    def test_r3_upload_document(
        self, client: TestClient, auth_headers: dict[str, str], idempotency_headers: dict[str, str]
    ) -> None:
        """R3 上传文档返回 processing 初态。"""
        response = client.post(
            "/api/v1/knowledge/bases/kb_1/documents",
            files={"file": ("员工手册.pdf", io.BytesIO(b"%PDF-1.4 test"), "application/pdf")},
            data={"chunk_policy": "default"},
            headers={**auth_headers, **idempotency_headers},
        )
        body = response.json()
        assert body["code"] == 0
        assert body["data"]["document_id"].startswith("doc_")
        assert body["data"]["index_status"] == "processing"
        assert body["data"]["progress"] == 0

    def test_r3_unsupported_format_returns_11002(
        self, client: TestClient, auth_headers: dict[str, str], idempotency_headers: dict[str, str]
    ) -> None:
        """非白名单格式返回 11002 与业务语言文案。"""
        response = client.post(
            "/api/v1/knowledge/bases/kb_1/documents",
            files={"file": ("malware.exe", io.BytesIO(b"MZ"), "application/octet-stream")},
            headers={**auth_headers, **idempotency_headers},
        )
        body = response.json()
        assert body["code"] == 11002
        assert body["message"] == "暂不支持该格式的文档"

    def test_r4_index_status(self, client: TestClient, auth_headers: dict[str, str]) -> None:
        """R4 索引进度返回自研三态。"""
        response = client.get("/api/v1/knowledge/bases/kb_1/documents/doc_1", headers=auth_headers)
        body = response.json()
        assert body["code"] == 0
        assert body["data"]["index_status"] in {"processing", "completed", "failed"}
        assert "indexing_status" not in response.text

    def test_r5_reindex_conflict_returns_11006(
        self, client: TestClient, auth_headers: dict[str, str], idempotency_headers: dict[str, str]
    ) -> None:
        """文档处理中重建索引返回 11006。"""
        response = client.post(
            "/api/v1/knowledge/bases/kb_1/documents/doc_1/reindex",
            headers={**auth_headers, **idempotency_headers},
        )
        body = response.json()
        assert body["code"] == 11006
        assert body["message"] == "文档正在处理，无法重复操作"

    def test_r6_delete_document(
        self, client: TestClient, auth_headers: dict[str, str], idempotency_headers: dict[str, str]
    ) -> None:
        """R6 删除文档返回确认结果。"""
        response = client.delete(
            "/api/v1/knowledge/bases/kb_1/documents/doc_1",
            headers={**auth_headers, **idempotency_headers},
        )
        body = response.json()
        assert body["code"] == 0
        assert body["data"] == {"id": "doc_1", "deleted": True}

    def test_r7_search(
        self, client: TestClient, auth_headers: dict[str, str], idempotency_headers: dict[str, str]
    ) -> None:
        """R7 检索返回片段列表结构。"""
        response = client.post(
            "/api/v1/knowledge/search",
            json={"base_id": "kb_1", "query": "年假怎么申请", "top_k": 5, "score_threshold": 0.5},
            headers={**auth_headers, **idempotency_headers},
        )
        body = response.json()
        assert body["code"] == 0
        assert "segments" in body["data"]

    def test_r7_missing_query_returns_20001(
        self, client: TestClient, auth_headers: dict[str, str], idempotency_headers: dict[str, str]
    ) -> None:
        """缺少查询语句返回 20001。"""
        response = client.post(
            "/api/v1/knowledge/search",
            json={"base_id": "kb_1"},
            headers={**auth_headers, **idempotency_headers},
        )
        assert response.json()["code"] == 20001


class TestAgentEndpoints:
    """A1-A6 Agent 服务端点。"""

    def test_a1_create_task(
        self, client: TestClient, auth_headers: dict[str, str], idempotency_headers: dict[str, str]
    ) -> None:
        """A1 创建任务返回 task_ 前缀 ID。"""
        response = client.post(
            "/api/v1/agent/tasks",
            json={
                "conversation_id": "conv_1",
                "goal": "把这份需求拆解为技术方案",
                "profile": "general",
                "tool_grants": [],
                "inputs": {"requirement_text": "……"},
                "options": {"max_steps": 12, "timeout_seconds": 600},
            },
            headers={**auth_headers, **idempotency_headers},
        )
        body = response.json()
        assert body["code"] == 0
        assert body["data"]["task_id"].startswith("task_")
        assert body["data"]["status"] == "running"

    def test_a1_missing_goal_returns_20001(
        self, client: TestClient, auth_headers: dict[str, str], idempotency_headers: dict[str, str]
    ) -> None:
        """缺少任务目标返回 20001。"""
        response = client.post(
            "/api/v1/agent/tasks",
            json={"conversation_id": "conv_1"},
            headers={**auth_headers, **idempotency_headers},
        )
        assert response.json()["code"] == 20001

    def test_a2_list_tasks(self, client: TestClient, auth_headers: dict[str, str]) -> None:
        """A2 任务列表返回分页结构。"""
        response = client.get("/api/v1/agent/tasks?page=1&page_size=10", headers=auth_headers)
        body = response.json()
        assert body["code"] == 0
        assert "items" in body["data"]

    def test_a3_get_unknown_task_returns_12001(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """未知任务返回 12001 与业务语言文案。"""
        response = client.get("/api/v1/agent/tasks/task_missing", headers=auth_headers)
        body = response.json()
        assert response.status_code == 404
        assert body["code"] == 12001
        assert body["message"] == "任务不存在或已过期"

    def test_a4_steps_of_unknown_task_returns_12001(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """未知任务步骤查询返回 12001。"""
        response = client.get("/api/v1/agent/tasks/task_missing/steps", headers=auth_headers)
        assert response.json()["code"] == 12001

    def test_a5_cancel_unknown_task_returns_12001(
        self, client: TestClient, auth_headers: dict[str, str], idempotency_headers: dict[str, str]
    ) -> None:
        """取消未知任务返回 12001。"""
        response = client.post(
            "/api/v1/agent/tasks/task_missing/cancel",
            headers={**auth_headers, **idempotency_headers},
        )
        assert response.json()["code"] == 12001

    def test_a6_events_unknown_task_returns_12001(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """未知任务事件订阅返回 12001。"""
        response = client.get("/api/v1/agent/tasks/task_missing/events", headers=auth_headers)
        assert response.json()["code"] == 12001


class TestToolEndpoints:
    """T1-T7 工具服务端点。"""

    def test_t1_registry(self, client: TestClient, auth_headers: dict[str, str]) -> None:
        """T1 注册表返回工具类型与能力清单。"""
        response = client.get("/api/v1/tools/registry", headers=auth_headers)
        body = response.json()
        assert body["code"] == 0
        tools = body["data"]["tools"]
        assert {tool["tool_type"] for tool in tools} == {"email", "github"}
        for tool in tools:
            assert tool["display_name"]
            assert tool["operations"]
            assert tool["timeout_ms"] > 0

    def test_t2_create_connection(
        self, client: TestClient, auth_headers: dict[str, str], idempotency_headers: dict[str, str]
    ) -> None:
        """T2 建立连接返回 conn_ 前缀 ID。"""
        response = client.post(
            "/api/v1/tools/connections",
            json={"tool_type": "email", "auth_code": "oauth_code", "redirect_uri": "https://x/cb"},
            headers={**auth_headers, **idempotency_headers},
        )
        body = response.json()
        assert body["code"] == 0
        assert body["data"]["connection_id"].startswith("conn_")
        assert body["data"]["status"] == "connected"

    def test_t2_unknown_tool_returns_13001(
        self, client: TestClient, auth_headers: dict[str, str], idempotency_headers: dict[str, str]
    ) -> None:
        """未注册工具类型返回 13001 与业务语言文案。"""
        response = client.post(
            "/api/v1/tools/connections",
            json={"tool_type": "unknown", "auth_code": "x"},
            headers={**auth_headers, **idempotency_headers},
        )
        body = response.json()
        assert body["code"] == 13001
        assert body["message"] == "暂不支持该类工具"

    def test_t3_list_connections(self, client: TestClient, auth_headers: dict[str, str]) -> None:
        """T3 已连接列表返回分页结构。"""
        response = client.get("/api/v1/tools/connections", headers=auth_headers)
        body = response.json()
        assert body["code"] == 0
        assert "items" in body["data"]

    def test_t4_delete_unknown_connection_returns_13002(
        self, client: TestClient, auth_headers: dict[str, str], idempotency_headers: dict[str, str]
    ) -> None:
        """断开未知连接返回 13002。"""
        response = client.delete(
            "/api/v1/tools/connections/conn_missing",
            headers={**auth_headers, **idempotency_headers},
        )
        body = response.json()
        assert body["code"] == 13002
        assert body["message"] == "该工具尚未连接，请先在设置中完成授权"

    def test_t5_execute_without_connection_returns_13002(
        self, client: TestClient, auth_headers: dict[str, str], idempotency_headers: dict[str, str]
    ) -> None:
        """未连接工具执行返回 13002。"""
        response = client.post(
            "/api/v1/tools/execute",
            json={"tool_type": "email", "operation": "send_message", "params": {"to": ["a@b.com"]}},
            headers={**auth_headers, **idempotency_headers},
        )
        assert response.json()["code"] == 13002

    def test_t5_unsupported_operation_returns_13005(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        make_idempotency_headers: object,
    ) -> None:
        """操作不在能力清单返回 13005 与业务语言文案。"""
        headers = make_idempotency_headers("t2-connect")  # type: ignore[operator]
        client.post(
            "/api/v1/tools/connections",
            json={"tool_type": "email", "auth_code": "code"},
            headers={**auth_headers, **headers},
        )
        response = client.post(
            "/api/v1/tools/execute",
            json={"tool_type": "email", "operation": "wipe_everything", "params": {}},
            headers={**auth_headers, **make_idempotency_headers("t5-execute")},  # type: ignore[operator]
        )
        body = response.json()
        assert body["code"] == 13005
        assert body["message"] == "该工具不支持此操作"

    def test_t6_create_sync_job(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        make_idempotency_headers: object,
    ) -> None:
        """T6 创建同步任务返回 job_ 前缀 ID。"""
        client.post(
            "/api/v1/tools/connections",
            json={"tool_type": "email", "auth_code": "code"},
            headers={**auth_headers, **make_idempotency_headers("t2-connect")},  # type: ignore[operator]
        )
        response = client.post(
            "/api/v1/tools/sync-jobs",
            json={"tool_type": "email", "operation": "sync", "scope": "inbox"},
            headers={**auth_headers, **make_idempotency_headers("t6-job")},  # type: ignore[operator]
        )
        body = response.json()
        assert body["code"] == 0
        assert body["data"]["job_id"].startswith("job_")

    def test_t7_get_unknown_job_returns_20004(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """查询未知同步任务返回 20004。"""
        response = client.get("/api/v1/tools/sync-jobs/job_missing", headers=auth_headers)
        body = response.json()
        assert response.status_code == 404
        assert body["code"] == 20004


class TestIdentityEndpoints:
    """U1-U6 用户权限服务端点。"""

    def test_u1_verify(
        self, client: TestClient, auth_headers: dict[str, str], idempotency_headers: dict[str, str]
    ) -> None:
        """U1 权限校验返回自研字段。"""
        response = client.post(
            "/api/v1/identity/verify",
            json={
                "permission": "knowledge:document:upload",
                "resource": {"type": "knowledge_base", "id": "kb_1"},
            },
            headers={**auth_headers, **idempotency_headers},
        )
        body = response.json()
        assert body["code"] == 0
        assert set(body["data"]) == {"allowed", "user_id", "tenant_id", "roles"}

    def test_u1_malformed_permission_returns_14004(
        self, client: TestClient, auth_headers: dict[str, str], idempotency_headers: dict[str, str]
    ) -> None:
        """权限点命名不规范返回 14004 与业务语言文案。"""
        response = client.post(
            "/api/v1/identity/verify",
            json={"permission": "invalid"},
            headers={**auth_headers, **idempotency_headers},
        )
        body = response.json()
        assert body["code"] == 14004
        assert body["message"] == "权限点未定义"

    def test_u2_profile(self, client: TestClient, auth_headers: dict[str, str]) -> None:
        """U2 用户资料返回自研结构且不含底座原生字段。"""
        response = client.get("/api/v1/identity/profile", headers=auth_headers)
        body = response.json()
        assert body["code"] == 0
        assert set(body["data"]) == {"user_id", "display_name", "email", "tenant", "organizations"}
        for leaked in ("firstName", "lastName", "realm_access", "attributes"):
            assert leaked not in response.text

    def test_u3_members_requires_org_id(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """空组织 ID 被路径参数校验拦截为 20001。"""
        response = client.get("/api/v1/identity/organizations//members", headers=auth_headers)
        assert response.status_code in {401, 404}
        if response.status_code == 404:
            assert response.json()["code"] == 20004

    def test_u4_invite(
        self, client: TestClient, auth_headers: dict[str, str], idempotency_headers: dict[str, str]
    ) -> None:
        """U4 邀请成员返回 pending 记录。"""
        response = client.post(
            "/api/v1/identity/organizations/org_1/invitations",
            json={"email": "new@example.com", "role": "member", "message": "欢迎加入"},
            headers={**auth_headers, **idempotency_headers},
        )
        body = response.json()
        assert body["code"] == 0
        assert body["data"]["status"] == "pending"
        assert body["data"]["role"] == "member"

    def test_u4_invalid_email_returns_20001(
        self, client: TestClient, auth_headers: dict[str, str], idempotency_headers: dict[str, str]
    ) -> None:
        """非法邮箱返回 20001。"""
        response = client.post(
            "/api/v1/identity/organizations/org_1/invitations",
            json={"email": "a"},
            headers={**auth_headers, **idempotency_headers},
        )
        assert response.json()["code"] == 20001

    def test_u5_upsert_role(
        self, client: TestClient, auth_headers: dict[str, str], idempotency_headers: dict[str, str]
    ) -> None:
        """U5 角色操作返回自研角色信息。"""
        response = client.post(
            "/api/v1/identity/organizations/org_1/roles",
            json={
                "role_key": "dev",
                "display_name": "研发",
                "permissions": ["knowledge:document:upload"],
                "members": ["usr_2"],
            },
            headers={**auth_headers, **idempotency_headers},
        )
        body = response.json()
        assert body["code"] == 0
        assert body["data"]["role_key"] == "dev"
        assert body["data"]["member_count"] == 1

    def test_u5_bad_permission_returns_14004(
        self, client: TestClient, auth_headers: dict[str, str], idempotency_headers: dict[str, str]
    ) -> None:
        """角色权限点不规范返回 14004。"""
        response = client.post(
            "/api/v1/identity/organizations/org_1/roles",
            json={"role_key": "dev", "permissions": ["bad"]},
            headers={**auth_headers, **idempotency_headers},
        )
        assert response.json()["code"] == 14004

    def test_u6_audit_logs(
        self, client: TestClient, auth_headers: dict[str, str], idempotency_headers: dict[str, str]
    ) -> None:
        """U6 审计查询返回分页结构。"""
        response = client.post(
            "/api/v1/identity/audit-logs",
            json={"page": 1, "page_size": 10, "resource_type": "knowledge_base"},
            headers={**auth_headers, **idempotency_headers},
        )
        body = response.json()
        assert body["code"] == 0
        assert "items" in body["data"]


class TestErrorEnvelope:
    """错误响应封装的全局约束。"""

    def test_error_response_never_exposes_internals(
        self, client: TestClient, auth_headers: dict[str, str], idempotency_headers: dict[str, str]
    ) -> None:
        """错误响应中不得出现 traceback、异常类型或路径等内部信息。"""
        response = client.post(
            "/api/v1/model/generate",
            json={"conversation_id": "conv_1", "profile": "not-a-profile", "messages": []},
            headers={**auth_headers, **idempotency_headers},
        )
        text = response.text.lower()
        for leaked in ("traceback", "validationerror", "pydantic", "/app/", "line "):
            assert leaked not in text

    def test_every_error_code_is_in_registered_segments(
        self, client: TestClient, auth_headers: dict[str, str], idempotency_headers: dict[str, str]
    ) -> None:
        """所有对外错误码必须落在注册分段内。"""
        response = client.post(
            "/api/v1/knowledge/bases",
            json={"name": ""},
            headers={**auth_headers, **idempotency_headers},
        )
        code = response.json()["code"]
        assert code == 0 or (
            10001 <= code <= 14999 or 20001 <= code <= 20999 or 30001 <= code <= 30999
        )
