"""工具调用记录端点测试（阶段二 B06，架构文档 §4.3 / PRD P2-TL-04）。

覆盖点（逐条对应 PRD 可执行断言）：

1. T1 返回 **5 款**工具，`operations` 全为中文业务语言，条目字段与阶段一**完全一致**
   （仅数组长度变化，PRD 验收 4 的 CI 断言口径）；
2. 未连接调用 T5 → `13002` + 文案「企业微信尚未连接，请先在设置中完成授权」（验收 2）；
3. 调用成功与失败都落 `tool_execution`；TL1 返回最近记录，失败项**无堆栈、无错误码**；
4. 部分成功：飞书创建日程有个别参与人未找到 → `status=success` + `result.warnings`；
5. 公众号两步确认：未确认发布 → `20001`；确认后成功；同 `draft_id` 重复发布 → `20007`；
6. TL1 权限：无 `tool:connection:manage` → `20003`；`tool_type` 过滤与分页生效。

测试运行在 `OA_BUSINESS_STORE=memory` 下，**不依赖 PostgreSQL**。
"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from tests.conftest import make_token

pytestmark = pytest.mark.integration

#: 私有化模式下的固定租户
TENANT = "default"

#: 权限点
TOOL_MANAGE = "tool:connection:manage"
TOOL_EXECUTE = "tool:execute"

#: 工具注册表条目的字段集合（阶段一契约，B06 不得增删）
TOOL_ITEM_KEYS = {
    "tool_type",
    "display_name",
    "category",
    "auth_type",
    "status",
    "operations",
    "timeout_ms",
}

#: 工具调用记录条目的字段集合（TL1 契约）
EXECUTION_ITEM_KEYS = {
    "execution_id",
    "tool_type",
    "tool_display_name",
    "operation",
    "operation_display_name",
    "status",
    "error_message",
    "executed_at",
    "operator_name",
}


def _key(tag: str) -> str:
    """按标签生成稳定的 UUID 形态幂等键。

    Args:
        tag: 标签。

    Returns:
        str: 幂等键。
    """
    digest = hashlib.sha256(tag.encode("utf-8")).hexdigest()
    return f"{digest[0:8]}-{digest[8:12]}-4{digest[13:16]}-8{digest[17:20]}-{digest[20:32]}"


def _headers(
    settings: Settings, roles: list[str] | None = None, user_id: str = "usr_owner"
) -> dict[str, str]:
    """构造鉴权请求头。

    Args:
        settings: 配置实例。
        roles: 令牌角色。
        user_id: 用户 ID。

    Returns:
        dict[str, str]: 请求头。
    """
    token = make_token(
        settings=settings, user_id=user_id, roles=roles or [TOOL_MANAGE, TOOL_EXECUTE]
    )
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": TENANT}


def _connect(client: TestClient, settings: Settings, tool_type: str, *, tag: str = "conn") -> str:
    """建立工具连接并返回连接 ID。

    Args:
        client: 测试客户端。
        settings: 配置实例。
        tool_type: 工具类型标识。
        tag: 幂等键标签。

    Returns:
        str: 连接 ID。
    """
    response = client.post(
        "/api/v1/tools/connections",
        json={"tool_type": tool_type, "auth_code": "code-1", "redirect_uri": "https://x/cb"},
        headers={**_headers(settings), "Idempotency-Key": _key(f"{tag}-{tool_type}")},
    )
    body = response.json()
    assert body["code"] == 0, body
    return str(body["data"]["connection_id"])


def _execute(
    client: TestClient,
    settings: Settings,
    *,
    tool_type: str,
    operation: str,
    params: dict[str, Any] | None = None,
    tag: str = "exec",
) -> Any:
    """调用 T5 执行工具操作。

    Args:
        client: 测试客户端。
        settings: 配置实例。
        tool_type: 工具类型标识。
        operation: 操作标识。
        params: 操作参数。
        tag: 幂等键标签。

    Returns:
        Any: HTTP 响应对象。
    """
    return client.post(
        "/api/v1/tools/execute",
        json={"tool_type": tool_type, "operation": operation, "params": params or {}},
        headers={**_headers(settings), "Idempotency-Key": _key(f"{tag}-{tool_type}-{operation}")},
    )


# ===========================================================================
# 1. T1 注册表：5 款工具 + 契约形态不变
# ===========================================================================
def test_t1_returns_five_tools_with_unchanged_shape(client: TestClient, settings: Settings) -> None:
    """T1 返回 5 款工具，条目字段与阶段一完全一致（仅数组长度变化）。"""
    response = client.get("/api/v1/tools/registry", headers=_headers(settings))
    body = response.json()
    assert body["code"] == 0
    tools = body["data"]["tools"]
    assert len(tools) == 5
    assert {tool["tool_type"] for tool in tools} == {
        "email",
        "github",
        "wecom",
        "feishu",
        "wechat_mp",
    }
    for tool in tools:
        assert set(tool) == TOOL_ITEM_KEYS
        assert tool["display_name"]
        for operation in tool["operations"]:
            assert set(operation) == {"operation", "display_name", "params_schema"}
            assert operation["display_name"]
            assert operation["params_schema"]["type"] == "object"

    wecom = next(tool for tool in tools if tool["tool_type"] == "wecom")
    assert wecom["display_name"] == "企业微信"
    assert [op["display_name"] for op in wecom["operations"]] == [
        "发送消息",
        "查看通讯录部门",
        "查看通讯录成员",
        "创建群聊",
    ]
    assert "/api/v1/tools/executions" in str(client.app.openapi()["paths"])


def test_t1_and_t5_paths_unchanged(client: TestClient, settings: Settings) -> None:
    """T1-T7 路径集合不变（B06 只加数据与连接器实现）。"""
    paths = set(client.app.openapi()["paths"])
    for expected in (
        "/api/v1/tools/registry",
        "/api/v1/tools/connections",
        "/api/v1/tools/connections/{conn_id}",
        "/api/v1/tools/execute",
        "/api/v1/tools/sync-jobs",
        "/api/v1/tools/sync-jobs/{job_id}",
        "/api/v1/tools/executions",
    ):
        assert expected in paths


# ===========================================================================
# 2. 未连接 → 13002（含工具专属文案）
# ===========================================================================
def test_execute_without_connection_returns_13002_with_tool_message(
    client: TestClient, settings: Settings
) -> None:
    """未连接调用 T5 → 13002，文案含工具名（PRD 验收 2）。"""
    response = _execute(
        client,
        settings,
        tool_type="wecom",
        operation="send_message",
        params={"content": "内容", "members": ["usr_a"]},
    )
    body = response.json()
    assert body["code"] == 13002
    assert body["message"] == "企业微信尚未连接，请先在设置中完成授权"


def test_failed_execution_is_recorded_without_stack_or_code(
    client: TestClient, settings: Settings
) -> None:
    """失败调用落库，TL1 只展示业务原因（无堆栈、无错误码，PRD 验收 3）。"""
    _connect(client, settings, "wecom", tag="fail-conn")
    failed = _execute(
        client,
        settings,
        tool_type="wecom",
        operation="send_message",
        params={"content": "内容", "members": ["usr_a"], "simulate": "member_missing"},
        tag="fail-exec",
    )
    assert failed.json()["code"] == 13006

    response = client.get(
        "/api/v1/tools/executions?page=1&page_size=20", headers=_headers(settings)
    )
    body = response.json()
    assert body["code"] == 0
    items = body["data"]["items"]
    assert len(items) == 1
    item = items[0]
    assert set(item) == EXECUTION_ITEM_KEYS
    assert item["tool_type"] == "wecom"
    assert item["tool_display_name"] == "企业微信"
    assert item["operation_display_name"] == "发送消息"
    assert item["status"] == "failed"
    assert item["error_message"] == "未找到该成员，请确认后在通讯录中核对"
    assert item["executed_at"]
    # 无堆栈、无错误码外泄
    assert "13006" not in response.text
    assert "Traceback" not in response.text
    assert 'File "' not in response.text


# ===========================================================================
# 3. 成功调用 + 部分成功
# ===========================================================================
def test_successful_execution_is_recorded(client: TestClient, settings: Settings) -> None:
    """成功调用返回 execution_id 并落库（TL1 可见）。"""
    _connect(client, settings, "wecom", tag="ok-conn")
    response = _execute(
        client,
        settings,
        tool_type="wecom",
        operation="send_message",
        params={"content": "下午三点面试", "members": ["usr_a"]},
        tag="ok-exec",
    )
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["status"] == "success"
    assert body["data"]["execution_id"].startswith("exec_")
    assert body["data"]["result"]["recipient_count"] == 1

    listed = client.get("/api/v1/tools/executions", headers=_headers(settings)).json()
    item = listed["data"]["items"][0]
    assert item["execution_id"] == body["data"]["execution_id"]
    assert item["status"] == "success"
    assert item["error_message"] is None


def test_feishu_partial_success_keeps_success_and_warnings(
    client: TestClient, settings: Settings
) -> None:
    """部分参与人未找到：T5 仍为成功，`result.warnings` 给出提示（PRD 验收 4）。"""
    _connect(client, settings, "feishu", tag="feishu-conn")
    response = _execute(
        client,
        settings,
        tool_type="feishu",
        operation="create_calendar_event",
        params={
            "title": "需求评审",
            "start_time": "2026-11-02T10:00:00Z",
            "participants": ["a@example.com", "b@example.com"],
            "invalid_participants": ["b@example.com"],
        },
        tag="feishu-exec",
    )
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["status"] == "success"
    assert body["data"]["result"]["warnings"] == ["有 1 位参与人未找到，请核对后重试"]

    listed = client.get(
        "/api/v1/tools/executions?tool_type=feishu", headers=_headers(settings)
    ).json()
    assert listed["data"]["meta"]["total"] == 1
    assert listed["data"]["items"][0]["operation_display_name"] == "创建日程"


# ===========================================================================
# 4. 公众号两步确认 + draft_id 幂等
# ===========================================================================
def test_wechat_mp_two_step_publish(client: TestClient, settings: Settings) -> None:
    """未确认不发布 → 20001；确认后发布成功；同草稿重复发布 → 20007。"""
    _connect(client, settings, "wechat_mp", tag="mp-conn")

    draft = _execute(
        client,
        settings,
        tool_type="wechat_mp",
        operation="create_draft",
        params={"title": "年度总结", "content": "今年的三件大事"},
        tag="mp-draft",
    )
    draft_body = draft.json()
    assert draft_body["code"] == 0
    draft_id = draft_body["data"]["result"]["draft_id"]
    assert draft_body["data"]["result"]["message"] == "已保存为草稿，确认后可发布"

    unconfirmed = _execute(
        client,
        settings,
        tool_type="wechat_mp",
        operation="publish_article",
        params={"draft_id": draft_id},
        tag="mp-publish-blocked",
    )
    assert unconfirmed.json()["code"] == 20001
    assert unconfirmed.json()["message"] == "请先确认发布"

    published = _execute(
        client,
        settings,
        tool_type="wechat_mp",
        operation="publish_article",
        params={"draft_id": draft_id, "confirmed": True},
        tag="mp-publish-ok",
    )
    assert published.json()["code"] == 0
    assert published.json()["data"]["result"]["article_id"].startswith("art_")

    again = _execute(
        client,
        settings,
        tool_type="wechat_mp",
        operation="publish_article",
        params={"draft_id": draft_id, "confirmed": True},
        tag="mp-publish-again",
    )
    assert again.json()["code"] == 20007


def test_wechat_mp_rejects_unauthorized_link(client: TestClient, settings: Settings) -> None:
    """正文含未授权外链 → 13006 + PRD 原文文案。"""
    _connect(client, settings, "wechat_mp", tag="mp-link-conn")
    response = _execute(
        client,
        settings,
        tool_type="wechat_mp",
        operation="create_draft",
        params={"title": "标题", "content": "详见 https://example.com/x"},
        tag="mp-link",
    )
    assert response.json()["code"] == 13006
    assert response.json()["message"] == "正文含未获授权的外部链接，请调整后再发布"


# ===========================================================================
# 5. TL1 分页 / 过滤 / 权限
# ===========================================================================
def test_tl1_returns_latest_twenty(client: TestClient, settings: Settings) -> None:
    """TL1 默认每页 20 条且按时间倒序（PRD 验收 3）。"""
    _connect(client, settings, "wecom", tag="page-conn")
    for index in range(25):
        response = _execute(
            client,
            settings,
            tool_type="wecom",
            operation="list_members",
            params={},
            tag=f"page-exec-{index}",
        )
        assert response.json()["code"] == 0

    body = client.get("/api/v1/tools/executions", headers=_headers(settings)).json()
    assert len(body["data"]["items"]) == 20
    assert body["data"]["meta"]["total"] == 25
    executed_at = [item["executed_at"] for item in body["data"]["items"]]
    assert executed_at == sorted(executed_at, reverse=True)


def test_tl1_filters_by_tool_type(client: TestClient, settings: Settings) -> None:
    """`tool_type` 过滤生效。"""
    _connect(client, settings, "wecom", tag="filter-wecom")
    _connect(client, settings, "feishu", tag="filter-feishu")
    _execute(
        client,
        settings,
        tool_type="wecom",
        operation="list_members",
        tag="filter-wecom-exec",
    )
    _execute(
        client,
        settings,
        tool_type="feishu",
        operation="list_members",
        tag="filter-feishu-exec",
    )

    wecom_only = client.get(
        "/api/v1/tools/executions?tool_type=wecom", headers=_headers(settings)
    ).json()
    assert wecom_only["data"]["meta"]["total"] == 1
    assert wecom_only["data"]["items"][0]["tool_type"] == "wecom"


def test_tl1_requires_permission(client: TestClient, settings: Settings) -> None:
    """无 `tool:connection:manage` → 403 / 20003。"""
    response = client.get("/api/v1/tools/executions", headers=_headers(settings, roles=["member"]))
    assert response.status_code == 403
    assert response.json()["code"] == 20003


def test_t3_lists_connections_after_connect(client: TestClient, settings: Settings) -> None:
    """连接后 T3 列表包含该工具（B06 连接落库后的读取路径）。"""
    _connect(client, settings, "wecom", tag="t3-conn")

    body = client.get("/api/v1/tools/connections", headers=_headers(settings)).json()
    assert body["code"] == 0
    assert body["data"]["meta"]["total"] == 1
    item = body["data"]["items"][0]
    assert item["tool_type"] == "wecom"
    assert item["display_name"] == "企业微信"
    assert item["status"] == "connected"


def test_t4_disconnect_removes_connection(client: TestClient, settings: Settings) -> None:
    """断开连接后 T3 列表为空，且再调用 T5 返回 13002。"""
    conn_id = _connect(client, settings, "wecom", tag="t4-conn")
    response = client.delete(
        f"/api/v1/tools/connections/{conn_id}",
        headers={**_headers(settings), "Idempotency-Key": _key("t4-delete")},
    )
    assert response.json()["code"] == 0

    listed = client.get("/api/v1/tools/connections", headers=_headers(settings)).json()
    assert listed["data"]["meta"]["total"] == 0

    executed = _execute(
        client,
        settings,
        tool_type="wecom",
        operation="list_members",
        tag="t4-exec",
    )
    assert executed.json()["code"] == 13002


# ===========================================================================
# 6. 真实 PostgreSQL：连接与调用记录仓储 SQL 冒烟（`--run-pg` 启用）
# ===========================================================================
@pytest.mark.pg
async def test_postgres_tool_repositories(pg_dsn: str) -> None:
    """真实库跑通 `tool_connection` / `tool_execution` 读写与状态流转。

    覆盖内存实现无法验证的 SQL 细节：部分唯一索引下的旧连接让位、
    `timestamptz` 空值写入、`result_summary` 的 JSONB 往返、个人连接优先解析。
    """
    from app.core.config import Settings as Conf
    from app.db.repositories.tool_repo import (
        PostgresToolStore,
        ToolConnectionRecord,
        ToolExecutionRecord,
    )
    from app.db.session import tenant_scope

    conf = Conf(
        env="test",
        deploy_mode="private",
        business_store="postgres",
        database_url=pg_dsn,
        credential_encryption_key="",
    )
    tenant = "tnt_pg_b06"
    store = PostgresToolStore(conf)

    try:
        first = await store.upsert_connection(
            ToolConnectionRecord(
                tenant_id=tenant,
                conn_id="conn_pgb06_001",
                tool_type="wecom",
                owner_user_id="usr_a",
                expires_at="2027-05-01T00:00:00Z",
            )
        )
        assert first.conn_id == "conn_pgb06_001"
        assert first.expires_at is not None

        # 同一使用者再次连接：旧连接让位（uq_conn_active）
        await store.upsert_connection(
            ToolConnectionRecord(
                tenant_id=tenant,
                conn_id="conn_pgb06_002",
                tool_type="wecom",
                owner_user_id="usr_a",
            )
        )
        rows, total = await store.list_connections(tenant, tool_type="wecom")
        assert total == 1
        assert rows[0].conn_id == "conn_pgb06_002"

        resolved = await store.find_active_connection(tenant, "wecom", user_id="usr_a")
        assert resolved is not None and resolved.conn_id == "conn_pgb06_002"
        assert await store.find_active_connection(tenant, "wecom", user_id="usr_other") is None

        await store.record_execution(
            ToolExecutionRecord(
                tenant_id=tenant,
                execution_id="exec_pgb06_001",
                tool_type="wecom",
                operation="send_message",
                status="failed",
                conn_id="conn_pgb06_002",
                error_code=13006,
                error_message="未找到该成员，请确认后在通讯录中核对",
                result_summary={"recipient_count": 1},
                operator_user_id="usr_a",
                duration_ms=12,
            )
        )
        executions, exec_total = await store.list_executions(tenant, tool_type="wecom")
        assert exec_total == 1
        assert executions[0].error_message == "未找到该成员，请确认后在通讯录中核对"
        assert executions[0].result_summary == {"recipient_count": 1}
        assert executions[0].executed_at

        revoked = await store.revoke_connection(tenant, "conn_pgb06_002")
        assert revoked is not None and revoked.status == "revoked"
        assert await store.find_active_connection(tenant, "wecom", user_id="usr_a") is None
    finally:
        async with tenant_scope(tenant, settings=conf) as uow:
            await uow.execute(
                "DELETE FROM tool_execution WHERE tenant_id = :tenant_id",
                {"tenant_id": tenant},
            )
            await uow.execute(
                "DELETE FROM tool_connection WHERE tenant_id = :tenant_id",
                {"tenant_id": tenant},
            )
