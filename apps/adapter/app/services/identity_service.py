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

from app.clients.keycloak_client import KeycloakClient, get_keycloak_client
from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.core.security import AuthContext, require_permission
from app.schemas.common import build_page
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
    async def list_members(self, org_id: str, query: ListMembersQuery, auth: AuthContext) -> Any:
        """查询组织成员列表。

        Args:
            org_id: 组织 ID。
            query: 分页与过滤参数。
            auth: 鉴权上下文。

        Returns:
            Any: `PageResult[MemberItem]` 分页结果。

        Raises:
            ApiError: 组织不存在（14002）。
        """
        if not org_id:
            raise ApiError(ErrorCode.ORGANIZATION_NOT_FOUND, internal_detail="empty org_id")
        if self._settings.mock_mode:
            logger.info("identity_members_stub", org_id=org_id, tenant_id=auth.tenant_id)
            return build_page(items=[], total=0, page=query.page, page_size=query.page_size)

        raw_members = await self._keycloak.list_group_members(org_id)
        members = [self._transform_member(item) for item in raw_members if isinstance(item, dict)]
        if query.role is not None:
            members = [item for item in members if item.role == query.role]
        if query.keyword:
            keyword = query.keyword.lower()
            members = [
                item
                for item in members
                if keyword in item.display_name.lower() or keyword in item.email.lower()
            ]
        start = query.offset
        return build_page(
            items=members[start : start + query.page_size],
            total=len(members),
            page=query.page,
            page_size=query.page_size,
        )

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

        Args:
            org_id: 组织 ID。
            email: 被邀请人邮箱。
            role: 分配角色。
            message: 邀请附言。
            auth: 鉴权上下文。

        Returns:
            InvitationData: 邀请记录。

        Raises:
            ApiError: 组织不存在（14002）。
        """
        if not org_id:
            raise ApiError(ErrorCode.ORGANIZATION_NOT_FOUND, internal_detail="empty org_id")
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
    def query_audit_logs(self, request: AuditLogQueryRequest, auth: AuthContext) -> Any:
        """查询审计日志。

        适配层不持有审计逻辑，仅提供查询出口；字段透传，脱敏由业务层写入时完成。

        Args:
            request: 查询请求。
            auth: 鉴权上下文。

        Returns:
            Any: `PageResult[AuditLogItem]` 分页结果。
        """
        del auth  # T01 桩模式不持有审计数据
        logger.info(
            "identity_audit_queried",
            resource_type=request.resource_type,
            start_at=request.start_at,
            end_at=request.end_at,
        )
        items: list[AuditLogItem] = []
        return build_page(
            items=items[request.offset : request.offset + request.page_size],
            total=len(items),
            page=request.page,
            page_size=request.page_size,
        )

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
