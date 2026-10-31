# ruff: noqa: S608 - 表名/列名均为模块常量（经 assert_identifier 校验），值一律命名参数绑定。


"""组织架构仓储 · PostgreSQL 实现（生产）。

两张表：`department`（物化路径 + `level ≤ 5`）与 `department_member`
（多部门 + 主部门唯一部分索引）。

**业务库是唯一权威源**：Keycloak 只做单向投影，**任何判权不得读 KC group**（D-13 第 2 条）。

物化路径约定：`path = "/dept_a/dept_b/"`（首尾都有斜杠），子树查询用
`path LIKE :old_path || '%'`，移动部门 = 原子更新子树 `path` 前缀。

本文件还承载**行映射器**（`_to_department` / `_to_link`）与时间还原
（`_to_timestamp` / `_to_optional_timestamp`）：它们只服务"从 PG 行还原 record"，
内存后端直接拿对象，用不到。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Final

from app.core.config import Settings, get_settings
from app.db.repositories.base import BaseRepository
from app.db.repositories.org.records import (
    DepartmentRecord,
    MemberLink,
)
from app.db.session import tenant_scope

#: 部门表名
DEPARTMENT_TABLE: Final[str] = "department"

#: 部门成员表名
MEMBER_TABLE: Final[str] = "department_member"

#: 批量关联查询的单批用户数上限（避免一次生成过多占位符）
_LINK_BATCH_SIZE: Final[int] = 200


class _DepartmentTableRepository(BaseRepository):
    """`department` 表仓储（强制租户条件）。"""

    table = DEPARTMENT_TABLE


class _MemberTableRepository(BaseRepository):
    """`department_member` 表仓储（强制租户条件）。"""

    table = MEMBER_TABLE


class PostgresOrgStore:
    """PostgreSQL 组织架构存储（一次调用一个短事务）。"""

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化。

        Args:
            settings: 可选配置实例。
        """
        self._settings = settings or get_settings()

    async def create_department(self, record: DepartmentRecord) -> DepartmentRecord:
        """写入部门。

        Args:
            record: 部门记录。

        Returns:
            DepartmentRecord: 落库后的记录。
        """
        async with tenant_scope(record.tenant_id, settings=self._settings) as uow:
            repo = _DepartmentTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                INSERT INTO {DEPARTMENT_TABLE} (
                    tenant_id, department_id, org_id, parent_id, name, path, level,
                    manager_user_id, member_count, sort_order, created_at, updated_at
                ) VALUES (
                    :tenant_id, :department_id, :org_id, NULLIF(:parent_id, ''), :name, :path,
                    :level, NULLIF(:manager_user_id, ''), :member_count, :sort_order, now(), now()
                )
                ON CONFLICT (tenant_id, department_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    parent_id = EXCLUDED.parent_id,
                    path = EXCLUDED.path,
                    level = EXCLUDED.level,
                    manager_user_id = EXCLUDED.manager_user_id,
                    updated_at = now()
                RETURNING tenant_id, department_id, org_id, parent_id, name, path, level,
                          manager_user_id, member_count, sort_order, deleted_at,
                          created_at, updated_at
                """,
                repo.params(
                    department_id=record.department_id,
                    org_id=record.org_id,
                    parent_id=record.parent_id,
                    name=record.name,
                    path=record.path,
                    level=record.level,
                    manager_user_id=record.manager_user_id,
                    member_count=record.member_count,
                    sort_order=record.sort_order,
                ),
            )
        return _to_department(row) if row is not None else record

    async def get_department(
        self, tenant_id: str, department_id: str, *, include_deleted: bool = False
    ) -> DepartmentRecord | None:
        """按 ID 读取部门。

        Args:
            tenant_id: 租户 ID。
            department_id: 部门 ID。
            include_deleted: 是否包含已删除部门。

        Returns:
            DepartmentRecord | None: 部门记录或 None。
        """
        deleted_filter = "" if include_deleted else "AND deleted_at IS NULL"
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _DepartmentTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                SELECT tenant_id, department_id, org_id, parent_id, name, path, level,
                       manager_user_id, member_count, sort_order, deleted_at,
                       created_at, updated_at
                FROM {DEPARTMENT_TABLE}
                WHERE {repo.tenant_clause()} AND department_id = :department_id {deleted_filter}
                """,
                repo.params(department_id=department_id),
            )
        return _to_department(row) if row is not None else None

    async def list_departments(
        self, tenant_id: str, org_id: str = "", *, include_deleted: bool = False
    ) -> list[DepartmentRecord]:
        """列出组织内部门（平铺）。

        Args:
            tenant_id: 租户 ID。
            org_id: 组织 ID（空表示不限）。
            include_deleted: 是否包含已删除部门。

        Returns:
            list[DepartmentRecord]: 部门列表。
        """
        org_filter = "AND org_id = :org_id" if org_id else ""
        deleted_filter = "" if include_deleted else "AND deleted_at IS NULL"
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _DepartmentTableRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT tenant_id, department_id, org_id, parent_id, name, path, level,
                       manager_user_id, member_count, sort_order, deleted_at,
                       created_at, updated_at
                FROM {DEPARTMENT_TABLE}
                WHERE {repo.tenant_clause()} {org_filter} {deleted_filter}
                ORDER BY level, sort_order, department_id
                """,
                repo.params(org_id=org_id),
            )
        return [_to_department(row) for row in rows]

    async def update_department(
        self,
        tenant_id: str,
        department_id: str,
        *,
        name: str | None = None,
        parent_id: str | None = None,
        manager_user_id: str | None = None,
    ) -> DepartmentRecord | None:
        """更新部门（列名为常量白名单，值走参数绑定）。

        Args:
            tenant_id: 租户 ID。
            department_id: 部门 ID。
            name: 新名称。
            parent_id: 新上级部门 ID。
            manager_user_id: 新负责人。

        Returns:
            DepartmentRecord | None: 更新后的记录；不存在返回 None。
        """
        assignments = ["updated_at = now()"]
        parameters: dict[str, Any] = {"department_id": department_id}
        if name is not None:
            assignments.append("name = :name")
            parameters["name"] = name
        if parent_id is not None:
            assignments.append("parent_id = NULLIF(:parent_id, '')")
            parameters["parent_id"] = parent_id
        if manager_user_id is not None:
            assignments.append("manager_user_id = NULLIF(:manager_user_id, '')")
            parameters["manager_user_id"] = manager_user_id
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _DepartmentTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                UPDATE {DEPARTMENT_TABLE}
                SET {", ".join(assignments)}
                WHERE {repo.tenant_clause()} AND department_id = :department_id
                  AND deleted_at IS NULL
                RETURNING tenant_id, department_id, org_id, parent_id, name, path, level,
                          manager_user_id, member_count, sort_order, deleted_at,
                          created_at, updated_at
                """,
                repo.params(**parameters),
            )
        return _to_department(row) if row is not None else None

    async def move_subtree(
        self, tenant_id: str, org_id: str, old_path: str, new_path_prefix: str, level_delta: int
    ) -> int:
        """原子移动子树（单条 UPDATE，`path` 前缀替换）。

        Args:
            tenant_id: 租户 ID。
            org_id: 组织 ID。
            old_path: 原子树路径前缀。
            new_path_prefix: 新路径前缀。
            level_delta: 层级增量。

        Returns:
            int: 受影响部门数。
        """
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _DepartmentTableRepository(uow)
            result = await repo.execute(
                f"""
                UPDATE {DEPARTMENT_TABLE}
                SET path = :new_path_prefix || substring(path FROM char_length(:old_path) + 1),
                    level = level + :level_delta,
                    updated_at = now()
                WHERE {repo.tenant_clause()} AND org_id = :org_id
                  AND path LIKE :old_path || '%'
                  AND deleted_at IS NULL
                """,
                repo.params(
                    org_id=org_id,
                    old_path=old_path,
                    new_path_prefix=new_path_prefix,
                    level_delta=level_delta,
                ),
            )
        return int(getattr(result, "rowcount", 0) or 0)

    async def soft_delete_department(self, tenant_id: str, department_id: str) -> bool:
        """软删除部门。

        Args:
            tenant_id: 租户 ID。
            department_id: 部门 ID。

        Returns:
            bool: 删除成功返回 True。
        """
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _DepartmentTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                UPDATE {DEPARTMENT_TABLE}
                SET deleted_at = now(), updated_at = now()
                WHERE {repo.tenant_clause()} AND department_id = :department_id
                  AND deleted_at IS NULL
                RETURNING department_id
                """,
                repo.params(department_id=department_id),
            )
        return row is not None

    async def count_children(self, tenant_id: str, department_id: str) -> int:
        """统计未删除的子部门数。

        Args:
            tenant_id: 租户 ID。
            department_id: 部门 ID。

        Returns:
            int: 子部门数。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _DepartmentTableRepository(uow)
            total = await repo.fetch_scalar(
                f"""
                SELECT COUNT(*) FROM {DEPARTMENT_TABLE}
                WHERE {repo.tenant_clause()} AND parent_id = :department_id
                  AND deleted_at IS NULL
                """,
                repo.params(department_id=department_id),
            )
        return int(total or 0)

    async def link_member(self, link: MemberLink) -> None:
        """写入成员-部门关联（幂等）。

        Args:
            link: 关联记录。
        """
        async with tenant_scope(link.tenant_id, settings=self._settings) as uow:
            repo = _MemberTableRepository(uow)
            await repo.execute(
                f"""
                INSERT INTO {MEMBER_TABLE} (
                    tenant_id, department_id, user_id, is_primary, joined_at
                ) VALUES (:tenant_id, :department_id, :user_id, :is_primary, now())
                ON CONFLICT (tenant_id, department_id, user_id) DO UPDATE
                SET is_primary = EXCLUDED.is_primary
                """,
                repo.params(
                    department_id=link.department_id,
                    user_id=link.user_id,
                    is_primary=link.is_primary,
                ),
            )

    async def unlink_member(self, tenant_id: str, department_id: str, user_id: str) -> bool:
        """删除成员-部门关联。

        Args:
            tenant_id: 租户 ID。
            department_id: 部门 ID。
            user_id: 用户 ID。

        Returns:
            bool: 存在并删除返回 True。
        """
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _MemberTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                DELETE FROM {MEMBER_TABLE}
                WHERE {repo.tenant_clause()} AND department_id = :department_id
                  AND user_id = :user_id
                RETURNING user_id
                """,
                repo.params(department_id=department_id, user_id=user_id),
            )
        return row is not None

    async def clear_primary(self, tenant_id: str, user_id: str) -> None:
        """清除该用户的主部门标记（部分唯一索引要求先清后设）。

        Args:
            tenant_id: 租户 ID。
            user_id: 用户 ID。
        """
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _MemberTableRepository(uow)
            await repo.execute(
                f"""
                UPDATE {MEMBER_TABLE} SET is_primary = false
                WHERE {repo.tenant_clause()} AND user_id = :user_id AND is_primary
                """,
                repo.params(user_id=user_id),
            )

    async def set_primary(self, tenant_id: str, department_id: str, user_id: str) -> bool:
        """设置主部门标记。

        Args:
            tenant_id: 租户 ID。
            department_id: 部门 ID。
            user_id: 用户 ID。

        Returns:
            bool: 关联不存在返回 False。
        """
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _MemberTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                UPDATE {MEMBER_TABLE} SET is_primary = true
                WHERE {repo.tenant_clause()} AND department_id = :department_id
                  AND user_id = :user_id
                RETURNING user_id
                """,
                repo.params(department_id=department_id, user_id=user_id),
            )
        return row is not None

    async def links_of_user(self, tenant_id: str, user_id: str) -> list[MemberLink]:
        """读取某成员的全部部门关联。

        Args:
            tenant_id: 租户 ID。
            user_id: 用户 ID。

        Returns:
            list[MemberLink]: 关联列表。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _MemberTableRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT tenant_id, department_id, user_id, is_primary, joined_at
                FROM {MEMBER_TABLE}
                WHERE {repo.tenant_clause()} AND user_id = :user_id
                ORDER BY is_primary DESC, department_id
                """,
                repo.params(user_id=user_id),
            )
        return [_to_link(row) for row in rows]

    async def links_of_users(
        self, tenant_id: str, user_ids: list[str]
    ) -> dict[str, list[MemberLink]]:
        """批量读取多个成员的部门关联（成员列表页消除 N+1）。

        Args:
            tenant_id: 租户 ID。
            user_ids: 用户 ID 列表。

        Returns:
            dict[str, list[MemberLink]]: 以用户 ID 为键的关联列表（主部门在前）。
        """
        if not user_ids:
            return {}
        grouped: dict[str, list[MemberLink]] = {}
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _MemberTableRepository(uow)
            for start in range(0, len(user_ids), _LINK_BATCH_SIZE):
                chunk = user_ids[start : start + _LINK_BATCH_SIZE]
                placeholders = ", ".join(f":u{index}" for index in range(len(chunk)))
                rows = await repo.fetch_all(
                    f"""
                    SELECT tenant_id, department_id, user_id, is_primary, joined_at
                    FROM {MEMBER_TABLE}
                    WHERE {repo.tenant_clause()} AND user_id IN ({placeholders})
                    ORDER BY user_id, is_primary DESC, department_id
                    """,
                    repo.params(**{f"u{index}": uid for index, uid in enumerate(chunk)}),
                )
                for row in rows:
                    link = _to_link(row)
                    grouped.setdefault(link.user_id, []).append(link)
        return grouped

    async def links_of_department(self, tenant_id: str, department_id: str) -> list[MemberLink]:
        """读取某部门的全部成员关联。

        Args:
            tenant_id: 租户 ID。
            department_id: 部门 ID。

        Returns:
            list[MemberLink]: 关联列表。
        """
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _MemberTableRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT tenant_id, department_id, user_id, is_primary, joined_at
                FROM {MEMBER_TABLE}
                WHERE {repo.tenant_clause()} AND department_id = :department_id
                """,
                repo.params(department_id=department_id),
            )
        return [_to_link(row) for row in rows]

    async def user_ids(self, tenant_id: str, org_id: str = "") -> list[str]:
        """列出组织内全部成员用户 ID（去重）。

        Args:
            tenant_id: 租户 ID。
            org_id: 组织 ID（空表示不限）。

        Returns:
            list[str]: 用户 ID 列表。
        """
        org_filter = (
            f"AND dm.department_id IN (SELECT department_id FROM {DEPARTMENT_TABLE} "
            "WHERE tenant_id = :tenant_id AND org_id = :org_id AND deleted_at IS NULL)"
            if org_id
            else ""
        )
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _MemberTableRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT DISTINCT dm.user_id
                FROM {MEMBER_TABLE} dm
                WHERE {repo.tenant_clause("dm")} {org_filter}
                ORDER BY dm.user_id
                """,
                repo.params(org_id=org_id),
            )
        return [str(row.get("user_id", "")) for row in rows]

    async def refresh_member_count(self, tenant_id: str, department_id: str) -> int:
        """按关联表重算并写回成员数。

        Args:
            tenant_id: 租户 ID。
            department_id: 部门 ID。

        Returns:
            int: 成员数。
        """
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _MemberTableRepository(uow)
            total = await repo.fetch_scalar(
                f"""
                SELECT COUNT(*) FROM {MEMBER_TABLE}
                WHERE {repo.tenant_clause()} AND department_id = :department_id
                """,
                repo.params(department_id=department_id),
            )
            department_repo = _DepartmentTableRepository(uow)
            await department_repo.execute(
                f"""
                UPDATE {DEPARTMENT_TABLE} SET member_count = :member_count, updated_at = now()
                WHERE {department_repo.tenant_clause()} AND department_id = :department_id
                """,
                department_repo.params(member_count=int(total or 0), department_id=department_id),
            )
        return int(total or 0)


