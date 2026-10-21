"""成员管理业务服务（阶段二 B08，架构文档 §4.4 ID8-ID10 / §4.7 U3 扩展）。

三条保护规则（缺口即越权，全部 fail-closed）：

1. **所有者保护**：任何针对 `owner` 的改角色/移出 → `14007`（含批量成员）；
2. **同级或更高级保护**：`rank(target) >= rank(actor)` → `14008`；操作自己 → `14008`；
3. **批量逐条失败不中断**：单条失败进 `failed[{member_id, reason}]`，
   成功条数进 `succeeded`（前端逐行提示，不整批回滚语义）。

移出成员时其创建的知识库 `owner_user_id` **转交组织管理员**，
数量由 `transferred_knowledge_base_count` 返回（PRD P2-ID-04 验收）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from app.business.audit_service import (
    ACTION_MEMBER_BATCH,
    ACTION_MEMBER_REMOVE,
    ACTION_MEMBER_UPDATE,
    RESOURCE_MEMBER,
    AuditService,
)
from app.business.policy_service import PolicyService, get_policy_service
from app.business.role_service import ROLE_KEY_OWNER, RoleService
from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.core.security import AuthContext
from app.db.repositories.knowledge_base_repo import (
    KnowledgeBaseStore,
    get_knowledge_base_store,
)
from app.db.repositories.org_repo import (
    MemberLink,
    OrgStore,
    get_org_store,
    normalize_department_id,
)
from app.db.repositories.role_repo import RoleStore, get_role_store
from app.schemas.common import build_page
from app.schemas.identity import ListMembersQuery, MemberItem, OrgRole
from app.schemas.identity_ext import (
    BatchMemberAction,
    BatchMemberData,
    BatchMemberFailure,
    BatchMemberRequest,
    MemberStatus,
    RemoveMemberData,
    UpdateMemberData,
    UpdateMemberRequest,
)

logger = get_logger(__name__)

#: 成员变更所需权限点（前端 `PERM.TEAM_UPDATE`）
TEAM_MEMBER_PERMISSION: Final[str] = "team:member:update"

#: 所有者不可变更的文案（14007）
MESSAGE_OWNER_PROTECTED: Final[str] = "不能移除企业所有者，请先转移所有权"

#: 同级/更高级/操作自己（14008）
MESSAGE_MODIFICATION_FORBIDDEN: Final[str] = "你没有权限修改该成员"

#: 角色标识 → 业务化角色枚举
_ROLE_ENUM_MAP: Final[dict[str, OrgRole]] = {
    "owner": OrgRole.OWNER,
    "org_admin": OrgRole.ADMIN,
    "admin": OrgRole.ADMIN,
    "member": OrgRole.MEMBER,
    "guest": OrgRole.GUEST,
}


def _now_iso() -> str:
    """当前 UTC 时间（ISO 8601）。

    Returns:
        str: 形如 `2026-11-02T09:00:00Z`。
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class MemberService:
    """成员管理业务服务（U3 列表 + ID8-ID10）。

    Attributes:
        _settings: 配置实例。
        _org: 组织架构存储。
        _roles: 角色存储。
        _bases: 知识库元数据存储（移出成员时转交所有权）。
        _audit: 操作记录服务。
        _policy: 权限点判定服务。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        org: OrgStore | None = None,
        roles: RoleStore | None = None,
        bases: KnowledgeBaseStore | None = None,
        audit: AuditService | None = None,
        policy: PolicyService | None = None,
    ) -> None:
        """初始化服务。

        Args:
            settings: 可选配置实例。
            org: 可选组织架构存储（测试注入）。
            roles: 可选角色存储（测试注入）。
            bases: 可选知识库存储（测试注入）。
            audit: 可选操作记录服务（测试注入）。
            policy: 可选权限判定服务（测试注入）。
        """
        self._settings = settings or get_settings()
        self._org: OrgStore = org or get_org_store(self._settings)
        self._roles: RoleStore = roles or get_role_store(self._settings)
        self._bases: KnowledgeBaseStore = bases or get_knowledge_base_store(self._settings)
        self._audit = audit or AuditService(self._settings)
        self._policy: PolicyService = policy or get_policy_service(self._settings)

    # ------------------------------------------------------------------
    # U3 成员列表（组织来源 = 业务库，D-13 第 2 条）
    # ------------------------------------------------------------------
    async def list_members(
        self, *, tenant_id: str, org_id: str, query: ListMembersQuery
    ) -> list[MemberItem]:
        """查询组织成员（业务库为权威源，展示名以用户 ID 兜底）。

        Args:
            tenant_id: 租户 ID。
            org_id: 组织 ID。
            query: 分页与过滤参数（角色/关键字/部门/状态）。

        Returns:
            list[MemberItem]: 过滤后的完整成员列表（分页由调用方切分）。
        """
        await self._roles.ensure_builtin_roles(tenant_id, org_id)
        user_ids = await self._collect_user_ids(tenant_id, org_id)
        role_keys = await self._role_keys(tenant_id, org_id)
        department_names = {
            record.department_id: record.name
            for record in await self._org.list_departments(tenant_id, org_id)
        }
        department_filter = (
            normalize_department_id(query.department_id) if query.department_id else ""
        )
        needle = query.keyword.strip().lower()

        items: list[MemberItem] = []
        for user_id in user_ids:
            links = await self._org.links_of_user(tenant_id, user_id)
            department_ids = [link.department_id for link in links]
            if department_filter and department_filter not in department_ids:
                continue
            role_key = role_keys.get(user_id, "")
            status = MemberStatus.ACTIVE.value if role_key else MemberStatus.INVITED.value
            if query.status and query.status != status:
                continue
            role_enum = _ROLE_ENUM_MAP.get(role_key, OrgRole.MEMBER)
            if query.role is not None and role_enum != query.role:
                continue
            if needle and needle not in user_id.lower():
                continue
            items.append(
                MemberItem(
                    user_id=user_id,
                    display_name=user_id,
                    email="",
                    role=role_enum,
                    joined_at=links[0].joined_at if links else "",
                    department_ids=department_ids,
                    department_names=[department_names.get(item, item) for item in department_ids],
                    status=status,
                )
            )
        items.sort(key=lambda item: item.user_id)
        return items

    async def _collect_user_ids(self, tenant_id: str, org_id: str) -> list[str]:
        """收集组织内全部成员用户 ID（部门关联 ∪ 角色分配）。

        Args:
            tenant_id: 租户 ID。
            org_id: 组织 ID。

        Returns:
            list[str]: 用户 ID 列表（去重升序）。
        """
        users = set(await self._org.user_ids(tenant_id, org_id))
        for record in await self._roles.list_roles(tenant_id, org_id):
            users.update(await self._roles.member_ids_of_role(tenant_id, record.role_id))
        return sorted(users)

    async def _role_keys(self, tenant_id: str, org_id: str) -> dict[str, str]:
        """构造 `user_id → role_key` 映射。

        Args:
            tenant_id: 租户 ID。
            org_id: 组织 ID。

        Returns:
            dict[str, str]: 角色映射。
        """
        mapping: dict[str, str] = {}
        for record in await self._roles.list_roles(tenant_id, org_id):
            for user_id in await self._roles.member_ids_of_role(tenant_id, record.role_id):
                mapping.setdefault(user_id, record.role_key)
        return mapping

    # ------------------------------------------------------------------
    # ID8 修改成员
    # ------------------------------------------------------------------
    async def update_member(
        self,
        *,
        tenant_id: str,
        org_id: str,
        member_id: str,
        request: UpdateMemberRequest,
        auth: AuthContext,
    ) -> UpdateMemberData:
        """变更成员角色 / 部门 / 主部门 / 在册状态。

        Args:
            tenant_id: 租户 ID。
            org_id: 组织 ID。
            member_id: 成员用户 ID。
            request: 变更请求。
            auth: 鉴权上下文。

        Returns:
            UpdateMemberData: 变更后的成员状态。

        Raises:
            ApiError: 用户不存在（14001）、所有者保护（14007）、
                同级或更高级保护（14008）、权限不足（20003）。
        """
        await self._policy.require_point(auth, TEAM_MEMBER_PERMISSION)
        await self._roles.ensure_builtin_roles(tenant_id, org_id)
        await self._assert_member_exists(tenant_id, org_id, member_id)

        before_role = await self._roles.role_key_of_user(tenant_id, member_id)
        self._assert_can_modify(actor=auth, target_user_id=member_id, target_role=before_role)
        if request.role == ROLE_KEY_OWNER and before_role != ROLE_KEY_OWNER:
            raise ApiError(
                ErrorCode.MEMBER_MODIFICATION_FORBIDDEN,
                message="不能把成员设置为企业所有者，请先转移所有权",
                internal_detail="assign owner forbidden",
            )

        if request.role is not None:
            role_id = await RoleService(self._settings, self._roles).resolve_role_id(
                tenant_id, org_id, request.role
            )
            if not role_id:
                raise ApiError(ErrorCode.ROLE_NOT_FOUND, internal_detail=f"role_key={request.role}")
            await self._roles.assign_role(
                tenant_id, org_id, member_id, role_id, assigned_by=auth.user_id
            )

        if request.department_ids is not None:
            await self._sync_departments(
                tenant_id=tenant_id,
                member_id=member_id,
                department_ids=request.department_ids,
                primary_department_id=request.primary_department_id,
            )
        elif request.primary_department_id:
            await self._set_primary(
                tenant_id=tenant_id,
                member_id=member_id,
                primary_department_id=request.primary_department_id,
            )

        after_role = await self._roles.role_key_of_user(tenant_id, member_id)
        links = await self._org.links_of_user(tenant_id, member_id)
        status = request.status.value if request.status is not None else MemberStatus.ACTIVE.value
        await self._audit.record(
            tenant_id=tenant_id,
            actor_id=auth.user_id,
            action=ACTION_MEMBER_UPDATE,
            resource_type=RESOURCE_MEMBER,
            resource_id=member_id,
            actor_role=auth.roles[0] if auth.roles else "",
            change_summary={
                "before": {
                    "role": before_role,
                    "department_ids": [link.department_id for link in links],
                },
                "after": {
                    "role": after_role,
                    "department_ids": [link.department_id for link in links],
                    "status": status,
                },
            },
        )
        self._policy.invalidate(tenant_id, member_id)
        logger.info("member_updated", member_id=member_id, tenant_id=tenant_id)
        return UpdateMemberData(
            member_id=member_id,
            role=after_role,
            department_ids=[link.department_id for link in links],
            status=status,
            updated_at=_now_iso(),
        )

    # ------------------------------------------------------------------
    # ID9 移出成员
    # ------------------------------------------------------------------
    async def remove_member(
        self, *, tenant_id: str, org_id: str, member_id: str, auth: AuthContext
    ) -> RemoveMemberData:
        """移出成员，并把其创建的知识库转交组织管理员。

        Args:
            tenant_id: 租户 ID。
            org_id: 组织 ID。
            member_id: 成员用户 ID。
            auth: 鉴权上下文。

        Returns:
            RemoveMemberData: 移出结果与转交库数量。

        Raises:
            ApiError: 用户不存在（14001）、所有者保护（14007）、同级保护（14008）。
        """
        await self._policy.require_point(auth, TEAM_MEMBER_PERMISSION)
        await self._roles.ensure_builtin_roles(tenant_id, org_id)
        await self._assert_member_exists(tenant_id, org_id, member_id)
        before_role = await self._roles.role_key_of_user(tenant_id, member_id)
        self._assert_can_modify(actor=auth, target_user_id=member_id, target_role=before_role)

        for link in await self._org.links_of_user(tenant_id, member_id):
            await self._org.unlink_member(tenant_id, link.department_id, member_id)
            await self._org.refresh_member_count(tenant_id, link.department_id)

        transferred = await self._bases.transfer_owner(
            tenant_id, from_user_id=member_id, to_user_id=auth.user_id
        )
        await self._audit.record(
            tenant_id=tenant_id,
            actor_id=auth.user_id,
            action=ACTION_MEMBER_REMOVE,
            resource_type=RESOURCE_MEMBER,
            resource_id=member_id,
            actor_role=auth.roles[0] if auth.roles else "",
            change_summary={
                "before": {"role": before_role, "member_id": member_id},
                "after": {"removed": True, "transferred_knowledge_base_count": transferred},
            },
        )
        self._policy.invalidate(tenant_id, member_id)
        logger.info(
            "member_removed",
            member_id=member_id,
            transferred=transferred,
            tenant_id=tenant_id,
        )
        return RemoveMemberData(
            member_id=member_id,
            removed=True,
            transferred_knowledge_base_count=transferred,
        )

    # ------------------------------------------------------------------
    # ID10 批量成员操作
    # ------------------------------------------------------------------
    async def batch(
        self,
        *,
        tenant_id: str,
        org_id: str,
        request: BatchMemberRequest,
        auth: AuthContext,
    ) -> BatchMemberData:
        """批量变更角色或移出成员（逐条失败不中断）。

        Args:
            tenant_id: 租户 ID。
            org_id: 组织 ID。
            request: 批量请求。
            auth: 鉴权上下文。

        Returns:
            BatchMemberData: 成功条数与失败清单。

        Raises:
            ApiError: 权限不足（20003）。
        """
        await self._policy.require_point(auth, TEAM_MEMBER_PERMISSION)
        succeeded = 0
        failures: list[BatchMemberFailure] = []
        for member_id in dict.fromkeys(request.member_ids):
            try:
                if request.action is BatchMemberAction.REMOVE:
                    await self.remove_member(
                        tenant_id=tenant_id, org_id=org_id, member_id=member_id, auth=auth
                    )
                else:
                    if not request.role:
                        raise ApiError(
                            ErrorCode.PARAM_INVALID,
                            message="请选择要变更的角色",
                            internal_detail="batch update_role without role",
                        )
                    await self.update_member(
                        tenant_id=tenant_id,
                        org_id=org_id,
                        member_id=member_id,
                        request=UpdateMemberRequest(role=request.role),
                        auth=auth,
                    )
                succeeded += 1
            except ApiError as exc:
                failures.append(BatchMemberFailure(member_id=member_id, reason=exc.message))
        await self._audit.record(
            tenant_id=tenant_id,
            actor_id=auth.user_id,
            action=ACTION_MEMBER_BATCH,
            resource_type=RESOURCE_MEMBER,
            resource_id=",".join(dict.fromkeys(request.member_ids)),
            actor_role=auth.roles[0] if auth.roles else "",
            result="success" if not failures else "failure",
            change_summary={
                "action": request.action.value,
                "role": request.role or "",
                "succeeded": succeeded,
                "failed": len(failures),
            },
        )
        logger.info(
            "member_batch_done",
            action=request.action.value,
            succeeded=succeeded,
            failed=len(failures),
            tenant_id=tenant_id,
        )
        return BatchMemberData(succeeded=succeeded, failed=failures)

    # ------------------------------------------------------------------
    # 保护规则
    # ------------------------------------------------------------------
    async def _assert_member_exists(self, tenant_id: str, org_id: str, member_id: str) -> None:
        """确认成员存在（部门关联或角色分配任一命中）。

        Args:
            tenant_id: 租户 ID。
            org_id: 组织 ID。
            member_id: 成员用户 ID。

        Raises:
            ApiError: 成员不存在（14001）。
        """
        if member_id in await self._collect_user_ids(tenant_id, org_id):
            return
        raise ApiError(ErrorCode.USER_NOT_FOUND, internal_detail=f"member: {member_id}")

    def _assert_can_modify(
        self, *, actor: AuthContext, target_user_id: str, target_role: str
    ) -> None:
        """校验「不得操作自己 / 不得操作同级或更高级 / 不得操作所有者」。

        Args:
            actor: 操作人鉴权上下文。
            target_user_id: 目标成员用户 ID。
            target_role: 目标成员角色标识。

        Raises:
            ApiError: 所有者被操作（14007）；自己或同级更高级（14008）。
        """
        if target_role == ROLE_KEY_OWNER:
            raise ApiError(
                ErrorCode.OWNER_PROTECTED,
                message=MESSAGE_OWNER_PROTECTED,
                internal_detail=f"owner protected: {target_user_id}",
            )
        if target_user_id == actor.user_id:
            raise ApiError(
                ErrorCode.MEMBER_MODIFICATION_FORBIDDEN,
                message=MESSAGE_MODIFICATION_FORBIDDEN,
                internal_detail="cannot modify self",
            )
        actor_rank = max((_RANK_OF.get(role, 1) for role in actor.roles), default=1)
        target_rank = _RANK_OF.get(target_role, 1)
        if target_rank >= actor_rank:
            raise ApiError(
                ErrorCode.MEMBER_MODIFICATION_FORBIDDEN,
                message=MESSAGE_MODIFICATION_FORBIDDEN,
                internal_detail=f"target_rank={target_rank} actor_rank={actor_rank}",
            )

    # ------------------------------------------------------------------
    # 部门同步
    # ------------------------------------------------------------------
    async def _sync_departments(
        self,
        *,
        tenant_id: str,
        member_id: str,
        department_ids: list[str],
        primary_department_id: str | None,
    ) -> None:
        """覆盖写成员的部门关联，并维护主部门唯一。

        Args:
            tenant_id: 租户 ID。
            member_id: 成员用户 ID。
            department_ids: 目标部门 ID 列表。
            primary_department_id: 主部门 ID（缺省取列表第一个）。

        Raises:
            ApiError: 部门不存在（14005）、主部门不在列表内（20001）。
        """
        targets = [normalize_department_id(item) for item in department_ids if item]
        primary = normalize_department_id(primary_department_id) if primary_department_id else ""
        if primary and primary not in targets:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message="主部门必须是所属部门之一，请检查后重试",
                internal_detail=f"primary={primary} targets={targets}",
            )
        if not primary and targets:
            primary = targets[0]
        for department_id in targets:
            record = await self._org.get_department(tenant_id, department_id)
            if record is None:
                raise ApiError(
                    ErrorCode.DEPARTMENT_NOT_FOUND,
                    internal_detail=f"department: {department_id}",
                )

        existing = {
            link.department_id for link in await self._org.links_of_user(tenant_id, member_id)
        }
        await self._org.clear_primary(tenant_id, member_id)
        for department_id in existing - set(targets):
            await self._org.unlink_member(tenant_id, department_id, member_id)
            await self._org.refresh_member_count(tenant_id, department_id)
        for department_id in targets:
            await self._org.link_member(
                MemberLink(tenant_id=tenant_id, department_id=department_id, user_id=member_id)
            )
            await self._org.refresh_member_count(tenant_id, department_id)
        if primary:
            await self._org.set_primary(tenant_id, primary, member_id)

    async def _set_primary(
        self, *, tenant_id: str, member_id: str, primary_department_id: str
    ) -> None:
        """只调整主部门（要求成员已在该部门内）。

        Args:
            tenant_id: 租户 ID。
            member_id: 成员用户 ID。
            primary_department_id: 主部门 ID。

        Raises:
            ApiError: 主部门不在成员所属部门内（20001）。
        """
        department_id = normalize_department_id(primary_department_id)
        links = await self._org.links_of_user(tenant_id, member_id)
        if department_id not in {link.department_id for link in links}:
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message="主部门必须是所属部门之一，请检查后重试",
                internal_detail=f"primary={department_id}",
            )
        await self._org.clear_primary(tenant_id, member_id)
        await self._org.set_primary(tenant_id, department_id, member_id)


#: 角色等级（与 `role_service._RANK` 同口径；此处独立常量避免跨模块私有依赖）
_RANK_OF: Final[dict[str, int]] = {
    "owner": 3,
    "org_admin": 2,
    "admin": 2,
    "member": 1,
    "guest": 0,
}


def paginate(items: list[MemberItem], query: ListMembersQuery) -> object:
    """按查询参数切分成员列表（U3 分页）。

    Args:
        items: 完整成员列表。
        query: 分页参数。

    Returns:
        object: `PageResult[MemberItem]`。
    """
    start = query.offset
    return build_page(
        items=items[start : start + query.page_size],
        total=len(items),
        page=query.page,
        page_size=query.page_size,
    )


__all__: list[str] = [
    "MESSAGE_MODIFICATION_FORBIDDEN",
    "MESSAGE_OWNER_PROTECTED",
    "TEAM_MEMBER_PERMISSION",
    "MemberService",
    "paginate",
]
