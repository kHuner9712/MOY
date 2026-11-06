"""操作记录导出端点集成测试（矩阵第 4 项，U7）。

覆盖：默认关闭、权限点、平台令牌拒绝、CSV / JSONL 响应与请求头、
导出留痕与"留痕不进导出集"、限流。
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.audit_actions import ACTION_AUDIT_LOG_EXPORT
from app.core.config import Settings
from app.db.repositories.audit_recorder import AuditEntry, get_in_memory_audit_recorder
from app.main import create_app
from tests.conftest import make_token

pytestmark = pytest.mark.integration

ADMIN = "usr_export_admin"
MEMBER = "usr_export_member"
TENANT = "default"
START = "2026-10-01T00:00:00Z"
END = "2026-11-06T00:00:00Z"
EXPORT_PATH = "/api/v1/identity/audit-logs/export"


def _settings(settings: Settings, **overrides: object) -> Settings:
    """在基础测试配置上打开导出开关。"""
    values: dict[str, object] = {"audit_log_export_enabled": True, "ratelimit_enabled": False}
    values.update(overrides)
    return settings.model_copy(update=values)


def _headers(
    settings: Settings, *, user_id: str = ADMIN, roles: list[str] | None = None
) -> dict[str, str]:
    """构造鉴权请求头（默认组织管理员）。"""
    token = make_token(
        settings=settings,
        user_id=user_id,
        roles=["org_admin"] if roles is None else roles,
        tenant_id=TENANT,
    )
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": TENANT}


async def _seed(count: int = 2) -> None:
    """写入若干条操作记录（时间落在导出范围内）。"""
    recorder = get_in_memory_audit_recorder()
    for index in range(count):
        await recorder.record(
            AuditEntry(
                tenant_id=TENANT,
                actor_id=ADMIN,
                action="knowledge_base.create",
                resource_type="knowledge_base",
                resource_id=f"kb_{index}",
                actor_role="org_admin",
                change_summary={"name": f"知识库{index}"},
                occurred_at=f"2026-10-15T0{index}:00:00Z",
                trace_id="trc_1",
            )
        )


def _params(**overrides: str) -> dict[str, str]:
    """构造查询参数。"""
    params = {"format": "csv", "start_at": START, "end_at": END}
    params.update(overrides)
    return params


def test_disabled_by_default(client: TestClient, settings: Settings) -> None:
    """默认关闭：即使有权限也不导出（20003），且不写留痕。"""
    import asyncio

    asyncio.run(_seed(1))
    response = client.get(EXPORT_PATH, params=_params(), headers=_headers(settings))
    assert response.status_code == 403
    assert response.json()["code"] == 20003
    entries = asyncio.run(get_in_memory_audit_recorder().entries_for(TENANT))
    assert [entry for entry in entries if entry.action == ACTION_AUDIT_LOG_EXPORT] == []


def test_requires_audit_read_permission(settings: Settings) -> None:
    """普通成员没有 `audit:log:read`，导出被拒。"""
    app = create_app(_settings(settings))
    with TestClient(app) as client:
        response = client.get(
            EXPORT_PATH,
            params=_params(),
            headers=_headers(settings, user_id=MEMBER, roles=["member"]),
        )
    assert response.status_code == 403
    assert response.json()["code"] == 20003


def test_csv_export_streams_and_records_trail(settings: Settings) -> None:
    """CSV 导出成功：响应头带行数，留痕写入且条数与导出条数一致。"""
    import asyncio

    asyncio.run(_seed(2))
    app = create_app(_settings(settings))
    with TestClient(app) as client:
        response = client.get(EXPORT_PATH, params=_params(), headers=_headers(settings))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["x-export-rows"] == "2"
    assert response.headers["cache-control"] == "no-store"
    assert "attachment; filename=" in response.headers["content-disposition"]
    lines = [line for line in response.text.splitlines() if line]
    assert len(lines) == 3
    entries = asyncio.run(get_in_memory_audit_recorder().entries_for(TENANT))
    trail = [entry for entry in entries if entry.action == ACTION_AUDIT_LOG_EXPORT]
    assert len(trail) == 1
    assert trail[0].change_summary["rows"] == 2


def test_jsonl_excludes_export_trail(settings: Settings) -> None:
    """JSONL 导出不含导出留痕，避免递归导出。"""
    import asyncio

    asyncio.run(_seed(1))
    recorder = get_in_memory_audit_recorder()
    asyncio.run(
        recorder.record(
            AuditEntry(
                tenant_id=TENANT,
                actor_id=ADMIN,
                action=ACTION_AUDIT_LOG_EXPORT,
                resource_type="audit_log",
                occurred_at="2026-10-20T00:00:00Z",
            )
        )
    )
    app = create_app(_settings(settings))
    with TestClient(app) as client:
        response = client.get(
            EXPORT_PATH, params=_params(format="jsonl"), headers=_headers(settings)
        )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert response.headers["x-export-rows"] == "1"
    payloads = [json.loads(line) for line in response.text.splitlines() if line]
    assert len(payloads) == 1
    assert payloads[0]["action"] == "knowledge_base.create"


def test_platform_token_is_rejected(settings: Settings) -> None:
    """平台令牌不能导出客户数据（平台不是租户）。"""
    app = create_app(_settings(settings))
    token = make_token(
        settings=settings,
        user_id="usr_platform",
        roles=["platform_admin"],
        tenant_id="__platform__",
    )
    with TestClient(app) as client:
        response = client.get(
            EXPORT_PATH,
            params=_params(),
            headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "__platform__"},
        )
    assert response.status_code == 403
    assert response.json()["code"] == 20003


def test_range_is_required_and_span_is_limited(settings: Settings) -> None:
    """起止时间必填；跨度沿用 90 天口径。"""
    app = create_app(_settings(settings))
    with TestClient(app) as client:
        missing = client.get(
            EXPORT_PATH, params={"format": "csv", "start_at": START}, headers=_headers(settings)
        )
        too_wide = client.get(
            EXPORT_PATH,
            params=_params(start_at="2026-01-01T00:00:00Z"),
            headers=_headers(settings),
        )
    assert missing.status_code == 400
    assert missing.json()["code"] == 20001
    assert too_wide.status_code == 400
    assert too_wide.json()["code"] == 20001
    assert "90" in too_wide.json()["message"]


def test_row_cap_is_reported_not_truncated(settings: Settings) -> None:
    """超过上限报错（20001），不是静默截断。"""
    import asyncio

    asyncio.run(_seed(2))
    app = create_app(_settings(settings, audit_log_export_max_rows=1))
    with TestClient(app) as client:
        response = client.get(EXPORT_PATH, params=_params(), headers=_headers(settings))
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == 20001
    assert body["data"] == {"rows": 2, "max_rows": 1}


def test_export_is_rate_limited(settings: Settings) -> None:
    """同一租户+用户的导出请求超过窗口配额返回 20006。"""
    app = create_app(_settings(settings, ratelimit_enabled=True, audit_log_export_rate_limit=1))
    with TestClient(app) as client:
        first = client.get(EXPORT_PATH, params=_params(), headers=_headers(settings))
        second = client.get(EXPORT_PATH, params=_params(), headers=_headers(settings))
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["code"] == 20006


@pytest.fixture(autouse=True)
def _isolate_limiter(app: FastAPI) -> None:
    """限流后端按用例重置（避免配额串到其他用例）。"""
    from app.core.ratelimit import reset_rate_limiter

    reset_rate_limiter()
    yield
    reset_rate_limiter()
