"""会话与消息仓储的单元验收（阶段四 T4-02.2，架构 §3.1 / §3.2 / §4.4）。

三条判据都是"错了会让用户看见别人的数据或丢失自己的数据"的类型：

1. **个人边界**：`user_id` 不同的会话必须查不到（RLS 只管租户，个人边界在仓储层）；
2. **游标分页无重无漏**：造 100 条消息逐页往前拉到空，集合比对必须与全集相等；
3. **终态幂等 + 正文不可变语义**：`finalize` 重复调用不改内容；已软删的会话读不到。

用内存替身跑（不需要真库），真库路径由 `test_conversation_rls_pg.py` 覆盖。
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest

from app.db.repositories.conversation import (
    MAX_TITLE_LENGTH,
    ConversationRecord,
    InMemoryConversationStore,
    InMemoryMessageStore,
    MessageRecord,
    decode_cursor,
    encode_cursor,
    new_conversation_id,
    new_message_id,
    recoverable_until,
)

pytestmark = pytest.mark.unit

TENANT = "tnt_test_tenant"
OTHER_TENANT = "tnt_other_tenant"
USER = "usr_test_user"
OTHER_USER = "usr_other_user"


async def _new_conversation(
    store: InMemoryConversationStore, *, user_id: str = USER, title: str = "上线计划"
) -> ConversationRecord:
    """建一个会话。

    Args:
        store: 会话存储。
        user_id: 归属人。
        title: 标题。

    Returns:
        ConversationRecord: 会话。
    """
    return await store.create(
        ConversationRecord(
            tenant_id=TENANT, conversation_id=new_conversation_id(), user_id=user_id, title=title
        )
    )


# ===========================================================================
# 1. 游标编解码
# ===========================================================================
def test_cursor_roundtrip() -> None:
    """编码后再解析得到同一个 `seq`。"""
    token = encode_cursor(17)
    assert decode_cursor(token) == 17


def test_cursor_is_opaque_base64url() -> None:
    """游标是不透明串（不含原始数字的明文），且不需要额外转义。"""
    token = encode_cursor(12345)
    assert "/" not in token and "+" not in token and "=" not in token
    raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
    assert json.loads(raw) == {"seq": 12345}


@pytest.mark.parametrize(
    "cursor",
    [
        "",
        "  ",
        "not-base64!!",
        base64.urlsafe_b64encode(b"not-json").decode().rstrip("="),
        base64.urlsafe_b64encode(b'{"seq": 0}').decode().rstrip("="),
        base64.urlsafe_b64encode(b'{"seq": -3}').decode().rstrip("="),
        base64.urlsafe_b64encode(b'{"seq": "7"}').decode().rstrip("="),
        base64.urlsafe_b64encode(b'{"seq": 7, "user_id": "x"}').decode().rstrip("="),
    ],
)
def test_invalid_cursor_returns_none(cursor: str) -> None:
    """非法游标一律返回 None（调用方据此报 20001，而不是静默从头开始）。

    特别覆盖「客户端塞私货」（多带 `user_id`）——游标里的键集合必须精确。
    """
    assert decode_cursor(cursor) is None


# ===========================================================================
# 2. 个人 / 租户边界
# ===========================================================================
async def test_conversation_is_invisible_to_other_user() -> None:
    """同一租户的**其他用户**读不到（RLS 管不到这一层，必须由仓储层强制）。"""
    store = InMemoryConversationStore()
    created = await _new_conversation(store)

    assert await store.get(TENANT, USER, created.conversation_id) is not None
    assert await store.get(TENANT, OTHER_USER, created.conversation_id) is None
    assert await store.get(OTHER_TENANT, USER, created.conversation_id) is None

    rows, total = await store.list_page(TENANT, OTHER_USER, page=1, page_size=20)
    assert rows == [] and total == 0


async def test_list_page_filters_archived_and_deleted() -> None:
    """归档开关与软删都必须影响列表（含"回收站"视图）。"""
    store = InMemoryConversationStore()
    active = await _new_conversation(store, title="进行中")
    archived = await _new_conversation(store, title="已归档")
    await store.update(TENANT, USER, archived.conversation_id, archived=True)
    deleted = await _new_conversation(store, title="已删除")
    await store.soft_delete(TENANT, USER, deleted.conversation_id)

    rows, total = await store.list_page(TENANT, USER, page=1, page_size=20)
    assert total == 1 and [row.conversation_id for row in rows] == [active.conversation_id]

    rows, total = await store.list_page(TENANT, USER, page=1, page_size=20, archived=True)
    assert total == 1 and rows[0].conversation_id == archived.conversation_id

    rows, total = await store.list_page(TENANT, USER, page=1, page_size=20, include_deleted=True)
    assert total == 2, "含软删时应看到未归档的 2 条（进行中 + 已删除）"


# ===========================================================================
# 3. 软删与恢复窗口
# ===========================================================================
async def test_soft_delete_hides_conversation_but_keeps_row() -> None:
    """软删后 `get` 返回 None，但记录仍在（可恢复）。"""
    store = InMemoryConversationStore()
    created = await _new_conversation(store)

    deleted = await store.soft_delete(TENANT, USER, created.conversation_id)

    assert deleted is not None and deleted.deleted_at
    assert await store.get(TENANT, USER, created.conversation_id) is None
    rows, _total = await store.list_page(TENANT, USER, page=1, page_size=20, include_deleted=True)
    assert [row.conversation_id for row in rows] == [created.conversation_id]


def test_recoverable_until_is_thirty_days_after_delete() -> None:
    """`recoverable_until` = 删除时间 + 默认 30 天（口径可配）。"""
    stamp = datetime(2026, 11, 1, 8, 0, tzinfo=UTC).isoformat().replace("+00:00", "Z")

    deadline = recoverable_until(stamp)

    assert deadline.startswith("2026-12-01T08:00:00")
    assert deadline.endswith("Z")


def test_recoverable_until_handles_empty_value() -> None:
    """空删除时间 → 空串（不抛异常，MC3 的幂等分支会用到）。"""
    assert recoverable_until("") == ""


# ===========================================================================
# 4. 标题长度与更新
# ===========================================================================
async def test_title_is_clamped_to_max_length() -> None:
    """标题超长按上限截断（DDL 的 CHECK 也限制 120，两侧口径一致）。"""
    store = InMemoryConversationStore()
    created = await store.create(
        ConversationRecord(
            tenant_id=TENANT,
            conversation_id=new_conversation_id(),
            user_id=USER,
            title="长" * 500,
        )
    )

    assert len(created.title) == MAX_TITLE_LENGTH


async def test_update_title_and_archived_only() -> None:
    """只传 title 时 archived 不变；只传 archived 时 title 不变。"""
    store = InMemoryConversationStore()
    created = await _new_conversation(store, title="原标题")

    renamed = await store.update(TENANT, USER, created.conversation_id, title="新标题")
    assert renamed is not None and renamed.title == "新标题" and renamed.archived is False

    archived = await store.update(TENANT, USER, created.conversation_id, archived=True)
    assert archived is not None and archived.archived is True and archived.title == "新标题"


async def test_update_unknown_conversation_returns_none() -> None:
    """改一个不存在的会话 → None（端点层翻成 18001）。"""
    store = InMemoryConversationStore()
    assert await store.update(TENANT, USER, "conv_missing", title="x") is None


# ===========================================================================
# 5. 消息：seq 单调、分页无重无漏
# ===========================================================================
async def test_message_seq_is_monotonic() -> None:
    """`seq` 严格递增（游标分页的正确性前提）。"""
    store = InMemoryMessageStore()
    conversation_id = new_conversation_id()
    seqs = []
    for index in range(5):
        record = await store.append(
            MessageRecord(
                tenant_id=TENANT,
                message_id=new_message_id(),
                conversation_id=conversation_id,
                user_id=USER,
                role="user" if index % 2 == 0 else "assistant",
                content=f"第 {index} 条",
                status="settled",
            )
        )
        seqs.append(record.seq)

    assert seqs == sorted(seqs) and len(set(seqs)) == 5


async def test_message_pagination_covers_all_100_without_dup_or_gap() -> None:
    """造 100 条消息，逐页往前拉到空：集合必须无重无漏（MC2 的核心判据）。"""
    store = InMemoryMessageStore()
    conversation_id = new_conversation_id()
    expected: list[str] = []
    for index in range(100):
        record = await store.append(
            MessageRecord(
                tenant_id=TENANT,
                message_id=new_message_id(),
                conversation_id=conversation_id,
                user_id=USER,
                role="user" if index % 2 == 0 else "assistant",
                content=f"第 {index} 条",
                status="settled",
            )
        )
        expected.append(record.message_id)

    seen: list[str] = []
    cursor: int | None = None
    for _round in range(20):  # 100 / 30 → 4 轮足够，留足余量
        page = await store.list_before(TENANT, conversation_id, before_seq=cursor, limit=30)
        if not page:
            break
        seen.extend(item.message_id for item in page)
        cursor = page[-1].seq

    assert len(seen) == len(set(seen)), "分页出现重复行"
    assert set(seen) == set(expected), f"分页漏了 {len(set(expected) - set(seen))} 条"
    assert seen == list(reversed(expected)), "应按 seq 降序返回（时间从新到旧）"


async def test_message_pagination_isolated_by_conversation_and_tenant() -> None:
    """别的会话、别的租户的消息不得混进来。"""
    store = InMemoryMessageStore()
    mine = new_conversation_id()
    other = new_conversation_id()
    await store.append(
        MessageRecord(
            tenant_id=TENANT,
            message_id="msg_mine",
            conversation_id=mine,
            role="user",
            content="我的",
        )
    )
    await store.append(
        MessageRecord(
            tenant_id=TENANT,
            message_id="msg_other_conv",
            conversation_id=other,
            role="user",
            content="别的会话",
        )
    )
    await store.append(
        MessageRecord(
            tenant_id=OTHER_TENANT,
            message_id="msg_other_tenant",
            conversation_id=mine,
            role="user",
            content="别的租户",
        )
    )

    page = await store.list_before(TENANT, mine, before_seq=None, limit=10)

    assert [item.message_id for item in page] == ["msg_mine"]


# ===========================================================================
# 6. 终态写入（幂等 + 正文不可变语义）
# ===========================================================================
async def test_finalize_writes_content_and_status() -> None:
    """streaming 行写终态：正文、状态、额度一次到位。"""
    store = InMemoryMessageStore()
    record = await store.append(
        MessageRecord(
            tenant_id=TENANT,
            message_id="msg_stream",
            conversation_id=new_conversation_id(),
            role="assistant",
            content="",
            status="streaming",
        )
    )

    updated = await store.finalize(
        TENANT,
        record.message_id,
        content="完整回答",
        status="settled",
        prompt_units=47,
        completion_units=16,
    )

    assert updated is not None
    assert updated.content == "完整回答" and updated.status == "settled"
    assert (updated.prompt_units, updated.completion_units) == (47, 16)


async def test_finalize_is_idempotent_for_same_terminal_status() -> None:
    """同终态重复调用不改内容（网络重试 / 断连补偿都会重复调）。"""
    store = InMemoryMessageStore()
    record = await store.append(
        MessageRecord(
            tenant_id=TENANT,
            message_id="msg_done",
            conversation_id=new_conversation_id(),
            role="assistant",
            content="答案",
            status="settled",
        )
    )

    again = await store.finalize(TENANT, record.message_id, content="答案", status="settled")

    assert again is not None and again.content == "答案"


async def test_finalize_unknown_message_returns_none() -> None:
    """消息不存在 → None（端点层不上抛，收口时按"已消失"处理）。"""
    store = InMemoryMessageStore()
    assert await store.finalize(TENANT, "msg_missing", content="x", status="failed") is None


# ===========================================================================
# 7. 孤儿消息（启动 reconciler）
# ===========================================================================
async def test_list_orphans_returns_only_stale_non_terminal() -> None:
    """只挑 `pending/streaming` 且超过阈值的行；终态行不得被误收。"""
    store = InMemoryMessageStore()
    conversation_id = new_conversation_id()
    stale = await store.append(
        MessageRecord(
            tenant_id=TENANT,
            message_id="msg_stale",
            conversation_id=conversation_id,
            role="assistant",
            status="streaming",
        )
    )
    stale.created_at = (
        (datetime.now(UTC) - timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
    )
    await store.append(
        MessageRecord(
            tenant_id=TENANT,
            message_id="msg_fresh",
            conversation_id=conversation_id,
            role="assistant",
            status="streaming",
        )
    )
    await store.append(
        MessageRecord(
            tenant_id=TENANT,
            message_id="msg_settled",
            conversation_id=conversation_id,
            role="assistant",
            status="settled",
        )
    )

    orphans = await store.list_orphans(TENANT, older_than=datetime.now(UTC) - timedelta(minutes=10))

    assert [item.message_id for item in orphans] == ["msg_stale"]
