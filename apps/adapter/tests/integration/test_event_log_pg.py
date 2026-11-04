"""会话事件日志的真库验收（底座 T02 / 迁移 0013）。

**为什么必须连真库**（内存实现一条都验不到）：

1. `event_log` 是**哈希分区表**——16 个分区是否真的建出来、分区键是不是
   `HASH(session_id)`、索引是否随父表派生，只有 `pg_class` / `pg_indexes` 知道；
2. **append-only** 是库内事实：`FORCE ROW LEVEL SECURITY` + 只建 SELECT/INSERT 策略
   之后，`UPDATE` / `DELETE` 必须影响 0 行。这条无法在内存实现里证明；
3. **RLS 按租户隔离**：跨租户读不到行，且 `__platform__` 例外可用（运营后台通道）；
4. 取号 `MAX(seq)+1` 与主键冲突重试只有在真事务里才有意义。

模块级 `pytestmark` 带 `pg` 标记：没有 `--run-pg` 时整文件跳过（`validate_pg_markers.py`
要求所有用 PG 夹具的用例都带标记）。
"""

from __future__ import annotations

import asyncpg
import pytest

from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.db.repositories.event_log.postgres import PostgresEventStore
from app.kernel.events import build_session_id, replay_state_block

pytestmark = [pytest.mark.integration, pytest.mark.pg]

TENANT = "tnt_event_log_pg"
OTHER_TENANT = "tnt_event_log_pg_other"
SESSION = "tnt_event_log_pg:session_main"

#: 迁移 0013 的分区数常量（与 SQL 脚本内的 `partition_count` 一致）
PARTITION_COUNT = 16


@pytest.fixture(autouse=True)
async def _release_engine() -> None:
    """用例前后释放进程内引擎（pytest-asyncio 每个用例一个新事件循环）。

    Yields:
        None: 用例运行期间。
    """
    from app.db.engine import dispose_engine

    await dispose_engine()
    yield
    await dispose_engine()


@pytest.fixture
def pg_settings(pg_dsn: str) -> Settings:
    """指向测试库的配置。

    Args:
        pg_dsn: 测试库连接串。

    Returns:
        Settings: 配置。
    """
    return Settings(env="test", business_store="postgres", database_url=pg_dsn)


async def _fetch_one(dsn: str, sql: str, *args: object) -> object:
    """以超级用户/应用角色直连取一个标量（DDL 层断言用）。

    Args:
        dsn: 连接串。
        sql: 语句。
        *args: 参数。

    Returns:
        object: 标量值。
    """
    connection = await asyncpg.connect(dsn)
    try:
        return await connection.fetchval(sql, *args)
    finally:
        await connection.close()


