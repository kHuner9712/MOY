# ruff: noqa: S608 - 本模块 SQL 的表名/列名均为常量（审计表不接收任何用户输入标识符），
# 值一律走命名参数绑定。
"""操作记录服务（阶段二 B04，架构文档 3.11 / PRD P2-KB-01 验收 4）。

`audit_log` 是**业务事实**（可供客户查询），与技术日志职责不同（架构文档 11.4）：
删除知识库这类不可逆操作必须留下可检索的记录，且软删除后记录仍保留。

双后端（`OA_BUSINESS_STORE`）：

- `memory`：进程内列表，供 development/test 与「审计有记录」断言；
- `postgres`：短事务 INSERT（`audit_log` 不提供更新/删除入口）。

写入时机：与业务写**同一请求内**完成；业务写失败时审计不落（由调用方决定）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, Protocol

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.session import platform_scope, tenant_scope
from app.db.tenant import PLATFORM_TENANT

logger = get_logger(__name__)

#: 审计表名
AUDIT_TABLE: Final[str] = "audit_log"

#: 审计写入 SQL（租户作用域与平台作用域共用，避免两份定义漂移）
_AUDIT_INSERT_SQL: Final[str] = f"""
    INSERT INTO {AUDIT_TABLE} (
        tenant_id, actor_id, action, resource_type, resource_id, result,
        actor_role, action_display_name, change_summary, trace_id, occurred_at
    ) VALUES (
        :tenant_id, :actor_id, :action, :resource_type, :resource_id, :result,
        :actor_role, :action_display_name, CAST(:change_summary AS jsonb),
        :trace_id, now()
    )
    """

#: 资源类型：知识库
RESOURCE_KNOWLEDGE_BASE: Final[str] = "knowledge_base"

#: 资源类型：文档
RESOURCE_DOCUMENT: Final[str] = "document"

#: 资源类型：知识库授权（B05）
RESOURCE_GRANT: Final[str] = "knowledge_base_grant"

#: 操作：创建知识库
ACTION_KB_CREATE: Final[str] = "knowledge_base.create"

#: 操作：更新知识库
ACTION_KB_UPDATE: Final[str] = "knowledge_base.update"

#: 操作：删除知识库
ACTION_KB_DELETE: Final[str] = "knowledge_base.delete"

#: 操作：新增成员授权（B05，PRD P2-KB-03 验收 6）
ACTION_GRANT_CREATE: Final[str] = "knowledge_base.grant_create"

#: 操作：撤销成员授权（B05）
ACTION_GRANT_REVOKE: Final[str] = "knowledge_base.grant_revoke"

#: 操作：上传文档
ACTION_DOC_UPLOAD: Final[str] = "document.upload"

#: 操作：删除文档
ACTION_DOC_DELETE: Final[str] = "document.delete"

#: 资源类型：部门（B08）
RESOURCE_DEPARTMENT: Final[str] = "department"

#: 资源类型：成员（B08）
RESOURCE_MEMBER: Final[str] = "member"

#: 资源类型：角色（B08）
RESOURCE_ROLE: Final[str] = "role"

#: 操作：新建部门（B08）
ACTION_DEPT_CREATE: Final[str] = "department.create"

#: 操作：修改/移动部门（B08）
ACTION_DEPT_UPDATE: Final[str] = "department.update"

#: 操作：删除部门（B08）
ACTION_DEPT_DELETE: Final[str] = "department.delete"

#: 操作：变更成员角色/部门/状态（B08）
ACTION_MEMBER_UPDATE: Final[str] = "member.update"

#: 操作：移出成员（B08）
ACTION_MEMBER_REMOVE: Final[str] = "member.remove"

#: 操作：批量变更成员（B08）
ACTION_MEMBER_BATCH: Final[str] = "member.batch_update"

#: 操作：删除角色（B08）
ACTION_ROLE_DELETE: Final[str] = "role.delete"

#: 操作：计量对账偏差（B07，G-23）
ACTION_RECONCILE_MISMATCH: Final[str] = "billing.reconcile_mismatch"

#: 资源类型：订阅（B09）
RESOURCE_SUBSCRIPTION: Final[str] = "subscription"

#: 资源类型：订单（B09）
RESOURCE_ORDER: Final[str] = "order"

#: 资源类型：企业（B10 运营后台）
RESOURCE_TENANT: Final[str] = "tenant"

#: 资源类型：系统配置（B10 运营后台）
RESOURCE_SETTINGS: Final[str] = "ops_settings"

#: 操作：开通/变更套餐与席位（B09，BL3/BL4）
ACTION_SUBSCRIPTION_UPDATE: Final[str] = "billing.subscription_update"

#: 操作：创建订单（B09，BL6）
ACTION_ORDER_CREATED: Final[str] = "billing.order_created"

#: 操作：订单支付成功（B09，BL10 回调 / OPS5 核销）
ACTION_ORDER_PAID: Final[str] = "billing.order_paid"

#: 操作：启用/停用企业（B10，OPS3）
ACTION_TENANT_STATUS: Final[str] = "ops.tenant_status"

#: 操作：订单核销（B10，OPS5）
ACTION_ORDER_CONFIRMED: Final[str] = "ops.order_confirm"

#: 操作：修改系统配置（B10，OPS8）
ACTION_SETTINGS_UPDATE: Final[str] = "ops.settings_update"

#: 资源类型：支付回调（B09 安全加固）
RESOURCE_PAYMENT_CALLBACK: Final[str] = "payment_callback"

#: 资源类型：插件（阶段三 B17）
RESOURCE_PLUGIN: Final[str] = "plugin"

#: 操作：添加插件（PL5，阶段三 B17）
ACTION_PLUGIN_INSTALL: Final[str] = "plugin.install"

#: 操作：变更企业能力开关（PL13，阶段三 B17）
ACTION_PLUGIN_CAPABILITY_POLICY: Final[str] = "plugin.capability_policy"

#: 操作：支付回调被拒绝（签名/金额/订单状态校验未通过，B09 安全加固）
#:
#: 拒绝事件**必须**进审计：`payment_callback_log` 只记「已接受并处理」的回调，
#: 若把未验签的载荷写进去，攻击者只要带上真实 `transaction_id` 就能占掉幂等键，
#: 让真实支付永远无法开通（P0-07）。因此拒绝事件只有 `audit_log` 一份归属，
#: 且未验签的渠道流水号**不进入**幂等键空间。
ACTION_PAYMENT_CALLBACK_REJECTED: Final[str] = "billing.payment_callback_rejected"

#: 操作 → 用户可见名称（写入 `action_display_name`，供操作记录页直接展示）
ACTION_DISPLAY_NAMES: Final[dict[str, str]] = {
    ACTION_KB_CREATE: "创建知识库",
    ACTION_KB_UPDATE: "修改知识库设置",
    ACTION_KB_DELETE: "删除知识库",
    ACTION_GRANT_CREATE: "添加成员授权",
    ACTION_GRANT_REVOKE: "撤销成员授权",
    ACTION_DOC_UPLOAD: "上传文档",
    ACTION_DOC_DELETE: "删除文档",
    ACTION_DEPT_CREATE: "新建部门",
    ACTION_DEPT_UPDATE: "修改部门",
    ACTION_DEPT_DELETE: "删除部门",
    ACTION_MEMBER_UPDATE: "变更成员角色与部门",
    ACTION_MEMBER_REMOVE: "移出成员",
    ACTION_MEMBER_BATCH: "批量变更成员",
    ACTION_ROLE_DELETE: "删除角色",
    ACTION_RECONCILE_MISMATCH: "计量对账偏差",
    ACTION_SUBSCRIPTION_UPDATE: "变更套餐与席位",
    ACTION_ORDER_CREATED: "创建订单",
    ACTION_ORDER_PAID: "订单支付完成",
    ACTION_TENANT_STATUS: "启用或停用企业",
    ACTION_ORDER_CONFIRMED: "订单核销",
    ACTION_SETTINGS_UPDATE: "修改系统配置",
    ACTION_PAYMENT_CALLBACK_REJECTED: "支付回调被拒绝",
    ACTION_PLUGIN_INSTALL: "添加插件",
    ACTION_PLUGIN_CAPABILITY_POLICY: "变更企业能力开关",
}


def _now_iso() -> str:
    """当前 UTC 时间（ISO 8601）。

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
        payload = entry if entry.occurred_at else _with_defaults(entry)
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


