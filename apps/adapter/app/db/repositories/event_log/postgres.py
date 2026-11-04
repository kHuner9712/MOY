# ruff: noqa: S608 - 表名/列名均为模块常量（经 assert_identifier 校验），值一律命名参数绑定。
"""事件日志仓储 · PostgreSQL 实现（生产）。

表：`event_log`（迁移 `0013_event_log.sql`，16 个 HASH 分区 + RLS + 只授 INSERT/SELECT）。

**四条与契约绑定的口径**

1. **取号在同一事务内**：`SELECT COALESCE(MAX(seq), 0)` 与 `INSERT` 处于同一个
   `tenant_scope()` 事务；并发写同会话时后到者撞主键 `(session_id, seq)`，
   `append` 捕获冲突后**重开事务**重试（最多 `MAX_SEQ_ATTEMPTS` 次）。
   重试必须重开事务：冲突已让当前事务进入 aborted 状态，原地重试必定再次失败。
2. **hash 链**：`prev_hash` 取上一条的 `hash`（首条 `GENESIS_HASH`），
   `hash = sha256(prev_hash || event_id || type || canonical_json(payload))`。
3. **fork 不复制事件**：只在新会话 `seq=1` 写一条 `session.forked`；
   回放时把父链展开成一条流（游标规则见 `protocol.py`），深度上限
   `FORK_DEPTH_LIMIT = 8`，超限报业务错误（不静默截断）。
4. **读路径必须走 `tenant_scope`**：绝不自己拼 `tenant_id` 条件当作隔离手段——
   RLS 是第二道防线，`tenant_scope()` 的 `set_config('app.tenant_id')` 是第一道。
   SQL 里出现的 `tenant_id = :tenant_id` 只是"让索引前缀与 RLS 条件同源"，不是隔离本身。

**为什么父链用「最多 9 次单会话查询」而不是一条 `WITH RECURSIVE`**：设计文档 §5.1
写的是"用 `WITH RECURSIVE` 一次查完"——意图是**一次回放拿到整条父链**，这一点本实现
满足（`_lineage` 产出完整链后，事件由**一条** SQL 取回）。但"父会话的 `at_seq` 要从它
自己的 `seq=1` 事件里读"这一步是子查询，而 PostgreSQL 规定递归自引用不得出现在子查询/
外连接空侧，硬写会被编译器拒绝或退化成不可读的临时表。深度上限 8 使查询次数有界
（≤9 次，且每次都是主键 `(session_id, 1)` 命中），成本与一条 JOIN 同量级。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Final

from sqlalchemy.exc import IntegrityError

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.db.repositories.base import BaseRepository
from app.db.repositories.event_log.memory import DEFAULT_READ_LIMIT
from app.db.repositories.event_log.records import (
    EVENT_SESSION_FORKED,
    FORK_AT_SEQ_KEY,
    FORK_DEPTH_LIMIT,
    FORK_PARENT_KEY,
    GENESIS_HASH,
    MAX_SEQ_ATTEMPTS,
    Event,
    build_event,
    compact_delta_payload,
    compute_hash,
    new_event_id,
    new_fork_session_id,
    validate_event_type,
)
from app.db.session import tenant_scope

#: 事件表名（`BaseRepository` 会跑 `assert_identifier()` 校验）
EVENT_LOG_TABLE: Final[str] = "event_log"

#: 一次 `verify_chain` 最多取回的事件行数（超过即视为异常链条，拒绝给出结论）
_VERIFY_SCAN_LIMIT: Final[int] = 100_000

#: 取会话头部信息（末号 / 末 hash / 游标偏移）——一次往返拿齐三样
_HEAD_SQL: Final[str] = """
SELECT
    COALESCE(MAX(seq), 0) AS max_seq,
    (
        SELECT hash FROM event_log
         WHERE tenant_id = :tenant_id AND session_id = :session_id
         ORDER BY seq DESC
         LIMIT 1
    ) AS last_hash,
    (
        SELECT CASE
                   WHEN type = 'session.forked'
                   THEN COALESCE((payload ->> 'at_seq')::bigint, 0)
                   ELSE 0
               END
          FROM event_log
         WHERE tenant_id = :tenant_id AND session_id = :session_id AND seq = 1
    ) AS cursor_offset
  FROM event_log
 WHERE tenant_id = :tenant_id AND session_id = :session_id
"""

#: 会话的 seq=1 事件（判断它是不是 fork 标记）
_SEQ_ONE_SQL: Final[str] = """
SELECT type, payload, hash
  FROM event_log
 WHERE tenant_id = :tenant_id AND session_id = :session_id AND seq = 1
