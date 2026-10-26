# ruff: noqa: S608 - 表名/列名均为模块常量（经 assert_identifier 校验），值一律命名参数绑定。
"""角色与权限点仓储 · PostgreSQL 实现（生产）。

四张表：

| 表 | 作用 |
| --- | --- |
| `permission_catalog` | 20 个权限点目录（平台共享行，`tenant_id='__platform__'`） |
| `role` | 4 个内置角色 + 自定义角色（一企业组织内 `role_key` 唯一） |
| `role_permission` | 角色 → 权限点 |
| `member_role` | 成员 → 角色（一企业一角色） |

表名常量与四个 `_*TableRepository` 放在本文件（而不是 `records.py`）：它们只服务
PG 实现，内存后端用不到；放进 records 会让"数据记录"层混入持久化细节。
"""

from __future__ import annotations

from typing import Final

from app.core.builtin_roles import BUILTIN_ROLE_KEYS, BUILTIN_ROLE_PERMISSIONS
from app.core.config import Settings, get_settings
from app.db.repositories.base import BaseRepository
from app.db.repositories.role.records import (
    PermissionCatalogEntry,
    RoleRecord,
    builtin_role_id,
    role_display_name,
)
from app.db.session import tenant_scope

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


__all__: list[str] = [
    "CATALOG_TABLE",
    "MEMBER_ROLE_TABLE",
    "PLATFORM_TENANT",
    "ROLE_PERMISSION_TABLE",
    "ROLE_TABLE",
    "PostgresRoleStore",
]
