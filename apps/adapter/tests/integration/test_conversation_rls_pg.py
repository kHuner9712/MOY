"""会话/消息的 RLS 与触发器真库验收（阶段四 T4-02.1，架构 §3.1 / §3.2 / §3.5）。

**为什么必须连真库**：RLS 与触发器都是**数据库侧**的行为，内存替身完全不经过它们。
"代码里带了 tenant_id 条件"不等于"数据库不会把别的租户的行吐给你"——本文件用
应用角色（`officeai_app`，`NOSUPERUSER NOBYPASSRLS`）直接连库断言。

⚠ 一条容易假绿的前提：**表属主 `officeai` 是超级用户，永远绕过 RLS**。所以本文件
   所有 RLS 断言都用**应用角色 DSN**（`pg_dsn` 夹具），绝不能用属主 DSN——
   否则会得到"有行"的假绿，而那正是线上最危险的那种绿灯。

四条判据：

1. 未设 `app.tenant_id` → `select` 返回 0 行（策略里 `current_setting(NULL)` 不成立）；
2. 跨租户 → 0 行；且**写入**别的租户被 `WITH CHECK` 挡住；
3. 触发器：`settled` 行改正文被拒；`streaming` 行改正文被允许；
4. 仓储层：100 条消息游标分页无重无漏（PG 实现，不是内存替身）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import asyncpg
import pytest

from app.core.config import Settings
from app.db.engine import dispose_engine
from app.db.repositories.conversation import (
    ConversationRecord,
    MessageRecord,
    PostgresConversationStore,
    PostgresMessageStore,
    new_conversation_id,
    new_message_id,
    reset_conversation_stores,
)

pytestmark = [pytest.mark.integration, pytest.mark.pg]


@pytest.fixture(autouse=True)
async def _dispose_engine_between_tests() -> AsyncIterator[None]:
    """每个用例后释放 SQLAlchemy 引擎。

    **为什么必须这么做**：`get_engine()` 是进程级缓存，而 pytest-asyncio 给每个用例
    一个新事件循环。上一个用例在旧循环里建的 asyncpg 连接留在池里，下一个用例取到它
    就会 `RuntimeError: Event loop is closed`——表现为"单独跑绿、一起跑红"。
    """
    yield
    await dispose_engine()


TENANT_A = "tnt_rls_conv_a"
TENANT_B = "tnt_rls_conv_b"
USER_A = "usr_rls_conv_a"


@pytest.fixture
def conv_settings(pg_dsn: str) -> Iterator[Settings]:
    """PG 后端配置（应用角色 DSN）。

    Args:
        pg_dsn: 测试库连接串（**应用角色**）。

    Returns:
        Settings: 配置实例。
    """
    reset_conversation_stores()
    settings = Settings(
        env="test",
        deploy_mode="private",
        downstream_enabled=False,
        auth_mode="stub",
        jwt_secret="conversation-rls-test-secret",
        ratelimit_enabled=False,
        business_store="postgres",
        database_url=pg_dsn,
    )
    yield settings
    reset_conversation_stores()


@pytest.fixture
async def conversations(conv_settings: Settings) -> AsyncIterator[PostgresConversationStore]:
    """会话存储（用例结束清理本次租户的行）。

    Args:
        conv_settings: 配置实例。

    Yields:
        AsyncIterator[PostgresConversationStore]: 存储。
    """
    store = PostgresConversationStore(conv_settings)
    yield store
    await _cleanup(conv_settings)


@pytest.fixture
async def messages(conv_settings: Settings) -> AsyncIterator[PostgresMessageStore]:
    """消息存储（用例结束清理本次租户的行）。

    Args:
        conv_settings: 配置实例。

    Yields:
        AsyncIterator[PostgresMessageStore]: 存储。
    """
    store = PostgresMessageStore(conv_settings)
    yield store
    await _cleanup(conv_settings)


async def _cleanup(settings: Settings) -> None:
    """删掉本次用例写入的行（平台模式，跨租户）。

    Args:
        settings: 配置实例。
    """
    dsn = settings.database_url
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute("SELECT set_config('app.tenant_id', '__platform__', FALSE)")
        await connection.execute("DELETE FROM message WHERE tenant_id LIKE 'tnt_rls_conv%'")
        await connection.execute("DELETE FROM conversation WHERE tenant_id LIKE 'tnt_rls_conv%'")
    finally:
        await connection.close()


# ===========================================================================
# 1. 未设租户会话变量 → 0 行
# ===========================================================================
async def test_select_without_tenant_returns_zero_rows(pg_dsn: str) -> None:
    """没有 `app.tenant_id` 时，应用角色读不到任何行（strict RLS 的默认拒绝）。"""
    dsn = pg_dsn
    seeder = await asyncpg.connect(dsn)
    try:
        await seeder.execute("SELECT set_config('app.tenant_id', $1, FALSE)", TENANT_A)
        await seeder.execute(
            "INSERT INTO conversation (tenant_id, conversation_id, user_id) "
            "VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
            TENANT_A,
            new_conversation_id(),
            USER_A,
        )
    finally:
        await seeder.close()

    fresh = await asyncpg.connect(dsn)  # 新连接：会话变量未设置
    try:
        assert await fresh.fetchval("SELECT count(*) FROM conversation") == 0
        assert await fresh.fetchval("SELECT count(*) FROM message") == 0
    finally:
        await fresh.close()
        cleanup = await asyncpg.connect(dsn)
        try:
            await cleanup.execute("SELECT set_config('app.tenant_id', '__platform__', FALSE)")
            await cleanup.execute("DELETE FROM conversation WHERE tenant_id = $1", TENANT_A)
        finally:
            await cleanup.close()


# ===========================================================================
# 2. 跨租户：读不到 + 写不进
# ===========================================================================
async def test_cross_tenant_rows_are_invisible(pg_dsn: str) -> None:
    """A 租户的行在 B 租户会话里 0 行。"""
    connection = await asyncpg.connect(pg_dsn)
    try:
        await connection.execute("SELECT set_config('app.tenant_id', $1, FALSE)", TENANT_A)
        await connection.execute(
            "INSERT INTO conversation (tenant_id, conversation_id, user_id, title) "
            "VALUES ($1, $2, $3, 'A 的会话')",
            TENANT_A,
            new_conversation_id(),
            USER_A,
        )
        assert await connection.fetchval("SELECT count(*) FROM conversation") == 1

        await connection.execute("SELECT set_config('app.tenant_id', $1, FALSE)", TENANT_B)
        assert await connection.fetchval("SELECT count(*) FROM conversation") == 0, (
            "跨租户读到了别的租户的会话——RLS 失效"
        )
    finally:
        await connection.execute("SELECT set_config('app.tenant_id', '__platform__', FALSE)")
        await connection.execute(
            "DELETE FROM conversation WHERE tenant_id IN ($1, $2)", TENANT_A, TENANT_B
        )
        await connection.close()


async def test_writing_other_tenant_is_rejected_by_with_check(pg_dsn: str) -> None:
    """在 A 的会话里插入 `tenant_id=B` 的行 → 被 `WITH CHECK` 拒绝。"""
    connection = await asyncpg.connect(pg_dsn)
    try:
        await connection.execute("SELECT set_config('app.tenant_id', $1, FALSE)", TENANT_A)
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await connection.execute(
                "INSERT INTO conversation (tenant_id, conversation_id, user_id) "
                "VALUES ($1, $2, $3)",
                TENANT_B,
                new_conversation_id(),
                USER_A,
            )
    finally:
        await connection.close()


# ===========================================================================
# 3. 正文不可变触发器
# ===========================================================================
async def test_content_update_on_settled_message_is_rejected(pg_dsn: str) -> None:
    """`settled` 行改正文 → 触发器拒（append-only 的机械保证）。"""
    connection = await asyncpg.connect(pg_dsn)
    message_id = new_message_id()
    try:
        await connection.execute("SELECT set_config('app.tenant_id', $1, FALSE)", TENANT_A)
        await connection.execute(
            "INSERT INTO message (tenant_id, message_id, conversation_id, user_id, role, "
            "content, status) VALUES ($1, $2, $3, $4, 'assistant', '已定稿', 'settled')",
            TENANT_A,
            message_id,
            new_conversation_id(),
            USER_A,
        )

        with pytest.raises(asyncpg.exceptions.RaiseError) as excinfo:
            await connection.execute(
                "UPDATE message SET content = '被改了' WHERE message_id = $1", message_id
            )
        assert "immutable" in str(excinfo.value)
    finally:
        await connection.execute("SELECT set_config('app.tenant_id', '__platform__', FALSE)")
        await connection.execute("DELETE FROM message WHERE tenant_id = $1", TENANT_A)
        await connection.close()


async def test_content_update_on_streaming_message_is_allowed(pg_dsn: str) -> None:
    """`streaming` 行可边收边写（流式路径的唯一合法形态）。"""
    connection = await asyncpg.connect(pg_dsn)
    message_id = new_message_id()
    try:
        await connection.execute("SELECT set_config('app.tenant_id', $1, FALSE)", TENANT_A)
        await connection.execute(
            "INSERT INTO message (tenant_id, message_id, conversation_id, user_id, role, "
            "content, status) VALUES ($1, $2, $3, $4, 'assistant', '', 'streaming')",
            TENANT_A,
            message_id,
            new_conversation_id(),
            USER_A,
        )

        await connection.execute(
            "UPDATE message SET content = '第一块' WHERE message_id = $1", message_id
        )
        await connection.execute(
            "UPDATE message SET content = '第一块第二块', status = 'settled' WHERE message_id = $1",
            message_id,
        )
        assert (
            await connection.fetchval(
                "SELECT content FROM message WHERE message_id = $1", message_id
            )
            == "第一块第二块"
        )
    finally:
        await connection.execute("SELECT set_config('app.tenant_id', '__platform__', FALSE)")
        await connection.execute("DELETE FROM message WHERE tenant_id = $1", TENANT_A)
        await connection.close()


async def test_status_cannot_regress_from_terminal(pg_dsn: str) -> None:
    """`settled` 不得回退到 `streaming`（防止"已完成"的行被复用）。"""
    connection = await asyncpg.connect(pg_dsn)
    message_id = new_message_id()
    try:
        await connection.execute("SELECT set_config('app.tenant_id', $1, FALSE)", TENANT_A)
        await connection.execute(
            "INSERT INTO message (tenant_id, message_id, conversation_id, user_id, role, "
            "content, status) VALUES ($1, $2, $3, $4, 'assistant', '完成', 'settled')",
            TENANT_A,
            message_id,
            new_conversation_id(),
            USER_A,
        )

        with pytest.raises(asyncpg.exceptions.RaiseError) as excinfo:
            await connection.execute(
                "UPDATE message SET status = 'streaming' WHERE message_id = $1", message_id
            )
        assert "regress" in str(excinfo.value)
    finally:
        await connection.execute("SELECT set_config('app.tenant_id', '__platform__', FALSE)")
        await connection.execute("DELETE FROM message WHERE tenant_id = $1", TENANT_A)
        await connection.close()


# ===========================================================================
# 4. 仓储层（PG 实现）
# ===========================================================================
async def test_conversation_roundtrip_and_soft_delete(
    conversations: PostgresConversationStore,
) -> None:
    """建 → 读 → 改 → 软删 → 读不到（但含软删的列表仍能看到）。"""
    created = await conversations.create(
        ConversationRecord(
            tenant_id=TENANT_A,
            conversation_id=new_conversation_id(),
            user_id=USER_A,
            title="PG 会话",
        )
    )

    fetched = await conversations.get(TENANT_A, USER_A, created.conversation_id)
    assert fetched is not None and fetched.title == "PG 会话"

    renamed = await conversations.update(
        TENANT_A, USER_A, created.conversation_id, title="改过的标题"
    )
    assert renamed is not None and renamed.title == "改过的标题"

    deleted = await conversations.soft_delete(TENANT_A, USER_A, created.conversation_id)
    assert deleted is not None and deleted.deleted_at
    assert await conversations.get(TENANT_A, USER_A, created.conversation_id) is None

    rows, total = await conversations.list_page(
        TENANT_A, USER_A, page=1, page_size=20, include_deleted=True
    )
    assert total == 1 and rows[0].conversation_id == created.conversation_id


async def test_message_pagination_over_pg_covers_all_without_dup(
    messages: PostgresMessageStore,
) -> None:
    """PG 实现下 100 条消息逐页往前拉到空：无重无漏（MC2 的核心判据）。"""
    conversation_id = new_conversation_id()
    expected: list[str] = []
    for index in range(100):
        record = await messages.append(
            MessageRecord(
                tenant_id=TENANT_A,
                message_id=new_message_id(),
                conversation_id=conversation_id,
                user_id=USER_A,
                role="user" if index % 2 == 0 else "assistant",
                content=f"第 {index} 条",
                status="settled",
            )
        )
        expected.append(record.message_id)

    seen: list[str] = []
    cursor: int | None = None
    for _round in range(20):
        page = await messages.list_before(TENANT_A, conversation_id, before_seq=cursor, limit=30)
        if not page:
            break
        seen.extend(item.message_id for item in page)
        cursor = page[-1].seq

    assert len(seen) == len(set(seen)), "分页出现重复行"
    assert set(seen) == set(expected), f"漏了 {len(set(expected) - set(seen))} 条"
    assert seen == list(reversed(expected)), "应按 seq 降序返回"


async def test_finalize_writes_terminal_state_over_pg(
    messages: PostgresMessageStore,
) -> None:
    """空壳按 `streaming` 插入 → `finalize` 写正文与终态 → 读回一致。"""
    conversation_id = new_conversation_id()
    placeholder = await messages.append(
        MessageRecord(
            tenant_id=TENANT_A,
            message_id=new_message_id(),
            conversation_id=conversation_id,
            user_id=USER_A,
            role="assistant",
            content="",
            status="streaming",  # 插入即 streaming：触发器只允许这种行写正文
        )
    )

    finalized = await messages.finalize(
        TENANT_A,
        placeholder.message_id,
        content="最终回答",
        status="settled",
        prompt_units=47,
        completion_units=16,
    )

    assert finalized is not None
    assert finalized.content == "最终回答" and finalized.status == "settled"
    assert (finalized.prompt_units, finalized.completion_units) == (47, 16)


async def test_orphan_scan_over_pg(messages: PostgresMessageStore) -> None:
    """滞留的 streaming 行能被 reconciler 扫到（终态行不得误收）。"""
    from datetime import UTC, datetime, timedelta

    conversation_id = new_conversation_id()
    await messages.append(
        MessageRecord(
            tenant_id=TENANT_A,
            message_id=new_message_id(),
            conversation_id=conversation_id,
            user_id=USER_A,
            role="assistant",
            status="streaming",
        )
    )
    await messages.append(
        MessageRecord(
            tenant_id=TENANT_A,
            message_id=new_message_id(),
            conversation_id=conversation_id,
            user_id=USER_A,
            role="assistant",
            status="settled",
        )
    )

    orphans = await messages.list_orphans(
        TENANT_A, older_than=datetime.now(UTC) + timedelta(minutes=1)
    )

    assert len(orphans) == 1 and orphans[0].status == "streaming"
