"""角色与权限点仓储 · records 层（`role_repo` 三拆后的数据记录与种子常量）。

本文件是 `app/db/repositories/role/` 包拆分后的**最底层**：只有数据记录（dataclass）
与模块级常量/纯函数，**不依赖** protocol / memory / postgres，因此可以被它们任意引用
而不会形成环（依赖方向 `records ← protocol ← {memory, postgres} ← __init__`）。

**权限点矩阵的唯一代码实现**在 `app/core/builtin_roles.py`
（`BUILTIN_ROLE_PERMISSIONS`）：判权（`core/security.py`）、角色播种
（`role/postgres.py`）与对象级判定（`business/policy_service.py`）共用同一份常量，
避免「目录里有一套、判权用另一套」的双口径漂移。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

#: 角色 ID 前缀
ROLE_ID_PREFIX: Final[str] = "role_"

#: 内置角色展示名
BUILTIN_ROLE_DISPLAY_NAMES: Final[dict[str, str]] = {
    "owner": "企业所有者",
    "org_admin": "管理员",
    "member": "普通成员",
    "guest": "访客",
}

#: 成员在册状态（U3/ID8 的 `status` 取值；不落库时按 `active` 返回）
MEMBER_STATUS_ACTIVE: Final[str] = "active"

#: 成员在册状态全集
MEMBER_STATUSES: Final[tuple[str, str, str]] = ("active", "invited", "disabled")


@dataclass(frozen=True, slots=True)
class PermissionCatalogEntry:
    """权限点目录条目（`permission_catalog` 行）。

    Attributes:
        permission: 权限点，形如 `team:member:update`。
        group_key: 分组键（`knowledge` / `tool` / `member` / `billing` / `audit` / `ops`）。
        group_name: 分组展示名。
        display_name: 权限点展示名。
        description: 业务语言说明。
        sort_order: 排序号。
    """

    permission: str
    group_key: str = ""
    group_name: str = ""
    display_name: str = ""
    description: str = ""
    sort_order: int = 0


@dataclass(frozen=True, slots=True)
class RoleRecord:
    """角色记录（`role` 行 + 权限点集合）。

    Attributes:
        tenant_id: 租户 ID。
        role_id: 角色 ID，形如 `role_owner`。
        org_id: 组织 ID。
        role_key: 角色标识（`owner` / `org_admin` / `member` / `guest` / 自定义）。
        display_name: 展示名。
        is_builtin: 是否内置（内置角色不可删除）。
        permissions: 该角色的权限点集合。
        member_count: 引用该角色的成员数。
    """

    tenant_id: str
    role_id: str
    org_id: str = ""
    role_key: str = ""
    display_name: str = ""
    is_builtin: bool = False
    permissions: frozenset[str] = field(default_factory=frozenset)
    member_count: int = 0


#: 20 个权限点目录（与 `deploy/init-schema-phase2.sql` 第 15 节种子**逐条一致**）。
#:
#: 为什么在代码里再写一份：内存后端（development/test）没有 PostgreSQL 表，
#: 但 ID5「权限点目录」与角色播种都需要这份数据；两份内容的等价性由
#: `tests/integration/test_org_endpoints.py::test_catalog_matches_ddl_seed` 保证。
PERMISSION_CATALOG_SEED: Final[tuple[PermissionCatalogEntry, ...]] = (
    PermissionCatalogEntry(
        "knowledge:base:create",
        "knowledge",
        "知识库",
        "创建知识库",
        "新建知识库并在其中上传资料",
        1,
    ),
    PermissionCatalogEntry(
        "knowledge:base:update",
        "knowledge",
        "知识库",
        "编辑知识库设置",
        "修改名称、描述、可见范围与内容切分精细度",
        2,
    ),
    PermissionCatalogEntry(
        "knowledge:base:delete", "knowledge", "知识库", "删除知识库", "删除知识库及其下全部文档", 3
    ),
    PermissionCatalogEntry(
        "knowledge:document:upload", "knowledge", "知识库", "上传文档", "向知识库上传文档", 4
    ),
    PermissionCatalogEntry(
        "knowledge:document:delete", "knowledge", "知识库", "删除文档", "删除知识库中的文档", 5
    ),
    PermissionCatalogEntry(
        "knowledge:grant:manage",
        "knowledge",
        "知识库",
        "管理成员授权",
        "设置谁可以查看与上传该知识库",
        6,
    ),
    PermissionCatalogEntry(
        "knowledge:base:read",
        "knowledge",
        "知识库",
        "提问与检索知识库",
        "在对话与检索中使用知识库",
        7,
    ),
    PermissionCatalogEntry(
        "tool:connection:manage",
        "tool",
        "办公工具",
        "连接与断开办公工具",
        "连接、断开、重新连接办公工具",
        8,
    ),
    PermissionCatalogEntry(
        "tool:execute", "tool", "办公工具", "调用办公工具", "在对话与自动化任务中调用办公工具", 9
    ),
    PermissionCatalogEntry(
        "team:member:invite", "member", "成员", "邀请成员", "邀请新成员加入企业", 10
    ),
    PermissionCatalogEntry(
        "team:member:update",
        "member",
        "成员",
        "变更角色与移出成员",
        "调整成员角色、部门与在册状态",
        11,
    ),
    PermissionCatalogEntry(
        "team:structure:manage",
        "member",
        "成员",
        "管理部门层级",
        "新建、移动、重命名与删除部门",
        12,
    ),
    PermissionCatalogEntry(
        "team:role:manage", "member", "成员", "配置角色权限", "设置每个角色可以做的事", 13
    ),
    PermissionCatalogEntry(
        "billing:subscription:read", "billing", "账单", "查看套餐与用量", "查看套餐、席位与用量", 14
    ),
    PermissionCatalogEntry(
        "billing:subscription:manage",
        "billing",
        "账单",
        "开通与变更套餐席位",
        "开通、升级、降级与增减席位",
        15,
    ),
    PermissionCatalogEntry(
        "audit:log:read", "audit", "操作记录", "查看操作记录", "查询企业内的操作记录", 16
    ),
    PermissionCatalogEntry(
        "ops:console:access", "ops", "运营管理", "访问运营管理", "进入运营管理后台", 91
    ),
    PermissionCatalogEntry(
        "ops:order:confirm", "ops", "运营管理", "订单核销", "确认对公转账到账并核销订单", 92
    ),
    PermissionCatalogEntry(
        "ops:tenant:manage", "ops", "运营管理", "企业启用与停用", "启用或停用企业账号", 93
    ),
    PermissionCatalogEntry(
        "ops:settings:manage", "ops", "运营管理", "修改系统配置", "调整额度阈值、试用天数与公告", 94
    ),
)


def builtin_role_id(role_key: str) -> str:
    """内置角色 ID（`role_<role_key>`，确定性便于断言与迁移）。

    Args:
        role_key: 角色标识。

    Returns:
        str: 角色 ID。
    """
    return f"{ROLE_ID_PREFIX}{role_key}"


def role_display_name(role_key: str, fallback: str = "") -> str:
    """角色展示名（内置角色查常量，自定义角色回退）。

    Args:
        role_key: 角色标识。
        fallback: 自定义角色的展示名。

    Returns:
        str: 展示名。
    """
    return BUILTIN_ROLE_DISPLAY_NAMES.get(role_key, fallback or role_key)


__all__: list[str] = [
    "BUILTIN_ROLE_DISPLAY_NAMES",
    "MEMBER_STATUSES",
    "MEMBER_STATUS_ACTIVE",
    "PERMISSION_CATALOG_SEED",
    "ROLE_ID_PREFIX",
    "PermissionCatalogEntry",
    "RoleRecord",
    "builtin_role_id",
    "role_display_name",
]
