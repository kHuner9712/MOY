"""操作记录导出服务测试（矩阵第 4 项）。

覆盖：开关默认关闭、时间范围必填与跨度上限、行数上限（报错不截断）、
CSV / JSON Lines 两种序列化、CSV 公式注入防护、导出留痕写入、
导出留痕不进导出集（防递归）。
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import AsyncIterator, Iterator

import pytest

from app.business.audit_export_service import (
    EXPORT_COLUMNS,
    AuditExportRequest,
    AuditExportService,
)
from app.business.audit_service import reset_audit_recorder
from app.core.audit_actions import ACTION_AUDIT_LOG_EXPORT
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.db.repositories.audit_recorder import (
    AuditEntry,
    get_in_memory_audit_recorder,
    reset_in_memory_audit_recorder,
)

pytestmark = pytest.mark.unit

TENANT = "tnt_export"
OTHER_TENANT = "tnt_other"
USER = "usr_exporter"
START = "2026-10-01T00:00:00Z"
END = "2026-11-01T00:00:00Z"


def _settings(**overrides: object) -> Settings:
    """构造测试配置（内存后端 + 导出开关打开）。"""
    values: dict[str, object] = {
        "env": "test",
        "business_store": "memory",
        "audit_log_export_enabled": True,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _entry(
    *,
    action: str = "knowledge_base.create",
    actor_id: str = USER,
    tenant_id: str = TENANT,
    occurred_at: str = "2026-10-15T08:00:00Z",
    summary: dict[str, object] | None = None,
) -> AuditEntry:
    """构造一条操作记录。"""
    return AuditEntry(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action=action,
        resource_type="knowledge_base",
        resource_id="kb_1",
        result="success",
        actor_role="org_admin",
        action_display_name="创建知识库",
        change_summary={"name": "制度库"} if summary is None else summary,
        occurred_at=occurred_at,
        trace_id="trc_1",
    )


async def _seed(*entries: AuditEntry) -> None:
    """写入内存后端（每个用例前由 fixture 清空）。"""
    recorder = get_in_memory_audit_recorder()
    for entry in entries:
        await recorder.record(entry)


def _request(**overrides: object) -> AuditExportRequest:
    """构造导出请求。"""
    values: dict[str, object] = {
        "tenant_id": TENANT,
        "actor_id": USER,
        "actor_role": "org_admin",
        "export_format": "csv",
        "start_at": START,
        "end_at": END,
    }
    values.update(overrides)
    return AuditExportRequest(**values)  # type: ignore[arg-type]


async def _collect(stream: AsyncIterator[bytes]) -> bytes:
    """把流式输出拼成完整字节。"""
    chunks: list[bytes] = []
    async for chunk in stream:
        chunks.append(chunk)
    return b"".join(chunks)


@pytest.fixture(autouse=True)
def _clean_recorder() -> Iterator[None]:
    """每个用例从空记录器开始（写入与导出共用同一单例）。"""
    reset_audit_recorder()
    reset_in_memory_audit_recorder()
    yield
    reset_audit_recorder()
    reset_in_memory_audit_recorder()


async def test_default_is_disabled() -> None:
    """开关默认关闭：即使有数据也拒绝（20003），且不写留痕。"""
    service = AuditExportService(_settings(audit_log_export_enabled=False))
    with pytest.raises(ApiError) as exc:
        await service.prepare(_request())
    assert exc.value.code == ErrorCode.FORBIDDEN
    assert await get_in_memory_audit_recorder().entries_for(TENANT) == []


async def test_range_is_required() -> None:
    """起止时间必须同时给出：不带范围的全量导出等于把整表外带。"""
    service = AuditExportService(_settings())
    with pytest.raises(ApiError) as exc:
        await service.prepare(_request(end_at=""))
    assert exc.value.code == ErrorCode.PARAM_INVALID


async def test_span_limit_is_reused() -> None:
    """跨度沿用 U6 的 90 天口径，不另立一套规则。"""
    service = AuditExportService(_settings())
    with pytest.raises(ApiError) as exc:
        await service.prepare(_request(start_at="2026-01-01T00:00:00Z"))
    assert exc.value.code == ErrorCode.PARAM_INVALID
    assert "90" in exc.value.message


async def test_invalid_format_rejected() -> None:
    """只支持 csv / jsonl。"""
    service = AuditExportService(_settings())
    with pytest.raises(ApiError) as exc:
        await service.prepare(_request(export_format="xlsx"))
    assert exc.value.code == ErrorCode.PARAM_INVALID


async def test_row_cap_fails_fast_without_writing_trail() -> None:
    """超过行数上限直接报错（不截断），且不写留痕（没导出就没有留痕）。"""
    await _seed(
        _entry(occurred_at="2026-10-01T00:00:00Z"), _entry(occurred_at="2026-10-02T00:00:00Z")
    )
    service = AuditExportService(_settings(audit_log_export_max_rows=1))
    with pytest.raises(ApiError) as exc:
        await service.prepare(_request())
    assert exc.value.code == ErrorCode.PARAM_INVALID
    assert exc.value.data == {"rows": 2, "max_rows": 1}
    entries = await get_in_memory_audit_recorder().entries_for(TENANT)
    assert [entry for entry in entries if entry.action == ACTION_AUDIT_LOG_EXPORT] == []


async def test_csv_export_shape_and_formula_guard() -> None:
    """CSV 列顺序与 EXPORT_COLUMNS 一致；以 = + - @ 开头的单元格加单引号。"""
    await _seed(
        _entry(occurred_at="2026-10-01T00:00:00Z"),
        _entry(actor_id="=cmd|' /C calc'!A0", occurred_at="2026-10-02T00:00:00Z"),
    )
    service = AuditExportService(_settings())
    plan = await service.prepare(_request())
    body = await _collect(service.stream(plan))
    text = body.decode("utf-8")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == [key for key, _ in EXPORT_COLUMNS]
    assert len(rows) == 3
    assert rows[2][2].startswith("'=")
    assert json.loads(rows[1][9]) == {"name": "制度库"}


async def test_jsonl_export_shape() -> None:
    """JSON Lines：每行一个对象，键集合与 CSV 列相同，摘要保持对象结构。"""
    await _seed(_entry())
    service = AuditExportService(_settings())
    plan = await service.prepare(_request(export_format="jsonl"))
    body = await _collect(service.stream(plan))
    lines = [line for line in body.decode("utf-8").splitlines() if line]
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert set(payload) == {key for key, _ in EXPORT_COLUMNS}
    assert payload["change_summary"] == {"name": "制度库"}
    assert payload["action"] == "knowledge_base.create"
    assert payload["log_id"] == "1"


async def test_export_trail_is_written_and_not_exported() -> None:
    """留痕写一条导出记录，且该记录不进导出集（防递归膨胀）。"""
    await _seed(_entry())
    service = AuditExportService(_settings())
    plan = await service.prepare(_request())
    entries = await get_in_memory_audit_recorder().entries_for(TENANT)
    trail = [entry for entry in entries if entry.action == ACTION_AUDIT_LOG_EXPORT]
    assert len(trail) == 1
    assert trail[0].change_summary["rows"] == 1
    assert trail[0].change_summary["format"] == "csv"
    assert trail[0].resource_id == plan.export_id

    # 第二次导出：总数仍为 1（导出留痕被排除），文件里也不含导出动作
    plan2 = await service.prepare(_request())
    assert plan2.rows == 1
    body = await _collect(service.stream(plan2))
    assert ACTION_AUDIT_LOG_EXPORT.encode("utf-8") not in body


async def test_tenant_isolation_in_memory() -> None:
    """导出只读本租户：其他租户的记录不出现。"""
    await _seed(_entry(), _entry(tenant_id=OTHER_TENANT))
    service = AuditExportService(_settings())
    plan = await service.prepare(_request())
    assert plan.rows == 1
