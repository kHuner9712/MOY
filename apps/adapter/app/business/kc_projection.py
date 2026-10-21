# ruff: noqa: S608 - 出箱表的 SET 片段是**常量字符串**（由本模块内部调用方传入），
# 值一律走命名参数绑定；无任何用户输入参与 SQL 拼接。
"""Keycloak 单向投影（阶段二 B08，架构文档 2.6 / D-13）。

**方向不可逆**（D-13 第 3 条）：

- 业务库是权威源，组织树/成员关系变更后**只**向 KC 投影（`kc_group_upsert` /
  `kc_group_delete` / `kc_user_group_sync`）；
- 投影走 `integration_outbox` 异步投递，指数退避 **1s → 8s → 64s，最多 8 次**；
- 用尽后置 `status='failed'` 并告警，**不允许**回滚业务事务
  （业务事实已成立，KC 只是「便于未来 SSO 分组展示」的投影）；
- **禁止反向写入**：本模块没有任何「读 KC group 改业务库」的代码路径。

投递失败绝不抛出到调用方：`enqueue()` 捕获一切异常并返回 False，
业务事务照常提交（这是「投影失败不得回滚业务事务」的落地方式）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Protocol

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.session import tenant_scope

logger = get_logger(__name__)

#: 出箱表名
OUTBOX_TABLE: Final[str] = "integration_outbox"

#: 出箱 ID 前缀
OUTBOX_ID_PREFIX: Final[str] = "obx_"

#: 投影种类（与 DDL CHECK 一致）
KIND_GROUP_UPSERT: Final[str] = "kc_group_upsert"

#: 投影种类：删除分组
KIND_GROUP_DELETE: Final[str] = "kc_group_delete"

#: 投影种类：成员-分组同步
KIND_USER_GROUP_SYNC: Final[str] = "kc_user_group_sync"

#: 全部投影种类
OUTBOX_KINDS: Final[tuple[str, str, str]] = (
    KIND_GROUP_UPSERT,
    KIND_GROUP_DELETE,
    KIND_USER_GROUP_SYNC,
)

#: 退避序列（秒）：1s → 8s → 64s（之后按 64s 递增）
BACKOFF_SECONDS: Final[tuple[int, ...]] = (1, 8, 64)

#: 最大尝试次数（超过即 failed）
MAX_ATTEMPTS: Final[int] = 8

#: 出箱状态
STATUS_PENDING: Final[str] = "pending"

#: 出箱状态：已投递
STATUS_SENT: Final[str] = "sent"

#: 出箱状态：投递失败
STATUS_FAILED: Final[str] = "failed"


def _now() -> datetime:
    """当前 UTC 时间。

    Returns:
        datetime: 带时区的当前时间。
    """
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class OutboxEntry:
    """一条投影出箱记录（`integration_outbox` 行）。

    Attributes:
        tenant_id: 租户 ID。
        outbox_id: 出箱 ID。
        kind: 投影种类。
        payload: 投影载荷（业务字段，不含密钥）。
        status: `pending` / `sent` / `failed`。
        attempts: 已尝试次数。
        next_attempt_at: 下次可投递时刻（ISO 8601）。
        last_error: 最后一次失败原因（内部诊断，不展示给用户）。
        created_at: 创建时间。
    """

    tenant_id: str
    outbox_id: str
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    status: str = STATUS_PENDING
    attempts: int = 0
    next_attempt_at: str = ""
    last_error: str = ""
    created_at: str = ""


class OutboxStore(Protocol):
    """投影出箱存储契约。"""

    async def enqueue(self, entry: OutboxEntry) -> OutboxEntry:
        """写入一条待投递记录。"""
        ...

    async def due(self, tenant_id: str, *, limit: int = 50) -> list[OutboxEntry]:
        """取出到期的待投递记录。"""
        ...

    async def mark_sent(self, tenant_id: str, outbox_id: str) -> None:
        """标记投递成功。"""
        ...

    async def mark_retry(
        self, tenant_id: str, outbox_id: str, *, attempts: int, next_attempt_at: str, error: str
    ) -> None:
        """标记投递失败并安排重试。"""
        ...

    async def mark_failed(self, tenant_id: str, outbox_id: str, *, error: str) -> None:
        """标记投递失败（重试用尽）。"""
        ...


class InMemoryOutboxStore:
    """进程内出箱存储（development/test）。"""

    def __init__(self) -> None:
        """初始化。"""
        self._entries: dict[tuple[str, str], OutboxEntry] = {}
        self._lock = asyncio.Lock()

    async def enqueue(self, entry: OutboxEntry) -> OutboxEntry:
        """写入一条待投递记录。

        Args:
            entry: 出箱记录。

        Returns:
            OutboxEntry: 落库后的记录。
        """
        payload = entry if entry.next_attempt_at else _with_defaults(entry)
        async with self._lock:
            self._entries[(entry.tenant_id, entry.outbox_id)] = payload
        return payload

    async def due(self, tenant_id: str, *, limit: int = 50) -> list[OutboxEntry]:
        """取出到期的待投递记录。

        Args:
            tenant_id: 租户 ID。
            limit: 返回条数上限。

        Returns:
            list[OutboxEntry]: 到期记录（按 next_attempt_at 升序）。
        """
        now = _now().isoformat()
        async with self._lock:
            items = [
                entry
                for (tid, _), entry in self._entries.items()
                if tid == tenant_id
                and entry.status == STATUS_PENDING
                and (not entry.next_attempt_at or entry.next_attempt_at <= now)
            ]
        items.sort(key=lambda item: item.next_attempt_at)
        return items[:limit]

    async def mark_sent(self, tenant_id: str, outbox_id: str) -> None:
        """标记投递成功。

        Args:
            tenant_id: 租户 ID。
            outbox_id: 出箱 ID。
        """
        async with self._lock:
            key = (tenant_id, outbox_id)
            entry = self._entries.get(key)
            if entry is not None:
                self._entries[key] = _replace_entry(entry, status=STATUS_SENT, last_error="")

    async def mark_retry(
        self, tenant_id: str, outbox_id: str, *, attempts: int, next_attempt_at: str, error: str
    ) -> None:
        """标记投递失败并安排重试。

        Args:
            tenant_id: 租户 ID。
            outbox_id: 出箱 ID。
            attempts: 已尝试次数。
            next_attempt_at: 下次投递时刻。
            error: 失败原因（内部诊断）。
        """
        async with self._lock:
            key = (tenant_id, outbox_id)
            entry = self._entries.get(key)
            if entry is not None:
                self._entries[key] = _replace_entry(
                    entry,
                    attempts=attempts,
                    next_attempt_at=next_attempt_at,
                    last_error=error,
                )

    async def mark_failed(self, tenant_id: str, outbox_id: str, *, error: str) -> None:
        """标记投递失败（重试用尽）。

        Args:
            tenant_id: 租户 ID。
            outbox_id: 出箱 ID。
            error: 失败原因。
        """
        async with self._lock:
            key = (tenant_id, outbox_id)
            entry = self._entries.get(key)
            if entry is not None:
                self._entries[key] = _replace_entry(entry, status=STATUS_FAILED, last_error=error)

    async def all_entries(self, tenant_id: str) -> list[OutboxEntry]:
        """读取该租户全部出箱记录（测试断言用）。

        Args:
            tenant_id: 租户 ID。

        Returns:
            list[OutboxEntry]: 记录列表。
        """
        async with self._lock:
            return [entry for (tid, _), entry in self._entries.items() if tid == tenant_id]

    def clear(self) -> None:
        """清空（测试隔离用）。"""
        self._entries.clear()


def _replace_entry(entry: OutboxEntry, **changes: Any) -> OutboxEntry:
    """替换出箱记录字段（frozen dataclass 的标准做法）。

    Args:
        entry: 原记录。
        **changes: 变更字段。

    Returns:
        OutboxEntry: 新记录。
    """
    return replace(entry, **changes)


def _with_defaults(entry: OutboxEntry) -> OutboxEntry:
    """补齐时间字段。

    Args:
        entry: 原记录。

    Returns:
        OutboxEntry: 补齐后的记录。
    """
    now = _now().isoformat()
    return _replace_entry(
        entry,
        next_attempt_at=entry.next_attempt_at or now,
        created_at=entry.created_at or now,
    )


class PostgresOutboxStore:
    """PostgreSQL 出箱存储（`idx_outbox_due` 索引扫描）。"""

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化。

        Args:
            settings: 可选配置实例。
        """
        self._settings = settings or get_settings()

    async def enqueue(self, entry: OutboxEntry) -> OutboxEntry:
        """写入一条待投递记录。

        Args:
            entry: 出箱记录。

        Returns:
            OutboxEntry: 落库后的记录。
        """
        import json

        async with tenant_scope(entry.tenant_id, settings=self._settings) as uow:
            await uow.execute(
                f"""
                INSERT INTO {OUTBOX_TABLE}
                    (tenant_id, outbox_id, kind, payload, status, attempts, next_attempt_at)
                VALUES (
                    :tenant_id, :outbox_id, :kind, CAST(:payload AS jsonb),
                    'pending', 0, now()
                )
                ON CONFLICT (tenant_id, outbox_id) DO NOTHING
                """,
                {
                    "tenant_id": entry.tenant_id,
                    "outbox_id": entry.outbox_id,
                    "kind": entry.kind,
                    "payload": json.dumps(entry.payload, ensure_ascii=False, sort_keys=True),
                },
            )
        return _with_defaults(entry)

    async def due(self, tenant_id: str, *, limit: int = 50) -> list[OutboxEntry]:
        """取出到期的待投递记录。

        Args:
            tenant_id: 租户 ID。
            limit: 返回条数上限。

        Returns:
            list[OutboxEntry]: 到期记录。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            rows = await uow.fetch_all(
                f"""
                SELECT tenant_id, outbox_id, kind, payload, status, attempts,
                       next_attempt_at, last_error, created_at
                FROM {OUTBOX_TABLE}
                WHERE tenant_id = :tenant_id AND status = 'pending'
                  AND next_attempt_at <= now()
                ORDER BY next_attempt_at
                LIMIT :limit
                """,
                {"tenant_id": tenant_id, "limit": limit},
            )
        return [_row_to_entry(row) for row in rows]

    async def mark_sent(self, tenant_id: str, outbox_id: str) -> None:
        """标记投递成功。

        Args:
            tenant_id: 租户 ID。
            outbox_id: 出箱 ID。
        """
        await self._update(
            tenant_id,
            outbox_id,
            "SET status = 'sent', last_error = NULL, updated_at = now()",
            {},
        )

    async def mark_retry(
        self, tenant_id: str, outbox_id: str, *, attempts: int, next_attempt_at: str, error: str
    ) -> None:
        """标记投递失败并安排重试。

        Args:
            tenant_id: 租户 ID。
            outbox_id: 出箱 ID。
            attempts: 已尝试次数。
            next_attempt_at: 下次投递时刻。
            error: 失败原因。
        """
        await self._update(
            tenant_id,
            outbox_id,
            "SET attempts = :attempts, "
            "next_attempt_at = CAST(CAST(:next_attempt_at AS text) AS timestamptz), "
            "last_error = :error, updated_at = now()",
            {"attempts": attempts, "next_attempt_at": next_attempt_at, "error": error},
        )

    async def mark_failed(self, tenant_id: str, outbox_id: str, *, error: str) -> None:
        """标记投递失败（重试用尽）。

        Args:
            tenant_id: 租户 ID。
            outbox_id: 出箱 ID。
            error: 失败原因。
        """
        await self._update(
            tenant_id,
            outbox_id,
            "SET status = 'failed', last_error = :error, updated_at = now()",
            {"error": error},
        )

    async def _update(
        self, tenant_id: str, outbox_id: str, assignments: str, parameters: dict[str, Any]
    ) -> None:
        """执行出箱状态更新。

        Args:
            tenant_id: 租户 ID。
            outbox_id: 出箱 ID。
            assignments: SET 片段（常量字符串）。
            parameters: 命名参数。
        """
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            await uow.execute(
                f"""
                UPDATE {OUTBOX_TABLE} {assignments}
                WHERE tenant_id = :tenant_id AND outbox_id = :outbox_id
                """,
                {"tenant_id": tenant_id, "outbox_id": outbox_id, **parameters},
            )


def _row_to_entry(row: Mapping[str, Any]) -> OutboxEntry:
    """把数据库行映射为出箱记录。

    Args:
        row: 行映射。

    Returns:
        OutboxEntry: 出箱记录。
    """
    payload = row.get("payload")
    return OutboxEntry(
        tenant_id=str(row.get("tenant_id", "")),
        outbox_id=str(row.get("outbox_id", "")),
        kind=str(row.get("kind", "")),
        payload=dict(payload) if isinstance(payload, dict) else {},
        status=str(row.get("status", STATUS_PENDING)),
        attempts=int(row.get("attempts", 0) or 0),
        next_attempt_at=_to_iso(row.get("next_attempt_at")),
        last_error=str(row.get("last_error", "") or ""),
        created_at=_to_iso(row.get("created_at")),
    )


def _to_iso(value: Any) -> str:
    """把时间值规范化为 ISO 8601 字符串。

    Args:
        value: 时间值。

    Returns:
        str: ISO 8601 字符串；空值返回空串。
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)