def _to_department(row: Mapping[str, Any]) -> DepartmentRecord:
    """把数据库行映射为部门记录。

    Args:
        row: 行映射。

    Returns:
        DepartmentRecord: 部门记录。
    """
    return DepartmentRecord(
        tenant_id=str(row.get("tenant_id", "")),
        department_id=str(row.get("department_id", "")),
        org_id=str(row.get("org_id", "") or ""),
        parent_id=str(row.get("parent_id", "") or ""),
        name=str(row.get("name", "")),
        path=str(row.get("path", "") or ""),
        level=int(row.get("level", 1) or 1),
        manager_user_id=str(row.get("manager_user_id", "") or ""),
        member_count=int(row.get("member_count", 0) or 0),
        sort_order=int(row.get("sort_order", 0) or 0),
        deleted_at=_to_optional_timestamp(row.get("deleted_at")),
        created_at=_to_timestamp(row.get("created_at")),
        updated_at=_to_timestamp(row.get("updated_at")),
    )


def _to_link(row: Mapping[str, Any]) -> MemberLink:
    """把数据库行映射为成员关联。

    Args:
        row: 行映射。

    Returns:
        MemberLink: 成员关联。
    """
    return MemberLink(
        tenant_id=str(row.get("tenant_id", "")),
        department_id=str(row.get("department_id", "")),
        user_id=str(row.get("user_id", "")),
        is_primary=bool(row.get("is_primary", False)),
        joined_at=_to_timestamp(row.get("joined_at")),
    )


def _to_timestamp(value: Any) -> str:
    """把数据库时间值规范化为 ISO 8601 字符串。

    Args:
        value: 时间值。

    Returns:
        str: ISO 8601 字符串；空值返回空串。
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)


def _to_optional_timestamp(value: Any) -> str | None:
    """把数据库时间值规范化为可空 ISO 8601 字符串。

    Args:
        value: 时间值。

    Returns:
        str | None: ISO 8601 字符串；空值返回 None。
    """
    if value is None:
        return None
    return _to_timestamp(value)


__all__: list[str] = [
    "DEPARTMENT_TABLE",
    "MEMBER_TABLE",
    "PostgresOrgStore",
]
