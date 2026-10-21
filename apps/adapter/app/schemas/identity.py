"""用户权限服务接口契约（架构文档 3.5，U1-U6）。

设计原则：适配层仅做**令牌校验与请求转发**，
用户/组织/角色权威数据在身份底座（Keycloak），权限策略判断在业务能力层，
适配层提供统一校验出口（FLOW-05：业务代码不得自行判权）。

权限点命名规范：`resource:action`（如 `knowledge:document:upload`）。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from app.schemas.common import PageQuery, ResourceRef


class OrgRole(StrEnum):
    """组织内角色枚举（业务化命名，非身份底座原始角色名）。"""

    OWNER = "owner"
    ADMIN = "org_admin"
    MEMBER = "member"
    GUEST = "guest"


class VerifyRequest(BaseModel):
    """U1 权限校验请求。

    Attributes:
        permission: 权限点，形如 `knowledge:document:upload`。
        resource: 资源上下文（可选）。
    """

    permission: str = Field(
        min_length=1, max_length=128, description="权限点，形如 resource:action"
    )
    resource: ResourceRef | None = Field(default=None, description="资源上下文")


class VerifyData(BaseModel):
    """U1 响应数据。

    Attributes:
        allowed: 是否允许。
        user_id: 用户 ID。
        tenant_id: 租户 ID。
        roles: 角色列表。
    """

    allowed: bool = Field(default=False, description="是否允许")
    user_id: str = Field(default="", description="用户 ID")
    tenant_id: str = Field(default="", description="租户 ID")
    roles: list[str] = Field(default_factory=list, description="角色列表")


class TenantInfo(BaseModel):
    """租户信息。

    Attributes:
        tenant_id: 租户 ID。
        name: 租户名称。
        plan: 套餐标识。
    """

    tenant_id: str = Field(default="", description="租户 ID")
    name: str = Field(default="", description="租户名称")
    plan: str = Field(default="", description="套餐标识")


class OrganizationInfo(BaseModel):
    """组织信息。

    Attributes:
        org_id: 组织 ID，形如 `org_01HF8...`。
        name: 组织名称。
        role: 当前用户在该组织中的角色。
    """

    org_id: str = Field(description="组织 ID，形如 org_01HF8...")
    name: str = Field(default="", description="组织名称")
    role: OrgRole = Field(default=OrgRole.MEMBER, description="当前用户角色")


class ProfileData(BaseModel):
    """U2 当前用户信息响应数据。

    Attributes:
        user_id: 用户 ID。
        display_name: 展示名。
        email: 邮箱。
        tenant: 租户信息。
        organizations: 组织列表。
    """

    user_id: str = Field(description="用户 ID")
    display_name: str = Field(default="", description="展示名")
    email: str = Field(default="", description="邮箱")
    tenant: TenantInfo = Field(default_factory=TenantInfo, description="租户信息")
    organizations: list[OrganizationInfo] = Field(default_factory=list, description="组织列表")


class MemberItem(BaseModel):
    """组织成员条目。

    Attributes:
        user_id: 用户 ID。
        display_name: 展示名。
        email: 邮箱。
        role: 角色。
        joined_at: 加入时间。
    """

    user_id: str = Field(description="用户 ID")
    display_name: str = Field(default="", description="展示名")
    email: str = Field(default="", description="邮箱")
    role: OrgRole = Field(default=OrgRole.MEMBER, description="角色")
    joined_at: str = Field(default="", description="加入时间，ISO 8601 格式")


class ListMembersQuery(PageQuery):
    """U3 组织成员列表查询参数。

    Attributes:
        role: 按角色过滤。
        keyword: 按姓名或邮箱关键字过滤。
    """

    role: OrgRole | None = Field(default=None, description="按角色过滤")
    keyword: str = Field(default="", max_length=128, description="按姓名或邮箱关键字过滤")


class InviteRequest(BaseModel):
    """U4 邀请成员请求。

    Attributes:
        email: 被邀请人邮箱。
        role: 分配角色。
        message: 邀请附言（业务语言）。
    """

    email: str = Field(min_length=3, max_length=254, description="被邀请人邮箱")
    role: OrgRole = Field(default=OrgRole.MEMBER, description="分配角色")
    message: str = Field(default="", max_length=512, description="邀请附言")


class InvitationData(BaseModel):
    """U4 邀请响应数据。

    Attributes:
        invitation_id: 邀请记录 ID。
        org_id: 组织 ID。
        email: 被邀请人邮箱。
        role: 分配角色。
        status: 邀请状态。
        expires_at: 过期时间。
    """

    invitation_id: str = Field(description="邀请记录 ID")
    org_id: str = Field(default="", description="组织 ID")
    email: str = Field(default="", description="被邀请人邮箱")
    role: OrgRole = Field(default=OrgRole.MEMBER, description="分配角色")
    status: str = Field(default="pending", description="邀请状态")
    expires_at: str = Field(default="", description="过期时间，ISO 8601 格式")


class UpsertRoleRequest(BaseModel):
    """U5 创建/分配角色请求。

    Attributes:
        role_key: 角色标识。
        display_name: 角色展示名。
        permissions: 权限点列表。
        members: 待分配成员 ID 列表。
    """

    role_key: str = Field(min_length=1, max_length=64, description="角色标识")
    display_name: str = Field(default="", max_length=128, description="角色展示名")
    permissions: list[str] = Field(default_factory=list, description="权限点列表")
    members: list[str] = Field(default_factory=list, description="待分配成员 ID 列表")


class RoleData(BaseModel):
    """U5 响应数据。

    Attributes:
        role_id: 角色 ID，形如 `role_01HF8...`。
        role_key: 角色标识。
        display_name: 角色展示名。
        permissions: 权限点列表。
        member_count: 已分配成员数。
    """

    role_id: str = Field(description="角色 ID，形如 role_01HF8...")
    role_key: str = Field(default="", description="角色标识")
    display_name: str = Field(default="", description="角色展示名")
    permissions: list[str] = Field(default_factory=list, description="权限点列表")
    member_count: int = Field(default=0, ge=0, description="已分配成员数")


class AuditLogQueryRequest(PageQuery):
    """U6 审计日志查询请求。

    Attributes:
        actor_id: 操作人 ID。
        action: 操作动作。
        resource_type: 资源类型。
        start_at: 开始时间。
        end_at: 结束时间。
    """

    actor_id: str = Field(default="", max_length=128, description="操作人 ID")
    action: str = Field(default="", max_length=128, description="操作动作")
    resource_type: str = Field(default="", max_length=64, description="资源类型")
    start_at: str = Field(default="", max_length=64, description="开始时间，ISO 8601 格式")
    end_at: str = Field(default="", max_length=64, description="结束时间，ISO 8601 格式")


class AuditLogItem(BaseModel):
    """审计日志条目（字段透传，脱敏由业务层写入时完成）。

    Attributes:
        log_id: 日志 ID。
        actor_id: 操作人 ID。
        action: 操作动作。
        resource_type: 资源类型。
        resource_id: 资源 ID。
        result: 操作结果。
        occurred_at: 发生时间。
    """

    log_id: str = Field(default="", description="日志 ID")
    actor_id: str = Field(default="", description="操作人 ID")
    action: str = Field(default="", description="操作动作")
    resource_type: str = Field(default="", description="资源类型")
    resource_id: str = Field(default="", description="资源 ID")
    result: str = Field(default="", description="操作结果")
    occurred_at: str = Field(default="", description="发生时间，ISO 8601 格式")