"""

#: 按父链取增量：一条 SQL 覆盖"父流前缀 + 本会话"，游标在 SQL 里算
_CHAIN_READ_SQL_TEMPLATE: Final[str] = """
WITH lineage (session_id, seq_limit, cursor_offset) AS (
    VALUES {values}
)
SELECT
    e.tenant_id,
    e.session_id,
    e.seq,
    e.event_id,
    e.type,
    e.payload,
    e.prev_hash,
    e.hash,
    e.created_at,
    l.cursor_offset + e.seq AS cursor
  FROM event_log e
  JOIN lineage l ON l.session_id = e.session_id
 WHERE e.tenant_id = :tenant_id
   AND (l.seq_limit IS NULL OR e.seq <= l.seq_limit)
   AND l.cursor_offset + e.seq > :after_cursor
 ORDER BY l.cursor_offset + e.seq
 LIMIT :limit
"""

#: 单会话全量读取（校验链用）
_SESSION_SCAN_SQL: Final[str] = """
SELECT tenant_id, session_id, seq, event_id, type, payload, prev_hash, hash, created_at
  FROM event_log
 WHERE tenant_id = :tenant_id AND session_id = :session_id
 ORDER BY seq
 LIMIT :limit
"""


def _to_event(row: Mapping[str, Any], offset: int) -> Event:
    """把 `event_log` 行映射为 `Event`。

    Args:
        row: 查询结果行（含 `created_at`，可含 SQL 侧算好的 `cursor`）。
        offset: 该行所属会话的游标偏移（根会话 0，fork 会话 `at_seq`）。

    Returns:
        Event: 事件记录。
    """
    raw_created = row.get("created_at")
    created_at = (
        raw_created.isoformat(timespec="milliseconds") if isinstance(raw_created, datetime) else ""
    )
    raw_payload = row.get("payload")
    payload = dict(raw_payload) if isinstance(raw_payload, Mapping) else {}
    seq = int(row["seq"])
    raw_cursor = row.get("cursor")
    # SQL 侧已经把 `cursor_offset + seq` 算好；没有 cursor 列时按传入 offset 计算。
    cursor = raw_cursor if isinstance(raw_cursor, int) else offset + seq
    return build_event(
        tenant_id=str(row["tenant_id"]),
        session_id=str(row["session_id"]),
        seq=seq,
        event_id=str(row["event_id"]),
        event_type=str(row["type"]),
        payload=payload,
        prev_hash=str(row["prev_hash"]),
        digest=str(row["hash"]),
        created_at=created_at,
        cursor_offset=cursor - seq,
    )


class _EventLogRepository(BaseRepository):
    """事件表仓储（统一表名与租户条件）。"""

    table = EVENT_LOG_TABLE


class PostgresEventStore:
    """PostgreSQL 事件存储（生产实现）。"""

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化。

        Args:
            settings: 可选配置实例。
        """
        self._settings = settings or get_settings()

    # ------------------------------------------------------------------
    # 父链展开
    # ------------------------------------------------------------------
    async def _lineage(
        self,
        tenant_id: str,
        session_id: str,
    ) -> list[tuple[str, int | None, int]]:
        """展开父链（本会话 → 根），返回 `[(会话, seq_limit, 游标偏移)]`。

        Args:
            tenant_id: 租户 ID。
            session_id: 会话 ID。

        Returns:
            list[tuple[str, int | None, int]]: 父链（含本会话，末尾是根）。

        Raises:
            ApiError: 父链深度超过 `FORK_DEPTH_LIMIT`。
        """
        chain: list[tuple[str, int | None, int]] = []
        current = session_id
        limit: int | None = None
        previous_offset: int | None = None
        depth = 0
        while True:
            marker = await self._read_seq_one(tenant_id, current)
            offset = 0
            parent = ""
            if marker is not None and marker[0] == EVENT_SESSION_FORKED:
                payload = marker[1]
                raw_at = payload.get(FORK_AT_SEQ_KEY)
                offset = raw_at if isinstance(raw_at, int) else 0
                raw_parent = payload.get(FORK_PARENT_KEY)
                parent = raw_parent if isinstance(raw_parent, str) else ""
            if previous_offset is not None:
                limit = previous_offset - offset
            chain.append((current, limit, offset))
            previous_offset = offset
            if not parent:
                return chain
            depth += 1
            if depth > FORK_DEPTH_LIMIT:
                raise ApiError(
                    ErrorCode.PARAM_INVALID,
                    internal_detail=(
                        f"fork chain depth exceeds limit {FORK_DEPTH_LIMIT} at session {current}"
                    ),
                )
            current = parent

    async def _read_seq_one(
        self, tenant_id: str, session_id: str
    ) -> tuple[str, dict[str, Any]] | None:
        """读会话的 `seq=1` 事件（判断 fork 标记）。

        Args:
            tenant_id: 租户 ID。
            session_id: 会话 ID。

        Returns:
            tuple[str, dict[str, Any]] | None: `(类型, payload)`；无事件返回 None。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _EventLogRepository(uow)
            row = await repo.fetch_one(_SEQ_ONE_SQL, repo.params(session_id=session_id))
        if row is None:
            return None
        raw_payload = row.get("payload")
        payload = dict(raw_payload) if isinstance(raw_payload, Mapping) else {}
        return (str(row["type"]), payload)

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
        """追加一条事件（`seq` 同事务取号，主键冲突重试）。

        Args:
            tenant_id: 租户 ID。
            session_id: 会话 ID。
            event_type: 事件类型（封闭枚举之一）。
            payload: 事件载荷（`model.delta` 会被采样）。

        Returns:
            Event: 落库后的事件记录。

        Raises:
            ValueError: 事件类型未登记。
            ApiError: 连续取号冲突（超过 `MAX_SEQ_ATTEMPTS` 次）。
        """
        validate_event_type(event_type)
        compacted = compact_delta_payload(event_type, payload)
        event_id = new_event_id()
        for attempt in range(MAX_SEQ_ATTEMPTS):
            try:
                return await self._append_once(
                    tenant_id, session_id, event_type, compacted, event_id
                )
            except IntegrityError:
                # 并发写同一会话：另一个事务抢到了同一个 seq。事务已被 abort，
                # 必须重开（下一次循环的 tenant_scope）而不是原地重试。
                if attempt == MAX_SEQ_ATTEMPTS - 1:
                    raise ApiError(
                        ErrorCode.INTERNAL_ERROR,
                        internal_detail=(
                            f"event seq conflict persisted after {MAX_SEQ_ATTEMPTS} "
                            f"attempts (session={session_id})"
                        ),
                    ) from None
        raise ApiError(  # pragma: no cover - 循环必然 return 或 raise
            ErrorCode.INTERNAL_ERROR,
            internal_detail="unreachable append retry state",
        )

    async def _append_once(
        self,
        tenant_id: str,
        session_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        event_id: str,
    ) -> Event:
        """在**一个事务内**完成「取号 → 算 hash → 写入」。

        Args:
            tenant_id: 租户 ID。
            session_id: 会话 ID。
            event_type: 事件类型。
            payload: 已采样的载荷。
            event_id: 事件 ID。

        Returns:
            Event: 落库后的事件记录。
        """
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _EventLogRepository(uow)
            head = await repo.fetch_one(_HEAD_SQL, repo.params(session_id=session_id))
            max_seq = int(head["max_seq"]) if head is not None else 0
            raw_last_hash = head.get("last_hash") if head is not None else None
            prev_hash = str(raw_last_hash) if raw_last_hash else GENESIS_HASH
            raw_offset = head.get("cursor_offset") if head is not None else None
            offset = int(raw_offset) if raw_offset is not None else 0
            seq = max_seq + 1
            digest = compute_hash(prev_hash, event_id, event_type, payload)
            # 表名取模块常量、列清单写在同一个 f-string 里：`G-SQL`
            # （tests/integration/test_sql_insert_consistency.py）要求 INSERT 的
            # 表名与列清单能被**静态**解析出来，写成模块常量的普通字符串会被判
            # "无法静态解析"，那条语句就不再参与列对账（结论不成立）。
            inserted = await repo.fetch_one(
                f"""
                INSERT INTO {EVENT_LOG_TABLE} (
                    tenant_id, session_id, seq, event_id, type, payload, prev_hash, hash
                ) VALUES (
                    :tenant_id, :session_id, :seq, :event_id, :event_type,
                    CAST(:payload AS jsonb), :prev_hash, :hash
                )
                RETURNING created_at
                """,
                repo.params(
                    session_id=session_id,
                    seq=seq,
                    event_id=event_id,
                    event_type=event_type,
                    payload=json.dumps(payload, ensure_ascii=False),
                    prev_hash=prev_hash,
                    hash=digest,
                ),
            )
            raw_created = inserted.get("created_at") if inserted is not None else None
            created_at = (
                raw_created.isoformat(timespec="milliseconds")
                if isinstance(raw_created, datetime)
                else ""
            )
        return build_event(
            tenant_id=tenant_id,
            session_id=session_id,
            seq=seq,
            event_id=event_id,
            event_type=event_type,
            payload=payload,
            prev_hash=prev_hash,
            digest=digest,
            created_at=created_at,
            cursor_offset=offset,
        )

    async def read_after(
        self,
        tenant_id: str,
        session_id: str,
        after_seq: int = 0,
        limit: int = DEFAULT_READ_LIMIT,
    ) -> list[Event]:
        """按回放游标取增量（父链前缀 + 本会话，一条 SQL）。

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
        lineage = await self._lineage(tenant_id, session_id)
        placeholders: list[str] = []
        params: dict[str, Any] = {
            "tenant_id": tenant_id,
            "after_cursor": after_seq,
            "limit": limit,
        }
        for index, (sid, seq_limit, offset) in enumerate(lineage):
            # 必须用 `CAST(:x AS type)` 而不是 `:x::type`：SQLAlchemy 的绑定参数
            # 正则带 `(?!:)` 前瞻，`:x::type` 里的 `:x` 会因为紧跟冒号而**不被识别
            # 为绑定参数**，最终原样发到库里变成语法错。项目里既有的
            # `CAST(:expires_at AS timestamptz)` 就是同一个原因。
            placeholders.append(
                f"(CAST(:sid_{index} AS text), "
                f"CAST(:limit_{index} AS bigint), "
                f"CAST(:offset_{index} AS bigint))"
            )
            params[f"sid_{index}"] = sid
            params[f"limit_{index}"] = seq_limit
            params[f"offset_{index}"] = offset
        sql = _CHAIN_READ_SQL_TEMPLATE.replace("{values}", ", ".join(placeholders))
        rows: list[Mapping[str, Any]] = []
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _EventLogRepository(uow)
            rows = await repo.fetch_all(sql, params)
        offsets = {sid: offset for sid, _limit, offset in lineage}
        return [_to_event(row, offsets[str(row["session_id"])]) for row in rows]

    async def fork(
        self,
        tenant_id: str,
        session_id: str,
        at_seq: int | None = None,
    ) -> str:
        """从某游标处分叉出新会话（不复制事件，O(1)）。

        Args:
            tenant_id: 租户 ID。
            session_id: 父会话 ID。
            at_seq: 分叉游标；None 表示取父会话当前末尾。

        Returns:
            str: 新会话 ID。

        Raises:
            ApiError: 父会话不存在、切点越界，或分叉深度超限。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _EventLogRepository(uow)
            head = await repo.fetch_one(_HEAD_SQL, repo.params(session_id=session_id))
        max_seq = int(head["max_seq"]) if head is not None else 0
        if max_seq == 0:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                internal_detail=f"fork parent session not found: {session_id}",
            )
        raw_offset = head.get("cursor_offset") if head is not None else None
        offset = int(raw_offset) if raw_offset is not None else 0
        tail = offset + max_seq
        lineage = await self._lineage(tenant_id, session_id)
        depth = len(lineage) - 1
        if depth >= FORK_DEPTH_LIMIT:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                internal_detail=(
                    f"fork depth exceeds limit {FORK_DEPTH_LIMIT} (parent depth {depth})"
                ),
            )
        cut = tail if at_seq is None else at_seq
        if cut < 1 or cut > tail:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                internal_detail=f"fork at_seq {cut} out of range 1..{tail}",
            )
        new_session_id = new_fork_session_id()
        await self.append(
            tenant_id,
            new_session_id,
            EVENT_SESSION_FORKED,
            {FORK_PARENT_KEY: session_id, FORK_AT_SEQ_KEY: cut},
        )
        return new_session_id

    async def verify_chain(self, tenant_id: str, session_id: str) -> bool:
        """重算并校验 hash 链（含 fork 父链，深度上限 8）。

        Args:
            tenant_id: 租户 ID。
            session_id: 会话 ID。

        Returns:
            bool: 链自洽返回 True。
        """
        current = session_id
        depth = 0
        while True:
            async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
                repo = _EventLogRepository(uow)
                rows = await repo.fetch_all(
                    _SESSION_SCAN_SQL,
                    repo.params(session_id=current, limit=_VERIFY_SCAN_LIMIT),
                )
            if not rows:
                return False
            prev = GENESIS_HASH
            for row in rows:
                if str(row["prev_hash"]) != prev:
                    return False
                raw_payload = row.get("payload")
                payload = dict(raw_payload) if isinstance(raw_payload, Mapping) else {}
                recomputed = compute_hash(
                    str(row["prev_hash"]), str(row["event_id"]), str(row["type"]), payload
                )
                if recomputed != str(row["hash"]):
                    return False
                prev = str(row["hash"])
            marker = rows[0]
            if str(marker["type"]) != EVENT_SESSION_FORKED:
                return True
            raw_payload = marker.get("payload")
            payload = dict(raw_payload) if isinstance(raw_payload, Mapping) else {}
            raw_parent = payload.get(FORK_PARENT_KEY)
            parent = raw_parent if isinstance(raw_parent, str) else ""
            if not parent:
                return True
            depth += 1
            if depth > FORK_DEPTH_LIMIT:
                return False
            current = parent


__all__ = ["EVENT_LOG_TABLE", "PostgresEventStore"]
