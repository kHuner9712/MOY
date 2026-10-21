# ruff: noqa: S608 - 本模块所有 SQL 的表名/列名均为常量（经 assert_identifier 校验），
# 值一律走命名参数绑定。
"""工具连接与调用记录仓储（阶段二 B06，架构文档 3.4 / PRD P2-TL-04）。

两张表各司其职：

| 表 | 用途 | 关键约束 |
| --- | --- | --- |
| `tool_connection` | 连接状态权威源 | `uq_conn_active`：同工具同使用者仅一条有效连接 |
| `tool_execution` | 调用记录（TL1 唯一数据源） | 失败项只存业务语言原因，禁止存堆栈 |

连接解析顺序遵循 PRD P2-TL-04 第 5 条：**个人连接优先于企业共用连接**，
断开个人连接不影响企业共用连接。

双后端（`OA_BUSINESS_STORE`）：`memory`（进程内）/ `postgres`（短事务 + 租户条件）。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Final, Protocol

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.repositories.base import BaseRepository
from app.db.session import tenant_scope

logger = get_logger(__name__)

#: 工具连接表名
TOOL_CONNECTION_TABLE: Final[str] = "tool_connection"

#: 工具调用记录表名
TOOL_EXECUTION_TABLE: Final[str] = "tool_execution"

#: 连接状态：已连接
CONNECTION_CONNECTED: Final[str] = "connected"

#: 连接状态：授权已过期
CONNECTION_EXPIRED: Final[str] = "expired"

#: 连接状态：已断开
CONNECTION_REVOKED: Final[str] = "revoked"

#: 执行状态：成功
EXECUTION_SUCCESS: Final[str] = "success"

#: 执行状态：失败
EXECUTION_FAILED: Final[str] = "failed"

#: 个人连接
SCOPE_PERSONAL: Final[str] = "personal"

#: 企业共用连接
SCOPE_ORGANIZATION: Final[str] = "organization"

#: 调用记录摘要的字符串截断长度（避免把大报文写进库）
SUMMARY_TEXT_LIMIT: Final[int] = 200


def _now_iso() -> str:
    """当前 UTC 时间（ISO 8601）。

    Returns:
        str: 形如 `2026-11-02T09:00:00Z`。
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class ToolConnectionRecord:
    """一条工具连接。

    Attributes:
        tenant_id: 租户 ID。
        conn_id: 连接 ID（`conn_` 前缀）。
        tool_type: 工具类型标识。
        scope: `personal`（个人）/ `organization`（企业共用）。
        owner_user_id: 个人连接的使用者；企业共用连接为空。
        org_id: 企业共用连接所属组织；个人连接为空。
        status: `connected` / `expired` / `revoked`。
        expires_at: 授权过期时间（ISO 8601）。
        created_at: 创建时间。
        updated_at: 最近更新时间。
    """

    tenant_id: str
    conn_id: str
    tool_type: str
    scope: str = SCOPE_PERSONAL
    owner_user_id: str = ""
    org_id: str = ""
    status: str = CONNECTION_CONNECTED
    expires_at: str | None = None
    created_at: str = ""
    updated_at: str = ""

    @property
    def is_active(self) -> bool:
        """连接是否仍可用。"""
        return self.status == CONNECTION_CONNECTED


@dataclass(frozen=True, slots=True)
class ToolExecutionRecord:
    """一条工具调用记录。

    Attributes:
        tenant_id: 租户 ID。
        execution_id: 调用记录 ID（`exec_` 前缀）。
        tool_type: 工具类型标识。
        operation: 操作标识。
        status: `success` / `failed`。
        conn_id: 使用的连接 ID。
        error_code: 失败时的自研错误码（仅供日志与诊断，**不对外返回**）。
        error_message: 失败原因（业务语言，可对外展示）。
        result_summary: 结果摘要（业务字段子集，禁止写入凭据与大报文）。
        operator_user_id: 操作人用户 ID。
        duration_ms: 耗时（毫秒）。
        executed_at: 调用时间（ISO 8601）。
    """

    tenant_id: str
    execution_id: str
    tool_type: str
    operation: str
    status: str = EXECUTION_SUCCESS
    conn_id: str = ""
    error_code: int | None = None
    error_message: str | None = None
    result_summary: dict[str, Any] | None = None
    operator_user_id: str = ""
    duration_ms: int = 0
    executed_at: str = ""


