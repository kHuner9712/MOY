# ruff: noqa: S608 - 表名/列名均为模块常量（经 assert_identifier 校验），值一律命名参数绑定。
"""角色与权限点仓储（阶段二 B08，架构文档 §4.4 / §4.8 / 2.6 D-13）。

四张表：

| 表 | 作用 |
| --- | --- |
| `permission_catalog` | 20 个权限点目录（平台共享行，`tenant_id='__platform__'`） |
| `role` | 4 个内置角色 + 自定义角色（一企业组织内 `role_key` 唯一） |
| `role_permission` | 角色 → 权限点 |
| `member_role` | 成员 → 角色（一企业一角色） |

**权限点矩阵的唯一代码实现**在 `app/core/builtin_roles.py`
（`BUILTIN_ROLE_PERMISSIONS`）：判权（`core/security.py`）、角色播种（本模块）
与对象级判定（`business/policy_service.py`）共用同一份常量，避免
「目录里有一套、判权用另一套」的双口径漂移。

> 该常量原在 `business/policy_service.py`，本模块为拿它而**延迟 import 业务层**
> （审计 P1-4：`db → business` 方向倒挂）。切片 1 下沉到 `core` 后改为顶部导入，
> 方向变成 `db → core`（矩阵内），延迟导入也随之删除。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from typing import Final, Protocol

from app.core.builtin_roles import BUILTIN_ROLE_KEYS, BUILTIN_ROLE_PERMISSIONS
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.repositories.base import BaseRepository
from app.db.session import tenant_scope

logger = get_logger(__name__)

#: 权限点目录表名
CATALOG_TABLE: Final[str] = "permission_catalog"

#: 角色表名
ROLE_TABLE: Final[str] = "role"

#: 角色-权限点表名
ROLE_PERMISSION_TABLE: Final[str] = "role_permission"

#: 成员-角色表名
MEMBER_ROLE_TABLE: Final[str] = "member_role"

#: 平台哨兵租户（目录共享行）
PLATFORM_TENANT: Final[str] = "__platform__"

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


class RoleStore(Protocol):
    """角色与权限点存储契约（内存 / PostgreSQL 双实现）。"""

    async def ensure_builtin_roles(self, tenant_id: str, org_id: str) -> list[RoleRecord]:
        """播种并返回 4 个内置角色（幂等）。"""
        ...

    async def list_roles(self, tenant_id: str, org_id: str) -> list[RoleRecord]:
        """列出组织内角色（含权限点与成员数）。"""
        ...

    async def get_role(self, tenant_id: str, role_id: str) -> RoleRecord | None:
        """按 ID 读取角色。"""
        ...

    async def count_members(self, tenant_id: str, role_id: str) -> int:
        """统计引用该角色的成员数。"""
        ...

    async def delete_role(self, tenant_id: str, role_id: str) -> bool:
        """删除角色（内置角色拒绝）。"""
        ...

    async def permission_catalog(self, tenant_id: str) -> list[PermissionCatalogEntry]:
        """读取权限点目录。"""
        ...

    async def permissions_of_user(self, tenant_id: str, user_id: str) -> frozenset[str]:
        """读取成员角色对应的权限点集合。"""
        ...

    async def assign_role(
        self, tenant_id: str, org_id: str, user_id: str, role_id: str, assigned_by: str = ""
    ) -> None:
        """分配成员角色（一企业一角色，覆盖写）。"""
        ...

    async def role_key_of_user(self, tenant_id: str, user_id: str) -> str:
        """读取成员角色标识（未分配返回空串）。"""
        ...

    async def role_ids_of_user(self, tenant_id: str, user_id: str) -> list[str]:
        """读取成员已分配的角色 ID（**只读**，不触发内置角色播种）。"""
        ...

    async def member_ids_of_role(self, tenant_id: str, role_id: str) -> list[str]:
        """列出引用该角色的成员 ID。"""
        ...


class InMemoryRoleStore:
    """进程内角色与权限点存储（development/test）。"""

    def __init__(self) -> None:
        """初始化。"""
        self._roles: dict[tuple[str, str], RoleRecord] = {}
        self._assignments: dict[tuple[str, str], str] = {}
        self._lock = asyncio.Lock()

    async def ensure_builtin_roles(self, tenant_id: str, org_id: str) -> list[RoleRecord]:
        """播种 4 个内置角色（幂等）。

        Args:
            tenant_id: 租户 ID。
            org_id: 组织 ID。

        Returns:
            list[RoleRecord]: 内置角色列表。
        """
        created: list[RoleRecord] = []
        async with self._lock:
            for role_key in BUILTIN_ROLE_KEYS:
                role_id = builtin_role_id(role_key)
                key = (tenant_id, role_id)
                if key not in self._roles:
                    self._roles[key] = RoleRecord(
                        tenant_id=tenant_id,
                        role_id=role_id,
                        org_id=org_id,
                        role_key=role_key,
                        display_name=role_display_name(role_key),
                        is_builtin=True,
                        permissions=BUILTIN_ROLE_PERMISSIONS.get(role_key, frozenset()),
                    )
                created.append(self._roles[key])
        return created

    async def list_roles(self, tenant_id: str, org_id: str) -> list[RoleRecord]:
        """列出组织内角色。

        Args:
            tenant_id: 租户 ID。
            org_id: 组织 ID。

        Returns:
            list[RoleRecord]: 角色列表（内置在前）。
        """
        await self.ensure_builtin_roles(tenant_id, org_id)
        async with self._lock:
            records = [
                record
                for (tid, _), record in self._roles.items()
                if tid == tenant_id and (not org_id or record.org_id == org_id)
            ]
            counts: dict[str, int] = {}
            for (tid, _), assigned in self._assignments.items():
                if tid == tenant_id:
                    counts[assigned] = counts.get(assigned, 0) + 1
        items = [replace(record, member_count=counts.get(record.role_id, 0)) for record in records]
        items.sort(key=lambda item: (not item.is_builtin, item.role_key))
        return items

    async def get_role(self, tenant_id: str, role_id: str) -> RoleRecord | None:
        """按 ID 读取角色。

        Args:
            tenant_id: 租户 ID。
            role_id: 角色 ID。

        Returns:
            RoleRecord | None: 角色记录或 None。
        """
        async with self._lock:
            return self._roles.get((tenant_id, role_id))

    async def count_members(self, tenant_id: str, role_id: str) -> int:
        """统计引用该角色的成员数。

        Args:
            tenant_id: 租户 ID。
            role_id: 角色 ID。

        Returns:
            int: 成员数。
        """
        async with self._lock:
            return sum(
                1
                for (tid, _), assigned in self._assignments.items()
                if tid == tenant_id and assigned == role_id
            )

    async def delete_role(self, tenant_id: str, role_id: str) -> bool:
        """删除角色（内置角色拒绝删除）。

        Args:
            tenant_id: 租户 ID。
            role_id: 角色 ID。

        Returns:
            bool: 删除成功返回 True；不存在或内置角色返回 False。
        """
        async with self._lock:
            key = (tenant_id, role_id)
            record = self._roles.get(key)
            if record is None or record.is_builtin:
                return False
            self._roles.pop(key, None)
            return True

    async def permission_catalog(self, tenant_id: str) -> list[PermissionCatalogEntry]:
        """读取权限点目录。

        Args:
            tenant_id: 租户 ID（目录为平台共享，忽略租户差异）。

        Returns:
            list[PermissionCatalogEntry]: 目录条目（按 sort_order）。
        """
        del tenant_id
        return sorted(PERMISSION_CATALOG_SEED, key=lambda item: item.sort_order)

    async def permissions_of_user(self, tenant_id: str, user_id: str) -> frozenset[str]:
        """读取成员角色对应的权限点集合。

        Args:
            tenant_id: 租户 ID。
            user_id: 用户 ID。

        Returns:
            frozenset[str]: 权限点集合；未分配角色返回空集（fail-closed）。
        """
        async with self._lock:
            role_id = self._assignments.get((tenant_id, user_id))
            if not role_id:
                return frozenset()
            record = self._roles.get((tenant_id, role_id))
        return record.permissions if record is not None else frozenset()

    async def assign_role(
        self, tenant_id: str, org_id: str, user_id: str, role_id: str, assigned_by: str = ""
    ) -> None:
        """分配成员角色。

        Args:
            tenant_id: 租户 ID。
            org_id: 组织 ID。
            user_id: 用户 ID。
            role_id: 角色 ID。
            assigned_by: 分配人（审计由调用方写入）。
        """
        del org_id, assigned_by
        async with self._lock:
            self._assignments[(tenant_id, user_id)] = role_id

    async def role_key_of_user(self, tenant_id: str, user_id: str) -> str:
        """读取成员角色标识。

        Args:
            tenant_id: 租户 ID。
            user_id: 用户 ID。

        Returns:
            str: 角色标识；未分配返回空串。
        """
        async with self._lock:
            role_id = self._assignments.get((tenant_id, user_id))
            if not role_id:
                return ""
            record = self._roles.get((tenant_id, role_id))
        return record.role_key if record is not None else ""

    async def role_ids_of_user(self, tenant_id: str, user_id: str) -> list[str]:
        """读取成员已分配的角色 ID（只读）。

        Args:
            tenant_id: 租户 ID。
            user_id: 用户 ID。

        Returns:
            list[str]: 角色 ID 列表；未分配返回空列表。
        """
        async with self._lock:
            role_id = self._assignments.get((tenant_id, user_id))
        return [role_id] if role_id else []

    async def member_ids_of_role(self, tenant_id: str, role_id: str) -> list[str]:
        """列出引用该角色的成员 ID。

        Args:
            tenant_id: 租户 ID。
            role_id: 角色 ID。

        Returns:
            list[str]: 成员 ID 列表。
        """
        async with self._lock:
            return sorted(
                user_id
                for (tid, user_id), assigned in self._assignments.items()
                if tid == tenant_id and assigned == role_id
            )

    def clear(self) -> None:
        """清空（测试隔离用）。"""
        self._roles.clear()
        self._assignments.clear()


class _RoleTableRepository(BaseRepository):
    """`role` 表仓储。"""

    table = ROLE_TABLE


class _RolePermissionTableRepository(BaseRepository):
    """`role_permission` 表仓储。"""

    table = ROLE_PERMISSION_TABLE


class _MemberRoleTableRepository(BaseRepository):
    """`member_role` 表仓储。"""

    table = MEMBER_ROLE_TABLE


class _CatalogTableRepository(BaseRepository):
    """`permission_catalog` 表仓储（平台共享行只读）。"""

    table = CATALOG_TABLE


class PostgresRoleStore:
    """PostgreSQL 角色与权限点存储（一次调用一个短事务）。"""

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化。

        Args:
            settings: 可选配置实例。
        """
        self._settings = settings or get_settings()

    async def ensure_builtin_roles(self, tenant_id: str, org_id: str) -> list[RoleRecord]:
        """播种 4 个内置角色与权限点（幂等）。

        Args:
            tenant_id: 租户 ID。
            org_id: 组织 ID。

        Returns:
            list[RoleRecord]: 内置角色列表。
        """
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            role_repo = _RoleTableRepository(uow)
            permission_repo = _RolePermissionTableRepository(uow)
            for role_key in BUILTIN_ROLE_KEYS:
                role_id = builtin_role_id(role_key)
                await role_repo.execute(
                    f"""
                    INSERT INTO {ROLE_TABLE}
                        (tenant_id, role_id, org_id, role_key, display_name, is_builtin)
                    VALUES (:tenant_id, :role_id, :org_id, :role_key, :display_name, true)
                    ON CONFLICT (tenant_id, role_id) DO NOTHING
                    """,
                    role_repo.params(
                        role_id=role_id,
                        org_id=org_id,
                        role_key=role_key,
                        display_name=role_display_name(role_key),
                    ),
                )
                for permission in sorted(BUILTIN_ROLE_PERMISSIONS.get(role_key, frozenset())):
                    await permission_repo.execute(
                        f"""
                        INSERT INTO {ROLE_PERMISSION_TABLE} (tenant_id, role_id, permission)
                        VALUES (:tenant_id, :role_id, :permission)
                        ON CONFLICT (tenant_id, role_id, permission) DO NOTHING
                        """,
                        permission_repo.params(role_id=role_id, permission=permission),
                    )
        return await self.list_roles(tenant_id, org_id)

    async def list_roles(self, tenant_id: str, org_id: str) -> list[RoleRecord]:
        """列出组织内角色（含权限点与成员数）。

        Args:
            tenant_id: 租户 ID。
            org_id: 组织 ID。

        Returns:
            list[RoleRecord]: 角色列表。
        """
        org_filter = "AND org_id = :org_id" if org_id else ""
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            role_repo = _RoleTableRepository(uow)
            rows = await role_repo.fetch_all(
                f"""
                SELECT tenant_id, role_id, org_id, role_key, display_name, is_builtin
                FROM {ROLE_TABLE}
                WHERE {role_repo.tenant_clause()} {org_filter}
                ORDER BY is_builtin DESC, role_key
                """,
                role_repo.params(org_id=org_id),
            )
            permission_repo = _RolePermissionTableRepository(uow)
            permission_rows = await permission_repo.fetch_all(
                f"""
                SELECT role_id, permission FROM {ROLE_PERMISSION_TABLE}
                WHERE {permission_repo.tenant_clause()}
                """,
                permission_repo.params(),
            )
            member_repo = _MemberRoleTableRepository(uow)
            member_rows = await member_repo.fetch_all(
                f"""
                SELECT role_id, COUNT(*) AS cnt FROM {MEMBER_ROLE_TABLE}
                WHERE {member_repo.tenant_clause()}
                GROUP BY role_id
                """,
                member_repo.params(),
            )
        grouped: dict[str, set[str]] = {}
        for row in permission_rows:
            grouped.setdefault(str(row.get("role_id", "")), set()).add(
                str(row.get("permission", ""))
            )
        counts = {str(row.get("role_id", "")): int(row.get("cnt", 0) or 0) for row in member_rows}
        return [
            RoleRecord(
                tenant_id=str(row.get("tenant_id", "")),
                role_id=str(row.get("role_id", "")),
                org_id=str(row.get("org_id", "") or ""),
                role_key=str(row.get("role_key", "")),
                display_name=str(row.get("display_name", "")),
                is_builtin=bool(row.get("is_builtin", False)),
                permissions=frozenset(grouped.get(str(row.get("role_id", "")), set())),
                member_count=counts.get(str(row.get("role_id", "")), 0),
            )
            for row in rows
        ]

    async def get_role(self, tenant_id: str, role_id: str) -> RoleRecord | None:
        """按 ID 读取角色。

        Args:
            tenant_id: 租户 ID。
            role_id: 角色 ID。

        Returns:
            RoleRecord | None: 角色记录或 None。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            role_repo = _RoleTableRepository(uow)
            row = await role_repo.fetch_one(
                f"""
                SELECT tenant_id, role_id, org_id, role_key, display_name, is_builtin
                FROM {ROLE_TABLE}
                WHERE {role_repo.tenant_clause()} AND role_id = :role_id
                """,
                role_repo.params(role_id=role_id),
            )
            if row is None:
                return None
            permission_repo = _RolePermissionTableRepository(uow)
            permission_rows = await permission_repo.fetch_all(
                f"""
                SELECT permission FROM {ROLE_PERMISSION_TABLE}
                WHERE {permission_repo.tenant_clause()} AND role_id = :role_id
                """,
                permission_repo.params(role_id=role_id),
            )
        return RoleRecord(
            tenant_id=str(row.get("tenant_id", "")),
            role_id=str(row.get("role_id", "")),
            org_id=str(row.get("org_id", "") or ""),
            role_key=str(row.get("role_key", "")),
            display_name=str(row.get("display_name", "")),
            is_builtin=bool(row.get("is_builtin", False)),
            permissions=frozenset(str(item.get("permission", "")) for item in permission_rows),
            member_count=await self.count_members(tenant_id, role_id),
        )

    async def count_members(self, tenant_id: str, role_id: str) -> int:
        """统计引用该角色的成员数。

        Args:
            tenant_id: 租户 ID。
            role_id: 角色 ID。

        Returns:
            int: 成员数。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _MemberRoleTableRepository(uow)
            total = await repo.fetch_scalar(
                f"""
                SELECT COUNT(*) FROM {MEMBER_ROLE_TABLE}
                WHERE {repo.tenant_clause()} AND role_id = :role_id
                """,
                repo.params(role_id=role_id),
            )
        return int(total or 0)

    async def delete_role(self, tenant_id: str, role_id: str) -> bool:
        """删除角色（内置角色拒绝）。

        Args:
            tenant_id: 租户 ID。
            role_id: 角色 ID。

        Returns:
            bool: 删除成功返回 True。
        """
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _RoleTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                DELETE FROM {ROLE_TABLE}
                WHERE {repo.tenant_clause()} AND role_id = :role_id AND is_builtin = false
                RETURNING role_id
                """,
                repo.params(role_id=role_id),
            )
        return row is not None

    async def permission_catalog(self, tenant_id: str) -> list[PermissionCatalogEntry]:
        """读取权限点目录（平台共享行）。

        Args:
            tenant_id: 租户 ID。

        Returns:
            list[PermissionCatalogEntry]: 目录条目。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _CatalogTableRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT permission, group_key, group_name, display_name, description, sort_order
                FROM {CATALOG_TABLE}
                WHERE tenant_id = '{PLATFORM_TENANT}'
                ORDER BY sort_order
                """,
                {},
            )
        return [
            PermissionCatalogEntry(
                permission=str(row.get("permission", "")),
                group_key=str(row.get("group_key", "")),
                group_name=str(row.get("group_name", "")),
                display_name=str(row.get("display_name", "")),
                description=str(row.get("description", "") or ""),
                sort_order=int(row.get("sort_order", 0) or 0),
            )
            for row in rows
        ]

    async def permissions_of_user(self, tenant_id: str, user_id: str) -> frozenset[str]:
        """读取成员角色对应的权限点集合。

        Args:
            tenant_id: 租户 ID。
            user_id: 用户 ID。

        Returns:
            frozenset[str]: 权限点集合（fail-closed：无角色返回空集）。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            member_repo = _MemberRoleTableRepository(uow)
            row = await member_repo.fetch_one(
                f"""
                SELECT role_id FROM {MEMBER_ROLE_TABLE}
                WHERE {member_repo.tenant_clause()} AND user_id = :user_id
                ORDER BY assigned_at DESC
                LIMIT 1
                """,
                member_repo.params(user_id=user_id),
            )
            if row is None:
                return frozenset()
            role_id = str(row.get("role_id", ""))
            permission_repo = _RolePermissionTableRepository(uow)
            permission_rows = await permission_repo.fetch_all(
                f"""
                SELECT permission FROM {ROLE_PERMISSION_TABLE}
                WHERE {permission_repo.tenant_clause()} AND role_id = :role_id
                """,
                permission_repo.params(role_id=role_id),
            )
        return frozenset(str(item.get("permission", "")) for item in permission_rows)

    async def assign_role(
        self, tenant_id: str, org_id: str, user_id: str, role_id: str, assigned_by: str = ""
    ) -> None:
        """分配成员角色（一企业一角色，覆盖写）。

        Args:
            tenant_id: 租户 ID。
            org_id: 组织 ID。
            user_id: 用户 ID。
            role_id: 角色 ID。
            assigned_by: 分配人。
        """
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _MemberRoleTableRepository(uow)
            await repo.execute(
                f"""
                INSERT INTO {MEMBER_ROLE_TABLE}
                    (tenant_id, org_id, user_id, role_id, assigned_by, assigned_at)
                VALUES (:tenant_id, :org_id, :user_id, :role_id, NULLIF(:assigned_by, ''), now())
                ON CONFLICT (tenant_id, org_id, user_id) DO UPDATE
                SET role_id = EXCLUDED.role_id,
                    assigned_by = EXCLUDED.assigned_by,
                    assigned_at = now()
                """,
                repo.params(
                    org_id=org_id, user_id=user_id, role_id=role_id, assigned_by=assigned_by
                ),
            )

    async def role_key_of_user(self, tenant_id: str, user_id: str) -> str:
        """读取成员角色标识。

        Args:
            tenant_id: 租户 ID。
            user_id: 用户 ID。

        Returns:
            str: 角色标识；未分配返回空串。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _MemberRoleTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                SELECT r.role_key AS role_key
                FROM {MEMBER_ROLE_TABLE} mr
                JOIN {ROLE_TABLE} r
                  ON r.tenant_id = mr.tenant_id AND r.role_id = mr.role_id
                WHERE {repo.tenant_clause("mr")} AND mr.user_id = :user_id
                LIMIT 1
                """,
                repo.params(user_id=user_id),
            )
        return str(row.get("role_key", "")) if row is not None else ""

    async def role_ids_of_user(self, tenant_id: str, user_id: str) -> list[str]:
        """读取成员已分配的角色 ID（只读）。

        Args:
            tenant_id: 租户 ID。
            user_id: 用户 ID。

        Returns:
            list[str]: 角色 ID 列表。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _MemberRoleTableRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT role_id FROM {MEMBER_ROLE_TABLE}
                WHERE {repo.tenant_clause()} AND user_id = :user_id
                """,
                repo.params(user_id=user_id),
            )
        return [str(row.get("role_id", "")) for row in rows]

    async def member_ids_of_role(self, tenant_id: str, role_id: str) -> list[str]:
        """列出引用该角色的成员 ID。

        Args:
            tenant_id: 租户 ID。
            role_id: 角色 ID。

        Returns:
            list[str]: 成员 ID 列表。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _MemberRoleTableRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT user_id FROM {MEMBER_ROLE_TABLE}
                WHERE {repo.tenant_clause()} AND role_id = :role_id
                ORDER BY user_id
                """,
                repo.params(role_id=role_id),
            )
        return [str(row.get("user_id", "")) for row in rows]


