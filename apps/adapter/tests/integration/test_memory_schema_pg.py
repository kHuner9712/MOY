"""记忆三表与后台任务表的真库验收（阶段四 T4-03.1，架构 §3.3 / §3.4）。

**为什么必须连真库**：这一版交付的全是**数据库侧**约束——`vector` 扩展是否存在、
`FORCE ROW LEVEL SECURITY` 是否真的生效、向量维度是否被拒绝。内存替身一条都验不到。

四条判据：

1. **扩展在**：`pg_extension` 里有 `vector`，且 `vector(1024)` 列能存能查
   （应用角色建不了扩展——`trusted=false`，只能由 initdb/DBA 建，这条同时
   验证了「新装库由 init 脚本建扩展」这条路径成立）；
2. **4 张表都在且 strict**：`relrowsecurity` 与 `relforcerowsecurity` 同为真；
3. **跨租户 0 行**：A 租户写入的行，在 B 租户会话下读不到（用**应用角色**连库，
   属主是超级用户会绕过 RLS，用属主断言会假绿）；
4. **向量维度**：1024 维可写；1023/1025 维被 `vector(1024)` 拒绝。
"""

# ruff: noqa: S608 - 表名来自本文件常量元组（非外部输入），值走参数绑定
from __future__ import annotations

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.pg]

TENANT_A = "tnt_mem_a"
TENANT_B = "tnt_mem_b"
USER_A = "usr_mem_a"

#: 记忆三表 + 任务表（全部应是 strict RLS）
MEMORY_TABLES = ("user_memory", "memory_embedding", "user_memory_setting", "background_job")


async def _connect(dsn: str) -> asyncpg.Connection:
    """建立应用角色连接。

    Args:
        dsn: 应用角色连接串。

    Returns:
        asyncpg.Connection: 连接。
    """
    return await asyncpg.connect(dsn)


async def _cleanup(dsn: str) -> None:
    """清理本次用例写入的行（平台模式跨租户删）。

    Args:
        dsn: 连接串。
    """
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute("SELECT set_config('app.tenant_id', '__platform__', FALSE)")
        for table in MEMORY_TABLES:
            await connection.execute(f"DELETE FROM {table} WHERE tenant_id LIKE 'tnt_mem%'")
    finally:
        await connection.close()


@pytest.fixture(autouse=True)
async def _clean(pg_dsn: str) -> None:
    """每个用例前后清理（避免跨用例串行污染）。

    Args:
        pg_dsn: 测试库连接串。
    """
    await _cleanup(pg_dsn)
    yield
    await _cleanup(pg_dsn)


async def test_vector_extension_is_installed(pg_dsn: str) -> None:
    """`vector` 扩展已装（由 initdb 的 init-schema-phase4.sql 建，非迁移建）。"""
    connection = await _connect(pg_dsn)
    try:
        version = await connection.fetchval(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
        )
    finally:
        await connection.close()

    assert version, "未安装 pgvector 扩展——0009 与记忆内核整体不可用"
    # 版本下限：`vector(n)` 与 HNSW 索引在 0.5 之前不可用，0.8 起才有稳定的
    # `vector_cosine_ops` + HNSW。这条断言存在的意义是**防"镜像换了但扩展没装"**：
    # 本机实测过一次——镜像里有扩展文件（pg_available_extensions 有 0.8.7），
    # 但库里未安装（pg_extension 只有 plpgsql），直到 init 脚本补了
    # `CREATE EXTENSION` 才真正可用。
    major, minor = (int(part) for part in version.split(".")[:2])
    assert (major, minor) >= (0, 8), f"pgvector 版本过低：{version}（需 ≥ 0.8）"