def summarize_result(result: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """把执行结果压缩成可入库的业务摘要。

    只保留标量与小数组，字符串截断到 200 字：既能让运营在调用记录里看懂结果，
    又不会把凭据、正文或大报文写进库（架构文档 9.3）。

    Args:
        result: 连接器返回的业务结果。

    Returns:
        dict[str, Any] | None: 摘要；入参为空返回 None。
    """
    if not result:
        return None
    summary: dict[str, Any] = {}
    for key, value in result.items():
        if isinstance(value, bool | int | float):
            summary[str(key)] = value
        elif isinstance(value, str):
            summary[str(key)] = value[:SUMMARY_TEXT_LIMIT]
        elif isinstance(value, list):
            summary[str(key)] = len(value)
        elif isinstance(value, dict):
            summary[str(key)] = len(value)
    return summary


class ToolStore(Protocol):
    """工具连接与调用记录存储契约（内存 / PostgreSQL 双实现）。"""

    async def upsert_connection(self, record: ToolConnectionRecord) -> ToolConnectionRecord:
        """写入（或刷新）一条连接，同工具同使用者的旧有效连接转 revoked。"""
        ...

    async def get_connection(self, tenant_id: str, conn_id: str) -> ToolConnectionRecord | None:
        """按 ID 读取连接（含已过期 / 已断开）。"""
        ...

    async def list_connections(
        self, tenant_id: str, *, tool_type: str = "", limit: int = 20, offset: int = 0
    ) -> tuple[list[ToolConnectionRecord], int]:
        """分页读取有效连接（个人优先，创建时间倒序）。"""
        ...

    async def find_active_connection(
        self, tenant_id: str, tool_type: str, *, user_id: str = ""
    ) -> ToolConnectionRecord | None:
        """解析某工具当前可用连接（个人连接优先）。"""
        ...

    async def revoke_connection(self, tenant_id: str, conn_id: str) -> ToolConnectionRecord | None:
        """把连接置为 revoked；不存在返回 None。"""
        ...

    async def record_execution(self, record: ToolExecutionRecord) -> ToolExecutionRecord:
        """写入一条调用记录。"""
        ...

    async def list_executions(
        self, tenant_id: str, *, tool_type: str = "", limit: int = 20, offset: int = 0
    ) -> tuple[list[ToolExecutionRecord], int]:
        """分页读取调用记录（时间倒序）。"""
        ...


class InMemoryToolStore:
    """进程内工具存储（development/test）。"""

    def __init__(self) -> None:
        """初始化空存储。"""
        self._connections: dict[tuple[str, str], ToolConnectionRecord] = {}
        self._executions: dict[tuple[str, str], ToolExecutionRecord] = {}
        self._lock = asyncio.Lock()

    async def upsert_connection(self, record: ToolConnectionRecord) -> ToolConnectionRecord:
        """写入（或刷新）一条连接。

        Args:
            record: 连接记录。

        Returns:
            ToolConnectionRecord: 落库后的记录。
        """
        stamp = _now_iso()
        async with self._lock:
            for key, existing in list(self._connections.items()):
                if (
                    existing.tenant_id == record.tenant_id
                    and existing.tool_type == record.tool_type
                    and existing.conn_id != record.conn_id
                    and existing.is_active
                    and existing.scope == record.scope
                    and existing.owner_user_id == record.owner_user_id
                    and existing.org_id == record.org_id
                ):
                    self._connections[key] = replace(
                        existing, status=CONNECTION_REVOKED, updated_at=stamp
                    )
            current = self._connections.get((record.tenant_id, record.conn_id))
            payload = replace(
                record,
                created_at=current.created_at
                if current is not None
                else (record.created_at or stamp),
                updated_at=stamp,
                status=CONNECTION_CONNECTED,
            )
            self._connections[(record.tenant_id, record.conn_id)] = payload
        return payload

    async def get_connection(self, tenant_id: str, conn_id: str) -> ToolConnectionRecord | None:
        """按 ID 读取连接。

        Args:
            tenant_id: 租户 ID。
            conn_id: 连接 ID。

        Returns:
            ToolConnectionRecord | None: 连接；不存在返回 None。
        """
        async with self._lock:
            return self._connections.get((tenant_id, conn_id))

    async def list_connections(
        self, tenant_id: str, *, tool_type: str = "", limit: int = 20, offset: int = 0
    ) -> tuple[list[ToolConnectionRecord], int]:
        """分页读取有效连接。

        Args:
            tenant_id: 租户 ID。
            tool_type: 工具类型过滤（空表示不过滤）。
            limit: 每页条数。
            offset: 偏移量。

        Returns:
            tuple[list[ToolConnectionRecord], int]: 当前页与总数。
        """
        async with self._lock:
            rows = [
                record
                for record in self._connections.values()
                if record.tenant_id == tenant_id
                and record.is_active
                and (not tool_type or record.tool_type == tool_type)
            ]
        rows.sort(key=lambda item: (item.created_at, item.conn_id), reverse=True)
        return rows[offset : offset + limit], len(rows)

    async def find_active_connection(
        self, tenant_id: str, tool_type: str, *, user_id: str = ""
    ) -> ToolConnectionRecord | None:
        """解析某工具当前可用连接（个人连接优先）。

        Args:
            tenant_id: 租户 ID。
            tool_type: 工具类型标识。
            user_id: 当前用户 ID。

        Returns:
            ToolConnectionRecord | None: 可用连接；无则返回 None。
        """
        async with self._lock:
            candidates = [
                record
                for record in self._connections.values()
                if record.tenant_id == tenant_id
                and record.tool_type == tool_type
                and record.is_active
                and (
                    record.scope == SCOPE_ORGANIZATION
                    or not user_id
                    or record.owner_user_id == user_id
                )
            ]
        if not candidates:
            return None
        candidates.sort(
            key=lambda item: (0 if item.scope == SCOPE_PERSONAL else 1, item.created_at)
        )
        return candidates[0]

    async def revoke_connection(self, tenant_id: str, conn_id: str) -> ToolConnectionRecord | None:
        """把连接置为 revoked。

        Args:
            tenant_id: 租户 ID。
            conn_id: 连接 ID。

        Returns:
            ToolConnectionRecord | None: 更新后的连接；不存在返回 None。
        """
        async with self._lock:
            key = (tenant_id, conn_id)
            current = self._connections.get(key)
            if current is None:
                return None
            revoked = replace(current, status=CONNECTION_REVOKED, updated_at=_now_iso())
            self._connections[key] = revoked
        return revoked

    async def record_execution(self, record: ToolExecutionRecord) -> ToolExecutionRecord:
        """写入一条调用记录。

        Args:
            record: 调用记录。

        Returns:
            ToolExecutionRecord: 补齐时间后的记录。
        """
        payload = record if record.executed_at else replace(record, executed_at=_now_iso())
        async with self._lock:
            self._executions[(record.tenant_id, record.execution_id)] = payload
        return payload

    async def list_executions(
        self, tenant_id: str, *, tool_type: str = "", limit: int = 20, offset: int = 0
    ) -> tuple[list[ToolExecutionRecord], int]:
        """分页读取调用记录（时间倒序）。

        Args:
            tenant_id: 租户 ID。
            tool_type: 工具类型过滤（空表示不过滤）。
            limit: 每页条数。
            offset: 偏移量。

        Returns:
            tuple[list[ToolExecutionRecord], int]: 当前页与总数。
        """
        async with self._lock:
            rows = [
                record
                for record in self._executions.values()
                if record.tenant_id == tenant_id
                and (not tool_type or record.tool_type == tool_type)
            ]
        rows.sort(key=lambda item: (item.executed_at, item.execution_id), reverse=True)
        return rows[offset : offset + limit], len(rows)

    def clear(self) -> None:
        """清空（测试隔离用）。"""
        self._connections.clear()
        self._executions.clear()


class _ToolConnectionRepository(BaseRepository):
    """`tool_connection` 表仓储（强制租户条件）。"""

    table = TOOL_CONNECTION_TABLE


class _ToolExecutionRepository(BaseRepository):
    """`tool_execution` 表仓储（强制租户条件）。"""

    table = TOOL_EXECUTION_TABLE


class PostgresToolStore:
    """PostgreSQL 工具存储（一次调用一个短事务）。"""

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化。

        Args:
            settings: 可选配置实例。
        """
        self._settings = settings or get_settings()

    async def upsert_connection(self, record: ToolConnectionRecord) -> ToolConnectionRecord:
        """写入（或刷新）一条连接。

        `uq_conn_active` 是部分唯一索引（`status='connected'`），因此必须先让
        同工具同使用者的旧有效连接转 revoked，再插入新行——顺序反了会撞唯一键。

        Args:
            record: 连接记录。

        Returns:
            ToolConnectionRecord: 落库后的记录。
        """
        async with tenant_scope(record.tenant_id, settings=self._settings) as uow:
            repo = _ToolConnectionRepository(uow)
            await repo.execute(
                f"""
                UPDATE {TOOL_CONNECTION_TABLE}
                SET status = 'revoked', updated_at = now()
                WHERE {repo.tenant_clause()} AND tool_type = :tool_type
                  AND status = 'connected' AND conn_id <> :conn_id
                  AND scope = :scope
                  AND owner_user_id IS NOT DISTINCT FROM NULLIF(:owner_user_id, '')
                  AND org_id IS NOT DISTINCT FROM NULLIF(:org_id, '')
                """,
                repo.params(
                    tool_type=record.tool_type,
                    conn_id=record.conn_id,
                    scope=record.scope,
                    owner_user_id=record.owner_user_id,
                    org_id=record.org_id,
                ),
            )
            row = await repo.fetch_one(
                f"""
                INSERT INTO {TOOL_CONNECTION_TABLE} (
                    tenant_id, conn_id, tool_type, scope, owner_user_id, org_id,
                    status, expires_at
                ) VALUES (
                    :tenant_id, :conn_id, :tool_type, :scope,
                    NULLIF(:owner_user_id, ''), NULLIF(:org_id, ''), 'connected',
                    CAST(NULLIF(:expires_at, '') AS timestamptz)
                )
                ON CONFLICT (tenant_id, conn_id) DO UPDATE SET
                    status = 'connected',
                    expires_at = EXCLUDED.expires_at,
                    updated_at = now()
                RETURNING tenant_id, conn_id, tool_type, scope, owner_user_id, org_id,
                          status, expires_at, created_at, updated_at
                """,
                repo.params(
                    conn_id=record.conn_id,
                    tool_type=record.tool_type,
                    scope=record.scope,
                    owner_user_id=record.owner_user_id,
                    org_id=record.org_id,
                    expires_at=record.expires_at or "",
                ),
            )
        return _row_to_connection(row) if row is not None else record

    async def get_connection(self, tenant_id: str, conn_id: str) -> ToolConnectionRecord | None:
        """按 ID 读取连接。

        Args:
            tenant_id: 租户 ID。
            conn_id: 连接 ID。

        Returns:
            ToolConnectionRecord | None: 连接；不存在返回 None。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _ToolConnectionRepository(uow)
            row = await repo.fetch_one(
                f"""
                SELECT tenant_id, conn_id, tool_type, scope, owner_user_id, org_id,
                       status, expires_at, created_at, updated_at
                FROM {TOOL_CONNECTION_TABLE}
                WHERE {repo.tenant_clause()} AND conn_id = :conn_id
                """,
                repo.params(conn_id=conn_id),
            )
        return _row_to_connection(row) if row is not None else None

    async def list_connections(
        self, tenant_id: str, *, tool_type: str = "", limit: int = 20, offset: int = 0
    ) -> tuple[list[ToolConnectionRecord], int]:
        """分页读取有效连接。

        Args:
            tenant_id: 租户 ID。
            tool_type: 工具类型过滤。
            limit: 每页条数。
            offset: 偏移量。

        Returns:
            tuple[list[ToolConnectionRecord], int]: 当前页与总数。
        """
        type_filter = "AND tool_type = :tool_type" if tool_type else ""
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _ToolConnectionRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT tenant_id, conn_id, tool_type, scope, owner_user_id, org_id,
                       status, expires_at, created_at, updated_at
                FROM {TOOL_CONNECTION_TABLE}
                WHERE {repo.tenant_clause()} AND status = 'connected' {type_filter}
                ORDER BY created_at DESC, conn_id DESC
                LIMIT :limit OFFSET :offset
                """,
                repo.params(tool_type=tool_type, limit=limit, offset=offset),
            )
            total = await repo.fetch_scalar(
                f"""
                SELECT COUNT(*) FROM {TOOL_CONNECTION_TABLE}
                WHERE {repo.tenant_clause()} AND status = 'connected' {type_filter}
                """,
                repo.params(tool_type=tool_type),
            )
        return [_row_to_connection(row) for row in rows], int(total or 0)

    async def find_active_connection(
        self, tenant_id: str, tool_type: str, *, user_id: str = ""
    ) -> ToolConnectionRecord | None:
        """解析某工具当前可用连接（个人连接优先）。

        Args:
            tenant_id: 租户 ID。
            tool_type: 工具类型标识。
            user_id: 当前用户 ID。

        Returns:
            ToolConnectionRecord | None: 可用连接；无则返回 None。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _ToolConnectionRepository(uow)
            row = await repo.fetch_one(
                f"""
                SELECT tenant_id, conn_id, tool_type, scope, owner_user_id, org_id,
                       status, expires_at, created_at, updated_at
                FROM {TOOL_CONNECTION_TABLE}
                WHERE {repo.tenant_clause()} AND tool_type = :tool_type
                  AND status = 'connected'
                  AND (scope = 'organization' OR owner_user_id = NULLIF(:user_id, ''))
                ORDER BY CASE WHEN scope = 'personal' THEN 0 ELSE 1 END,
                         created_at DESC, conn_id DESC
                LIMIT 1
                """,
                repo.params(tool_type=tool_type, user_id=user_id),
            )
        return _row_to_connection(row) if row is not None else None

    async def revoke_connection(self, tenant_id: str, conn_id: str) -> ToolConnectionRecord | None:
        """把连接置为 revoked。

        Args:
            tenant_id: 租户 ID。
            conn_id: 连接 ID。

        Returns:
            ToolConnectionRecord | None: 更新后的连接；不存在返回 None。
        """
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _ToolConnectionRepository(uow)
            row = await repo.fetch_one(
                f"""
                UPDATE {TOOL_CONNECTION_TABLE}
                SET status = 'revoked', updated_at = now()
                WHERE {repo.tenant_clause()} AND conn_id = :conn_id
                  AND status = 'connected'
                RETURNING tenant_id, conn_id, tool_type, scope, owner_user_id, org_id,
                          status, expires_at, created_at, updated_at
                """,
                repo.params(conn_id=conn_id),
            )
        return _row_to_connection(row) if row is not None else None

    async def record_execution(self, record: ToolExecutionRecord) -> ToolExecutionRecord:
        """写入一条调用记录。

        Args:
            record: 调用记录。

        Returns:
            ToolExecutionRecord: 落库后的记录。
        """
        summary = json.dumps(record.result_summary or {}, ensure_ascii=False, sort_keys=True)
        async with tenant_scope(record.tenant_id, settings=self._settings) as uow:
            repo = _ToolExecutionRepository(uow)
            row = await repo.fetch_one(
                f"""
                INSERT INTO {TOOL_EXECUTION_TABLE} (
                    tenant_id, execution_id, conn_id, tool_type, operation, status,
                    error_code, error_message, result_summary, operator_user_id, duration_ms
                ) VALUES (
                    :tenant_id, :execution_id, NULLIF(:conn_id, ''), :tool_type, :operation,
                    :status, :error_code, :error_message, CAST(:result_summary AS jsonb),
                    :operator_user_id, :duration_ms
                )
                RETURNING tenant_id, execution_id, conn_id, tool_type, operation, status,
                          error_code, error_message, result_summary, operator_user_id,
                          duration_ms, executed_at
                """,
                repo.params(
                    execution_id=record.execution_id,
                    conn_id=record.conn_id,
                    tool_type=record.tool_type,
                    operation=record.operation,
                    status=record.status,
                    error_code=record.error_code,
                    error_message=record.error_message,
                    result_summary=summary,
                    operator_user_id=record.operator_user_id,
                    duration_ms=record.duration_ms,
                ),
            )
        return _row_to_execution(row) if row is not None else record

    async def list_executions(
        self, tenant_id: str, *, tool_type: str = "", limit: int = 20, offset: int = 0
    ) -> tuple[list[ToolExecutionRecord], int]:
        """分页读取调用记录（时间倒序）。

        Args:
            tenant_id: 租户 ID。
            tool_type: 工具类型过滤。
            limit: 每页条数。
            offset: 偏移量。

        Returns:
            tuple[list[ToolExecutionRecord], int]: 当前页与总数。
        """
        type_filter = "AND tool_type = :tool_type" if tool_type else ""
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _ToolExecutionRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT tenant_id, execution_id, conn_id, tool_type, operation, status,
                       error_code, error_message, result_summary, operator_user_id,
                       duration_ms, executed_at
                FROM {TOOL_EXECUTION_TABLE}
                WHERE {repo.tenant_clause()} {type_filter}
                ORDER BY executed_at DESC, execution_id DESC
                LIMIT :limit OFFSET :offset
                """,
                repo.params(tool_type=tool_type, limit=limit, offset=offset),
            )
            total = await repo.fetch_scalar(
                f"""
                SELECT COUNT(*) FROM {TOOL_EXECUTION_TABLE}
                WHERE {repo.tenant_clause()} {type_filter}
                """,
                repo.params(tool_type=tool_type),
            )
        return [_row_to_execution(row) for row in rows], int(total or 0)


def _to_timestamp(value: Any) -> str:
    """把数据库时间值规范化为 ISO 8601 字符串。

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


def _row_to_connection(row: Mapping[str, Any]) -> ToolConnectionRecord:
    """把数据库行映射为连接记录。

    Args:
        row: 行映射。

    Returns:
        ToolConnectionRecord: 连接记录。
    """
    expires_at = row.get("expires_at")
    return ToolConnectionRecord(
        tenant_id=str(row.get("tenant_id", "")),
        conn_id=str(row.get("conn_id", "")),
        tool_type=str(row.get("tool_type", "")),
        scope=str(row.get("scope", SCOPE_PERSONAL)),
        owner_user_id=str(row.get("owner_user_id", "") or ""),
        org_id=str(row.get("org_id", "") or ""),
        status=str(row.get("status", CONNECTION_CONNECTED)),
        expires_at=_to_timestamp(expires_at) if expires_at is not None else None,
        created_at=_to_timestamp(row.get("created_at")),
        updated_at=_to_timestamp(row.get("updated_at")),
    )


def _row_to_execution(row: Mapping[str, Any]) -> ToolExecutionRecord:
    """把数据库行映射为调用记录。

    Args:
        row: 行映射。

    Returns:
        ToolExecutionRecord: 调用记录。
    """
    summary = row.get("result_summary")
    error_code = row.get("error_code")
    return ToolExecutionRecord(
        tenant_id=str(row.get("tenant_id", "")),
        execution_id=str(row.get("execution_id", "")),
        tool_type=str(row.get("tool_type", "")),
        operation=str(row.get("operation", "")),
        status=str(row.get("status", EXECUTION_SUCCESS)),
        conn_id=str(row.get("conn_id", "") or ""),
        error_code=int(error_code) if error_code is not None else None,
        error_message=str(row.get("error_message", "") or "") or None,
        result_summary=(
            {str(key): value for key, value in summary.items()}
            if isinstance(summary, dict)
            else None
        ),
        operator_user_id=str(row.get("operator_user_id", "") or ""),
        duration_ms=int(row.get("duration_ms", 0) or 0),
        executed_at=_to_timestamp(row.get("executed_at")),
    )


# ===========================================================================
# 进程级单例（内存后端跨模块共享，postgres 后端无状态）
# ===========================================================================
_memory_store: InMemoryToolStore | None = None


def get_tool_store(settings: Settings | None = None) -> ToolStore:
    """获取工具存储。

    Args:
        settings: 可选配置实例。

    Returns:
        ToolStore: `business_store=postgres` 时为数据库实现，否则为内存实现。
    """
    global _memory_store

    conf = settings or get_settings()
    if conf.uses_postgres:
        return PostgresToolStore(conf)
    if _memory_store is None:
        _memory_store = InMemoryToolStore()
    return _memory_store


def reset_tool_store() -> None:
    """重置内存后端单例（测试隔离用）。"""
    global _memory_store
    if _memory_store is not None:
        _memory_store.clear()
    _memory_store = None


__all__: list[str] = [
    "CONNECTION_CONNECTED",
    "CONNECTION_EXPIRED",
    "CONNECTION_REVOKED",
    "EXECUTION_FAILED",
    "EXECUTION_SUCCESS",
    "SCOPE_ORGANIZATION",
    "SCOPE_PERSONAL",
    "SUMMARY_TEXT_LIMIT",
    "TOOL_CONNECTION_TABLE",
    "TOOL_EXECUTION_TABLE",
    "InMemoryToolStore",
    "PostgresToolStore",
    "ToolConnectionRecord",
    "ToolExecutionRecord",
    "ToolStore",
    "get_tool_store",
    "reset_tool_store",
    "summarize_result",
]
