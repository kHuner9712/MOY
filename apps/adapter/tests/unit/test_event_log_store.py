"""会话事件日志仓储 · 语义用例（内存实现 + 纯函数，A3/A4 验收在此）。

覆盖的判据（规格 §3.1 E1 / 设计文档 §5.1）：

- hash 链可复算（客户拿到导出文件能自己算）；
- `model.delta` 采样**生效**（R3 风险对冲：行数与字节数都不随 turn 长度膨胀）；
- fork 是 **O(1)**（A4：500 事件会话分叉 < 100ms，不复制历史）；
- 回放**确定性**（A3：同一 `session_id` 回放三次，状态块与产物键完全一致）；
- fork 父链递归深度上限 8（超限报业务错误，不静默截断）。
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import time
from dataclasses import replace
from typing import Any

import pytest

from app.core.errors import ApiError, ErrorCode
from app.db.repositories.event_log.memory import InMemoryEventStore
from app.db.repositories.event_log.records import (
    DELTA_SAMPLE_HEAD_CHARS,
    DELTA_SAMPLE_TAIL_CHARS,
    EVENT_MODEL_DELTA,
    EVENT_SESSION_FORKED,
    EVENT_TOOL_COMPLETED,
    EVENT_TOOL_REQUESTED,
    EVENT_TURN_FINISHED,
    EVENT_TURN_STARTED,
    EVENT_TYPES,
    FORK_DEPTH_LIMIT,
    GENESIS_HASH,
    MAX_DELTA_ROWS_PER_TURN,
    build_session_id,
    canonical_json,
    compact_delta_payload,
    compute_hash,
    plan_delta_rows,
    validate_event_type,
)
from app.kernel.events import append_model_deltas, replay_state_block

TENANT = "tnt_event_log"


def _store() -> InMemoryEventStore:
    """构造干净的内存事件存储。"""
    return InMemoryEventStore()


# ===========================================================================
# 纯函数：规范化 JSON / hash 链 / 事件类型封闭枚举
# ===========================================================================
class TestCanonicalJsonAndHash:
    """hash 链的输入必须是**规范化** JSON，否则客户复算会漂移。"""

    def test_canonical_json_sorts_keys_and_drops_spaces(self) -> None:
        """键排序 + 无空格 + UTF-8 不转义，三条同时成立。"""
        assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'
        assert canonical_json({"k": "中文"}) == '{"k":"中文"}'
        assert " " not in canonical_json({"a": [1, 2], "b": {"c": None}})

    def test_key_order_does_not_change_hash(self) -> None:
        """同一 payload 的两种键序必须算出同一个 hash（跨语言复算的前提）。"""
        first = compute_hash(GENESIS_HASH, "evt_1", "turn.started", {"a": 1, "b": 2})
        second = compute_hash(GENESIS_HASH, "evt_1", "turn.started", {"b": 2, "a": 1})
        assert first == second
        assert len(first) == 64

    def test_hash_covers_prev_event_id_type_and_payload(self) -> None:
        """四个输入任一变化都必须改变 hash（否则链断了看不出来）。"""
        base = compute_hash(GENESIS_HASH, "evt_1", "turn.started", {"a": 1})
        assert base != compute_hash("1" * 64, "evt_1", "turn.started", {"a": 1})
        assert base != compute_hash(GENESIS_HASH, "evt_2", "turn.started", {"a": 1})
        assert base != compute_hash(GENESIS_HASH, "evt_1", "turn.finished", {"a": 1})
        assert base != compute_hash(GENESIS_HASH, "evt_1", "turn.started", {"a": 2})

    def test_event_type_enum_is_closed_at_fourteen(self) -> None:
        """V1.0 事件类型封闭 14 类；`subagent.spawn` 不在内（V1.1 预留）。"""
        assert len(EVENT_TYPES) == 14
        assert "subagent.spawn" not in EVENT_TYPES
        assert validate_event_type("model.delta") == "model.delta"
        with pytest.raises(ValueError):
            validate_event_type("subagent.spawn")


# ===========================================================================
# `model.delta` 采样（R3 风险对冲，必须证明确实生效）
# ===========================================================================
class TestDeltaSampling:
    """采样有两层：载荷级（原文压成首尾 + 摘要）+ 行级（一个 turn 只落首尾两条）。"""

    def test_payload_compaction_keeps_only_head_tail_and_digest(self) -> None:
        """长文本只留首尾与摘要，字节数不再随 turn 长度线性增长。"""
        text = "甲" * 5000
        compacted = compact_delta_payload(EVENT_MODEL_DELTA, {"delta": text, "index": 3})
        assert compacted["delta"] == ""
        assert compacted["index"] == 3
        sample = compacted["sample"]
        assert sample["head"] == text[:DELTA_SAMPLE_HEAD_CHARS]
        assert sample["tail"] == text[-DELTA_SAMPLE_TAIL_CHARS:]
        assert sample["chars"] == 5000
        assert sample["sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()
        assert sample["sampled"] is True
        assert len(canonical_json(compacted)) < 600

    def test_compaction_is_idempotent(self) -> None:
        """已采样的载荷再采一次不变（否则 sha256 会算成摘要的摘要）。"""
        once = compact_delta_payload(EVENT_MODEL_DELTA, {"delta": "x" * 300})
        twice = compact_delta_payload(EVENT_MODEL_DELTA, once)
        assert twice == once

    def test_short_delta_keeps_empty_tail(self) -> None:
        """短文本没有"尾部"可留：tail 为空串，head 就是全文。"""
        compacted = compact_delta_payload(EVENT_MODEL_DELTA, {"text": "短"})
        assert compacted["sample"]["head"] == "短"
        assert compacted["sample"]["tail"] == ""

    def test_non_delta_payload_untouched(self) -> None:
        """非 `model.delta` 的载荷原样保留（压缩它会改变回放结果）。"""
        payload = {"summary": "x" * 5000}
        assert compact_delta_payload("model.response", payload) == payload

    def test_row_plan_keeps_only_first_and_last(self) -> None:
        """行级：1 条留 1 条，≥2 条只留首尾（上限 2 行/turn）。"""
        assert plan_delta_rows(0) == []
        assert plan_delta_rows(1) == [0]
        assert plan_delta_rows(40) == [0, 39]
        assert len(plan_delta_rows(40)) <= MAX_DELTA_ROWS_PER_TURN

    async def test_store_compacts_delta_on_write(self) -> None:
        """写进存储的 `model.delta` 一定是采样态：原文不进库。"""
        store = _store()
        event = await store.append(TENANT, "s_delta", EVENT_MODEL_DELTA, {"delta": "乙" * 2000})
        assert event.payload["delta"] == ""
        assert event.payload["sample"]["chars"] == 2000

    async def test_append_model_deltas_writes_at_most_two_rows(self) -> None:
        """40 条增量片段只落 2 行（否则行数会到 turn 数的 20 倍）。"""
        store = _store()
        deltas = [f"片段{index}" for index in range(40)]
        written = await append_model_deltas(store, TENANT, "s_turn", deltas)
        assert len(written) == MAX_DELTA_ROWS_PER_TURN
        stored = await store.read_after(TENANT, "s_turn")
        assert len(stored) == MAX_DELTA_ROWS_PER_TURN
        assert [item.payload["index"] for item in stored] == [0, 39]
        # 载荷里仍是采样态（head/tail/摘要），不是原文
        assert all(item.payload["delta"] == "" for item in stored)


# ===========================================================================
# 追加语义：seq 取号、创世 hash、hash 链
# ===========================================================================
class TestAppendSemantics:
    """append 的三条硬约束：seq 从 1 连续、首条 prev_hash 全 0、hash 可复算。"""

    async def test_seq_starts_at_one_and_is_contiguous(self) -> None:
        """`seq` 从 1 开始且连续递增（主键 `(session_id, seq)` 的取号前提）。"""
        store = _store()
        events = [
            await store.append(TENANT, "s1", EVENT_TURN_STARTED, {"n": index}) for index in range(3)
        ]
        assert [event.seq for event in events] == [1, 2, 3]
        assert [event.cursor for event in events] == [1, 2, 3]

    async def test_first_event_prev_hash_is_genesis(self) -> None:
        """首条事件的 `prev_hash` 是 64 个 `0`。"""
        store = _store()
        first = await store.append(TENANT, "s1", EVENT_TURN_STARTED, {"n": 1})
        assert first.prev_hash == GENESIS_HASH == "0" * 64

    async def test_hash_chain_links_each_event(self) -> None:
        """每条事件都能用 `prev_hash || event_id || type || payload` 复算出来。"""
        store = _store()
        events = [
            await store.append(TENANT, "s1", EVENT_TURN_STARTED, {"n": index}) for index in range(4)
        ]
        for previous, current in itertools.pairwise(events):
            assert current.prev_hash == previous.hash
        for event in events:
            assert (
                compute_hash(event.prev_hash, event.event_id, event.type, event.payload)
                == event.hash
            )

    async def test_sessions_are_independent(self) -> None:
        """两个会话各自的 seq 与链互不干扰。"""
        store = _store()
        await store.append(TENANT, "s1", EVENT_TURN_STARTED, {})
        second = await store.append(TENANT, "s2", EVENT_TURN_STARTED, {})
        assert second.seq == 1
        assert second.prev_hash == GENESIS_HASH

    async def test_unregistered_type_is_rejected(self) -> None:
        """未登记的事件类型直接拒绝写入（不静默落库）。"""
        store = _store()
        with pytest.raises(ValueError):
            await store.append(TENANT, "s1", "subagent.spawn", {})

    async def test_three_tenants_with_same_task_id_do_not_collide(self) -> None:
        """同一个 `task_id` 在两个租户下是两条互不干扰的会话（会话 ID 编入租户）。"""
        store = _store()
        task_id = "task_01K4Q8Z2X7M9B3N5P6R8T0V2W4"
        first = build_session_id("tnt_a", task_id)
        second = build_session_id("tnt_b", task_id)
        assert first != second
        await store.append("tnt_a", first, EVENT_TURN_STARTED, {})
        await store.append("tnt_b", second, EVENT_TURN_STARTED, {})
        assert len(await store.read_after("tnt_a", first)) == 1
        assert len(await store.read_after("tnt_b", second)) == 1


# ===========================================================================
# 读取语义：游标、批读、limit
# ===========================================================================
class TestReadSemantics:
    """`read_after` 是续传的唯一取数路径，游标语义必须精确。"""

    async def test_read_after_cursor_excludes_the_cursor_itself(self) -> None:
        """`cursor > after_seq`：等于游标的那条不重复下发。"""
        store = _store()
        for index in range(5):
            await store.append(TENANT, "s1", EVENT_TURN_STARTED, {"n": index})
        tail = await store.read_after(TENANT, "s1", 2)
        assert [event.cursor for event in tail] == [3, 4, 5]

    async def test_read_after_respects_limit(self) -> None:
        """limit 生效，且不改变顺序。"""
        store = _store()
        for index in range(5):
            await store.append(TENANT, "s1", EVENT_TURN_STARTED, {"n": index})
        batch = await store.read_after(TENANT, "s1", 0, limit=2)
        assert [event.cursor for event in batch] == [1, 2]

    async def test_non_positive_limit_is_rejected(self) -> None:
        """limit 非正是调用方缺陷，直接 ValueError（不静默返回全量）。"""
        store = _store()
        with pytest.raises(ValueError):
            await store.read_after(TENANT, "s1", 0, limit=0)

    async def test_empty_tenant_is_rejected(self) -> None:
        """空租户直接拒绝（与 PG 侧 `tenant_scope()` 的守卫对齐：缺租户不进内核）。"""
        store = _store()
        with pytest.raises(ApiError) as exc_info:
            await store.append("", "s1", EVENT_TURN_STARTED, {})
        assert exc_info.value.code == ErrorCode.PARAM_INVALID
        with pytest.raises(ApiError):
            await store.read_after("", "s1")
        with pytest.raises(ApiError):
            await store.verify_chain("", "s1")

    async def test_platform_tenant_requires_explicit_scope(self) -> None:
        """`__platform__` 不得由仓储层自行放行（越权进平台模式是编码缺陷）。"""
        store = _store()
        with pytest.raises(ApiError) as exc_info:
            await store.append("__platform__", "s1", EVENT_TURN_STARTED, {})
        assert exc_info.value.code == ErrorCode.INTERNAL_ERROR

    async def test_unknown_session_returns_empty(self) -> None:
        """未知会话返回空列表，不抛错（前端首次打开空白任务页是常态）。"""
        store = _store()
        assert await store.read_after(TENANT, "s_missing") == []


# ===========================================================================
# fork：O(1) + 父链回放 + 深度上限
# ===========================================================================
class TestFork:
    """fork 不复制历史（A4），回放递归接父流（设计文档 §5.1）。"""

    async def test_fork_writes_single_marker_at_seq_one(self) -> None:
        """分叉只在新会话写一条 `session.forked`，父会话事件数不变。"""
        store = _store()
        for index in range(3):
            await store.append(TENANT, "s1", EVENT_TURN_STARTED, {"n": index})
        before = len(await store.read_after(TENANT, "s1"))

        new_session = await store.fork(TENANT, "s1", 2)

        assert len(await store.read_after(TENANT, "s1")) == before
        own = await store.read_after(TENANT, new_session, 2)
        assert len(own) == 1
        assert own[0].type == EVENT_SESSION_FORKED
        assert own[0].seq == 1
        assert own[0].payload == {"parent_session": "s1", "at_seq": 2}

    async def test_forked_replay_prepends_parent_prefix(self) -> None:
        """回放 = 父流前 `at_seq` 条 + 本会话全部，游标全局单调。"""
        store = _store()
        for index in range(5):
            await store.append(TENANT, "s1", EVENT_TURN_STARTED, {"n": index})
        new_session = await store.fork(TENANT, "s1", 3)
        await store.append(TENANT, new_session, EVENT_TOOL_COMPLETED, {"step": {"step_id": "a"}})

        replay = await store.read_after(TENANT, new_session)
        assert [(event.session_id, event.seq, event.cursor) for event in replay] == [
            ("s1", 1, 1),
            ("s1", 2, 2),
            ("s1", 3, 3),
            (new_session, 1, 4),
            (new_session, 2, 5),
        ]

    async def test_nested_fork_keeps_cursor_space_flat(self) -> None:
        """嵌套 fork 的游标连续（父流前缀 + 本流），不会出现重复游标。"""
        store = _store()
        for index in range(4):
            await store.append(TENANT, "root", EVENT_TURN_STARTED, {"n": index})
        child = await store.fork(TENANT, "root", 2)
        grandchild = await store.fork(TENANT, child)
        replay = await store.read_after(TENANT, grandchild)
        cursors = [event.cursor for event in replay]
        assert cursors == sorted(cursors) == list(range(1, len(cursors) + 1))

    async def test_fork_without_at_seq_cuts_at_tail(self) -> None:
        """`at_seq=None` 表示分叉到当前末尾。"""
        store = _store()
        for index in range(3):
            await store.append(TENANT, "s1", EVENT_TURN_STARTED, {"n": index})
        new_session = await store.fork(TENANT, "s1")
        replay = await store.read_after(TENANT, new_session)
        marker = next(event for event in replay if event.session_id == new_session)
        assert marker.parent_at_seq() == 3

    async def test_fork_missing_parent_is_business_error(self) -> None:
        """父会话不存在报业务错误（不建出一个空血统的分叉）。"""
        store = _store()
        with pytest.raises(ApiError) as exc_info:
            await store.fork(TENANT, "s_missing")
        assert exc_info.value.code == ErrorCode.PARAM_INVALID

    async def test_fork_at_seq_out_of_range_is_rejected(self) -> None:
        """切点越界（0 或超过末尾）拒绝。"""
        store = _store()
        await store.append(TENANT, "s1", EVENT_TURN_STARTED, {})
        for bad in (0, 2):
            with pytest.raises(ApiError) as exc_info:
                await store.fork(TENANT, "s1", bad)
            assert exc_info.value.code == ErrorCode.PARAM_INVALID

    async def test_fork_depth_limit_is_enforced(self) -> None:
        """递归深度上限 8：第 9 层分叉报业务错误（不静默截断历史）。"""
        store = _store()
        await store.append(TENANT, "s0", EVENT_TURN_STARTED, {})
        session = "s0"
        for _ in range(FORK_DEPTH_LIMIT):
            session = await store.fork(TENANT, session)
        with pytest.raises(ApiError) as exc_info:
            await store.fork(TENANT, session)
        assert exc_info.value.code == ErrorCode.PARAM_INVALID

    async def test_fork_of_500_event_session_is_fast(self) -> None:
        """**A4 验收**：500 事件会话分叉 < 100ms（不复制事件 = O(1)）。"""
        store = _store()
        for index in range(500):
            await store.append(TENANT, "s_big", EVENT_TURN_STARTED, {"n": index})

        started = time.perf_counter()
        new_session = await asyncio.wait_for(store.fork(TENANT, "s_big"), timeout=5.0)
        elapsed_ms = (time.perf_counter() - started) * 1000

        assert elapsed_ms < 100, f"fork 耗时 {elapsed_ms:.1f}ms，超过 A4 的 100ms 上限"
        # 分叉后父会话仍是 500 条：没有复制历史
        assert len(await store.read_after(TENANT, "s_big")) == 500
        assert len(await store.read_after(TENANT, new_session, 500)) == 1


# ===========================================================================
# 回放确定性（A3）与 hash 链校验
# ===========================================================================
class TestReplayDeterminism:
    """同一 `session_id` 回放三次，状态块与产物键必须完全一致。"""

    async def _seed(self, store: InMemoryEventStore) -> str:
        """造一条含步骤 / 产物 / 审批 / turn 结束的会话。

        Args:
            store: 事件存储。

        Returns:
            str: 会话 ID。
        """
        session = "s_replay"
        await store.append(TENANT, session, "turn.started", {"turn": 1})
        await store.append(TENANT, session, EVENT_TOOL_REQUESTED, {"step": {"step_id": "step_1"}})
        await store.append(TENANT, session, EVENT_TOOL_COMPLETED, {"step": {"step_id": "step_1"}})
        await store.append(TENANT, session, "artifact.created", {"key": "art_a", "version": 1})
        await store.append(TENANT, session, "artifact.created", {"key": "art_b", "version": 1})
        await store.append(TENANT, session, "approval.requested", {"approval_id": "apr_1"})
        await store.append(TENANT, session, EVENT_TURN_FINISHED, {"turn": 1})
        return session

    async def test_replay_three_times_is_identical(self) -> None:
        """**A3 验收**：回放三次，`TaskStateBlock` 与产物键逐字一致。"""
        store = _store()
        session = await self._seed(store)

        blocks = [await replay_state_block(store, TENANT, session) for _ in range(3)]
        assert blocks[0] == blocks[1] == blocks[2]
        assert blocks[0]["artifact_keys"] == ["art_a", "art_b"]
        assert blocks[0]["completed_steps"] == ["step_1"]
        assert blocks[0]["pending_approvals"] == ["apr_1"]
        assert blocks[0]["turns"] == 1

    async def test_replay_stream_digest_is_stable(self) -> None:
        """事件序列的逐字节摘要三次一致（回放不依赖任何进程内状态）。"""
        store = _store()
        session = await self._seed(store)

        digests = []
        for _ in range(3):
            events = await store.read_after(TENANT, session)
            material = "".join(f"{event.cursor}:{event.hash}" for event in events)
            digests.append(hashlib.sha256(material.encode("utf-8")).hexdigest())
        assert len(set(digests)) == 1

    async def test_approval_resolved_is_removed_from_pending(self) -> None:
        """投影规则：`approval.resolved` 把待确认项消掉。"""
        store = _store()
        await store.append(TENANT, "s", "approval.requested", {"approval_id": "apr_1"})
        await store.append(TENANT, "s", "approval.resolved", {"approval_id": "apr_1"})
        block = await replay_state_block(store, TENANT, "s")
        assert block["pending_approvals"] == []


class TestStoreSingleton:
    """仓储单例的选择与重置（按 `business_store` 选后端，不新增第二套开关）。"""

    def test_memory_backend_is_selected_for_memory_store(self) -> None:
        """`business_store=memory` 选内存实现（PG 实现会让每次写都抛 30004）。"""
        from app.core.config import Settings
        from app.db.repositories.event_log import (
            InMemoryEventStore,
            PostgresEventStore,
            get_event_store,
            reset_event_store,
        )

        reset_event_store()
        store = get_event_store(Settings(env="test", business_store="memory"))
        assert isinstance(store, InMemoryEventStore)
        assert get_event_store() is store, "单例必须复用同一个实例"
        reset_event_store()

        pg_store = get_event_store(Settings(env="test", business_store="postgres"))
        assert isinstance(pg_store, PostgresEventStore)
        reset_event_store()

    def test_memory_store_accessor_returns_none_when_postgres(self) -> None:
        """当前是 PG 实现时，测试出口 `get_memory_event_store()` 返回 None。"""
        from app.core.config import Settings
        from app.db.repositories.event_log import (
            get_event_store,
            get_memory_event_store,
            reset_event_store,
        )

        reset_event_store()
        get_event_store(Settings(env="test", business_store="postgres"))
        assert get_memory_event_store() is None
        reset_event_store()

        memory = get_memory_event_store()
        assert isinstance(memory, InMemoryEventStore)
        reset_event_store()


class TestVerifyChain:
    """`verify_chain` 是审计导出的前置自检。"""

    async def test_valid_chain_returns_true(self) -> None:
        """刚写出来的链自洽。"""
        store = _store()
        for index in range(3):
            await store.append(TENANT, "s1", EVENT_TURN_STARTED, {"n": index})
        assert await store.verify_chain(TENANT, "s1") is True

    async def test_missing_session_returns_false(self) -> None:
        """不存在的会话返回 False（不是抛错：自检的结论就是"验不过"）。"""
        store = _store()
        assert await store.verify_chain(TENANT, "s_missing") is False

    async def test_tampered_hash_is_detected(self) -> None:
        """篡改任一条的 hash 即被发现（这正是给客户的完整性证据）。"""
        store = _store()
        await store.append(TENANT, "s1", EVENT_TURN_STARTED, {"n": 1})
        await store.append(TENANT, "s1", EVENT_TURN_STARTED, {"n": 2})

        tampered: list[Any] = list(store._streams[(TENANT, "s1")])  # noqa: SLF001
        tampered[1] = replace(tampered[1], payload={"n": 999})
        store._streams[(TENANT, "s1")] = tampered  # noqa: SLF001

        assert await store.verify_chain(TENANT, "s1") is False

    async def test_forked_chain_verifies_parent(self) -> None:
        """分叉会话的自检覆盖父链（导出证据要能一路算到根）。"""
        store = _store()
        await store.append(TENANT, "root", EVENT_TURN_STARTED, {})
        child = await store.fork(TENANT, "root")
        assert await store.verify_chain(TENANT, child) is True
