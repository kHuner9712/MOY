# ruff: noqa: S608 - 本模块 SQL 的表名/列名均为常量，值一律走命名参数绑定。
"""MCP server 登记仓储（矩阵第 3 项，2026-11-06）。

只存**声明**，不存凭据明文：

| 列 | 说明 |
| --- | --- |
| `endpoint` | http(s) 地址，或 stdio 的可执行文件路径 |
| `args` | stdio 的启动参数（JSON 数组） |
| `credential_ref` | 凭据**引用**；明文只在 `credential_store` 密文里 |
| `allowed_tools` | 允许调用的工具白名单（JSON 数组） |

双后端（`OA_BUSINESS_STORE`）：`memory`（进程内）/ `postgres`（短事务 + 租户条件 +
FORCE RLS）。所有 SQL 都带 `tenant_id`，仓储层不做任何跨租户读取。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Final, Protocol

from app.core.config import Settings, get_settings
from app.db.repositories.base import BaseRepository
from app.db.session import tenant_scope

#: MCP server 表名
MCP_SERVER_TABLE: Final[str] = "mcp_server"


def _now_iso() -> str:
    """当前 UTC 时间（ISO 8601）。

    Returns:
        str: 形如 `2026-11-06T09:00:00Z`。
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class McpServerRecord:
    """一条 MCP server 登记。

    Attributes:
        tenant_id: 租户 ID。
        server_id: 登记 ID（`mcp_` 前缀）。
        name: 租户内唯一的服务器名（插件 `operation` 的前半段）。
        transport: `http` / `stdio`。
        endpoint: http(s) 地址，或 stdio 可执行文件路径。
        args: stdio 启动参数。
        credential_ref: 凭据引用（**永不存明文**）。
        allowed_tools: 允许调用的工具名白名单。
        enabled: 是否启用。
        timeout_ms: 单次调用总超时。
        max_output_bytes: 返回体字节上限。
        created_at: 创建时间。
        updated_at: 最近更新时间。
    """

    tenant_id: str
    server_id: str
    name: str
    transport: str = "http"
    endpoint: str = ""
    args: tuple[str, ...] = ()
    credential_ref: str = ""
    allowed_tools: tuple[str, ...] = ()
    enabled: bool = False
    timeout_ms: int = 15_000
    max_output_bytes: int = 64 * 1024
    created_at: str = ""
    updated_at: str = ""


class McpServerStore(Protocol):
    """MCP server 存储协议（内存与数据库实现共用）。"""

    async def list_servers(self, tenant_id: str) -> list[McpServerRecord]:
        """列出租户内全部登记。"""
        ...

    async def get_server(self, tenant_id: str, server_id: str) -> McpServerRecord | None:
        """按 ID 读取登记。"""
        ...

    async def get_by_name(self, tenant_id: str, name: str) -> McpServerRecord | None:
        """按名称读取登记。"""
        ...

    async def upsert_server(self, record: McpServerRecord) -> McpServerRecord:
        """写入或更新登记。"""
        ...

    async def delete_server(self, tenant_id: str, server_id: str) -> bool:
        """删除登记。"""
        ...

    async def count_servers(self, tenant_id: str) -> int:
        """统计租户内登记数。"""
        ...


class InMemoryMcpServerStore:
    """进程内实现（仅开发/测试）。"""

    def __init__(self) -> None:
        """初始化。"""
        self._rows: dict[tuple[str, str], McpServerRecord] = {}
        self._lock = asyncio.Lock()

    async def list_servers(self, tenant_id: str) -> list[McpServerRecord]:
        """列出租户内全部登记（按名称排序）。

        Args:
            tenant_id: 租户 ID。

        Returns:
            list[McpServerRecord]: 登记列表。
        """
        async with self._lock:
            rows = [row for (tnt, _), row in self._rows.items() if tnt == tenant_id]
        rows.sort(key=lambda item: item.name)
        return rows

    async def get_server(self, tenant_id: str, server_id: str) -> McpServerRecord | None:
        """按 ID 读取。

        Args:
            tenant_id: 租户 ID。
            server_id: 登记 ID。

        Returns:
            McpServerRecord | None: 登记；不存在返回 None。
        """
        async with self._lock:
            return self._rows.get((tenant_id, server_id))

    async def get_by_name(self, tenant_id: str, name: str) -> McpServerRecord | None:
        """按名称读取。

        Args:
            tenant_id: 租户 ID。
            name: 服务器名。

        Returns:
            McpServerRecord | None: 登记；不存在返回 None。
        """
        async with self._lock:
            for (tnt, _), row in self._rows.items():
                if tnt == tenant_id and row.name == name:
                    return row
        return None

    async def upsert_server(self, record: McpServerRecord) -> McpServerRecord:
        """写入或更新（补齐时间戳）。

        Args:
            record: 登记。

        Returns:
            McpServerRecord: 落库后的登记。
        """
        now = _now_iso()
        async with self._lock:
            current = self._rows.get((record.tenant_id, record.server_id))
            payload = replace(
                record,
                created_at=current.created_at if current is not None else now,
                updated_at=now,
            )
            self._rows[(record.tenant_id, record.server_id)] = payload
        return payload

    async def delete_server(self, tenant_id: str, server_id: str) -> bool:
        """删除登记。

        Args:
            tenant_id: 租户 ID。
            server_id: 登记 ID。

        Returns:
            bool: 删除成功返回 True。
        """
        async with self._lock:
            return self._rows.pop((tenant_id, server_id), None) is not None

    async def count_servers(self, tenant_id: str) -> int:
        """统计租户内登记数。

        Args:
            tenant_id: 租户 ID。

        Returns:
            int: 数量。
        """
        async with self._lock:
            return sum(1 for (tnt, _) in self._rows if tnt == tenant_id)

    def clear(self) -> None:
        """清空（测试隔离用）。"""
        self._rows.clear()


