"""用户权限服务协议转换（架构文档 3.5 / 3.6 类图 `IdentityService`）。

设计原则（架构文档 3.5.1）：适配层仅做**令牌校验与请求转发**，
用户/组织/角色权威数据在身份底座，权限策略判断在业务能力层，
适配层提供统一校验出口（FLOW-05：业务代码不得自行判权）。

转换职责（架构文档 3.5.4）：
- 身份底座 `firstName`/`lastName`/`email` → 自研 `display_name`/`email`；
- 身份底座 `realm_access.roles` → 自研权限点（`resource:action` 命名）；
- 身份底座 `active`/`exp` → 自研 `allowed`；
- 租户与套餐信息取自业务库，适配层只做组装转发，**不做额度计算**。

T01 阶段说明：返回业务化 mock 结果，不产生任何真实身份底座请求。
"""

from __future__ import annotations

from typing import Any, Final

from app.business.audit_export_service import AuditExportPlan, AuditExportRequest
from app.clients.keycloak_client import KeycloakClient, get_keycloak_client
from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.core.security import AuthContext, require_permission
from app.schemas.common import PageResult, build_page
from app.schemas.identity import (
    AuditLogItem,
    AuditLogQueryRequest,
    InvitationData,
    ListMembersQuery,
    MemberItem,
    OrganizationInfo,
    OrgRole,
    ProfileData,
    RoleData,
    TenantInfo,
    UpsertRoleRequest,
    VerifyData,
    VerifyRequest,
)

logger = get_logger(__name__)

#: 自研权限点命名规范（架构文档 3.5.2）：`resource:action` 或 `resource:sub_resource:action`
PERMISSION_PATTERN: Final[str] = ":"

#: 权限点允许的最大层级数（资源 : 子资源 : 动作）
PERMISSION_MAX_PARTS: Final[int] = 3

#: 操作记录查询所需权限点（前端 `PERM.AUDIT_READ`）
AUDIT_READ_PERMISSION: Final[str] = "audit:log:read"

#: 操作记录查询时间跨度超限文案（同码 20001 的细分文案）
MESSAGE_AUDIT_SPAN_EXCEEDED: Final[str] = "查询时间范围最长 90 天，请缩小范围"

#: 角色标识 → 业务化角色枚举映射
_ROLE_MAP: Final[dict[str, OrgRole]] = {
    "owner": OrgRole.OWNER,
    "org_admin": OrgRole.ADMIN,
    "admin": OrgRole.ADMIN,
    "member": OrgRole.MEMBER,
    "guest": OrgRole.GUEST,
}


def map_role(raw_role: str) -> OrgRole:
    """将身份底座角色映射为自研业务化角色。

    Args:
        raw_role: 身份底座原生角色名。

    Returns:
        OrgRole: 自研角色枚举；未识别时按 `member` 处理。
    """
    return _ROLE_MAP.get(raw_role, OrgRole.MEMBER)


