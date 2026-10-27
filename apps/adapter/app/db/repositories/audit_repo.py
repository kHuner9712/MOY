# ruff: noqa: S608 - 表名/列名均为模块常量，值一律命名参数绑定；无用户输入拼进 SQL。
"""操作记录查询仓储（阶段二 B08，架构文档 §4.4 U6 / §4.7）。

`audit_log` 是**业务事实**（可供客户在「操作记录」页查询），与技术日志职责不同。
本模块只负责**查询**：写入统一走 `business/audit_service.py`（唯一写入口，
不提供更新/删除入口）。**本模块不 import `app.business`**（审计 P1-4）：
条目类型与内存记录器已在 `db/repositories/audit_recorder.py`，动作常量在
`core/audit_actions.py`，依赖方向全部落在矩阵内。

U6 的多维筛选（架构文档 §4.4 要点）：

| 维度 | 字段 |
| --- | --- |
| 操作人 | `actor_id`（精确）/ `actor_keyword`（模糊） |
| 动作 | `action`（精确）/ `action_group`（前缀域，如 `department`） |
| 资源 | `resource_type` |
| 结果 | `result`（`success` / `failure`） |
| 时间 | `start_at` / `end_at`（跨度 > 90 天由服务层拒绝） |

双后端：内存实现读 `InMemoryAuditRecorder`（development/test），
PostgreSQL 实现走带索引的 SQL（`idx_audit_log_tenant_time` / `_actor` / `_action`）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

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
    ACTION_ORDER_CONFIRMED,
    ACTION_ORDER_CREATED,
    ACTION_ORDER_PAID,
    ACTION_PAYMENT_CALLBACK_REJECTED,
    ACTION_PLUGIN_CAPABILITY_GRANT,
    ACTION_PLUGIN_CAPABILITY_POLICY,
    ACTION_PLUGIN_INSTALL,
    ACTION_PLUGIN_STATE_CHANGE,
    ACTION_PLUGIN_UNINSTALL,
    ACTION_PLUGIN_VERSION_CHANGE,
    ACTION_QUOTA_EXHAUSTED_100,
    ACTION_QUOTA_WARN_80,
    ACTION_RECONCILE_MISMATCH,
    ACTION_ROLE_DELETE,
    ACTION_SETTINGS_UPDATE,
    ACTION_SUBSCRIPTION_UPDATE,
    ACTION_TENANT_STATUS,
    AUDIT_TABLE,
)
from app.core.config import Settings, get_settings
from app.db.repositories.audit_recorder import (
    AuditEntry,
    get_in_memory_audit_recorder,
)
from app.db.session import tenant_scope

#: 单次查询的最大时间跨度（天）：超过则由服务层返回 20001（架构文档 §4.4 要点）
MAX_QUERY_SPAN_DAYS: Final[int] = 90

#: 操作域 → 用户可见名称（U6 的 `action_group` 选项与 ID11 的分组展示共用）
ACTION_GROUP_NAMES: Final[dict[str, str]] = {
    "knowledge_base": "知识库",
    "document": "文档",
    "department": "部门",
    "member": "成员",
    "role": "角色",
    "billing": "计量与账单",
    "ops": "运营管理",
    "plugin": "插件与工作台",
}

#: ID11 动作目录（分组 → 动作清单），顺序即前端下拉顺序
ACTION_CATALOG: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("knowledge_base", (ACTION_KB_CREATE, ACTION_KB_UPDATE, ACTION_KB_DELETE)),
    ("knowledge_base", (ACTION_GRANT_CREATE, ACTION_GRANT_REVOKE)),
    ("document", (ACTION_DOC_UPLOAD, ACTION_DOC_DELETE)),
    ("department", (ACTION_DEPT_CREATE, ACTION_DEPT_UPDATE, ACTION_DEPT_DELETE)),
    ("member", (ACTION_MEMBER_UPDATE, ACTION_MEMBER_REMOVE, ACTION_MEMBER_BATCH)),
    ("role", (ACTION_ROLE_DELETE,)),
    # P4-11 ④：管理员通知类动作（按租户+周期幂等，见 usage_service）
    ("billing", (ACTION_QUOTA_WARN_80, ACTION_QUOTA_EXHAUSTED_100)),
    ("billing", (ACTION_RECONCILE_MISMATCH,)),
    (
        "billing",
        (
            ACTION_SUBSCRIPTION_UPDATE,
            ACTION_ORDER_CREATED,
            ACTION_ORDER_PAID,
            ACTION_PAYMENT_CALLBACK_REJECTED,
            ACTION_PLUGIN_CAPABILITY_POLICY,
            ACTION_PLUGIN_INSTALL,
        ),
    ),
    ("ops", (ACTION_TENANT_STATUS, ACTION_ORDER_CONFIRMED, ACTION_SETTINGS_UPDATE)),
    (
        "plugin",
        (
            ACTION_PLUGIN_INSTALL,
            ACTION_PLUGIN_STATE_CHANGE,
            ACTION_PLUGIN_UNINSTALL,
            ACTION_PLUGIN_VERSION_CHANGE,
            ACTION_PLUGIN_CAPABILITY_GRANT,
            ACTION_PLUGIN_CAPABILITY_POLICY,
        ),
    ),
)

#: 动作全集（去重保序）
ALL_ACTIONS: Final[tuple[str, ...]] = tuple(
    dict.fromkeys(action for _, actions in ACTION_CATALOG for action in actions)
)


def action_group_of(action: str) -> str:
    """取动作所属的域（`a.b` 的前缀）。

    Args:
        action: 动作编码。

    Returns:
        str: 域；无分隔符时返回动作本身。
    """
    return action.split(".", 1)[0] if action else ""


@dataclass(slots=True)
class AuditQuery:
    """操作记录查询条件。

    Attributes:
        actor_id: 操作人 ID（精确）。
        actor_keyword: 操作人关键字（模糊，匹配用户 ID）。
        action: 动作编码（精确）。
        action_group: 动作域（前缀匹配）。
        resource_type: 资源类型。
        result: 结果（`success` / `failure`）。
        start_at: 起始时间（ISO 8601，含）。
        end_at: 结束时间（ISO 8601，含）。
        page: 页码（从 1 开始）。
        page_size: 每页条数。
    """

    actor_id: str = ""
    actor_keyword: str = ""
    action: str = ""
    action_group: str = ""
    resource_type: str = ""
    result: str = ""
    start_at: str = ""
    end_at: str = ""
    page: int = 1
    page_size: int = 20

    @property
    def offset(self) -> int:
        """分页偏移量。"""
        return max(self.page - 1, 0) * self.page_size


@dataclass(slots=True)
class AuditQueryResult:
    """操作记录查询结果。

    Attributes:
        items: 当前页记录。
        total: 满足条件的总条数。
    """

    items: list[AuditEntry] = field(default_factory=list)
    total: int = 0


def _matches(entry: AuditEntry, query: AuditQuery) -> bool:
    """判断单条记录是否命中全部筛选条件（内存后端使用）。

    Args:
        entry: 操作记录。
        query: 查询条件。

    Returns:
        bool: 命中返回 True。
    """
    if query.actor_id and entry.actor_id != query.actor_id:
        return False
    if query.actor_keyword and query.actor_keyword.lower() not in entry.actor_id.lower():
        return False
    if query.action and entry.action != query.action:
        return False
    if query.action_group and action_group_of(entry.action) != query.action_group:
        return False
    if query.resource_type and entry.resource_type != query.resource_type:
        return False
    if query.result and entry.result != query.result:
        return False
    if query.start_at and entry.occurred_at < query.start_at:
        return False
    if query.end_at and entry.occurred_at > query.end_at:
        return False
    return True


async def query_audit_logs(
    settings: Settings | None, tenant_id: str, query: AuditQuery
) -> AuditQueryResult:
    """按条件分页查询操作记录。

    Args:
        settings: 可选配置实例。
        tenant_id: 租户 ID。
        query: 查询条件。

    Returns:
        AuditQueryResult: 当前页记录与总条数。
    """
    conf = settings or get_settings()
    if conf.uses_postgres:
        return await _query_postgres(conf, tenant_id, query)
    # 内存后端：**直接取数据侧单例**（审计 P1-4）。此前是
    # `get_audit_recorder(conf)` + `isinstance(..., InMemoryAuditRecorder)` 探测——
    # 那要求本模块 import 业务层的记录器与工厂，并把「后端选择」重复实现一遍；
    # 现在 PG / 内存的分支判据就是 `conf.uses_postgres` 一处，不再有探测式防御。
    recorder = get_in_memory_audit_recorder(conf)
    entries = await recorder.entries_for(tenant_id)
    matched = [entry for entry in entries if _matches(entry, query)]
    return AuditQueryResult(
        items=matched[query.offset : query.offset + query.page_size], total=len(matched)
    )


async def _query_postgres(
    settings: Settings, tenant_id: str, query: AuditQuery
) -> AuditQueryResult:
    """PostgreSQL 查询实现（条件全部走命名参数绑定）。

    Args:
        settings: 配置实例。
        tenant_id: 租户 ID。
        query: 查询条件。

    Returns:
        AuditQueryResult: 当前页记录与总条数。
    """
    conditions = ["tenant_id = :tenant_id"]
    parameters: dict[str, object] = {"tenant_id": tenant_id}
    if query.actor_id:
        conditions.append("actor_id = :actor_id")
        parameters["actor_id"] = query.actor_id
    if query.actor_keyword:
        conditions.append("actor_id ILIKE :actor_keyword")
        parameters["actor_keyword"] = f"%{query.actor_keyword}%"
    if query.action:
        conditions.append("action = :action")
        parameters["action"] = query.action
    if query.action_group:
        conditions.append("action LIKE :action_group")
        parameters["action_group"] = f"{query.action_group}.%"
    if query.resource_type:
        conditions.append("resource_type = :resource_type")
        parameters["resource_type"] = query.resource_type
    if query.result:
        conditions.append("result = :result")
        parameters["result"] = query.result
    if query.start_at:
        conditions.append("occurred_at >= CAST(CAST(:start_at AS text) AS timestamptz)")
        parameters["start_at"] = query.start_at
    if query.end_at:
        conditions.append("occurred_at <= CAST(CAST(:end_at AS text) AS timestamptz)")
        parameters["end_at"] = query.end_at
    where_clause = " AND ".join(conditions)

    async with tenant_scope(tenant_id, read_only=True, settings=settings) as uow:
        rows = await uow.fetch_all(
            f"""
            SELECT id, tenant_id, actor_id, action, resource_type, resource_id, result,
                   actor_role, action_display_name, change_summary, occurred_at, trace_id
            FROM {AUDIT_TABLE}
            WHERE {where_clause}
            ORDER BY occurred_at DESC, id DESC
            LIMIT :limit OFFSET :offset
            """,
            {**parameters, "limit": query.page_size, "offset": query.offset},
        )
        total = await uow.fetch_scalar(
            f"SELECT COUNT(*) FROM {AUDIT_TABLE} WHERE {where_clause}", parameters
        )
    return AuditQueryResult(items=[_row_to_entry(row) for row in rows], total=int(total or 0))


def _row_to_entry(row: object) -> AuditEntry:
    """把数据库行映射为操作记录条目。

    Args:
        row: 行映射。

    Returns:
        AuditEntry: 操作记录条目。
    """
    data = dict(row) if isinstance(row, dict) else {}
    summary = data.get("change_summary")
    change_summary = dict(summary) if isinstance(summary, dict) else {}
    occurred = data.get("occurred_at")
    return AuditEntry(
        tenant_id=str(data.get("tenant_id", "")),
        actor_id=str(data.get("actor_id", "")),
        action=str(data.get("action", "")),
        resource_type=str(data.get("resource_type", "")),
        resource_id=str(data.get("resource_id", "") or ""),
        result=str(data.get("result", "success")),
        actor_role=str(data.get("actor_role", "") or ""),
        action_display_name=str(data.get("action_display_name", "") or ""),
        change_summary=change_summary,
        occurred_at=_to_timestamp(occurred),
        trace_id=str(data.get("trace_id", "") or ""),
    )


def _to_timestamp(value: object) -> str:
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


def span_exceeds_limit(start_at: str, end_at: str) -> bool:
    """判断查询时间跨度是否超过 90 天。

    Args:
        start_at: 起始时间（ISO 8601；空串表示不限）。
        end_at: 结束时间（ISO 8601；空串表示不限）。

    Returns:
        bool: 两端都给出且跨度 > 90 天时返回 True。
    """
    if not start_at or not end_at:
        return False
    try:
        start = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(end_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    return (end - start).days > MAX_QUERY_SPAN_DAYS


@dataclass(frozen=True, slots=True)
class ActionGroupEntry:
    """动作分组条目（ID11 响应构造用）。

    Attributes:
        group: 分组键。
        group_name: 分组展示名。
        actions: `(动作编码, 展示名)` 列表。
    """

    group: str
    group_name: str
    actions: tuple[tuple[str, str], ...] = ()


def action_groups() -> list[ActionGroupEntry]:
    """构造 ID11 的动作目录（分组 → 动作 + 展示名）。

    Returns:
        list[ActionGroupEntry]: 分组列表（保持注册顺序）。
    """
    grouped: dict[str, list[tuple[str, str]]] = {}
    for group, actions in ACTION_CATALOG:
        bucket = grouped.setdefault(group, [])
        for action in actions:
            if any(item[0] == action for item in bucket):
                continue
            bucket.append((action, ACTION_DISPLAY_NAMES.get(action, action)))
    return [
        ActionGroupEntry(
            group=group,
            group_name=ACTION_GROUP_NAMES.get(group, group),
            actions=tuple(actions),
        )
        for group, actions in grouped.items()
    ]


__all__: list[str] = [
    "ACTION_CATALOG",
    "ACTION_GROUP_NAMES",
    "ALL_ACTIONS",
    "MAX_QUERY_SPAN_DAYS",
    "ActionGroupEntry",
    "AuditQuery",
    "AuditQueryResult",
    "action_group_of",
    "action_groups",
    "query_audit_logs",
    "span_exceeds_limit",
]
