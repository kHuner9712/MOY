"""角色与权限点业务服务（阶段二 B08，架构文档 §4.4 ID5-ID7 / §4.8）。

三条硬规则：

1. **4 个内置角色不可删除**（`is_builtin=true`），自定义角色在被引用时也不可删除；
2. **角色被引用 → `20005`**，文案带成员数（裁决 #3：不新增码，符合 PRD 原文）；
3. **权限点目录是平台共享数据**（20 个：16 业务 + 4 运营），DB 与代码常量同源；
   等价性由 `test_catalog_matches_ddl_seed` 断言，避免「目录一套、判权另一套」。

改角色后判权 ≤5s 生效：`PolicyService` 的快照缓存 TTL 上限为 5s，
角色变更方调用 `invalidate()` 可做到下一请求即生效。
"""

from __future__ import annotations

from typing import Final

from app.business.audit_service import ACTION_ROLE_DELETE, RESOURCE_ROLE, AuditService
from app.business.policy_service import PolicyService, get_policy_service
from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.core.security import AuthContext
from app.db.repositories.role import (
    PermissionCatalogEntry,
    RoleRecord,
    RoleStore,
    get_role_store,
)
from app.schemas.identity_ext import (
    DeleteRoleData,
    PermissionCatalogData,
    PermissionGroup,
    PermissionItem,
    RoleItem,
    RoleListData,
)

logger = get_logger(__name__)

#: 角色管理所需权限点（前端 `PERM.TEAM_ROLE`）
TEAM_ROLE_PERMISSION: Final[str] = "team:role:manage"

#: 角色被引用时的文案模板（裁决 #3：码值 `20005` 不变，仅细分文案）
MESSAGE_ROLE_IN_USE: Final[str] = "该角色下还有 {count} 名成员，请先调整成员角色"

#: 内置角色不可删除的文案（同为 `20005`：状态冲突语义）
MESSAGE_ROLE_BUILTIN: Final[str] = "内置角色不可删除，请调整成员角色后使用自定义角色"