class PostgresAuditRecorder:
    """PostgreSQL 操作记录存储（一次调用一个短事务）。"""

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化。

        Args:
            settings: 可选配置实例。
        """
        self._settings = settings or get_settings()

    async def record(self, entry: AuditEntry) -> AuditEntry:
        """写入一条操作记录。

        Args:
            entry: 操作记录条目。

        Returns:
            AuditEntry: 补齐时间与展示名后的条目。
        """
        import json

        payload = entry if entry.occurred_at else _with_defaults(entry)
        summary = json.dumps(payload.change_summary, ensure_ascii=False, sort_keys=True)
        parameters = {
            "tenant_id": payload.tenant_id,
            "actor_id": payload.actor_id,
            "action": payload.action,
            "resource_type": payload.resource_type,
            "resource_id": payload.resource_id,
            "result": payload.result,
            "actor_role": payload.actor_role,
            "action_display_name": payload.display_name,
            "change_summary": summary,
            "trace_id": payload.trace_id,
        }
        # 平台级安全事件（如未验签的支付回调）以 `__platform__` 落库：
        # `tenant_scope()` 会因平台哨兵未声明 allow_platform 而拒绝（30003），
        # 故显式分流到 `platform_scope()`——它不是「绕过隔离」，而是「平台视角专线」。
        if payload.tenant_id == PLATFORM_TENANT:
            async with platform_scope(self._settings) as uow:
                await uow.execute(_AUDIT_INSERT_SQL, parameters)
        else:
            async with tenant_scope(payload.tenant_id, settings=self._settings) as uow:
                await uow.execute(_AUDIT_INSERT_SQL, parameters)
        return payload

    async def recent(
        self, tenant_id: str, *, action: str = "", resource_id: str = "", limit: int = 50
    ) -> list[AuditEntry]:
        """按时间倒序读取。

        Args:
            tenant_id: 租户 ID。
            action: 操作编码过滤。
            resource_id: 资源 ID 过滤。
            limit: 返回条数上限。

        Returns:
            list[AuditEntry]: 操作记录列表。
        """
        action_filter = "AND action = :action" if action else ""
        resource_filter = "AND resource_id = :resource_id" if resource_id else ""
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            rows = await uow.fetch_all(
                f"""
                SELECT tenant_id, actor_id, action, resource_type, resource_id, result,
                       actor_role, action_display_name, change_summary, trace_id, occurred_at
                FROM {AUDIT_TABLE}
                WHERE tenant_id = :tenant_id {action_filter} {resource_filter}
                ORDER BY occurred_at DESC, id DESC
                LIMIT :limit
                """,
                {
                    "tenant_id": tenant_id,
                    "action": action,
                    "resource_id": resource_id,
                    "limit": limit,
                },
            )
        return [_row_to_entry(row) for row in rows]


def _row_to_entry(row: Mapping[str, Any]) -> AuditEntry:
    """把数据库行映射为操作记录条目。

    Args:
        row: 行映射。

    Returns:
        AuditEntry: 操作记录条目。
    """
    summary = row.get("change_summary")
    change_summary: dict[str, Any] = {}
    if isinstance(summary, dict):
        change_summary = {str(key): value for key, value in summary.items()}
    occurred = row.get("occurred_at")
    return AuditEntry(
        tenant_id=str(row.get("tenant_id", "")),
        actor_id=str(row.get("actor_id", "")),
        action=str(row.get("action", "")),
        resource_type=str(row.get("resource_type", "")),
        resource_id=str(row.get("resource_id", "") or ""),
        result=str(row.get("result", "success")),
        actor_role=str(row.get("actor_role", "") or ""),
        action_display_name=str(row.get("action_display_name", "") or ""),
        change_summary=change_summary,
        occurred_at=_to_timestamp(occurred),
        trace_id=str(row.get("trace_id", "") or ""),
    )


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


def _with_defaults(entry: AuditEntry) -> AuditEntry:
    """补齐发生时间与操作展示名。

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
        occurred_at=entry.occurred_at or _now_iso(),
        trace_id=entry.trace_id,
    )