async def test_memory_tables_exist_with_force_rls(pg_dsn: str) -> None:
    """4 张表都在，且 `relrowsecurity` + `relforcerowsecurity` 同为真（strict）。"""
    connection = await _connect(pg_dsn)
    try:
        rows = await connection.fetch(
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = current_schema() AND c.relname = ANY($1::text[])",
            list(MEMORY_TABLES),
        )
    finally:
        await connection.close()

    found = {row["relname"]: (row["relrowsecurity"], row["relforcerowsecurity"]) for row in rows}
    assert set(found) == set(MEMORY_TABLES), f"缺表：{set(MEMORY_TABLES) - set(found)}"
    for table, (enabled, forced) in found.items():
        assert enabled is True, f"{table} 未启用 RLS"
        assert forced is True, f"{table} 未 FORCE RLS（表属主会绕过策略）"


async def test_cross_tenant_memory_is_invisible(pg_dsn: str) -> None:
    """A 租户的记忆在 B 租户会话里 0 行；切回 A 能看到。"""
    connection = await _connect(pg_dsn)
    try:
        await connection.execute("SELECT set_config('app.tenant_id', $1, FALSE)", TENANT_A)
        await connection.execute(
            "INSERT INTO user_memory (tenant_id, memory_id, user_id, category, content) "
            "VALUES ($1, 'mem_rls_1', $2, 'preference', '偏好中文回复')",
            TENANT_A,
            USER_A,
        )
        assert await connection.fetchval("SELECT count(*) FROM user_memory") == 1

        await connection.execute("SELECT set_config('app.tenant_id', $1, FALSE)", TENANT_B)
        assert await connection.fetchval("SELECT count(*) FROM user_memory") == 0, (
            "跨租户读到了记忆——RLS 失效"
        )

        await connection.execute("SELECT set_config('app.tenant_id', $1, FALSE)", TENANT_A)
        assert await connection.fetchval("SELECT count(*) FROM user_memory") == 1
    finally:
        await connection.close()


async def test_embedding_accepts_1024_dims_and_rejects_other(pg_dsn: str) -> None:
    """`vector(1024)`：1024 维可写；1023/1025 维被拒（维度是索引前提）。"""
    connection = await _connect(pg_dsn)
    try:
        await connection.execute("SELECT set_config('app.tenant_id', $1, FALSE)", TENANT_A)
        literal_1024 = "[" + ",".join(["0.01"] * 1024) + "]"
        await connection.execute(
            "INSERT INTO memory_embedding (tenant_id, memory_id, model, dim, embedding) "
            "VALUES ($1, 'mem_vec_1', 'fake-embedding', 1024, $2::vector)",
            TENANT_A,
            literal_1024,
        )
        assert await connection.fetchval("SELECT count(*) FROM memory_embedding") == 1

        for bad_dim in (1023, 1025):
            bad = "[" + ",".join(["0.01"] * bad_dim) + "]"
            with pytest.raises(asyncpg.exceptions.DataError):
                await connection.execute(
                    "INSERT INTO memory_embedding (tenant_id, memory_id, model, dim, embedding) "
                    "VALUES ($1, $2, 'fake-embedding', $3, $4::vector)",
                    TENANT_A,
                    f"mem_vec_bad_{bad_dim}",
                    bad_dim,
                    bad,
                )
    finally:
        await connection.close()


async def test_background_job_kind_and_dedupe_constraints(pg_dsn: str) -> None:
    """`kind` 只接受三种；同租户同 kind 同 dedupe_key 的未终结行唯一。"""
    connection = await _connect(pg_dsn)
    try:
        await connection.execute("SELECT set_config('app.tenant_id', $1, FALSE)", TENANT_A)
        await connection.execute(
            "INSERT INTO background_job (tenant_id, job_id, kind, dedupe_key) "
            "VALUES ($1, 'job_1', 'memory_extract', 'conv_1')",
            TENANT_A,
        )
        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            await connection.execute(
                "INSERT INTO background_job (tenant_id, job_id, kind, dedupe_key) "
                "VALUES ($1, 'job_2', 'memory_extract', 'conv_1')",
                TENANT_A,
            )
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await connection.execute(
                "INSERT INTO background_job (tenant_id, job_id, kind) "
                "VALUES ($1, 'job_3', 'not_a_kind')",
                TENANT_A,
            )
    finally:
        await connection.close()
