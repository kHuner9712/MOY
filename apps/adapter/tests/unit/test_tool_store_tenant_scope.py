"""工具调用记录存储的跨租户隔离（H-03，2026-11-07）。

`api/v1/tools_ext.py`（TL1）在私有化模式下租户固定，HTTP 层构造不出
"两个企业"；跨租户保证落在存储契约上。本文件直接压 `InMemoryToolStore`，
同时给 PostgreSQL 实现留同一份判据（SQL 侧由 `test_tool_executions.py`
的 `--run-pg` 用例覆盖读写，本处只校准语义：**过滤条件必须带 tenant_id**）。
"""

from __future__ import annotations

from app.db.repositories.tool_repo import (
    EXECUTION_FAILED,
    EXECUTION_SUCCESS,
    InMemoryToolStore,
    ToolExecutionRecord,
)


def _record(
    tenant_id: str, execution_id: str, *, status: str = EXECUTION_SUCCESS
) -> ToolExecutionRecord:
    """构造一条调用记录。

    Args:
        tenant_id: 租户。
        execution_id: 记录 ID。
        status: 状态。

    Returns:
        ToolExecutionRecord: 记录。
    """
    return ToolExecutionRecord(
        tenant_id=tenant_id,
        execution_id=execution_id,
        tool_type="wecom",
        operation="send_message",
        status=status,
        error_message="未找到该成员，请确认后在通讯录中核对"
        if status == EXECUTION_FAILED
        else None,
    )


async def test_list_executions_is_tenant_scoped() -> None:
    """A 的记录对 B 不可见，总数也只算自己的。"""
    store = InMemoryToolStore()
    await store.record_execution(_record("tnt_a", "exec_a1"))
    await store.record_execution(_record("tnt_a", "exec_a2", status=EXECUTION_FAILED))
    await store.record_execution(_record("tnt_b", "exec_b1"))

    rows_a, total_a = await store.list_executions("tnt_a")
    assert total_a == 2
    assert {row.execution_id for row in rows_a} == {"exec_a1", "exec_a2"}

    rows_b, total_b = await store.list_executions("tnt_b")
    assert total_b == 1
    assert rows_b[0].execution_id == "exec_b1"

    rows_none, total_none = await store.list_executions("tnt_c")
    assert rows_none == []
    assert total_none == 0


async def test_list_executions_paginates_within_tenant() -> None:
    """分页在租户内生效（offset 不会把别人的记录翻进来）。"""
    store = InMemoryToolStore()
    for index in range(3):
        await store.record_execution(_record("tnt_a", f"exec_{index}"))
    await store.record_execution(_record("tnt_b", "exec_b"))

    page1, total = await store.list_executions("tnt_a", limit=1, offset=0)
    page2, _ = await store.list_executions("tnt_a", limit=1, offset=1)

    assert total == 3
    assert len(page1) == 1
    assert len(page2) == 1
    assert {page1[0].execution_id, page2[0].execution_id} <= {
        "exec_0",
        "exec_1",
        "exec_2",
    }
    assert "exec_b" not in {page1[0].execution_id, page2[0].execution_id}