class AuditService:
    """操作记录服务（业务层唯一入口）。

    Attributes:
        _settings: 配置实例。
        _recorder: 存储实现（内存 / PostgreSQL）。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        recorder: AuditRecorder | None = None,
    ) -> None:
        """初始化服务。

        Args:
            settings: 可选配置实例。
            recorder: 可选存储实现（测试注入）。
        """
        self._settings = settings or get_settings()
        self._recorder: AuditRecorder = recorder or get_audit_recorder(self._settings)

    @property
    def recorder(self) -> AuditRecorder:
        """当前存储实现（测试可据此断言「审计有记录」）。"""
        return self._recorder

    async def record(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str = "",
        result: str = "success",
        actor_role: str = "",
        change_summary: dict[str, Any] | None = None,
        trace_id: str = "",
    ) -> AuditEntry:
        """写入一条操作记录。

        Args:
            tenant_id: 租户 ID。
            actor_id: 操作人用户 ID。
            action: 操作编码。
            resource_type: 资源类型。
            resource_id: 资源 ID。
            result: 操作结果。
            actor_role: 操作人角色。
            change_summary: 变更摘要（业务字段，禁止密钥与正文）。
            trace_id: 链路追踪 ID。

        Returns:
            AuditEntry: 已写入的条目。
        """
        entry = AuditEntry(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            result=result,
            actor_role=actor_role,
            change_summary=dict(change_summary or {}),
            occurred_at=_now_iso(),
            trace_id=trace_id,
        )
        stored = await self._recorder.record(entry)
        logger.info(
            "audit_recorded",
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            tenant_id=tenant_id,
        )
        return stored

    async def recent(
        self, tenant_id: str, *, action: str = "", resource_id: str = "", limit: int = 50
    ) -> list[AuditEntry]:
        """读取最近的操作记录。

        Args:
            tenant_id: 租户 ID。
            action: 操作编码过滤。
            resource_id: 资源 ID 过滤。
            limit: 返回条数上限。

        Returns:
            list[AuditEntry]: 操作记录列表（最近在前）。
        """
        return await self._recorder.recent(
            tenant_id, action=action, resource_id=resource_id, limit=limit
        )


# ===========================================================================
# 进程级单例
# ===========================================================================
_memory_recorder: InMemoryAuditRecorder | None = None


def get_audit_recorder(settings: Settings | None = None) -> AuditRecorder:
    """获取操作记录存储。

    Args:
        settings: 可选配置实例。

    Returns:
        AuditRecorder: `business_store=postgres` 时为数据库实现，否则为内存实现。
    """
    global _memory_recorder

    conf = settings or get_settings()
    if conf.uses_postgres:
        return PostgresAuditRecorder(conf)
    if _memory_recorder is None:
        _memory_recorder = InMemoryAuditRecorder()
    return _memory_recorder


def reset_audit_recorder() -> None:
    """重置内存后端单例（测试隔离用）。"""
    global _memory_recorder
    if _memory_recorder is not None:
        _memory_recorder.clear()
    _memory_recorder = None


__all__: list[str] = [
    "ACTION_DEPT_CREATE",
    "ACTION_DEPT_DELETE",
    "ACTION_DEPT_UPDATE",
    "ACTION_DISPLAY_NAMES",
    "ACTION_DOC_DELETE",
    "ACTION_DOC_UPLOAD",
    "ACTION_GRANT_CREATE",
    "ACTION_GRANT_REVOKE",
    "ACTION_KB_CREATE",
    "ACTION_KB_DELETE",
    "ACTION_KB_UPDATE",
    "ACTION_MEMBER_BATCH",
    "ACTION_MEMBER_REMOVE",
    "ACTION_MEMBER_UPDATE",
    "ACTION_ORDER_CONFIRMED",
    "ACTION_ORDER_CREATED",
    "ACTION_ORDER_PAID",
    "ACTION_PAYMENT_CALLBACK_REJECTED",
    "ACTION_PLUGIN_CAPABILITY_POLICY",
    "ACTION_PLUGIN_INSTALL",
    "ACTION_RECONCILE_MISMATCH",
    "ACTION_ROLE_DELETE",
    "ACTION_SETTINGS_UPDATE",
    "ACTION_SUBSCRIPTION_UPDATE",
    "ACTION_TENANT_STATUS",
    "AUDIT_TABLE",
    "RESOURCE_DEPARTMENT",
    "RESOURCE_DOCUMENT",
    "RESOURCE_GRANT",
    "RESOURCE_KNOWLEDGE_BASE",
    "RESOURCE_MEMBER",
    "RESOURCE_ORDER",
    "RESOURCE_PAYMENT_CALLBACK",
    "RESOURCE_PLUGIN",
    "RESOURCE_ROLE",
    "RESOURCE_SETTINGS",
    "RESOURCE_SUBSCRIPTION",
    "RESOURCE_TENANT",
    "AuditEntry",
    "AuditRecorder",
    "AuditService",
    "InMemoryAuditRecorder",
    "PostgresAuditRecorder",
    "get_audit_recorder",
    "reset_audit_recorder",
]