class RoleService:
    """角色与权限点业务服务。

    Attributes:
        _settings: 配置实例。
        _store: 角色存储。
        _audit: 操作记录服务。
        _policy: 权限点判定服务。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        store: RoleStore | None = None,
        audit: AuditService | None = None,
        policy: PolicyService | None = None,
    ) -> None:
        """初始化服务。

        Args:
            settings: 可选配置实例。
            store: 可选角色存储（测试注入）。
            audit: 可选操作记录服务（测试注入）。
            policy: 可选权限判定服务（测试注入）。
        """
        self._settings = settings or get_settings()
        self._store: RoleStore = store or get_role_store(self._settings)
        self._audit = audit or AuditService(self._settings)
        self._policy: PolicyService = policy or get_policy_service(self._settings)

    @property
    def store(self) -> RoleStore:
        """当前角色存储（测试可据此断言）。"""
        return self._store

    # ------------------------------------------------------------------
    # ID5 权限点目录（登录即可读）
    # ------------------------------------------------------------------
    async def permission_catalog(self, *, tenant_id: str) -> PermissionCatalogData:
        """查询权限点目录（按分组）。

        Args:
            tenant_id: 租户 ID。

        Returns:
            PermissionCatalogData: 分组后的权限点。
        """
        entries = await self._store.permission_catalog(tenant_id)
        return PermissionCatalogData(groups=_group_permissions(entries))

    # ------------------------------------------------------------------
    # ID6 角色列表
    # ------------------------------------------------------------------
    async def list_roles(self, *, tenant_id: str, org_id: str, auth: AuthContext) -> RoleListData:
        """查询组织内角色（含权限点与成员数）。

        Args:
            tenant_id: 租户 ID。
            org_id: 组织 ID。
            auth: 鉴权上下文。

        Returns:
            RoleListData: 角色列表。

        Raises:
            ApiError: 权限不足（20003）。
        """
        await self._policy.require_point(auth, TEAM_ROLE_PERMISSION)
        await self._store.ensure_builtin_roles(tenant_id, org_id)
        records = await self._store.list_roles(tenant_id, org_id)
        return RoleListData(
            items=[
                RoleItem(
                    role_id=record.role_id,
                    role_key=record.role_key,
                    display_name=record.display_name,
                    is_builtin=record.is_builtin,
                    member_count=record.member_count,
                    permissions=sorted(record.permissions),
                )
                for record in records
            ]
        )

    # ------------------------------------------------------------------
    # ID7 删除角色
    # ------------------------------------------------------------------
    async def delete_role(
        self, *, tenant_id: str, org_id: str, role_id: str, auth: AuthContext
    ) -> DeleteRoleData:
        """删除角色（内置或仍被引用时拒绝）。

        Args:
            tenant_id: 租户 ID。
            org_id: 组织 ID。
            role_id: 角色 ID。
            auth: 鉴权上下文。

        Returns:
            DeleteRoleData: 删除结果。

        Raises:
            ApiError: 角色不存在（14003）、仍被引用/内置（20005）、权限不足（20003）。
        """
        await self._policy.require_point(auth, TEAM_ROLE_PERMISSION)
        await self._store.ensure_builtin_roles(tenant_id, org_id)
        record = await self._store.get_role(tenant_id, role_id)
        if record is None:
            raise ApiError(ErrorCode.ROLE_NOT_FOUND, internal_detail=f"role: {role_id}")
        if record.is_builtin:
            raise ApiError(
                ErrorCode.CONFLICT,
                message=MESSAGE_ROLE_BUILTIN,
                internal_detail=f"builtin role: {role_id}",
            )
        member_count = await self._store.count_members(tenant_id, role_id)
        if member_count > 0:
            raise ApiError(
                ErrorCode.CONFLICT,
                message=MESSAGE_ROLE_IN_USE.format(count=member_count),
                internal_detail=f"role={role_id} members={member_count}",
            )

        deleted = await self._store.delete_role(tenant_id, role_id)
        if not deleted:  # pragma: no cover - 并发删除窗口
            raise ApiError(ErrorCode.ROLE_NOT_FOUND, internal_detail="delete failed")
        await self._audit.record(
            tenant_id=tenant_id,
            actor_id=auth.user_id,
            action=ACTION_ROLE_DELETE,
            resource_type=RESOURCE_ROLE,
            resource_id=role_id,
            actor_role=auth.roles[0] if auth.roles else "",
            change_summary={
                "before": {
                    "role_id": record.role_id,
                    "role_key": record.role_key,
                    "display_name": record.display_name,
                },
                "after": {},
            },
        )
        self._policy.invalidate(tenant_id)
        logger.info("role_deleted", role_id=role_id, tenant_id=tenant_id)
        return DeleteRoleData(role_id=role_id, deleted=True)

    # ------------------------------------------------------------------
    # 角色解析（成员服务复用）
    # ------------------------------------------------------------------
    async def resolve_role_id(self, tenant_id: str, org_id: str, role_key: str) -> str:
        """按角色标识解析角色 ID（内置或自定义）。

        Args:
            tenant_id: 租户 ID。
            org_id: 组织 ID。
            role_key: 角色标识。

        Returns:
            str: 角色 ID；未找到返回空串。
        """
        await self._store.ensure_builtin_roles(tenant_id, org_id)
        records = await self._store.list_roles(tenant_id, org_id)
        for record in records:
            if record.role_key == role_key or record.role_id == role_key:
                return record.role_id
        return ""

    async def role_rank(self, tenant_id: str, org_id: str, role_key: str) -> int:
        """取角色的权限等级（用于「同级或更高级」保护判定）。

        Args:
            tenant_id: 租户 ID。
            org_id: 组织 ID。
            role_key: 角色标识。

        Returns:
            int: 等级（owner=3 / org_admin=2 / member=1 / guest=0 / 自定义=1）。
        """
        del tenant_id, org_id
        return _RANK.get(role_key, 1)

    async def ensure_builtin(self, tenant_id: str, org_id: str) -> list[RoleRecord]:
        """播种内置角色（幂等）。

        Args:
            tenant_id: 租户 ID。
            org_id: 组织 ID。

        Returns:
            list[RoleRecord]: 内置角色列表。
        """
        return await self._store.ensure_builtin_roles(tenant_id, org_id)


#: 角色等级（数值越大权限越高；自定义角色与普通成员同级）
_RANK: Final[dict[str, int]] = {
    "owner": 3,
    "org_admin": 2,
    "admin": 2,
    "member": 1,
    "guest": 0,
}

#: 所有者角色标识
ROLE_KEY_OWNER: Final[str] = "owner"


def _group_permissions(entries: list[PermissionCatalogEntry]) -> list[PermissionGroup]:
    """把权限点目录按分组聚合（保持 `sort_order` 顺序）。

    Args:
        entries: 目录条目（已按 sort_order 排序）。

    Returns:
        list[PermissionGroup]: 分组列表。
    """
    grouped: dict[str, PermissionGroup] = {}
    for entry in entries:
        group = grouped.get(entry.group_key)
        if group is None:
            group = PermissionGroup(
                group=entry.group_key, group_name=entry.group_name, permissions=[]
            )
            grouped[entry.group_key] = group
        group.permissions.append(
            PermissionItem(
                permission=entry.permission,
                display_name=entry.display_name,
                description=entry.description,
            )
        )
    return list(grouped.values())


__all__: list[str] = [
    "MESSAGE_ROLE_BUILTIN",
    "MESSAGE_ROLE_IN_USE",
    "ROLE_KEY_OWNER",
    "TEAM_ROLE_PERMISSION",
    "RoleService",
]
