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

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Final

from app.core.audit_actions import (
    ACTION_DEPT_CREATE,
    ACTION_DEPT_DELETE,
    ACTION_DEPT_UPDATE,
    ACTION_DISPLAY_NAMES,
    ACTION_DOC_DELETE,
    ACTION_DOC_UPLOAD,
    ACTION_GRANT_CREATE,
    ACTION_GRANT_REVOKE,
    ACTION_KB_CREATE,
    ACTION_KB_DELETE,
    ACTION_KB_UPDATE,
    ACTION_MEMBER_BATCH,
    ACTION_MEMBER_REMOVE,
    ACTION_MEMBER_UPDATE,
    ACTION_MEMORY_DELETE,
    ACTION_MEMORY_DELETE_ALL,
    ACTION_MEMORY_DELETE_CATEGORY,
    ACTION_MEMORY_SETTINGS,
    ACTION_ORDER_CONFIRMED,
    ACTION_ORDER_CREATED,
    ACTION_ORDER_PAID,
    ACTION_PAYMENT_CALLBACK_REJECTED,
    ACTION_PERMISSION_DENIED,
    ACTION_PLUGIN_CAPABILITY_GRANT,
    ACTION_PLUGIN_CAPABILITY_POLICY,
    ACTION_PLUGIN_INSTALL,
    ACTION_PLUGIN_STATE_CHANGE,
    ACTION_PLUGIN_UNINSTALL,
    ACTION_PLUGIN_VERSION_CHANGE,
    ACTION_RECONCILE_MISMATCH,
    ACTION_ROLE_DELETE,
    ACTION_SETTINGS_UPDATE,
    ACTION_SUBSCRIPTION_UPDATE,
    ACTION_TASK_FINISH,
    ACTION_TASK_RETRY,
    ACTION_TASK_START,
    ACTION_TENANT_DATA_DELETE,
    ACTION_TENANT_DATA_EXPORT,
    ACTION_TENANT_STATUS,
    AUDIT_TABLE,
)
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.repositories.audit_recorder import (
    AuditEntry,
    AuditRecorder,
    InMemoryAuditRecorder,
    get_in_memory_audit_recorder,
    now_iso,
    reset_in_memory_audit_recorder,
    with_defaults,
)
from app.db.session import platform_scope, tenant_scope
from app.db.tenant import PLATFORM_TENANT

logger = get_logger(__name__)

# ===========================================================================
# 常量（审计 P1-4：动作编码 / 展示名 / 表名已下沉 `app.core.audit_actions`）
#
# 为什么下沉：这些常量是**跨层共享的词汇表**——业务层写审计、仓储层
# （`db/repositories/audit_repo.py`）查审计与分组、前端展示动作名，全都用它们。
# 留在业务层，就会让仓储层为了拿一个常量而**反向依赖业务层**（P1-4 实测的
# `app.business` import 正是这一处）；动作清单改一个字，仓储跟着重新编译。
#
# 为什么在业务层继续 re-export：`audit_service` 是「审计」这件事的业务入口，
# 49 处调用方（含测试）都按 `from app.business.audit_service import ACTION_*`
# 取值。这里原样 re-export，等于把 import 路径的兼容面钉在业务层，
# 让常量搬家**不触碰任何调用方**（零改动下沉）。
# ===========================================================================

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

#: 资源类型：部门（B08）
RESOURCE_DEPARTMENT: Final[str] = "department"

#: 资源类型：成员（B08）
RESOURCE_MEMBER: Final[str] = "member"

#: 资源类型：角色（B08）
RESOURCE_ROLE: Final[str] = "role"

#: 资源类型：订阅（B09）
RESOURCE_SUBSCRIPTION: Final[str] = "subscription"

#: 资源类型：订单（B09）
RESOURCE_ORDER: Final[str] = "order"

#: 资源类型：企业（B10 运营后台）
RESOURCE_TENANT: Final[str] = "tenant"

#: 资源类型：系统配置（B10 运营后台）
RESOURCE_SETTINGS: Final[str] = "ops_settings"

#: 资源类型：支付回调（B09 安全加固）
RESOURCE_PAYMENT_CALLBACK: Final[str] = "payment_callback"

#: 资源类型：插件（阶段三 B17）
RESOURCE_PLUGIN: Final[str] = "plugin"


