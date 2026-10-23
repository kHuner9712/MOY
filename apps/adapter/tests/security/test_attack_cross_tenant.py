# ruff: noqa: S608 - 表名来自本文件常量（非外部输入），值走参数绑定
"""攻击 3：跨租户读（阶段四 T4-04.6）。

判据不是"接口报错"，而是**同一张表在另一个租户的会话里查不到行**——RLS 装错、
策略写成 `USING (true)`、或者应用角色带了 `BYPASSRLS`，接口层都还是 200，
只有直接连库按租户会话变量查才能证明隔离成立。

表选了**个人数据**与**业务数据**各几张：`message` / `conversation` / `user_memory`
是个人数据（RLS 管租户边界、仓储层管个人边界），`usage_event` / `plugin_task` 是业务数据。
"""

from __future__ import annotations

import asyncpg
import pytest

pytestmark = [pytest.mark.security, pytest.mark.pg, pytest.mark.integration]

TENANT_A = "tnt_sec_ct_a"
TENANT_B = "tnt_sec_ct_b"
USER_B = "usr_sec_ct_b"

#: 受检表（覆盖个人数据与业务数据：会话 / 消息 / 记忆 / 计量 / 后台任务）
TABLES = ("conversation", "message", "user_memory", "usage_event", "background_job")


@pytest.fixture(autouse=True)
async def _cleanup(pg_dsn: str) -> None:
    """清理本文件写入的行。

    Args:
        pg_dsn: 测试库连接串。
    """
    await _platform_execute(pg_dsn, "DELETE FROM message WHERE tenant_id LIKE 'tnt_sec_ct%'")
    for table in ("conversation", "user_memory", "usage_event", "background_job"):
        await _platform_execute(pg_dsn, f"DELETE FROM {table} WHERE tenant_id LIKE 'tnt_sec_ct%'")
    yield
    await _platform_execute(pg_dsn, "DELETE FROM message WHERE tenant_id LIKE 'tnt_sec_ct%'")
    for table in ("conversation", "user_memory", "usage_event", "background_job"):
        await _platform_execute(pg_dsn, f"DELETE FROM {table} WHERE tenant_id LIKE 'tnt_sec_ct%'")


async def _platform_execute(dsn: str, sql: str) -> None:
    """以平台模式执行一条管理语句。

    Args:
        dsn: 连接串。
        sql: 语句。
    """
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute("SELECT set_config('app.tenant_id', '__platform__', FALSE)")
        await connection.execute(sql)
    finally:
        await connection.close()


async def _seed_tenant_b(dsn: str) -> None:
    """以 B 租户身份写入五张表各一行。

    Args:
        dsn: 连接串。
    """
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute("SELECT set_config('app.tenant_id', $1, FALSE)", TENANT_B)
        await connection.execute(
            "INSERT INTO conversation (tenant_id, conversation_id, user_id, title) "
            "VALUES ($1, 'conv_sec_b', $2, 'B 的会话')",
            TENANT_B,
            USER_B,
        )
        await connection.execute(
            "INSERT INTO message (tenant_id, message_id, conversation_id, user_id, role, "
            "content, status) VALUES ($1, 'msg_sec_b', 'conv_sec_b', $2, 'user', "
            "'B 的私密内容', 'settled')",
            TENANT_B,
            USER_B,
        )
        await connection.execute(
            "INSERT INTO user_memory (tenant_id, memory_id, user_id, category, content) "
            "VALUES ($1, 'mem_sec_b', $2, 'identity', 'B 叫李工')",
            TENANT_B,
            USER_B,
        )
        await connection.execute(
            "INSERT INTO usage_event (tenant_id, event_id, user_id, capability, idem_key, "
            "units, period) VALUES ($1, 'use_sec_b', $2, 'chat', 'gen_sec_b', 5, '2026-10')",
            TENANT_B,
            USER_B,
        )
        await connection.execute(
            "INSERT INTO background_job (tenant_id, job_id, kind, dedupe_key) "
            "VALUES ($1, 'job_sec_b', 'memory_extract', 'conv_sec_b')",
            TENANT_B,
        )
    finally:
        await connection.close()


async def test_cross_tenant_read_returns_zero_rows(pg_dsn: str) -> None:
    """A 租户会话读 B 租户的五张表 → 全部 0 行（RLS 生效，不是靠应用层过滤）。"""
    await _seed_tenant_b(pg_dsn)

    connection = await asyncpg.connect(pg_dsn)
    try:
        # 先确认 B 自己有数据（否则"0 行"可能只是没插进去）
        await connection.execute("SELECT set_config('app.tenant_id', $1, FALSE)", TENANT_B)
        for table in TABLES:
            own = await connection.fetchval(
                f"SELECT count(*) FROM {table} WHERE tenant_id = $1", TENANT_B
            )
            assert own >= 1, f"{table}：种子数据没写进去，后续断言不成立"

        # 切到 A：五张表都必须查不到 B 的行
        await connection.execute("SELECT set_config('app.tenant_id', $1, FALSE)", TENANT_A)
        for table in TABLES:
            leaked = await connection.fetchval(
                f"SELECT count(*) FROM {table} WHERE tenant_id = $1", TENANT_B
            )
            assert leaked == 0, f"{table}：A 租户能读到 B 的 {leaked} 行"

        # 反向再确认一次：A 也看不到"所有行"（防策略被写成 USING (true)）
        for table in TABLES:
            visible = await connection.fetchval(f"SELECT count(*) FROM {table}")
            assert visible == 0, f"{table}：A 租户在无自身数据时仍看到 {visible} 行"
    finally:
        await connection.close()
