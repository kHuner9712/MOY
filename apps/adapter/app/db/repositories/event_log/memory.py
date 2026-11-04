"""事件日志仓储 · 内存实现（development / test）。

行为与 `postgres.py` **逐步对齐**（G-38 门禁逐方法比对签名，`tests/unit/test_event_log_store.py`
逐语义比对两条路径），差异只允许出现在"存储载体"上：

- 内存：`dict[(tenant_id, session_id)] -> list[Event]`，取号靠 `len + 1`；
- PG：`event_log` 哈希分区表，取号靠 `MAX(seq)+1` + 主键冲突重试。

**为什么内存实现不是"随便写写"**：测试跑的是内存实现、生产跑的是 PG 实现，两条
路径只在生产才分叉。内存侧少实现一条语义（例如忘了 fork 展平父链），用例全绿
而线上续传丢事件——这类缺陷没有任何用例会红，只能靠"两侧同语义"的纪律。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any, Final

from app.core.errors import ApiError, ErrorCode
from app.db.repositories.event_log.records import (
    EVENT_SESSION_FORKED,
    FORK_AT_SEQ_KEY,
    FORK_DEPTH_LIMIT,
    FORK_PARENT_KEY,
    GENESIS_HASH,
    Event,
    build_event,
    compact_delta_payload,
    compute_hash,
    new_event_id,
    new_fork_session_id,
    now_iso,
    validate_event_type,
)
from app.db.tenant import assert_tenant_allowed

#: 单次读取的默认上限（与协议默认值一致）
DEFAULT_READ_LIMIT: Final[int] = 500

#: 会话索引键
_SessionKey = tuple[str, str]


class InMemoryEventStore:
    """内存事件存储（append-only、hash 链、可回放、可 fork）。"""

    def __init__(self) -> None:
        """初始化空存储。"""
        self._streams: dict[_SessionKey, list[Event]] = {}

    # ------------------------------------------------------------------
    # 读侧内部工具（fork 展平的唯一实现，read_after / fork / verify 共用）
    # ------------------------------------------------------------------
    @staticmethod
    def _key(tenant_id: str, session_id: str) -> _SessionKey:
        """构造会话索引键（租户 + 会话，与 PG 的 RLS 维度一致）。

        Args:
            tenant_id: 租户 ID。
            session_id: 会话 ID。

        Returns:
            _SessionKey: 复合键。
        """
        return (tenant_id, session_id)

    @staticmethod
    def _tenant(tenant_id: str) -> str:
        """校验并规范化租户 ID（与 PG 侧 `tenant_scope()` 的守卫对齐）。

        PG 实现的每次读写都经 `tenant_scope()`，那里会对空租户抛 20001、对
        `__platform__` 抛 30003。内存实现若不做这一步，"同一个调用在内存后端能过、
        在 PG 后端炸"——而这条差异**只在生产出现**，正是 G-38 想拦住的那类漂移。

        Args:
            tenant_id: 租户 ID。

        Returns:
            str: 规范化后的租户 ID。

        Raises:
            ApiError: 租户 ID 为空（20001）或越权进入平台模式（30003）。
        """
        return assert_tenant_allowed(tenant_id)

    def _fork_edge(self, tenant_id: str, session_id: str) -> tuple[str, int] | None:
        """取会话的分叉边（父会话 + 切点）。

        Args:
            tenant_id: 租户 ID。
            session_id: 会话 ID。

        Returns:
            tuple[str, int] | None: `(父会话 ID, at_seq)`；非 fork 会话返回 None。
        """
        events = self._streams.get(self._key(tenant_id, session_id))
        if not events:
            return None
        first = events[0]
        if not first.is_fork_marker():
            return None
        parent = first.parent_session()
        if not parent:
            return None
        return (parent, first.parent_at_seq())

    def _cursor_offset(self, tenant_id: str, session_id: str) -> int:
        """取会话自身事件的游标偏移（根会话 0，fork 会话 `at_seq`）。

        Args:
            tenant_id: 租户 ID。
            session_id: 会话 ID。

        Returns:
            int: 游标偏移。
        """
        edge = self._fork_edge(tenant_id, session_id)
        return edge[1] if edge is not None else 0

    def _lineage(self, tenant_id: str, session_id: str) -> list[tuple[str, int | None, int]]:
        """展开父链（本会话 → 根），返回 `[(会话, seq_limit, 游标偏移)]`。

        为什么 seq_limit 是 `父切点 - 本偏移` 而不是父切点本身：父会话自己的事件
        在父流里的位置是 `A_父 + seq`，而子会话只取父流的前 `A_子` 条，于是父会话
        自身事件被保留到 `A_父 + seq <= A_子` 为止，即 `seq <= A_子 - A_父`。

        Args:
            tenant_id: 租户 ID。
            session_id: 会话 ID。

        Returns:
            list[tuple[str, int | None, int]]: 从本会话到根的链（深度 ≤ 上限 + 1）。

        Raises:
            ApiError: 父链深度超过 `FORK_DEPTH_LIMIT`（拒绝给出被截断的回放）。
        """
        chain: list[tuple[str, int | None, int]] = []
        current = session_id
        limit: int | None = None
        previous_offset: int | None = None
        depth = 0
        while True:
            offset = self._cursor_offset(tenant_id, current)
            if previous_offset is not None:
                # 本层的事件在父流里的位置是 `A_本 + seq`，子会话只取父流前 `A_子` 条
                # → 本层自身事件保留到 `seq <= A_子 - A_本`。
                limit = previous_offset - offset
            chain.append((current, limit, offset))
            previous_offset = offset
            edge = self._fork_edge(tenant_id, current)
            if edge is None:
                return chain
            depth += 1
            if depth > FORK_DEPTH_LIMIT:
                raise ApiError(
                    ErrorCode.PARAM_INVALID,
                    internal_detail=(
                        f"fork chain depth exceeds limit {FORK_DEPTH_LIMIT} at session {current}"
                    ),
                )
            current = edge[0]

    def _flatten(self, tenant_id: str, session_id: str) -> list[Event]:
        """把父链展开成一条可回放的事件流（根在前，游标升序）。

        Args:
            tenant_id: 租户 ID。
            session_id: 会话 ID。

        Returns:
            list[Event]: 展平后的事件流。
        """
        lineage = self._lineage(tenant_id, session_id)
        flattened: list[Event] = []
        for sid, seq_limit, offset in reversed(lineage):
            for event in self._streams.get(self._key(tenant_id, sid), []):
                if seq_limit is not None and event.seq > seq_limit:
                    continue
                # 只改派生字段 `cursor`：用 `replace` 而不是重新构造，
                # 保证"记录形状"只有一个来源（`records.build_event`），
                # 这也是 G-38 的 R4（两侧构造点关键字集合）能一直对齐的原因。
                flattened.append(
                    replace(event, payload=dict(event.payload), cursor=offset + event.seq)
                )
        flattened.sort(key=lambda item: item.cursor)
        return flattened

    # ------------------------------------------------------------------
    # 协议实现
    # ------------------------------------------------------------------
    async def append(
        self,
        tenant_id: str,
        session_id: str,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> Event:
        """追加一条事件。

        Args:
            tenant_id: 租户 ID。
            session_id: 会话 ID。
            event_type: 事件类型（封闭枚举之一）。
            payload: 事件载荷（`model.delta` 会被采样）。

        Returns:
            Event: 落库后的事件记录。

        Raises:
            ValueError: 事件类型未登记。
        """
        validate_event_type(event_type)
        tenant_id = self._tenant(tenant_id)
        key = self._key(tenant_id, session_id)
        stream = self._streams.setdefault(key, [])
        seq = len(stream) + 1
        prev_hash = stream[-1].hash if stream else GENESIS_HASH
        compacted = compact_delta_payload(event_type, payload)
        event_id = new_event_id()
        digest = compute_hash(prev_hash, event_id, event_type, compacted)
        # fork 会话的游标偏移取自它自己的 `session.forked`（seq=1）；根会话为 0。
        # 分叉标记自身写入时，偏移就是它 payload 里的 at_seq。
        if event_type == EVENT_SESSION_FORKED:
            offset_value = compacted.get(FORK_AT_SEQ_KEY)
            offset = offset_value if isinstance(offset_value, int) else 0
        else:
            offset = self._cursor_offset(tenant_id, session_id)
        event = build_event(
            tenant_id=tenant_id,
            session_id=session_id,
            seq=seq,
            event_id=event_id,
            event_type=event_type,
            payload=compacted,
            prev_hash=prev_hash,
            digest=digest,
            created_at=now_iso(),
            cursor_offset=offset,
        )
        stream.append(event)
        return event

    async def read_after(
        self,
        tenant_id: str,
        session_id: str,
        after_seq: int = 0,
        limit: int = DEFAULT_READ_LIMIT,
    ) -> list[Event]:
        """按回放游标取增量。

        Args:
            tenant_id: 租户 ID。
            session_id: 会话 ID。
            after_seq: 回放游标（传 0 表示从头）。
            limit: 单次最多返回条数。

        Returns:
            list[Event]: 升序事件列表。

        Raises:
            ValueError: `limit` 非正。
        """
        if limit <= 0:
            raise ValueError(f"limit must be positive, got {limit}")
        tenant_id = self._tenant(tenant_id)
        events = [item for item in self._flatten(tenant_id, session_id) if item.cursor > after_seq]
        return events[:limit]

    async def fork(
        self,
        tenant_id: str,
        session_id: str,
        at_seq: int | None = None,
    ) -> str:
        """从某游标处分叉出新会话（不复制事件）。

        Args:
            tenant_id: 租户 ID。
            session_id: 父会话 ID。
            at_seq: 分叉游标；None 表示取父会话当前末尾。

        Returns:
            str: 新会话 ID。

        Raises:
            ApiError: 父会话不存在、切点越界，或分叉深度超限。
        """
        tenant_id = self._tenant(tenant_id)
        stream = self._streams.get(self._key(tenant_id, session_id))
        if not stream:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                internal_detail=f"fork parent session not found: {session_id}",
            )
        depth = len(self._lineage(tenant_id, session_id)) - 1
        if depth >= FORK_DEPTH_LIMIT:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                internal_detail=(
                    f"fork depth exceeds limit {FORK_DEPTH_LIMIT} (parent depth {depth})"
                ),
            )
        tail = self._cursor_offset(tenant_id, session_id) + stream[-1].seq
        cut = tail if at_seq is None else at_seq
        # 切点下限是**父会话自身偏移 + 1**（缺陷 D7），不是 1：fork 会话自己的
        # 事件从游标 `偏移 + 1` 开始，允许切到 `偏移` 或更小会把新标记插进**父流
        # 中间**，于是同一段父流在两层回放里出现两次、游标重复——续传的"零重复"
        # 当场失效（三层 fork 实测可复现）。
        lowest = self._cursor_offset(tenant_id, session_id) + 1
        if cut < lowest or cut > tail:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                internal_detail=f"fork at_seq {cut} out of range {lowest}..{tail}",
            )
        new_session_id = new_fork_session_id()
        await self.append(
            tenant_id,
            new_session_id,
            EVENT_SESSION_FORKED,
            {FORK_PARENT_KEY: session_id, FORK_AT_SEQ_KEY: cut},
        )
        return new_session_id

    async def tail_cursor(self, tenant_id: str, session_id: str) -> int:
        """取会话回放流的末尾游标（空会话返回 0）。

        Args:
            tenant_id: 租户 ID。
            session_id: 会话 ID。

        Returns:
            int: 末尾游标。
        """
        tenant_id = self._tenant(tenant_id)
        stream = self._streams.get(self._key(tenant_id, session_id))
        if not stream:
            return 0
        return self._cursor_offset(tenant_id, session_id) + stream[-1].seq

    async def verify_chain(self, tenant_id: str, session_id: str) -> bool:
        """重算并校验 hash 链（含 fork 父链，深度上限 8）。

        Args:
            tenant_id: 租户 ID。
            session_id: 会话 ID。

        Returns:
            bool: 链自洽返回 True。
        """
        tenant_id = self._tenant(tenant_id)
        current = session_id
        depth = 0
        while True:
            stream = self._streams.get(self._key(tenant_id, current))
            if not stream:
                return False
            prev = GENESIS_HASH
            for event in stream:
                if event.prev_hash != prev:
                    return False
                if compute_hash(event.prev_hash, event.event_id, event.type, event.payload) != (
                    event.hash
                ):
                    return False
                prev = event.hash
            edge = self._fork_edge(tenant_id, current)
            if edge is None:
                return True
            depth += 1
            if depth > FORK_DEPTH_LIMIT:
                return False
            current = edge[0]

    # ------------------------------------------------------------------
    # 测试隔离钩子（`TEST_HOOKS`：只允许存在于内存侧）
    # ------------------------------------------------------------------
    def clear(self) -> None:
        """清空全部会话（测试隔离用；PG 侧不得出现同名方法）。"""
        self._streams.clear()

    def counts(self) -> int:
        """返回事件总条数（测试断言用；PG 侧不得出现同名方法）。

        Returns:
            int: 全部会话的事件总数。
        """
        return sum(len(stream) for stream in self._streams.values())


__all__ = ["DEFAULT_READ_LIMIT", "InMemoryEventStore"]