# ===========================================================================
# 进程级单例（出箱存储）
# ===========================================================================
_memory_store: InMemoryOutboxStore | None = None


def get_outbox_store(settings: Settings | None = None) -> OutboxStore:
    """获取投影出箱存储。

    Args:
        settings: 可选配置实例。

    Returns:
        OutboxStore: 存储实现。
    """
    global _memory_store

    conf = settings or get_settings()
    if conf.uses_postgres:
        return PostgresOutboxStore(conf)
    if _memory_store is None:
        _memory_store = InMemoryOutboxStore()
    return _memory_store


def reset_outbox_store() -> None:
    """重置内存后端单例（测试隔离用）。"""
    global _memory_store
    if _memory_store is not None:
        _memory_store.clear()
    _memory_store = None


class KeycloakProjector(Protocol):
    """投影执行器契约（测试可注入桩实现）。"""

    async def upsert_group(self, *, group_id: str, name: str, parent_id: str) -> None:
        """在身份底座创建或更新分组投影。"""
        ...

    async def delete_group(self, group_id: str) -> None:
        """在身份底座删除分组投影。"""
        ...

    async def sync_user_groups(self, *, user_id: str, group_ids: list[str]) -> None:
        """把成员的部门关系投影为分组归属。"""
        ...


class NoopProjector:
    """空投影器（`OA_DOWNSTREAM_ENABLED=false` 或私有化单租户时的默认实现）。"""

    async def upsert_group(self, *, group_id: str, name: str, parent_id: str) -> None:
        """空实现（不产生任何下游请求）。

        Args:
            group_id: 分组 ID。
            name: 分组名称。
            parent_id: 上级分组 ID。
        """
        logger.debug("kc_projection_skipped", kind=KIND_GROUP_UPSERT, group_id=group_id)
        del name, parent_id

    async def delete_group(self, group_id: str) -> None:
        """空实现。

        Args:
            group_id: 分组 ID。
        """
        logger.debug("kc_projection_skipped", kind=KIND_GROUP_DELETE, group_id=group_id)

    async def sync_user_groups(self, *, user_id: str, group_ids: list[str]) -> None:
        """空实现。

        Args:
            user_id: 用户 ID。
            group_ids: 分组 ID 列表。
        """
        logger.debug(
            "kc_projection_skipped",
            kind=KIND_USER_GROUP_SYNC,
            user_id=user_id,
            groups=len(group_ids),
        )


