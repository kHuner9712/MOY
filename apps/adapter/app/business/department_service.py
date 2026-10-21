"""部门树业务服务（阶段二 B08，架构文档 §4.4 ID1-ID4 / 2.6 D-13）。

**业务库是唯一权威源**（D-13）：部门树、负责人、主部门全部落在
`department` / `department_member`，Keycloak 只接收单向投影（`kc_projection`）。

五条硬规则（每条都有对应错误码，验收逐条可测）：

1. **最多 5 层**：建第 6 层 → `14006`（文案「组织层级最多 5 层，请调整后重试」）；
2. **不得移动到自身子树**：`new_parent.path` 以 `old_path` 开头 → `14006`
   （文案「不能把部门移动到自己的下级部门」）；
3. **移动后子树层级不得超限**：最深后代 `level + delta > 5` → `14006`；
4. **有成员或子部门不得删除** → `14006`（默认文案）；
5. **所有变更写 `audit_log` 且含前后值**（`change_summary` 存业务字段，不含正文）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from app.business.audit_service import (
    ACTION_DEPT_CREATE,
    ACTION_DEPT_DELETE,
    ACTION_DEPT_UPDATE,
    RESOURCE_DEPARTMENT,
    AuditService,
)
from app.business.policy_service import PolicyService, get_policy_service
from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.ids import new_resource_id
from app.core.logging import get_logger
from app.core.security import AuthContext
from app.db.repositories.org_repo import (
    DEPARTMENT_ID_PREFIX,
    MAX_DEPARTMENT_LEVEL,
    DepartmentRecord,
    OrgStore,
    get_org_store,
    normalize_department_id,
)
from app.schemas.identity_ext import (
    CreateDepartmentData,
    CreateDepartmentRequest,
    DeleteDepartmentData,
    DepartmentNode,
    DepartmentTreeData,
    UpdateDepartmentData,
    UpdateDepartmentRequest,
)

logger = get_logger(__name__)

#: 部门层级超限文案（同码多文案之一）
MESSAGE_LEVEL_EXCEEDED: Final[str] = "组织层级最多 5 层，请调整后重试"

#: 移动到自身子树文案（同码多文案之一）
MESSAGE_MOVE_INTO_SUBTREE: Final[str] = "不能把部门移动到自己的下级部门"

#: 删除有成员/子部门文案（同码多文案之一，与 `14006` 默认文案一致）
MESSAGE_DELETE_CONFLICT: Final[str] = "该部门下仍有成员或子部门，请先迁移后再删除"

#: 修改请求至少一项的文案
MESSAGE_EMPTY_PATCH: Final[str] = "请求内容不完整，请检查后重试"

#: 部门管理所需权限点（前端 `PERM.TEAM_STRUCT`）
TEAM_STRUCTURE_PERMISSION: Final[str] = "team:structure:manage"


def _now_iso() -> str:
    """当前 UTC 时间（ISO 8601）。

    Returns:
        str: 形如 `2026-11-02T09:00:00Z`。
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class DepartmentChanges:
    """部门变更的前后值（写 `audit_log.change_summary`）。

    Attributes:
        before: 变更前关键字段。
        after: 变更后关键字段。
    """

    before: dict[str, object]
    after: dict[str, object]

    def to_summary(self) -> dict[str, object]:
        """转换为变更摘要字典。

        Returns:
            dict[str, object]: `{"before": ..., "after": ...}`。
        """
        return {"before": dict(self.before), "after": dict(self.after)}


def _snapshot(record: DepartmentRecord | None) -> dict[str, object]:
    """取部门的关键字段快照（审计用，不含正文/密钥类字段）。

    Args:
        record: 部门记录。

    Returns:
        dict[str, object]: 快照。
    """
    if record is None:
        return {}
    return {
        "department_id": record.department_id,
        "name": record.name,
        "parent_id": record.parent_id,
        "level": record.level,
        "manager_user_id": record.manager_user_id,
    }


