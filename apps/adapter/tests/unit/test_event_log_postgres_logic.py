"""`PostgresEventStore` 的**控制流**用例（不连库，用假 UoW 驱动）。

**为什么需要这一层**（它不替代 `tests/integration/test_event_log_pg.py`）：

- 真库用例带 `pg` 标记，`--run-pg` 才跑；本地默认档里 `postgres.py` 一行都执行不到，
  覆盖率与**控制流分支**（取号重试、fork 越界、父链深度、链校验失败）全是盲区。
  盲区里的缺陷只在生产暴露——那是本项目最贵的一类缺陷。
- 这里用一个**假工作单元**（`tenant_scope` 被 monkeypatch）驱动同一份实现代码：
  判定对象是「实现的控制流 + 绑定参数」，SQL 的**语义**仍由真库用例负责。
  两者分工写清楚，避免"用假对象测出假安全感"。

假 UoW 只实现 `BaseRepository` 用到的三样：`tenant_id` / `fetch_one` / `fetch_all`。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.db.repositories.event_log.postgres import PostgresEventStore
from app.kernel.events import GENESIS_HASH, build_session_id, compute_hash

pytestmark = pytest.mark.unit

TENANT = "tnt_el_pg_logic"


class _FakeUoW:
    """假工作单元（只覆盖 `BaseRepository` 用到的接口）。"""

    def __init__(self, cluster: _FakeCluster, tenant_id: str) -> None:
        """初始化。

        Args:
            cluster: 假集群（行存储）。
            tenant_id: 生效租户（与真 UoW 一致：由 tenant_scope 决定）。
        """
        self._cluster = cluster
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        """生效租户 ID。"""
        return self._tenant_id

    async def fetch_one(self, statement: Any, parameters: dict[str, Any] | None = None) -> Any:
        """执行"查询单行"。

        Args:
            statement: SQL（本层只按文本特征分派）。
            parameters: 命名参数。

        Returns:
            dict[str, Any] | None: 行。
        """
        rows = self._cluster.handle(str(statement), parameters or {})
        return rows[0] if rows else None

    async def fetch_all(self, statement: Any, parameters: dict[str, Any] | None = None) -> Any:
        """执行"查询多行"。

        Args:
            statement: SQL。
            parameters: 命名参数。

        Returns:
            list[dict[str, Any]]: 行列表。
        """
        return self._cluster.handle(str(statement), parameters or {})


class _FakeCluster:
    """极简"库"：按 `(tenant, session)` 存行，实现三条读 SQL 的**最小**语义。

    刻意不追求 SQL 等价：真正的 SQL 语义由 `test_event_log_pg.py` 在真库上验。
    这里只需要让实现的控制流走遍（取号、游标换算、父链展开、冲突重试）。
    """

    def __init__(self) -> None:
        """初始化空集群。"""
        self.rows: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self.conflict_once = False

    def _session_rows(self, tenant_id: str, session_id: str) -> list[dict[str, Any]]:
        """取某会话的行（升序）。

        Args:
            tenant_id: 租户 ID。
            session_id: 会话 ID。

        Returns:
            list[dict[str, Any]]: 行列表。
        """
        return self.rows.setdefault((tenant_id, session_id), [])

    def handle(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """按 SQL 文本特征分派。

        Args:
            sql: SQL 文本。
            params: 命名参数。

        Returns:
            list[dict[str, Any]]: 结果行。
        """
        tenant_id = str(params.get("tenant_id", ""))
        if "MAX(seq)" in sql:
            return [self._head(tenant_id, str(params["session_id"]))]
        if "seq = 1" in sql:
            session_id = str(params["session_id"])
            rows = self._session_rows(tenant_id, session_id)
            return rows[:1]
        if "INSERT INTO" in sql:
            return [self._insert(tenant_id, params)]
        if "WITH lineage" in sql:
            return self._chain(tenant_id, params)
        if "ORDER BY seq" in sql:
            return list(self._session_rows(tenant_id, str(params["session_id"])))
        return []  # set_config / SET TRANSACTION 等无关语句

    def _head(self, tenant_id: str, session_id: str) -> dict[str, Any]:
        """构造 `_HEAD_SQL` 的结果行。

        Args:
            tenant_id: 租户 ID。
            session_id: 会话 ID。

        Returns:
            dict[str, Any]: `{max_seq, last_hash, cursor_offset}`。
        """
        rows = self._session_rows(tenant_id, session_id)
        max_seq = rows[-1]["seq"] if rows else 0
        marker = rows[0] if rows else None
        offset = 0
        if marker is not None and marker["type"] == "session.forked":
            raw = marker["payload"].get("at_seq")
            offset = raw if isinstance(raw, int) else 0
        return {
            "max_seq": max_seq,
            "last_hash": rows[-1]["hash"] if rows else None,
            "cursor_offset": offset if rows else None,
        }

    def _insert(self, tenant_id: str, params: dict[str, Any]) -> dict[str, Any]:
        """写入一行（模拟 `INSERT ... RETURNING created_at`）。

        Args:
            tenant_id: 租户 ID。
            params: 命名参数。

        Returns:
            dict[str, Any]: `{created_at}`。

        Raises:
            IntegrityError: `conflict_once` 置位时抛一次（模拟主键冲突重试）。
        """
        if self.conflict_once:
            self.conflict_once = False
            raise IntegrityError("insert", {}, Exception("duplicate key"))
        session_id = str(params["session_id"])
        rows = self._session_rows(tenant_id, session_id)
        created = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
        rows.append(
            {
                "tenant_id": tenant_id,
                "session_id": session_id,
                "seq": int(params["seq"]),
                "event_id": str(params["event_id"]),
                "type": str(params["event_type"]),
                "payload": _payload_of(params),
                "prev_hash": str(params["prev_hash"]),
                "hash": str(params["hash"]),
                "created_at": created,
            }
        )
        return {"created_at": created}

    def _chain(self, tenant_id: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """构造 `_CHAIN_READ_SQL_TEMPLATE` 的结果行。

        Args:
            tenant_id: 租户 ID。
            params: 命名参数（`sid_i` / `limit_i` / `offset_i` / `after_cursor`）。

        Returns:
            list[dict[str, Any]]: 计算结果行（含 `cursor`）。
        """
        after = int(params.get("after_cursor", 0))
        limit = int(params.get("limit", 500))
        flattened: list[dict[str, Any]] = []
        index = 0
        while f"sid_{index}" in params:
            sid = str(params[f"sid_{index}"])
            seq_limit = params[f"limit_{index}"]
            offset = int(params[f"offset_{index}"])
            for row in self._session_rows(tenant_id, sid):
                if seq_limit is not None and row["seq"] > int(seq_limit):
                    continue
                cursor = offset + int(row["seq"])
                if cursor <= after:
                    continue
                flattened.append({**row, "cursor": cursor})
            index += 1
        flattened.sort(key=lambda item: item["cursor"])
        return flattened[:limit]


def _payload_of(params: dict[str, Any]) -> dict[str, Any]:
    """从 INSERT 参数里还原 payload（本假实现里是 JSON 文本）。

    Args:
        params: 命名参数。

    Returns:
        dict[str, Any]: 载荷。
    """
    import json

    raw = params.get("payload")
    if isinstance(raw, str):
        return json.loads(raw)
    return dict(raw) if isinstance(raw, dict) else {}


@pytest.fixture
def cluster(monkeypatch: pytest.MonkeyPatch) -> _FakeCluster:
    """把 `tenant_scope` 换成假工作单元的工厂。

    Args:
        monkeypatch: pytest 补丁器。

    Returns:
        _FakeCluster: 假集群。
    """
    fake = _FakeCluster()

    def _factory(tenant_id: str, **_kwargs: Any) -> Callable[[], Any]:
        @asynccontextmanager
        async def _scope() -> AsyncIterator[_FakeUoW]:
            yield _FakeUoW(fake, tenant_id)

        return _scope()  # type: ignore[return-value]

    monkeypatch.setattr(
        "app.db.repositories.event_log.postgres.tenant_scope",
        _factory,
    )
    return fake


def _store() -> PostgresEventStore:
    """构造 PG 实现（连的是假集群）。

    Returns:
        PostgresEventStore: 存储实例。
    """
    return PostgresEventStore(Settings(env="test", business_store="postgres"))


class TestAppendControlFlow:
    """取号、hash 链、冲突重试。"""

    async def test_first_append_uses_genesis_prev_hash(self, cluster: _FakeCluster) -> None:
        """空会话的第一条 `prev_hash` 是创世值、`seq=1`。"""
        store = _store()
        event = await store.append(TENANT, "s1", "turn.started", {"n": 1})
        assert (event.seq, event.cursor) == (1, 1)
        assert event.prev_hash == GENESIS_HASH
        assert event.created_at.startswith("2026-11-04T12:00:00")

    async def test_second_append_links_previous_hash(self, cluster: _FakeCluster) -> None:
        """第二条接上第一条的 hash，且 hash 可复算。"""
        store = _store()
        first = await store.append(TENANT, "s1", "turn.started", {"n": 1})
        second = await store.append(TENANT, "s1", "turn.finished", {"n": 2})
        assert second.seq == 2
        assert second.prev_hash == first.hash
        assert second.hash == compute_hash(
            first.hash, second.event_id, "turn.finished", second.payload
        )

    async def test_seq_conflict_is_retried(self, cluster: _FakeCluster) -> None:
        """主键冲突重试一次后成功（重试必须重开事务，假 UoW 每次都是新作用域）。"""
        store = _store()
        cluster.conflict_once = True
        event = await store.append(TENANT, "s1", "turn.started", {"n": 1})
        assert event.seq == 1

    async def test_persistent_conflict_raises(self, cluster: _FakeCluster) -> None:
        """连续冲突超过重试上限报 30003（不无限重试、不静默丢事件）。"""
        store = _store()

        def _always_conflict(tenant_id: str, params: dict[str, Any]) -> dict[str, Any]:
            raise IntegrityError("insert", {}, Exception("duplicate key"))

        cluster._insert = _always_conflict  # noqa: SLF001 - 测试替身：换成"永远冲突"
        with pytest.raises(ApiError) as exc_info:
            await store.append(TENANT, "s1", "turn.started", {"n": 1})
        assert exc_info.value.code == ErrorCode.INTERNAL_ERROR

    async def test_unregistered_type_rejected_before_touching_store(
        self, cluster: _FakeCluster
    ) -> None:
        """未登记类型在写库前被拒绝（不产生半条记录）。"""
        store = _store()
        with pytest.raises(ValueError):
            await store.append(TENANT, "s1", "subagent.spawn", {})
        assert cluster.rows == {}


class TestReadControlFlow:
    """游标换算与父链展开。"""

    async def test_read_root_session(self, cluster: _FakeCluster) -> None:
        """根会话：游标等于 seq。"""
        store = _store()
        for index in range(3):
            await store.append(TENANT, "s1", "turn.started", {"n": index})
        events = await store.read_after(TENANT, "s1")
        assert [item.cursor for item in events] == [1, 2, 3]
        assert [item.payload["n"] for item in events] == [0, 1, 2]

    async def test_read_after_cursor(self, cluster: _FakeCluster) -> None:
        """`cursor > after_seq`：等于游标的条目不重复下发。"""
        store = _store()
        for index in range(3):
            await store.append(TENANT, "s1", "turn.started", {"n": index})
        events = await store.read_after(TENANT, "s1", 2)
        assert [item.cursor for item in events] == [3]

    async def test_non_positive_limit_rejected(self, cluster: _FakeCluster) -> None:
        """limit 非正即 ValueError（与内存实现同语义）。"""
        with pytest.raises(ValueError):
            await _store().read_after(TENANT, "s1", 0, limit=0)

    async def test_forked_session_replays_parent_prefix(self, cluster: _FakeCluster) -> None:
        """fork 会话：父流前缀 + 本会话，游标连续且从 1 开始。"""
        store = _store()
        for index in range(4):
            await store.append(TENANT, "s1", "turn.started", {"n": index})
        child = await store.fork(TENANT, "s1", 3)
        await store.append(TENANT, child, "tool.completed", {"step": {"step_id": "s1"}})

        events = await store.read_after(TENANT, child)
        assert [(item.session_id, item.seq, item.cursor) for item in events] == [
            ("s1", 1, 1),
            ("s1", 2, 2),
            ("s1", 3, 3),
            (child, 1, 4),
            (child, 2, 5),
        ]


class TestForkAndVerifyControlFlow:
    """fork 的前置校验与链校验的失败路径。"""

    async def test_fork_missing_parent(self, cluster: _FakeCluster) -> None:
        """父会话不存在报业务错误。"""
        with pytest.raises(ApiError) as exc_info:
            await _store().fork(TENANT, "s_missing")
        assert exc_info.value.code == ErrorCode.PARAM_INVALID

    async def test_fork_at_seq_out_of_range(self, cluster: _FakeCluster) -> None:
        """切点越界拒绝。"""
        store = _store()
        await store.append(TENANT, "s1", "turn.started", {})
        for bad in (0, 5):
            with pytest.raises(ApiError) as exc_info:
                await store.fork(TENANT, "s1", bad)
            assert exc_info.value.code == ErrorCode.PARAM_INVALID

    async def test_fork_depth_limit(self, cluster: _FakeCluster) -> None:
        """深度超过 8 报业务错误。"""
        store = _store()
        parent = build_session_id(TENANT, "root")
        await store.append(TENANT, parent, "turn.started", {})
        current = parent
        for _ in range(8):
            current = await store.fork(TENANT, current)
        with pytest.raises(ApiError) as exc_info:
            await store.fork(TENANT, current)
        assert exc_info.value.code == ErrorCode.PARAM_INVALID

    async def test_verify_chain_ok_and_missing(self, cluster: _FakeCluster) -> None:
        """链自洽 True、不存在 False。"""
        store = _store()
        await store.append(TENANT, "s1", "turn.started", {"n": 1})
        assert await store.verify_chain(TENANT, "s1") is True
        assert await store.verify_chain(TENANT, "s_missing") is False

    async def test_verify_chain_detects_tampering(self, cluster: _FakeCluster) -> None:
        """篡改 payload 后链校验失败（hash 与内容不再匹配）。"""
        store = _store()
        await store.append(TENANT, "s1", "turn.started", {"n": 1})
        cluster.rows[(TENANT, "s1")][0]["payload"] = {"n": 999}
        assert await store.verify_chain(TENANT, "s1") is False

    async def test_verify_chain_detects_broken_link(self, cluster: _FakeCluster) -> None:
        """`prev_hash` 被改写即失败（链的"连接处"同样有证据）。"""
        store = _store()
        await store.append(TENANT, "s1", "turn.started", {"n": 1})
        cluster.rows[(TENANT, "s1")][0]["prev_hash"] = "1" * 64
        assert await store.verify_chain(TENANT, "s1") is False
