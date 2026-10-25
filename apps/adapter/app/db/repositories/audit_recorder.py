"""操作记录器（内存实现 + 契约）—— 从 `business/audit_service.py` 下沉（审计 P1-4）。

**为什么下沉到 `db`**：`InMemoryAuditRecorder` 是**存储实现**，「操作记录查询仓储」
（`db/repositories/audit_repo.py`）要读它，于是仓储层必须反向 `import app.business`
（方向倒挂，P1-4 实测的正是这一处）。记录器与仓储同属数据侧，归 `db/repositories` 后
依赖方向变成 `db → db`（自引用）与 `db → core`（动作常量），矩阵内成立。

**为什么 `PostgresAuditRecorder` 不走**：它是**业务写入入口**的 PG 实现，需要
`tenant_scope` / `platform_scope` 的租户上下文与「平台哨兵分流」口径，随写入语义
一起留在 `business/audit_service.py`；它从这里复用 `AuditEntry` 与 `with_defaults`，
再加一个 `db → db` 自引用边，方向不变。

**为什么 `business/audit_service.py` 继续 re-export**：`AuditEntry` / `AuditRecorder` /
`InMemoryAuditRecorder` 是 49 处调用方（含测试）的既有 import 路径，re-export 保证
**零改动下沉**。

本模块只依赖 `core`（动作展示名常量）与标准库。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from app.core.audit_actions import ACTION_DISPLAY_NAMES
from app.core.config import Settings


def now_iso() -> str:
    """当前 UTC 时间（ISO 8601）。

    **公开**（不带下划线）是有意的：`business/audit_service.py` 写入时也要盖时间戳，
    跨模块 import 一个 `_private` 名字会立刻被 ruff SLF/F401 口径质疑，语义上也不对
    ——「现在几点」是记录器契约的一部分，不是本模块的私有实现细节。

    Returns:
        str: 形如 `2026-11-02T09:00:00Z`。
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """操作记录条目。

    Attributes:
        tenant_id: 租户 ID。
        actor_id: 操作人用户 ID。
        action: 操作编码（`{域}_{对象}_{动作}`）。
        resource_type: 资源类型。
        resource_id: 资源 ID。
        result: 操作结果（`success` / `failure`）。
        actor_role: 操作人当时的角色（首个角色，便于筛选）。
        action_display_name: 操作的用户可见名称。
        change_summary: 变更摘要（只放业务字段，禁止放密钥/正文）。
        occurred_at: 发生时间（ISO 8601）。
        trace_id: 链路追踪 ID。
    """

    tenant_id: str
    actor_id: str
    action: str
    resource_type: str
    resource_id: str = ""
    result: str = "success"
    actor_role: str = ""
    action_display_name: str = ""
    change_summary: dict[str, Any] = field(default_factory=dict)
    occurred_at: str = ""
    trace_id: str = ""

    @property
    def display_name(self) -> str:
        """操作的用户可见名称（未登记时回退为操作编码）。"""
        return self.action_display_name or ACTION_DISPLAY_NAMES.get(self.action, self.action)


class AuditRecorder(Protocol):
    """操作记录存储契约。"""

    async def record(self, entry: AuditEntry) -> AuditEntry:
        """写入一条操作记录。"""
        ...

    async def recent(
        self, tenant_id: str, *, action: str = "", resource_id: str = "", limit: int = 50
    ) -> list[AuditEntry]:
        """按时间倒序读取最近的操作记录。"""
        ...