class KcProjectionService:
    """单向投影服务（业务库 → Keycloak）。

    Attributes:
        _settings: 配置实例。
        _store: 出箱存储。
        _projector: 投影执行器。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        store: OutboxStore | None = None,
        projector: KeycloakProjector | None = None,
    ) -> None:
        """初始化服务。

        Args:
            settings: 可选配置实例。
            store: 可选出箱存储（测试注入）。
            projector: 可选投影执行器（测试注入）。
        """
        self._settings = settings or get_settings()
        self._store: OutboxStore = store or get_outbox_store(self._settings)
        self._projector: KeycloakProjector = projector or NoopProjector()

    @property
    def store(self) -> OutboxStore:
        """当前出箱存储（测试可据此断言）。"""
        return self._store

    async def enqueue_group_upsert(
        self, *, tenant_id: str, group_id: str, name: str, parent_id: str = ""
    ) -> bool:
        """登记一次分组投影（失败不影响业务事务）。

        Args:
            tenant_id: 租户 ID。
            group_id: 部门 ID（作为分组 ID）。
            name: 部门名称。
            parent_id: 上级部门 ID。

        Returns:
            bool: 登记成功返回 True；登记失败只记 WARN 并返回 False。
        """
        return await self._enqueue(
            tenant_id=tenant_id,
            kind=KIND_GROUP_UPSERT,
            payload={"group_id": group_id, "name": name, "parent_id": parent_id},
        )

    async def enqueue_group_delete(self, *, tenant_id: str, group_id: str) -> bool:
        """登记一次分组删除投影。

        Args:
            tenant_id: 租户 ID。
            group_id: 部门 ID。

        Returns:
            bool: 登记成功返回 True。
        """
        return await self._enqueue(
            tenant_id=tenant_id, kind=KIND_GROUP_DELETE, payload={"group_id": group_id}
        )

    async def enqueue_user_groups(
        self, *, tenant_id: str, user_id: str, group_ids: list[str]
    ) -> bool:
        """登记一次成员分组同步投影。

        Args:
            tenant_id: 租户 ID。
            user_id: 用户 ID。
            group_ids: 部门 ID 列表。

        Returns:
            bool: 登记成功返回 True。
        """
        return await self._enqueue(
            tenant_id=tenant_id,
            kind=KIND_USER_GROUP_SYNC,
            payload={"user_id": user_id, "group_ids": list(group_ids)},
        )

    async def _enqueue(self, *, tenant_id: str, kind: str, payload: dict[str, Any]) -> bool:
        """写入出箱（异常一律降级为 WARN，不回滚业务事务）。

        Args:
            tenant_id: 租户 ID。
            kind: 投影种类。
            payload: 投影载荷。

        Returns:
            bool: 成功返回 True。
        """
        from app.core.ids import new_resource_id

        try:
            await self._store.enqueue(
                OutboxEntry(
                    tenant_id=tenant_id,
                    outbox_id=new_resource_id(OUTBOX_ID_PREFIX),
                    kind=kind,
                    payload=payload,
                )
            )
        except Exception as exc:  # 投影登记失败不得回滚业务事务（D-13 第 3 条）
            logger.warning(
                "kc_projection_enqueue_failed",
                kind=kind,
                tenant_id=tenant_id,
                detail=type(exc).__name__,
            )
            return False
        return True

    async def run_once(self, tenant_id: str, *, limit: int = 50) -> int:
        """投递一批到期投影（指数退避重试）。

        Args:
            tenant_id: 租户 ID。
            limit: 单批条数上限。

        Returns:
            int: 成功投递条数。
        """
        delivered = 0
        for entry in await self._store.due(tenant_id, limit=limit):
            try:
                await self._deliver(entry)
            except Exception as exc:  # 投递失败：退避重试，绝不回滚业务
                attempts = entry.attempts + 1
                if attempts >= MAX_ATTEMPTS:
                    await self._store.mark_failed(
                        tenant_id, entry.outbox_id, error=type(exc).__name__
                    )
                    logger.error(
                        "kc_projection_failed",
                        outbox_id=entry.outbox_id,
                        kind=entry.kind,
                        attempts=attempts,
                    )
                    continue
                await self._store.mark_retry(
                    tenant_id,
                    entry.outbox_id,
                    attempts=attempts,
                    next_attempt_at=next_attempt_at(attempts),
                    error=type(exc).__name__,
                )
                logger.warning(
                    "kc_projection_retry",
                    outbox_id=entry.outbox_id,
                    attempts=attempts,
                    kind=entry.kind,
                )
                continue
            await self._store.mark_sent(tenant_id, entry.outbox_id)
            delivered += 1
        return delivered

    async def _deliver(self, entry: OutboxEntry) -> None:
        """执行一条投影。

        Args:
            entry: 出箱记录。
        """
        if entry.kind == KIND_GROUP_UPSERT:
            await self._projector.upsert_group(
                group_id=str(entry.payload.get("group_id", "")),
                name=str(entry.payload.get("name", "")),
                parent_id=str(entry.payload.get("parent_id", "")),
            )
            return
        if entry.kind == KIND_GROUP_DELETE:
            await self._projector.delete_group(str(entry.payload.get("group_id", "")))
            return
        await self._projector.sync_user_groups(
            user_id=str(entry.payload.get("user_id", "")),
            group_ids=[str(item) for item in entry.payload.get("group_ids", [])],
        )


def next_attempt_at(attempts: int, *, now: datetime | None = None) -> str:
    """计算下次投递时刻（1s → 8s → 64s，之后固定 64s）。

    Args:
        attempts: 已尝试次数（从 1 开始）。
        now: 基准时间（缺省取当前 UTC）。

    Returns:
        str: ISO 8601 时刻。
    """
    index = min(max(attempts, 1) - 1, len(BACKOFF_SECONDS) - 1)
    base = now or _now()
    return (base + timedelta(seconds=BACKOFF_SECONDS[index])).isoformat()


__all__: list[str] = [
    "BACKOFF_SECONDS",
    "KIND_GROUP_DELETE",
    "KIND_GROUP_UPSERT",
    "KIND_USER_GROUP_SYNC",
    "MAX_ATTEMPTS",
    "OUTBOX_KINDS",
    "OUTBOX_TABLE",
    "InMemoryOutboxStore",
    "KcProjectionService",
    "KeycloakProjector",
    "NoopProjector",
    "OutboxEntry",
    "OutboxStore",
    "PostgresOutboxStore",
    "get_outbox_store",
    "next_attempt_at",
    "reset_outbox_store",
]