class _McpServerRepository(BaseRepository):
    """`mcp_server` 表仓储（强制租户条件）。"""

    table = MCP_SERVER_TABLE


class PostgresMcpServerStore:
    """PostgreSQL 存储（一次操作一个短事务）。"""

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化。

        Args:
            settings: 可选配置实例。
        """
        self._settings = settings or get_settings()

    async def list_servers(self, tenant_id: str) -> list[McpServerRecord]:
        """列出租户内全部登记。

        Args:
            tenant_id: 租户 ID。

        Returns:
            list[McpServerRecord]: 登记列表。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _McpServerRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT tenant_id, server_id, name, transport, endpoint, args,
                       credential_ref, allowed_tools, enabled, timeout_ms,
                       max_output_bytes, created_at, updated_at
                FROM {MCP_SERVER_TABLE}
                WHERE {repo.tenant_clause()}
                ORDER BY name, server_id
                """
            )
        return [_row_to_record(row) for row in rows]

    async def get_server(self, tenant_id: str, server_id: str) -> McpServerRecord | None:
        """按 ID 读取。

        Args:
            tenant_id: 租户 ID。
            server_id: 登记 ID。

        Returns:
            McpServerRecord | None: 登记。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _McpServerRepository(uow)
            row = await repo.fetch_one(
                f"""
                SELECT tenant_id, server_id, name, transport, endpoint, args,
                       credential_ref, allowed_tools, enabled, timeout_ms,
                       max_output_bytes, created_at, updated_at
                FROM {MCP_SERVER_TABLE}
                WHERE {repo.tenant_clause()} AND server_id = :server_id
                """,
                repo.params(server_id=server_id),
            )
        return _row_to_record(row) if row is not None else None

    async def get_by_name(self, tenant_id: str, name: str) -> McpServerRecord | None:
        """按名称读取。

        Args:
            tenant_id: 租户 ID。
            name: 服务器名。

        Returns:
            McpServerRecord | None: 登记。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _McpServerRepository(uow)
            row = await repo.fetch_one(
                f"""
                SELECT tenant_id, server_id, name, transport, endpoint, args,
                       credential_ref, allowed_tools, enabled, timeout_ms,
                       max_output_bytes, created_at, updated_at
                FROM {MCP_SERVER_TABLE}
                WHERE {repo.tenant_clause()} AND name = :name
                """,
                repo.params(name=name),
            )
        return _row_to_record(row) if row is not None else None

    async def upsert_server(self, record: McpServerRecord) -> McpServerRecord:
        """写入或更新登记。

        Args:
            record: 登记。

        Returns:
            McpServerRecord: 落库后的登记。
        """
        async with tenant_scope(record.tenant_id, settings=self._settings) as uow:
            repo = _McpServerRepository(uow)
            row = await repo.fetch_one(
                f"""
                INSERT INTO {MCP_SERVER_TABLE} (
                    tenant_id, server_id, name, transport, endpoint, args,
                    credential_ref, allowed_tools, enabled, timeout_ms, max_output_bytes
                ) VALUES (
                    :tenant_id, :server_id, :name, :transport, :endpoint,
                    CAST(:args AS jsonb), :credential_ref, CAST(:allowed_tools AS jsonb),
                    :enabled, :timeout_ms, :max_output_bytes
                )
                ON CONFLICT (tenant_id, server_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    transport = EXCLUDED.transport,
                    endpoint = EXCLUDED.endpoint,
                    args = EXCLUDED.args,
                    credential_ref = EXCLUDED.credential_ref,
                    allowed_tools = EXCLUDED.allowed_tools,
                    enabled = EXCLUDED.enabled,
                    timeout_ms = EXCLUDED.timeout_ms,
                    max_output_bytes = EXCLUDED.max_output_bytes,
                    updated_at = now()
                RETURNING tenant_id, server_id, name, transport, endpoint, args,
                          credential_ref, allowed_tools, enabled, timeout_ms,
                          max_output_bytes, created_at, updated_at
                """,
                repo.params(
                    server_id=record.server_id,
                    name=record.name,
                    transport=record.transport,
                    endpoint=record.endpoint,
                    args=json.dumps(list(record.args), ensure_ascii=False),
                    credential_ref=record.credential_ref,
                    allowed_tools=json.dumps(list(record.allowed_tools), ensure_ascii=False),
                    enabled=record.enabled,
                    timeout_ms=record.timeout_ms,
                    max_output_bytes=record.max_output_bytes,
                ),
            )
        return _row_to_record(row) if row is not None else record

    async def delete_server(self, tenant_id: str, server_id: str) -> bool:
        """删除登记。

        Args:
            tenant_id: 租户 ID。
            server_id: 登记 ID。

        Returns:
            bool: 删除成功返回 True。
        """
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _McpServerRepository(uow)
            row = await repo.fetch_one(
                f"""
                DELETE FROM {MCP_SERVER_TABLE}
                WHERE {repo.tenant_clause()} AND server_id = :server_id
                RETURNING server_id
                """,
                repo.params(server_id=server_id),
            )
        return row is not None

    async def count_servers(self, tenant_id: str) -> int:
        """统计租户内登记数。

        Args:
            tenant_id: 租户 ID。

        Returns:
            int: 数量。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _McpServerRepository(uow)
            total = await repo.fetch_scalar(
                f"""
                SELECT COUNT(*) FROM {MCP_SERVER_TABLE}
                WHERE {repo.tenant_clause()}
                """
            )
        return int(total or 0)


def _to_timestamp(value: Any) -> str:
    """把数据库时间值规范化为 ISO 8601。

    Args:
        value: 时间值。

    Returns:
        str: ISO 8601；空值返回空串。
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)


def _json_list(value: Any) -> tuple[str, ...]:
    """把 JSONB 数组列转成字符串元组。

    Args:
        value: 数据库返回的 jsonb 值。

    Returns:
        tuple[str, ...]: 字符串元组（非法值按空数组处理）。
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return ()
    if not isinstance(value, list | tuple):
        return ()
    return tuple(str(item) for item in value)


def _row_to_record(row: Mapping[str, Any]) -> McpServerRecord:
    """把数据库行映射为登记记录。

    Args:
        row: 行映射。

    Returns:
        McpServerRecord: 登记记录。
    """
    return McpServerRecord(
        tenant_id=str(row.get("tenant_id", "")),
        server_id=str(row.get("server_id", "")),
        name=str(row.get("name", "")),
        transport=str(row.get("transport", "http")),
        endpoint=str(row.get("endpoint", "")),
        args=_json_list(row.get("args")),
        credential_ref=str(row.get("credential_ref", "") or ""),
        allowed_tools=_json_list(row.get("allowed_tools")),
        enabled=bool(row.get("enabled", False)),
        timeout_ms=int(row.get("timeout_ms", 15_000) or 15_000),
        max_output_bytes=int(row.get("max_output_bytes", 64 * 1024) or 64 * 1024),
        created_at=_to_timestamp(row.get("created_at")),
        updated_at=_to_timestamp(row.get("updated_at")),
    )


# ===========================================================================
# 进程级单例（内存后端跨模块共享，postgres 后端无状态）
# ===========================================================================
_memory_store: InMemoryMcpServerStore | None = None


def get_mcp_server_store(settings: Settings | None = None) -> McpServerStore:
    """获取 MCP server 存储。

    Args:
        settings: 可选配置实例。

    Returns:
        McpServerStore: `business_store=postgres` 时为数据库实现，否则内存实现。
    """
    global _memory_store

    conf = settings or get_settings()
    if conf.uses_postgres:
        return PostgresMcpServerStore(conf)
    if _memory_store is None:
        _memory_store = InMemoryMcpServerStore()
    return _memory_store


def reset_mcp_server_store() -> None:
    """重置内存后端单例（测试隔离用）。"""
    global _memory_store
    if _memory_store is not None:
        _memory_store.clear()
    _memory_store = None


__all__: list[str] = [
    "MCP_SERVER_TABLE",
    "InMemoryMcpServerStore",
    "McpServerRecord",
    "McpServerStore",
    "PostgresMcpServerStore",
    "get_mcp_server_store",
    "reset_mcp_server_store",
]