# ===========================================================================
# 进程级单例
# ===========================================================================
_memory_store: InMemoryRoleStore | None = None


def get_role_store(settings: Settings | None = None) -> RoleStore:
    """获取角色与权限点存储。

    Args:
        settings: 可选配置实例。

    Returns:
        RoleStore: `business_store=postgres` 时为数据库实现，否则为内存实现。
    """
    global _memory_store

    conf = settings or get_settings()
    if conf.uses_postgres:
        return PostgresRoleStore(conf)
    if _memory_store is None:
        _memory_store = InMemoryRoleStore()
    return _memory_store


def reset_role_store() -> None:
    """重置内存后端单例（测试隔离用）。"""
    global _memory_store
    if _memory_store is not None:
        _memory_store.clear()
    _memory_store = None


__all__: list[str] = [
    "BUILTIN_ROLE_DISPLAY_NAMES",
    "CATALOG_TABLE",
    "MEMBER_ROLE_TABLE",
    "MEMBER_STATUSES",
    "MEMBER_STATUS_ACTIVE",
    "PERMISSION_CATALOG_SEED",
    "PLATFORM_TENANT",
    "ROLE_ID_PREFIX",
    "ROLE_PERMISSION_TABLE",
    "ROLE_TABLE",
    "InMemoryRoleStore",
    "PermissionCatalogEntry",
    "PostgresRoleStore",
    "RoleRecord",
    "RoleStore",
    "builtin_role_id",
    "get_role_store",
    "reset_role_store",
    "role_display_name",
]