class DepartmentService:
    """部门树业务服务（ID1-ID4 的唯一业务入口）。

    Attributes:
        _settings: 配置实例。
        _store: 组织架构存储。
        _audit: 操作记录服务。
        _policy: 权限点判定服务。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        store: OrgStore | None = None,
        audit: AuditService | None = None,
        policy: PolicyService | None = None,
    ) -> None:
        """初始化服务。

        Args:
            settings: 可选配置实例。
            store: 可选组织架构存储（测试注入）。
            audit: 可选操作记录服务（测试注入）。
            policy: 可选权限判定服务（测试注入）。
        """
        self._settings = settings or get_settings()
        self._store: OrgStore = store or get_org_store(self._settings)
        self._audit = audit or AuditService(self._settings)
        self._policy: PolicyService = policy or get_policy_service(self._settings)

    @property
    def store(self) -> OrgStore:
        """当前组织架构存储（测试可据此断言）。"""
        return self._store

    # ------------------------------------------------------------------
    # ID1 部门树
    # ------------------------------------------------------------------
    async def tree(self, *, tenant_id: str, org_id: str, flat: bool = False) -> DepartmentTreeData:
        """查询部门树（登录即可读）。

        **不做 N+1**：一次取出组织内全部部门，`path_names` 由路径段 + 本地字典组装。

        Args:
            tenant_id: 租户 ID。
            org_id: 组织 ID。
            flat: 是否返回平铺数组。

        Returns:
            DepartmentTreeData: 部门列表。
        """
        records = await self._store.list_departments(tenant_id, org_id)
        names = {record.department_id: record.name for record in records}
        nodes = {record.department_id: self._to_node(record, names) for record in records}
        if flat:
            ordered = sorted(
                records, key=lambda item: (item.level, item.sort_order, item.department_id)
            )
            return DepartmentTreeData(departments=[nodes[item.department_id] for item in ordered])
        roots: list[DepartmentNode] = []
        for record in records:
            node = nodes[record.department_id]
            parent = nodes.get(record.parent_id)
            if parent is None:
                roots.append(node)
            else:
                parent.children.append(node)
        roots.sort(key=lambda item: (item.level, item.department_id))
        return DepartmentTreeData(departments=roots)

    def _to_node(self, record: DepartmentRecord, names: dict[str, str]) -> DepartmentNode:
        """把部门记录转换为树节点（含 `path_names`）。

        Args:
            record: 部门记录。
            names: `department_id → 名称`。

        Returns:
            DepartmentNode: 树节点。
        """
        segments = [item for item in record.path.split("/") if item]
        return DepartmentNode(
            department_id=record.department_id,
            name=record.name,
            parent_id=record.parent_id,
            path_names=[names.get(segment, segment) for segment in segments],
            level=record.level,
            manager_name=record.manager_user_id,
            member_count=record.member_count,
        )

    # ------------------------------------------------------------------
    # ID2 新建部门
    # ------------------------------------------------------------------
    async def create(
        self, *, tenant_id: str, org_id: str, request: CreateDepartmentRequest, auth: AuthContext
    ) -> CreateDepartmentData:
        """新建部门。

        Args:
            tenant_id: 租户 ID。
            org_id: 组织 ID。
            request: 新建请求。
            auth: 鉴权上下文。

        Returns:
            CreateDepartmentData: 新部门信息。

        Raises:
            ApiError: 上级部门不存在（14005）、层级超限（14006）、权限不足（20003）。
        """
        await self._policy.require_point(auth, TEAM_STRUCTURE_PERMISSION)
        parent: DepartmentRecord | None = None
        level = 1
        if request.parent_id:
            parent_id = normalize_department_id(request.parent_id)
            parent = await self._store.get_department(tenant_id, parent_id)
            if parent is None:
                raise ApiError(
                    ErrorCode.DEPARTMENT_NOT_FOUND,
                    internal_detail=f"parent not found: {parent_id}",
                )
            level = parent.level + 1
            if level > MAX_DEPARTMENT_LEVEL:
                raise ApiError(
                    ErrorCode.DEPARTMENT_STRUCTURE_CONFLICT,
                    message=MESSAGE_LEVEL_EXCEEDED,
                    internal_detail=f"level={level} limit={MAX_DEPARTMENT_LEVEL}",
                )

        department_id = new_resource_id(DEPARTMENT_ID_PREFIX)
        prefix = parent.path if parent is not None else "/"
        record = await self._store.create_department(
            DepartmentRecord(
                tenant_id=tenant_id,
                department_id=department_id,
                org_id=org_id,
                parent_id=parent.department_id if parent is not None else "",
                name=request.name,
                path=f"{prefix}{department_id}/",
                level=level,
            )
        )
        await self._record_audit(
            tenant_id=tenant_id,
            auth=auth,
            action=ACTION_DEPT_CREATE,
            resource_id=department_id,
            changes=DepartmentChanges(before={}, after=_snapshot(record)),
        )
        logger.info(
            "department_created",
            department_id=department_id,
            level=level,
            tenant_id=tenant_id,
        )
        return CreateDepartmentData(
            department_id=record.department_id,
            name=record.name,
            parent_id=record.parent_id,
            level=record.level,
        )

    # ------------------------------------------------------------------
    # ID3 修改 / 移动部门
    # ------------------------------------------------------------------
    async def update(
        self,
        *,
        tenant_id: str,
        department_id: str,
        request: UpdateDepartmentRequest,
        auth: AuthContext,
    ) -> UpdateDepartmentData:
        """修改部门名称 / 负责人，或移动部门（含整棵子树）。

        Args:
            tenant_id: 租户 ID。
            department_id: 部门 ID。
            request: 更新请求。
            auth: 鉴权上下文。

        Returns:
            UpdateDepartmentData: 更新后的部门信息。

        Raises:
            ApiError: 部门不存在（14005）、层级/移动冲突（14006）、参数非法（20001）。
        """
        await self._policy.require_point(auth, TEAM_STRUCTURE_PERMISSION)
        if request.name is None and request.parent_id is None and request.manager_id is None:
            raise ApiError(ErrorCode.PARAM_INVALID, message=MESSAGE_EMPTY_PATCH)

        target_id = normalize_department_id(department_id)
        current = await self._store.get_department(tenant_id, target_id)
        if current is None:
            raise ApiError(
                ErrorCode.DEPARTMENT_NOT_FOUND, internal_detail=f"not found: {target_id}"
            )
        before = _snapshot(current)

        updated = await self._store.update_department(
            tenant_id,
            target_id,
            name=request.name,
            manager_user_id=request.manager_id,
        )
        if updated is None:  # pragma: no cover - 并发删除窗口
            raise ApiError(ErrorCode.DEPARTMENT_NOT_FOUND, internal_detail="vanished")

        if request.parent_id is not None:
            updated = await self._move(
                tenant_id=tenant_id, record=updated, new_parent_id=request.parent_id
            )

        await self._record_audit(
            tenant_id=tenant_id,
            auth=auth,
            action=ACTION_DEPT_UPDATE,
            resource_id=target_id,
            changes=DepartmentChanges(before=before, after=_snapshot(updated)),
        )
        return UpdateDepartmentData(
            department_id=updated.department_id,
            name=updated.name,
            parent_id=updated.parent_id,
            level=updated.level,
            manager_name=updated.manager_user_id,
            updated_at=updated.updated_at or _now_iso(),
        )

    async def _move(
        self, *, tenant_id: str, record: DepartmentRecord, new_parent_id: str
    ) -> DepartmentRecord:
        """把部门（及其子树）移动到新的上级部门。

        Args:
            tenant_id: 租户 ID。
            record: 当前部门记录。
            new_parent_id: 新上级部门 ID（空串表示移动到根）。

        Returns:
            DepartmentRecord: 移动后的记录。

        Raises:
            ApiError: 新上级不存在（14005）、移动到自身子树（14006）、层级超限（14006）。
        """
        parent: DepartmentRecord | None = None
        if new_parent_id:
            parent_id = normalize_department_id(new_parent_id)
            parent = await self._store.get_department(tenant_id, parent_id)
            if parent is None:
                raise ApiError(
                    ErrorCode.DEPARTMENT_NOT_FOUND,
                    internal_detail=f"new parent not found: {parent_id}",
                )
            # 防「移动到自身子树」：新上级的路径只要以本部门路径开头即非法（含自身）
            if parent.path.startswith(record.path):
                raise ApiError(
                    ErrorCode.DEPARTMENT_STRUCTURE_CONFLICT,
                    message=MESSAGE_MOVE_INTO_SUBTREE,
                    internal_detail=f"{parent.path} under {record.path}",
                )

        new_level = (parent.level + 1) if parent is not None else 1
        level_delta = new_level - record.level
        new_prefix = (
            f"{parent.path}{record.department_id}/"
            if parent is not None
            else (f"/{record.department_id}/")
        )
        if level_delta > 0:
            deepest = await self._deepest_level(tenant_id, record)
            if deepest + level_delta > MAX_DEPARTMENT_LEVEL:
                raise ApiError(
                    ErrorCode.DEPARTMENT_STRUCTURE_CONFLICT,
                    message=MESSAGE_LEVEL_EXCEEDED,
                    internal_detail=f"deepest={deepest} delta={level_delta}",
                )

        await self._store.move_subtree(
            tenant_id, record.org_id, record.path, new_prefix, level_delta
        )
        updated = await self._store.update_department(
            tenant_id, record.department_id, parent_id=parent.department_id if parent else ""
        )
        moved = updated if updated is not None else record
        logger.info(
            "department_moved",
            department_id=record.department_id,
            new_parent=parent.department_id if parent else "",
            level_delta=level_delta,
            tenant_id=tenant_id,
        )
        return moved

    async def _deepest_level(self, tenant_id: str, record: DepartmentRecord) -> int:
        """取该部门子树中的最深层级。

        Args:
            tenant_id: 租户 ID。
            record: 部门记录。

        Returns:
            int: 最深层级（无子部门时为本部门层级）。
        """
        records = await self._store.list_departments(tenant_id, record.org_id)
        descendants = [item.level for item in records if item.path.startswith(record.path)]
        return max(descendants) if descendants else record.level

    # ------------------------------------------------------------------
    # ID4 删除部门
    # ------------------------------------------------------------------
    async def delete(
        self, *, tenant_id: str, department_id: str, auth: AuthContext
    ) -> DeleteDepartmentData:
        """删除部门（有成员或子部门时拒绝）。

        Args:
            tenant_id: 租户 ID。
            department_id: 部门 ID。
            auth: 鉴权上下文。

        Returns:
            DeleteDepartmentData: 删除结果。

        Raises:
            ApiError: 部门不存在（14005）、仍有成员或子部门（14006）、权限不足（20003）。
        """
        await self._policy.require_point(auth, TEAM_STRUCTURE_PERMISSION)
        target_id = normalize_department_id(department_id)
        current = await self._store.get_department(tenant_id, target_id)
        if current is None:
            raise ApiError(
                ErrorCode.DEPARTMENT_NOT_FOUND, internal_detail=f"not found: {target_id}"
            )
        children = await self._store.count_children(tenant_id, target_id)
        members = await self._store.links_of_department(tenant_id, target_id)
        if children > 0 or members:
            raise ApiError(
                ErrorCode.DEPARTMENT_STRUCTURE_CONFLICT,
                message=MESSAGE_DELETE_CONFLICT,
                internal_detail=f"children={children} members={len(members)}",
            )

        deleted = await self._store.soft_delete_department(tenant_id, target_id)
        if not deleted:  # pragma: no cover - 并发删除窗口
            raise ApiError(ErrorCode.DEPARTMENT_NOT_FOUND, internal_detail="delete failed")
        await self._record_audit(
            tenant_id=tenant_id,
            auth=auth,
            action=ACTION_DEPT_DELETE,
            resource_id=target_id,
            changes=DepartmentChanges(before=_snapshot(current), after={}),
        )
        logger.info("department_deleted", department_id=target_id, tenant_id=tenant_id)
        return DeleteDepartmentData(department_id=target_id, deleted=True)

    # ------------------------------------------------------------------
    # 内部：审计
    # ------------------------------------------------------------------
    async def _record_audit(
        self,
        *,
        tenant_id: str,
        auth: AuthContext,
        action: str,
        resource_id: str,
        changes: DepartmentChanges,
    ) -> None:
        """写入操作记录（含前后值）。

        Args:
            tenant_id: 租户 ID。
            auth: 鉴权上下文。
            action: 动作编码。
            resource_id: 资源 ID。
            changes: 前后值。
        """
        await self._audit.record(
            tenant_id=tenant_id,
            actor_id=auth.user_id,
            action=action,
            resource_type=RESOURCE_DEPARTMENT,
            resource_id=resource_id,
            actor_role=auth.roles[0] if auth.roles else "",
            change_summary=changes.to_summary(),
        )


__all__: list[str] = [
    "MESSAGE_DELETE_CONFLICT",
    "MESSAGE_LEVEL_EXCEEDED",
    "MESSAGE_MOVE_INTO_SUBTREE",
    "TEAM_STRUCTURE_PERMISSION",
    "DepartmentChanges",
    "DepartmentService",
]