class InMemoryAuditRecorder:
    """进程内操作记录存储（development/test）。"""

    def __init__(self) -> None:
        """初始化。"""
        self._entries: list[AuditEntry] = []
        self._lock = asyncio.Lock()

    async def record(self, entry: AuditEntry) -> AuditEntry:
        """写入一条操作记录。

        Args:
            entry: 操作记录条目。

        Returns:
            AuditEntry: 补齐时间与展示名后的条目。
        """
        payload = entry if entry.occurred_at else with_defaults(entry)
        async with self._lock:
            self._entries.append(payload)
        return payload

    async def recent(
        self, tenant_id: str, *, action: str = "", resource_id: str = "", limit: int = 50
    ) -> list[AuditEntry]:
        """按时间倒序读取。

        Args:
            tenant_id: 租户 ID。
            action: 操作编码过滤（空表示不过滤）。
            resource_id: 资源 ID 过滤（空表示不过滤）。
            limit: 返回条数上限。

        Returns:
            list[AuditEntry]: 操作记录列表（最近在前）。
        """
        async with self._lock:
            candidates = [item for item in self._entries if item.tenant_id == tenant_id]
        if action:
            candidates = [item for item in candidates if item.action == action]
        if resource_id:
            candidates = [item for item in candidates if item.resource_id == resource_id]
        candidates.sort(key=lambda item: item.occurred_at, reverse=True)
        return candidates[:limit]

    async def entries_for(self, tenant_id: str) -> list[AuditEntry]:
        """读取该租户的全部操作记录（B08 的 U6 多维筛选在仓储层完成）。

        与 `recent()` 的区别：不做条数截断，供 `audit_repo` 做多条件过滤与分页。

        Args:
            tenant_id: 租户 ID。

        Returns:
            list[AuditEntry]: 操作记录列表（时间倒序）。
        """
        async with self._lock:
            items = [item for item in self._entries if item.tenant_id == tenant_id]
        items.sort(key=lambda item: item.occurred_at, reverse=True)
        return items

    def clear(self) -> None:
        """清空（测试隔离用）。"""
        self._entries.clear()


def with_defaults(entry: AuditEntry) -> AuditEntry:
    """补齐发生时间与操作展示名。

    **公开**（不带下划线）是有意的：`business/audit_service.py` 的
    `PostgresAuditRecorder` 与 `AuditService` 都要用它，跨模块 import 一个
    `_private` 名字既会被 lint 质疑，语义上也不对——「补齐缺省字段」是记录器
    契约的一部分，不是本模块的私有实现细节。

    Args:
        entry: 原始条目。

    Returns:
        AuditEntry: 补齐后的条目。
    """
    display = entry.action_display_name or ACTION_DISPLAY_NAMES.get(entry.action, entry.action)
    return AuditEntry(
        tenant_id=entry.tenant_id,
        actor_id=entry.actor_id,
        action=entry.action,
        resource_type=entry.resource_type,
        resource_id=entry.resource_id,
        result=entry.result,
        actor_role=entry.actor_role,
        action_display_name=display,
        change_summary=dict(entry.change_summary),
        occurred_at=entry.occurred_at or now_iso(),
        trace_id=entry.trace_id,
    )


# ===========================================================================
# 进程级单例（内存后端）
#
# 必须是单例：写入方（业务服务）与查询方（`audit_repo` 的 U6 分页）必须看到
# **同一份**记录，否则「刚记的操作查不到」。单例归数据侧持有，业务层的
# `get_audit_recorder()` 委托到这里。
# ===========================================================================
_memory_recorder: InMemoryAuditRecorder | None = None


def get_in_memory_audit_recorder(settings: Settings | None = None) -> InMemoryAuditRecorder:
    """获取进程内操作记录存储单例。

    Args:
        settings: 配置实例。**本实现不依赖配置**，参数保留是为了与
            `get_audit_recorder()` 的调用形态对称，调用方无需分支处理。

    Returns:
        InMemoryAuditRecorder: 进程内唯一实例。
    """
    del settings  # 内存实现不读配置（签名对称性保留）
    global _memory_recorder
    if _memory_recorder is None:
        _memory_recorder = InMemoryAuditRecorder()
    return _memory_recorder


def reset_in_memory_audit_recorder() -> None:
    """重置内存后端单例（测试隔离用）。"""
    global _memory_recorder
    if _memory_recorder is not None:
        _memory_recorder.clear()
    _memory_recorder = None


__all__: list[str] = [
    "AuditEntry",
    "AuditRecorder",
    "InMemoryAuditRecorder",
    "get_in_memory_audit_recorder",
    "now_iso",
    "reset_in_memory_audit_recorder",
    "with_defaults",
]
