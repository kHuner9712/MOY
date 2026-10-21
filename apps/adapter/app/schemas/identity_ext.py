"""组织与权限扩展接口契约（阶段二 B08，架构文档 §4.4 ID1-ID11）。

字段口径：

- 部门树**最多 5 层**（`level` 1-5），`path_names` 由服务端一次批量查询组装（不做 N+1）；
- 角色 = 4 个内置角色 + 自定义角色，权限点来自 `permission_catalog`（20 个）；
- 成员变更一律返回**变更后**的状态，便于前端直接替换本地行；
- 批量操作返回 `succeeded` 与 `failed[{member_id, reason}]`（失败原因用业务语言）。

本文件只定义契约，不含任何业务规则。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

#: 部门名称最长 64 字（与 DDL CHECK 一致）
DEPARTMENT_NAME_MAX: int = 64

#: 批量成员操作单批上限（架构文档 §4.4 与 §7.1 同码文案「一次最多处理 100 位成员」）
BATCH_MEMBER_LIMIT: int = 100


class MemberStatus(StrEnum):
    """成员在册状态（ID8 `status` 取值）。"""

    ACTIVE = "active"
    INVITED = "invited"
    DISABLED = "disabled"


class BatchMemberAction(StrEnum):
    """ID10 批量动作。"""

    UPDATE_ROLE = "update_role"
    REMOVE = "remove"


# ===========================================================================
# ID1 部门树
# ===========================================================================
class DepartmentNode(BaseModel):
    """部门树节点。

    Attributes:
        department_id: 部门 ID。
        name: 部门名称。
        parent_id: 上级部门 ID（一级部门为空串）。
        path_names: 从根到本部门的名称链。
        level: 层级（1-5）。
        manager_name: 负责人展示名（未设置时为空串）。
        member_count: 成员数。
        children: 子部门。
    """

    department_id: str = Field(description="部门 ID")
    name: str = Field(default="", description="部门名称")
    parent_id: str = Field(default="", description="上级部门 ID")
    path_names: list[str] = Field(default_factory=list, description="从根到本部门的名称链")
    level: int = Field(default=1, ge=1, le=5, description="层级，1-5")
    manager_name: str = Field(default="", description="负责人展示名")
    member_count: int = Field(default=0, ge=0, description="成员数")
    children: list[DepartmentNode] = Field(default_factory=list, description="子部门")


DepartmentNode.model_rebuild()


class DepartmentTreeData(BaseModel):
    """ID1 响应数据。

    Attributes:
        departments: 部门列表（`flat=false` 为树，`flat=true` 为平铺数组）。
    """

    departments: list[DepartmentNode] = Field(default_factory=list, description="部门列表")


# ===========================================================================
# ID2 新建部门
# ===========================================================================
class CreateDepartmentRequest(BaseModel):
    """ID2 新建部门请求。

    Attributes:
        name: 部门名称（1-64 字）。
        parent_id: 上级部门 ID（留空为一级部门）。
    """

    name: str = Field(min_length=1, max_length=DEPARTMENT_NAME_MAX, description="部门名称，1-64 字")
    parent_id: str = Field(default="", max_length=128, description="上级部门 ID；留空为一级部门")


class CreateDepartmentData(BaseModel):
    """ID2 响应数据。

    Attributes:
        department_id: 新部门 ID。
        name: 部门名称。
        parent_id: 上级部门 ID。
        level: 层级。
    """

    department_id: str = Field(description="部门 ID")
    name: str = Field(default="", description="部门名称")
    parent_id: str = Field(default="", description="上级部门 ID")
    level: int = Field(default=1, ge=1, le=5, description="层级")


# ===========================================================================
# ID3 修改/移动部门
# ===========================================================================
class UpdateDepartmentRequest(BaseModel):
    """ID3 修改部门请求（三项至少提供一项）。

    Attributes:
        name: 新名称。
        parent_id: 新上级部门 ID（空串表示移动到根）。
        manager_id: 新负责人用户 ID（空串表示清空）。
    """

    name: str | None = Field(
        default=None, min_length=1, max_length=DEPARTMENT_NAME_MAX, description="新名称"
    )
    parent_id: str | None = Field(default=None, max_length=128, description="新上级部门 ID")
    manager_id: str | None = Field(default=None, max_length=128, description="负责人用户 ID")


class UpdateDepartmentData(BaseModel):
    """ID3 响应数据。

    Attributes:
        department_id: 部门 ID。
        name: 部门名称。
        parent_id: 上级部门 ID。
        level: 层级。
        manager_name: 负责人展示名。
        updated_at: 更新时间。
    """

    department_id: str = Field(description="部门 ID")
    name: str = Field(default="", description="部门名称")
    parent_id: str = Field(default="", description="上级部门 ID")
    level: int = Field(default=1, ge=1, le=5, description="层级")
    manager_name: str = Field(default="", description="负责人展示名")
    updated_at: str = Field(default="", description="更新时间（ISO 8601）")


# ===========================================================================
# ID4 删除部门
# ===========================================================================
class DeleteDepartmentData(BaseModel):
    """ID4 响应数据。

    Attributes:
        department_id: 部门 ID。
        deleted: 是否已删除。
    """

    department_id: str = Field(description="部门 ID")
    deleted: bool = Field(default=True, description="是否已删除")


# ===========================================================================
# ID5 权限点目录
# ===========================================================================
class PermissionItem(BaseModel):
    """权限点条目。

    Attributes:
        permission: 权限点标识。
        display_name: 展示名。
        description: 业务语言说明。
    """

    permission: str = Field(description="权限点标识")
    display_name: str = Field(default="", description="展示名")
    description: str = Field(default="", description="业务语言说明")


class PermissionGroup(BaseModel):
    """权限点分组。

    Attributes:
        group: 分组键。
        group_name: 分组展示名。
        permissions: 组内权限点。
    """

    group: str = Field(default="", description="分组键")
    group_name: str = Field(default="", description="分组展示名")
    permissions: list[PermissionItem] = Field(default_factory=list, description="组内权限点")


class PermissionCatalogData(BaseModel):
    """ID5 响应数据。

    Attributes:
        groups: 权限点分组列表。
    """

    groups: list[PermissionGroup] = Field(default_factory=list, description="权限点分组列表")


# ===========================================================================
# ID6 角色列表
# ===========================================================================
class RoleItem(BaseModel):
    """角色条目。

    Attributes:
        role_id: 角色 ID。
        role_key: 角色标识。
        display_name: 展示名。
        is_builtin: 是否内置角色（不可删除）。
        member_count: 引用该角色的成员数。
        permissions: 权限点列表。
    """

    role_id: str = Field(description="角色 ID")
    role_key: str = Field(default="", description="角色标识")
    display_name: str = Field(default="", description="展示名")
    is_builtin: bool = Field(default=False, description="是否内置角色")
    member_count: int = Field(default=0, ge=0, description="引用该角色的成员数")
    permissions: list[str] = Field(default_factory=list, description="权限点列表")


class RoleListData(BaseModel):
    """ID6 响应数据。

    Attributes:
        items: 角色列表。
    """

    items: list[RoleItem] = Field(default_factory=list, description="角色列表")


# ===========================================================================
# ID7 删除角色
# ===========================================================================
class DeleteRoleData(BaseModel):
    """ID7 响应数据。

    Attributes:
        role_id: 角色 ID。
        deleted: 是否已删除。
    """

    role_id: str = Field(description="角色 ID")
    deleted: bool = Field(default=True, description="是否已删除")


# ===========================================================================
# ID8 修改成员
# ===========================================================================
class UpdateMemberRequest(BaseModel):
    """ID8 修改成员请求（四项至少提供一项）。

    Attributes:
        role: 新角色标识（`owner` / `org_admin` / `member` / `guest` / 自定义 `role_key`）。
        department_ids: 新的部门 ID 列表（覆盖写）。
        primary_department_id: 主部门 ID（必须在 `department_ids` 内）。
        status: 在册状态。
    """

    role: str | None = Field(default=None, max_length=64, description="新角色标识")
    department_ids: list[str] | None = Field(
        default=None, max_length=20, description="新的部门 ID 列表（覆盖写）"
    )
    primary_department_id: str | None = Field(
        default=None, max_length=128, description="主部门 ID，必须在 department_ids 内"
    )
    status: MemberStatus | None = Field(default=None, description="在册状态")


class UpdateMemberData(BaseModel):
    """ID8 响应数据。

    Attributes:
        member_id: 成员用户 ID。
        role: 变更后的角色标识。
        department_ids: 变更后的部门 ID 列表。
        status: 变更后的在册状态。
        updated_at: 更新时间。
    """

    member_id: str = Field(description="成员用户 ID")
    role: str = Field(default="", description="角色标识")
    department_ids: list[str] = Field(default_factory=list, description="部门 ID 列表")
    status: str = Field(default="active", description="在册状态")
    updated_at: str = Field(default="", description="更新时间（ISO 8601）")


# ===========================================================================
# ID9 移出成员
# ===========================================================================
class RemoveMemberData(BaseModel):
    """ID9 响应数据。

    Attributes:
        member_id: 成员用户 ID。
        removed: 是否已移出。
        transferred_knowledge_base_count: 转交给企业管理员的库数量。
    """

    member_id: str = Field(description="成员用户 ID")
    removed: bool = Field(default=True, description="是否已移出")
    transferred_knowledge_base_count: int = Field(
        default=0, ge=0, description="转交给企业管理员的库数量"
    )


# ===========================================================================
# ID10 批量成员操作
# ===========================================================================
class BatchMemberRequest(BaseModel):
    """ID10 批量成员操作请求。

    Attributes:
        member_ids: 成员用户 ID 列表（1-100）。
        action: 批量动作。
        role: `update_role` 时的目标角色标识。
    """

    member_ids: list[str] = Field(
        min_length=1, max_length=BATCH_MEMBER_LIMIT, description="成员用户 ID 列表，最多 100 位"
    )
    action: BatchMemberAction = Field(description="批量动作：update_role / remove")
    role: str | None = Field(default=None, max_length=64, description="update_role 的目标角色")


class BatchMemberFailure(BaseModel):
    """批量操作的单条失败原因。

    Attributes:
        member_id: 成员用户 ID。
        reason: 业务语言原因。
    """

    member_id: str = Field(description="成员用户 ID")
    reason: str = Field(default="", description="业务语言原因")


class BatchMemberData(BaseModel):
    """ID10 响应数据。

    Attributes:
        succeeded: 成功条数。
        failed: 失败清单。
    """

    succeeded: int = Field(default=0, ge=0, description="成功条数")
    failed: list[BatchMemberFailure] = Field(default_factory=list, description="失败清单")


# ===========================================================================
# ID11 动作目录
# ===========================================================================
class ActionItem(BaseModel):
    """操作动作条目。

    Attributes:
        action: 动作编码。
        display_name: 展示名。
    """

    action: str = Field(description="动作编码")
    display_name: str = Field(default="", description="展示名")


class ActionGroup(BaseModel):
    """操作动作分组。

    Attributes:
        group: 分组键。
        group_name: 分组展示名。
        actions: 组内动作。
    """

    group: str = Field(default="", description="分组键")
    group_name: str = Field(default="", description="分组展示名")
    actions: list[ActionItem] = Field(default_factory=list, description="组内动作")


class ActionCatalogData(BaseModel):
    """ID11 响应数据。

    Attributes:
        groups: 动作分组列表。
    """

    groups: list[ActionGroup] = Field(default_factory=list, description="动作分组列表")


__all__: list[str] = [
    "BATCH_MEMBER_LIMIT",
    "DEPARTMENT_NAME_MAX",
    "ActionCatalogData",
    "ActionGroup",
    "ActionItem",
    "BatchMemberAction",
    "BatchMemberData",
    "BatchMemberFailure",
    "BatchMemberRequest",
    "CreateDepartmentData",
    "CreateDepartmentRequest",
    "DeleteDepartmentData",
    "DeleteRoleData",
    "DepartmentNode",
    "DepartmentTreeData",
    "MemberStatus",
    "PermissionCatalogData",
    "PermissionGroup",
    "PermissionItem",
    "RemoveMemberData",
    "RoleItem",
    "RoleListData",
    "UpdateDepartmentData",
    "UpdateDepartmentRequest",
    "UpdateMemberData",
    "UpdateMemberRequest",
]