# ===========================================================================
# 记录器契约与内存实现（审计 P1-4：已下沉 `app/db/repositories/audit_recorder.py`）
#
# 为什么下沉：`InMemoryAuditRecorder` 是**存储实现**，而「操作记录查询仓储」
# （`db/repositories/audit_repo.py`）要读它 → 仓储层被迫反向 `import app.business`。
# 记录器与仓储同属数据侧，归 `db` 后方向变成 `db → db` / `db → core`（矩阵内）。
#
# 为什么在这里继续 re-export：`AuditEntry` / `AuditRecorder` / `InMemoryAuditRecorder`
# 是 49 处调用方（含测试）的既有 import 路径，re-export 保证**零改动下沉**。
#
# `PostgresAuditRecorder` **故意不走**：它属于业务写入入口的 PG 实现，要处理
# `tenant_scope` / `platform_scope` 的平台哨兵分流口径，随写入语义留在业务层。
# ===========================================================================


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

        payload = entry if entry.occurred_at else with_defaults(entry)
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

    async def exists(self, *, tenant_id: str, action: str, resource_id: str) -> bool:
        """该租户在本期该资源上是否已有这条审计记录（P4-11 ④ 的幂等判据）。

        为什么要"先查再写"而不是靠唯一索引挡：`audit_log` **没有**
        (tenant, action, resource_id) 唯一约束（历史数据里同动作多次出现是合法的），
        所以必须显式查重；这也让判据能断言"走了查重分支"（fake 返 True 时不写），
        而不是靠"写了再看条数"反推——后者证明不了那行代码被执行过。

        只在 `warn or exhausted` 时被调用，普通请求零额外查询。

        `start_at` 收窄到**本月初**：同一租户跨月会有多条同动作记录（每月一条），
        限定本期后扫描量稳定在 1~2 条，不必动 `AuditQuery` 的结构（它没有
        `resource_id` 过滤项，为一个调用点改查询结构与两个后端不划算）。

        Args:
            tenant_id: 租户 ID。
            action: 动作编码（`billing.quota_warn_80` / `billing.quota_exhausted_100`）。
            resource_id: 资源 ID（本期 `YYYY-MM`）。

        Returns:
            bool: 已有同 (租户, 动作, 本期) 的记录返回 True。
        """
        from app.db.repositories.audit_repo import AuditQuery, query_audit_logs

        result = await query_audit_logs(
            self._settings,
            tenant_id,
            AuditQuery(action=action, start_at=f"{resource_id}-01", page_size=50),
        )
        return any(item.resource_id == resource_id for item in result.items)

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
            occurred_at=now_iso(),
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
# 进程级单例（内存后端由 `db/repositories/audit_recorder.py` 持有）
# ===========================================================================


def get_audit_service(settings: Settings | None = None) -> AuditService:
    """获取操作记录服务（业务层唯一入口）。

    Args:
        settings: 可选配置实例。

    Returns:
        AuditService: 服务实例（每次新建，内部复用进程级 recorder）。
    """
    return AuditService(settings)


def get_audit_recorder(settings: Settings | None = None) -> AuditRecorder:
    """获取操作记录存储。

    单例所有权在数据侧（`db/repositories/audit_recorder.py`）：写入方与查询方
    （`audit_repo` 的 U6 分页）读的必须是**同一份**记录，否则「刚记的操作查不到」。
    本函数只做后端选择，内存分支直接委托。

    Args:
        settings: 可选配置实例。

    Returns:
        AuditRecorder: `business_store=postgres` 时为数据库实现，否则为内存实现。
    """
    conf = settings or get_settings()
    if conf.uses_postgres:
        return PostgresAuditRecorder(conf)
    return get_in_memory_audit_recorder(conf)


def reset_audit_recorder() -> None:
    """重置内存后端单例（测试隔离用，委托数据侧单例）。"""
    reset_in_memory_audit_recorder()


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
    "ACTION_MEMORY_DELETE",
    "ACTION_MEMORY_DELETE_ALL",
    "ACTION_MEMORY_DELETE_CATEGORY",
    "ACTION_MEMORY_SETTINGS",
    "ACTION_ORDER_CONFIRMED",
    "ACTION_ORDER_CREATED",
    "ACTION_ORDER_PAID",
    "ACTION_PAYMENT_CALLBACK_REJECTED",
    "ACTION_PERMISSION_DENIED",
    "ACTION_PLUGIN_CAPABILITY_GRANT",
    "ACTION_PLUGIN_CAPABILITY_POLICY",
    "ACTION_PLUGIN_INSTALL",
    "ACTION_PLUGIN_STATE_CHANGE",
    "ACTION_PLUGIN_UNINSTALL",
    "ACTION_PLUGIN_VERSION_CHANGE",
    "ACTION_RECONCILE_MISMATCH",
    "ACTION_ROLE_DELETE",
    "ACTION_SETTINGS_UPDATE",
    "ACTION_SUBSCRIPTION_UPDATE",
    "ACTION_TASK_FINISH",
    "ACTION_TASK_RETRY",
    "ACTION_TASK_START",
    "ACTION_TENANT_DATA_DELETE",
    "ACTION_TENANT_DATA_EXPORT",
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
    "get_audit_service",
    "reset_audit_recorder",
]