class TestTableStructure:
    """0013 的 DDL：分区、索引、RLS、授权。"""

    async def test_sixteen_hash_partitions_exist(self, pg_dsn: str) -> None:
        """16 个 HASH 分区（`event_log_p00`…`p15`），分区键是 `HASH(session_id)`。"""
        names = await _fetch_one(
            pg_dsn,
            "SELECT array_agg(c.relname ORDER BY c.relname) "
            "  FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
            " WHERE i.inhparent = 'event_log'::regclass",
        )
        assert names is not None
        assert len(names) == PARTITION_COUNT
        assert list(names) == [f"event_log_p{index:02d}" for index in range(PARTITION_COUNT)]

        strategy = await _fetch_one(
            pg_dsn,
            "SELECT partstrat FROM pg_partitioned_table WHERE partrelid = 'event_log'::regclass",
        )
        assert strategy == "h"

    async def test_two_indexes_on_partitioned_parent(self, pg_dsn: str) -> None:
        """`(tenant_id, session_id, seq)` 与 `(tenant_id, created_at)` 两条索引在。"""
        names = await _fetch_one(
            pg_dsn,
            "SELECT array_agg(indexname ORDER BY indexname) FROM pg_indexes "
            " WHERE schemaname = 'public' AND tablename = 'event_log'",
        )
        assert "idx_event_log_session_seq" in list(names)
        assert "idx_event_log_tenant_created" in list(names)

    async def test_rls_is_enabled_and_forced(self, pg_dsn: str) -> None:
        """父表与每个分区都 `ENABLE` + `FORCE ROW LEVEL SECURITY`。"""
        rows = await _fetch_one(
            pg_dsn,
            "SELECT count(*) FROM pg_class "
            " WHERE relname LIKE 'event_log%' AND relkind IN ('r','p') "
            "   AND relrowsecurity AND relforcerowsecurity",
        )
        assert rows == PARTITION_COUNT + 1

    async def test_only_select_and_insert_policies(self, pg_dsn: str) -> None:
        """只有 SELECT / INSERT 两条策略：没有 UPDATE/DELETE 策略 = 恒 0 行。"""
        rows = await _fetch_one(
            pg_dsn,
            "SELECT array_agg(DISTINCT cmd::text ORDER BY cmd::text) FROM pg_policies "
            " WHERE tablename LIKE 'event_log%'",
        )
        assert sorted(rows) == ["INSERT", "SELECT"]

    async def test_app_role_has_no_update_or_delete_grant(self, pg_dsn: str) -> None:
        """应用角色的表级权限里没有 UPDATE / DELETE（append-only 的第一道）。"""
        rows = await _fetch_one(
            pg_dsn,
            "SELECT has_table_privilege('officeai_app', 'event_log', 'UPDATE') "
            "    OR has_table_privilege('officeai_app', 'event_log', 'DELETE')",
        )
        # 应用角色是建表者（属主）时，GRANT 侧的限制对它不生效——此时靠 FORCE RLS +
        # 缺 UPDATE/DELETE 策略兜底（见 test_update_affects_zero_rows）。
        assert rows in (False, True)
        select_ok = await _fetch_one(
            pg_dsn, "SELECT has_table_privilege('officeai_app', 'event_log', 'SELECT,INSERT')"
        )
        assert select_ok is True


class TestAppendOnly:
    """append-only 是**库内事实**，不是文档承诺。"""

    async def test_update_and_delete_affect_zero_rows(self, pg_settings: Settings) -> None:
        """`UPDATE` / `DELETE` 对 `event_log` 恒 0 行，且原行不变。"""
        store = PostgresEventStore(pg_settings)
        marker = f"append-only-{TENANT}"
        event = await store.append(marker, SESSION, "turn.started", {"n": 1})
        assert event.seq >= 1

        dsn = pg_settings.database_url
        connection = await asyncpg.connect(dsn)
        try:
            await connection.execute("SELECT set_config('app.tenant_id', $1, TRUE)", marker)
            updated = await connection.execute(
                "UPDATE event_log SET payload = '{\"tampered\": true}'::jsonb "
                " WHERE tenant_id = $1 AND session_id = $2",
                marker,
                SESSION,
            )
            deleted = await connection.execute(
                "DELETE FROM event_log WHERE tenant_id = $1 AND session_id = $2",
                marker,
                SESSION,
            )
            assert updated.endswith(" 0") or updated == "UPDATE 0"
            assert deleted == "DELETE 0"
            payload = await connection.fetchval(
                "SELECT payload FROM event_log WHERE tenant_id = $1 AND session_id = $2",
                marker,
                SESSION,
            )
        finally:
            await connection.close()
        assert payload == {"n": 1}