class IdentityService:
    """用户权限服务。

    Attributes:
        _settings: 配置实例。
        _keycloak: 身份底座客户端。
    """

    def __init__(
        self,
        settings: Settings | None = None,
        keycloak: KeycloakClient | None = None,
    ) -> None:
        """初始化服务。

        Args:
            settings: 可选配置实例。
            keycloak: 可选身份底座客户端。
        """
        self._settings = settings or get_settings()
        self._keycloak = keycloak or get_keycloak_client()

    # ------------------------------------------------------------------
    # U1 令牌与权限点校验
    # ------------------------------------------------------------------
    def verify(self, request: VerifyRequest, auth: AuthContext) -> VerifyData:
        """令牌与权限点校验（内部高频调用）。

        Args:
            request: 校验请求。
            auth: 鉴权上下文。

        Returns:
            VerifyData: 校验结论与角色列表。

        Raises:
            ApiError: 权限点格式非法（14004 未注册）、权限不足（20003）。
        """
        self._validate_permission_point(request.permission)
        allowed = True
        try:
            require_permission(auth, request.permission)
        except ApiError:
            allowed = False
        logger.info(
            "identity_verify",
            permission=request.permission,
            allowed=allowed,
            tenant_id=auth.tenant_id,
        )
        return VerifyData(
            allowed=allowed,
            user_id=auth.user_id,
            tenant_id=auth.tenant_id,
            roles=list(auth.roles),
        )

    @staticmethod
    def _validate_permission_point(permission: str) -> None:
        """校验权限点命名规范。

        合法形态为 `resource:action`（如 `team:read`）或
        `resource:sub_resource:action`（如 `knowledge:document:upload`）。

        Args:
            permission: 权限点字符串。

        Raises:
            ApiError: 命名不规范视为未注册（14004）。
        """
        parts = permission.split(PERMISSION_PATTERN)
        if not PERMISSION_MAX_PARTS >= len(parts) >= 2:
            raise ApiError(
                ErrorCode.PERMISSION_NOT_DEFINED,
                internal_detail=f"permission not registered: {permission}",
            )
        if not all(part.strip() for part in parts):
            raise ApiError(
                ErrorCode.PERMISSION_NOT_DEFINED,
                internal_detail=f"permission not registered: {permission}",
            )

    # ------------------------------------------------------------------
    # U2 当前用户信息
    # ------------------------------------------------------------------
    async def get_profile(self, auth: AuthContext) -> ProfileData:
        """查询当前登录用户信息。

        Args:
            auth: 鉴权上下文。

        Returns:
            ProfileData: 自研用户资料（租户与套餐信息取自业务库，适配层只组装转发）。

        Raises:
            ApiError: 用户不存在（14001）。
        """
        if self._settings.mock_mode:
            logger.info("identity_profile_stub", tenant_id=auth.tenant_id)
            return ProfileData(
                user_id=auth.user_id,
                display_name="演示用户",
                email=auth.email,
                tenant=TenantInfo(tenant_id=auth.tenant_id, name="演示租户", plan="professional"),
                organizations=[],
            )

        payload = await self._keycloak.get_user(auth.user_id)
        return self._transform_profile(auth, payload)

    def _transform_profile(self, auth: AuthContext, payload: dict[str, Any]) -> ProfileData:
        """将身份底座原生用户对象转换为自研用户资料。

        Args:
            auth: 鉴权上下文。
            payload: 身份底座原生用户对象。

        Returns:
            ProfileData: 自研用户资料。

        Raises:
            ApiError: 用户不存在（14001）。
        """
        user_id = str(payload.get("id", ""))
        if not user_id:
            raise ApiError(ErrorCode.USER_NOT_FOUND, internal_detail="empty user id from idp")
        attributes = payload.get("attributes", {})
        attributes = attributes if isinstance(attributes, dict) else {}
        tenant_name = (
            str(attributes.get("tenant_name", [""])[0] or "")
            if attributes.get("tenant_name")
            else ""
        )
        plan = str(attributes.get("plan", [""])[0] or "") if attributes.get("plan") else ""
        first = str(payload.get("firstName", "") or "")
        last = str(payload.get("lastName", "") or "")
        return ProfileData(
            user_id=user_id,
            display_name=f"{first}{last}".strip() or str(payload.get("username", "")),
            email=str(payload.get("email", "")),
            tenant=TenantInfo(tenant_id=auth.tenant_id, name=tenant_name, plan=plan),
            organizations=[],
        )

    # ------------------------------------------------------------------
    # U3 组织成员列表
    # ------------------------------------------------------------------
    async def list_members(
        self, org_id: str, query: ListMembersQuery, auth: AuthContext
    ) -> PageResult[MemberItem]:
        """查询组织成员列表。

        **D-13 第 2 条**：组织来源改为业务库（`department_member` ∪ `member_role`），
        不再读身份底座 group —— 否则「部门调整后 U3 看不到变化」，
        且判权会退化为读 KC group。展示名以用户 ID 兜底（身份底座不提供批量姓名接口）。

        分页下推：无部门过滤时只对当前页批量读取部门关联（见
        `MemberService.list_members_page`）。

        Args:
            org_id: 组织 ID。
            query: 分页与过滤参数（角色 / 关键字 / 部门 / 在册状态）。
            auth: 鉴权上下文。

        Returns:
            PageResult[MemberItem]: 分页结果。

        Raises:
            ApiError: 组织不存在（14002）。
        """
        if not org_id:
            raise ApiError(ErrorCode.ORGANIZATION_NOT_FOUND, internal_detail="empty org_id")
        from app.business.member_service import MemberService

        service = MemberService(self._settings)
        page = await service.list_members_page(tenant_id=auth.tenant_id, org_id=org_id, query=query)
        logger.info(
            "identity_members_listed",
            org_id=org_id,
            total=page.meta.total,
            tenant_id=auth.tenant_id,
        )
        return page

    @staticmethod
    def _transform_member(payload: dict[str, Any]) -> MemberItem:
        """将身份底座原生成员对象转换为自研成员条目。

        Args:
            payload: 身份底座原生成员对象。

        Returns:
            MemberItem: 自研成员条目。
        """
        attributes = payload.get("attributes", {})
        attributes = attributes if isinstance(attributes, dict) else {}
        raw_role = ""
        if attributes.get("org_role"):
            raw_role = str(attributes["org_role"][0])
        first = str(payload.get("firstName", "") or "")
        last = str(payload.get("lastName", "") or "")
        return MemberItem(
            user_id=str(payload.get("id", "")),
            display_name=f"{first}{last}".strip() or str(payload.get("username", "")),
            email=str(payload.get("email", "")),
            role=map_role(raw_role),
            joined_at=str(payload.get("createdTimestamp", "")),
        )

    # ------------------------------------------------------------------
    # U4 邀请成员
    # ------------------------------------------------------------------
    async def invite_member(
        self, org_id: str, email: str, role: OrgRole, message: str, auth: AuthContext
    ) -> InvitationData:
        """邀请成员加入组织。

        适配层扩展能力（身份底座无原生邀请流）：生成邀请令牌 → 建用户 → 落审计。
        邀请令牌由业务能力层签发，适配层仅做转发。

        **席位约束（PRD P2-BL-02 验收 4 / QA-P2-W3-02）**：邀请会占用 1 个席位，
        席位不足时返回 `15003`，不得「先邀请、后失败」。判据与 BL3/BL4 共用
        `SubscriptionService.ensure_seat_available()`（唯一出口，避免第三套口径）。

        Args:
            org_id: 组织 ID。
            email: 被邀请人邮箱。
            role: 分配角色。
            message: 邀请附言。
            auth: 鉴权上下文。

        Returns:
            InvitationData: 邀请记录。

        Raises:
            ApiError: 组织不存在（14002）、席位不足（15003）。
        """
        if not org_id:
            raise ApiError(ErrorCode.ORGANIZATION_NOT_FOUND, internal_detail="empty org_id")
        await self._ensure_invite_seat(auth.tenant_id)
        logger.info(
            "identity_member_invited",
            org_id=org_id,
            role=role.value,
            tenant_id=auth.tenant_id,
            has_message=bool(message),
        )
        return InvitationData(
            invitation_id=f"inv_{org_id}",
            org_id=org_id,
            email=email,
            role=role,
            status="pending",
            expires_at="",
        )

    async def _ensure_invite_seat(self, tenant_id: str) -> None:
        """校验邀请后席位是否够用（不足 → `15003`）。

        口径：`在册成员数 + 1（本次邀请）+ 待接受邀请数 > seats_total` → 拒绝。
        当前邀请**没有持久化**（无 invitation 表，`invitation_id` 为现编），
        因此「待接受邀请数」恒为 0；邀请表落地后把该值加到 `extra_reserved`
        即可，调用方签名不变（见 `ensure_seat_available` 的文档）。

        Args:
            tenant_id: 租户 ID。

        Raises:
            ApiError: 席位不足（15003）。
        """
        from app.business.subscription_service import get_subscription_service

        subscriptions = get_subscription_service(self._settings)
        current = await subscriptions.current(tenant_id=tenant_id)
        await subscriptions.ensure_seat_available(
            tenant_id=tenant_id,
            target_seats=current.seats_total,
            extra_reserved=1,
        )

    # ------------------------------------------------------------------
    # U5 创建/分配角色
    # ------------------------------------------------------------------
    async def upsert_role(
        self, org_id: str, request: UpsertRoleRequest, auth: AuthContext
    ) -> RoleData:
        """创建或分配角色。

        Args:
            org_id: 组织 ID。
            request: 角色请求。
            auth: 鉴权上下文。

        Returns:
            RoleData: 角色信息。

        Raises:
            ApiError: 组织不存在（14002）、权限点未注册（14004）。
        """
        if not org_id:
            raise ApiError(ErrorCode.ORGANIZATION_NOT_FOUND, internal_detail="empty org_id")
        for permission in request.permissions:
            self._validate_permission_point(permission)

        if not self._settings.mock_mode:
            await self._keycloak.create_role(request.role_key, request.display_name)

        logger.info(
            "identity_role_upserted",
            org_id=org_id,
            role_key=request.role_key,
            permission_count=len(request.permissions),
            tenant_id=auth.tenant_id,
        )
        return RoleData(
            role_id=f"role_{request.role_key}",
            role_key=request.role_key,
            display_name=request.display_name,
            permissions=list(request.permissions),
            member_count=len(request.members),
        )

    # ------------------------------------------------------------------
    # U6 审计日志查询
    # ------------------------------------------------------------------
    async def query_audit_logs(self, request: AuditLogQueryRequest, auth: AuthContext) -> Any:
        """查询操作记录（多维筛选 + 分页）。

        判权走 `PolicyService`（统一出口，fail-closed）：无 `audit:log:read` 权限
        一律 `20003`。时间跨度 > 90 天返回 `20001`（架构文档 §4.4 要点）。

        Args:
            request: 查询请求。
            auth: 鉴权上下文。

        Returns:
            Any: `PageResult[AuditLogItem]` 分页结果。

        Raises:
            ApiError: 权限不足（20003）、时间跨度超限（20001）。
        """
        from app.business.policy_service import get_policy_service
        from app.db.repositories.audit_repo import AuditQuery, query_audit_logs, span_exceeds_limit

        await get_policy_service(self._settings).require_point(auth, AUDIT_READ_PERMISSION)
        if span_exceeds_limit(request.start_at, request.end_at):
            raise ApiError(
                ErrorCode.PARAM_INVALID,
                message=MESSAGE_AUDIT_SPAN_EXCEEDED,
                internal_detail=f"{request.start_at}..{request.end_at}",
            )
        result = await query_audit_logs(
            self._settings,
            auth.tenant_id,
            AuditQuery(
                actor_id=request.actor_id,
                actor_keyword=request.actor_keyword,
                action=request.action,
                action_group=request.action_group,
                resource_type=request.resource_type,
                result=request.result,
                start_at=request.start_at,
                end_at=request.end_at,
                page=request.page,
                page_size=request.page_size,
            ),
        )
        logger.info(
            "identity_audit_queried",
            total=result.total,
            action=request.action,
            action_group=request.action_group,
            tenant_id=auth.tenant_id,
        )
        return build_page(
            items=[
                AuditLogItem(
                    log_id=f"{entry.occurred_at}#{index}",
                    actor_id=entry.actor_id,
                    action=entry.action,
                    resource_type=entry.resource_type,
                    resource_id=entry.resource_id,
                    result=entry.result,
                    occurred_at=entry.occurred_at,
                    actor_role=entry.actor_role,
                    ip_masked="",
                    action_display_name=entry.display_name,
                    change_summary=dict(entry.change_summary),
                )
                for index, entry in enumerate(result.items, start=request.offset)
            ],
            total=result.total,
            page=request.page,
            page_size=request.page_size,
        )

    async def export_audit_logs(
        self, request: AuditExportRequest, auth: AuthContext
    ) -> AuditExportPlan:
        """校验导出权限与租户边界，交给业务层做范围/上限判定与留痕。

        与 U6 共用同一个权限点（`audit:log:read`）。**平台令牌一律拒绝**：
        平台不是租户，`__platform__` 行不属于任何客户，允许平台"按租户导出"
        就等于把跨租户读取能力开了一个口子。

        Args:
            request: 导出请求（`tenant_id` 会被令牌覆盖，不信任入参）。
            auth: 鉴权上下文。

        Returns:
            AuditExportPlan: 已校验并已留痕的导出计划。

        Raises:
            ApiError: 权限不足（20003）、平台令牌（20003）。
        """
        from dataclasses import replace

        from app.business.audit_export_service import get_audit_export_service
        from app.business.policy_service import get_policy_service
        from app.core.security import is_platform_scope

        await get_policy_service(self._settings).require_point(auth, AUDIT_READ_PERMISSION)
        if is_platform_scope(auth, auth.tenant_id):
            raise ApiError(
                ErrorCode.FORBIDDEN,
                message="平台账号不能导出客户的操作记录",
                internal_detail="audit_export_rejected_platform_scope",
            )
        scoped = replace(request, tenant_id=auth.tenant_id)
        return await get_audit_export_service(self._settings).prepare(scoped)

    # ------------------------------------------------------------------
    # 辅助：组织信息组装
    # ------------------------------------------------------------------
    def build_organizations(self, raw_groups: list[dict[str, Any]]) -> list[OrganizationInfo]:
        """将身份底座原生 group 列表转换为自研组织列表。

        Args:
            raw_groups: 身份底座原生 group 列表。

        Returns:
            list[OrganizationInfo]: 自研组织列表。
        """
        organizations: list[OrganizationInfo] = []
        for group in raw_groups:
            if not isinstance(group, dict):
                continue
            attributes = group.get("attributes", {})
            attributes = attributes if isinstance(attributes, dict) else {}
            raw_role = (
                str(attributes.get("org_role", [""])[0] or "") if attributes.get("org_role") else ""
            )
            organizations.append(
                OrganizationInfo(
                    org_id=str(group.get("id", "")),
                    name=str(group.get("name", "")),
                    role=map_role(raw_role),
                )
            )
        return organizations


_service: IdentityService | None = None


def get_identity_service() -> IdentityService:
    """获取用户权限服务单例。

    Returns:
        IdentityService: 进程内唯一实例。
    """
    global _service
    if _service is None:
        _service = IdentityService()
    return _service


def reset_identity_service() -> None:
    """重置用户权限服务单例，供测试隔离用例。"""
    global _service
    _service = None