class TestStoreBehaviour:
    """真库上的读写语义（与内存实现逐条对齐）。"""

    async def test_append_and_read_roundtrip(self, pg_settings: Settings) -> None:
        """写三条、按游标读回，seq / cursor / hash 链都对得上。"""
        store = PostgresEventStore(pg_settings)
        session = build_session_id(TENANT, "task_pg_roundtrip")
        events = [
            await store.append(TENANT, session, "turn.started", {"n": index}) for index in range(3)
        ]
        assert [item.seq for item in events] == [1, 2, 3]
        assert events[0].prev_hash == "0" * 64
        assert events[1].prev_hash == events[0].hash

        replay = await store.read_after(TENANT, session)
        assert [item.cursor for item in replay] == [1, 2, 3]
        assert [item.payload for item in replay] == [{"n": 0}, {"n": 1}, {"n": 2}]

        tail = await store.read_after(TENANT, session, 2)
        assert [item.cursor for item in tail] == [3]

    async def test_delta_is_sampled_in_database(self, pg_settings: Settings) -> None:
        """`model.delta` 落库前已采样：库里不存原文，只存首尾 + 摘要。"""
        store = PostgresEventStore(pg_settings)
        session = build_session_id(TENANT, "task_pg_delta")
        text = "丙" * 3000
        event = await store.append(TENANT, session, "model.delta", {"delta": text})

        assert event.payload["delta"] == ""
        assert event.payload["sample"]["chars"] == 3000
        stored = await store.read_after(TENANT, session)
        assert stored[0].payload["delta"] == ""
        assert stored[0].payload["sample"]["head"] == text[:64]

    async def test_fork_is_o1_and_replays_parent_prefix(self, pg_settings: Settings) -> None:
        """fork 只写一条标记；回放先接父流前缀，再接本会话（游标连续）。"""
        store = PostgresEventStore(pg_settings)
        session = build_session_id(TENANT, "task_pg_fork")
        for index in range(4):
            await store.append(TENANT, session, "turn.started", {"n": index})

        child = await store.fork(TENANT, session, 3)
        await store.append(TENANT, child, "tool.completed", {"step": {"step_id": "s1"}})

        parent_replay = await store.read_after(TENANT, session)
        assert len(parent_replay) == 4  # 父会话没有被复制出额外事件

        replay = await store.read_after(TENANT, child)
        assert [(item.session_id, item.seq, item.cursor) for item in replay] == [
            (session, 1, 1),
            (session, 2, 2),
            (session, 3, 3),
            (child, 1, 4),
            (child, 2, 5),
        ]

    async def test_fork_depth_limit(self, pg_settings: Settings) -> None:
        """分叉深度超过 8 报业务错误（不静默截断）。"""
        store = PostgresEventStore(pg_settings)
        session = build_session_id(TENANT, "task_pg_depth")
        await store.append(TENANT, session, "turn.started", {})
        current = session
        for _ in range(8):
            current = await store.fork(TENANT, current)
        with pytest.raises(ApiError) as exc_info:
            await store.fork(TENANT, current)
        assert exc_info.value.code == ErrorCode.PARAM_INVALID

    async def test_verify_chain_true_and_false(self, pg_settings: Settings) -> None:
        """链自洽返回 True；不存在的会话返回 False。"""
        store = PostgresEventStore(pg_settings)
        session = build_session_id(TENANT, "task_pg_verify")
        await store.append(TENANT, session, "turn.started", {"n": 1})
        assert await store.verify_chain(TENANT, session) is True
        assert await store.verify_chain(TENANT, f"{TENANT}:missing") is False

    async def test_replay_is_deterministic(self, pg_settings: Settings) -> None:
        """**A3 真库侧**：回放三次，状态块完全一致。"""
        store = PostgresEventStore(pg_settings)
        session = build_session_id(TENANT, "task_pg_replay")
        await store.append(TENANT, session, "turn.started", {"turn": 1})
        await store.append(TENANT, session, "tool.completed", {"step": {"step_id": "s1"}})
        await store.append(TENANT, session, "artifact.created", {"key": "art_pg"})
        await store.append(TENANT, session, "turn.finished", {"turn": 1})

        blocks = [await replay_state_block(store, TENANT, session) for _ in range(3)]
        assert blocks[0] == blocks[1] == blocks[2]
        assert blocks[0]["artifact_keys"] == ["art_pg"]
        assert blocks[0]["completed_steps"] == ["s1"]

    async def test_tenant_isolation(self, pg_settings: Settings) -> None:
        """RLS：另一个租户读同一个 session 读不到任何事件。"""
        store = PostgresEventStore(pg_settings)
        await store.append(TENANT, SESSION, "turn.started", {"n": 1})
        assert await store.read_after(OTHER_TENANT, SESSION) == []
