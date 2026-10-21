# ruff: noqa: S608 - 表名/列名均为模块常量（经 assert_identifier 校验），值一律命名参数绑定。
"""组织架构仓储（阶段二 B08，架构文档 2.6 / D-13）。

**业务库是唯一权威源**：`department`（物化路径 + `level ≤ 5`）与
`department_member`（多部门 + 主部门唯一部分索引）。Keycloak 只做单向投影，
**任何判权不得读 KC group**（D-13 第 2 条）。

物化路径约定：`path = "/dept_a/dept_b/"`（首尾都有斜杠），子树查询用
`path LIKE :old_path || '%'`，移动部门 = 原子更新子树 `path` 前缀。

双后端（`OA_BUSINESS_STORE`）：内存实现供 development/test（无 PG 也能全绿），
PostgreSQL 实现为生产唯一路径；两者方法签名与语义完全一致。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Final, Protocol

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.db.repositories.base import BaseRepository
from app.db.session import tenant_scope

logger = get_logger(__name__)

#: 部门表名
DEPARTMENT_TABLE: Final[str] = "department"

#: 部门成员表名
MEMBER_TABLE: Final[str] = "department_member"

#: 组织层级上限（架构文档 2.6 第 5 条；DB 侧另有 CHECK 兜底）
MAX_DEPARTMENT_LEVEL: Final[int] = 5

#: 部门 ID 前缀（唯一规范形态）
DEPARTMENT_ID_PREFIX: Final[str] = "dept_"

#: 根部门挂载点：未指定 `parent_id` 时的一级部门
ROOT_PARENT_ID: Final[str] = ""


def _now_iso() -> str:
    """当前 UTC 时间（ISO 8601）。

    Returns:
        str: 形如 `2026-11-02T09:00:00Z`。
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class DepartmentRecord:
    """部门记录（`department` 行）。

    Attributes:
        tenant_id: 租户 ID。
        department_id: 部门 ID，形如 `dept_01HF8...`。
        org_id: 组织 ID。
        parent_id: 上级部门 ID（一级部门为空串）。
        name: 部门名称（1-64 字）。
        path: 物化路径，形如 `/dept_a/dept_b/`。
        level: 层级（1-5）。
        manager_user_id: 部门负责人用户 ID。
        member_count: 成员数快照。
        sort_order: 排序号。
        deleted_at: 软删除时间（ISO 8601）；未删除为 None。
        created_at: 创建时间。
        updated_at: 更新时间。
    """

    tenant_id: str
    department_id: str
    org_id: str = ""
    parent_id: str = ROOT_PARENT_ID
    name: str = ""
    path: str = ""
    level: int = 1
    manager_user_id: str = ""
    member_count: int = 0
    sort_order: int = 0
    deleted_at: str | None = None
    created_at: str = ""
    updated_at: str = ""

    @property
    def is_deleted(self) -> bool:
        """是否已软删除。"""
        return bool(self.deleted_at)


@dataclass(frozen=True, slots=True)
class MemberLink:
    """成员-部门关联（`department_member` 行）。

    Attributes:
        tenant_id: 租户 ID。
        department_id: 部门 ID。
        user_id: 用户 ID。
        is_primary: 是否主部门（全租户唯一，由部分唯一索引强制）。
        joined_at: 加入时间。
    """

    tenant_id: str
    department_id: str
    user_id: str
    is_primary: bool = False
    joined_at: str = ""


class OrgStore(Protocol):
    """组织架构存储契约（内存 / PostgreSQL 双实现）。"""

    async def create_department(self, record: DepartmentRecord) -> DepartmentRecord:
        """写入部门。"""
        ...

    async def get_department(
        self, tenant_id: str, department_id: str, *, include_deleted: bool = False
    ) -> DepartmentRecord | None:
        """按 ID 读取部门。"""
        ...

    async def list_departments(
        self, tenant_id: str, org_id: str = "", *, include_deleted: bool = False
    ) -> list[DepartmentRecord]:
        """列出组织内部门（平铺）。"""
        ...

    async def update_department(
        self,
        tenant_id: str,
        department_id: str,
        *,
        name: str | None = None,
        parent_id: str | None = None,
        manager_user_id: str | None = None,
    ) -> DepartmentRecord | None:
        """更新部门（仅这三项允许修改）。"""
        ...

    async def move_subtree(
        self, tenant_id: str, org_id: str, old_path: str, new_path_prefix: str, level_delta: int
    ) -> int:
        """原子移动子树（更新 `path` 前缀与 `level`）。"""
        ...

    async def soft_delete_department(self, tenant_id: str, department_id: str) -> bool:
        """软删除部门。"""
        ...

    async def count_children(self, tenant_id: str, department_id: str) -> int:
        """统计未删除的子部门数。"""
        ...

    async def link_member(self, link: MemberLink) -> None:
        """写入成员-部门关联。"""
        ...

    async def unlink_member(self, tenant_id: str, department_id: str, user_id: str) -> bool:
        """删除成员-部门关联。"""
        ...

    async def clear_primary(self, tenant_id: str, user_id: str) -> None:
        """清除该用户的主部门标记。"""
        ...

    async def set_primary(self, tenant_id: str, department_id: str, user_id: str) -> bool:
        """设置主部门标记。"""
        ...

    async def links_of_user(self, tenant_id: str, user_id: str) -> list[MemberLink]:
        """读取某成员的全部部门关联。"""
        ...

    async def links_of_department(self, tenant_id: str, department_id: str) -> list[MemberLink]:
        """读取某部门的全部成员关联。"""
        ...

    async def user_ids(self, tenant_id: str, org_id: str = "") -> list[str]:
        """列出组织内全部成员用户 ID（部门关联去重）。"""
        ...

    async def refresh_member_count(self, tenant_id: str, department_id: str) -> int:
        """按关联表重算并写回成员数。"""
        ...


class InMemoryOrgStore:
    """进程内组织架构存储（development/test）。"""

    def __init__(self) -> None:
        """初始化。"""
        self._departments: dict[tuple[str, str], DepartmentRecord] = {}
        self._links: dict[tuple[str, str, str], MemberLink] = {}
        self._lock = asyncio.Lock()

    async def create_department(self, record: DepartmentRecord) -> DepartmentRecord:
        """写入部门。

        Args:
            record: 部门记录。

        Returns:
            DepartmentRecord: 落库后的记录。
        """
        stamp = record.created_at or _now_iso()
        payload = replace(record, created_at=stamp, updated_at=record.updated_at or stamp)
        async with self._lock:
            self._departments[(record.tenant_id, record.department_id)] = payload
        return payload

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
        async with self._lock:
            record = self._departments.get((tenant_id, department_id))
        if record is None:
            return None
        if record.is_deleted and not include_deleted:
            return None
        return record

    async def list_departments(
        self, tenant_id: str, org_id: str = "", *, include_deleted: bool = False
    ) -> list[DepartmentRecord]:
        """列出组织内部门（平铺，按层级与排序号）。

        Args:
            tenant_id: 租户 ID。
            org_id: 组织 ID（空表示不限）。
            include_deleted: 是否包含已删除部门。

        Returns:
            list[DepartmentRecord]: 部门列表。
        """
        async with self._lock:
            items = [
                record
                for (tid, _), record in self._departments.items()
                if tid == tenant_id
                and (not org_id or record.org_id == org_id)
                and (include_deleted or not record.is_deleted)
            ]
        items.sort(key=lambda item: (item.level, item.sort_order, item.department_id))
        return items

    async def update_department(
        self,
        tenant_id: str,
        department_id: str,
        *,
        name: str | None = None,
        parent_id: str | None = None,
        manager_user_id: str | None = None,
    ) -> DepartmentRecord | None:
        """更新部门。

        Args:
            tenant_id: 租户 ID。
            department_id: 部门 ID。
            name: 新名称。
            parent_id: 新上级部门 ID。
            manager_user_id: 新负责人。

        Returns:
            DepartmentRecord | None: 更新后的记录；不存在返回 None。
        """
        async with self._lock:
            key = (tenant_id, department_id)
            current = self._departments.get(key)
            if current is None or current.is_deleted:
                return None
            changes: dict[str, Any] = {"updated_at": _now_iso()}
            if name is not None:
                changes["name"] = name
            if parent_id is not None:
                changes["parent_id"] = parent_id
            if manager_user_id is not None:
                changes["manager_user_id"] = manager_user_id
            updated = replace(current, **changes)
            self._departments[key] = updated
        return updated

    async def move_subtree(
        self, tenant_id: str, org_id: str, old_path: str, new_path_prefix: str, level_delta: int
    ) -> int:
        """原子移动子树。

        Args:
            tenant_id: 租户 ID。
            org_id: 组织 ID。
            old_path: 原子树路径前缀。
            new_path_prefix: 新路径前缀。
            level_delta: 层级增量（可为负）。

        Returns:
            int: 受影响的部门数。
        """
        affected = 0
        async with self._lock:
            for key, record in list(self._departments.items()):
                if key[0] != tenant_id or record.org_id != org_id or record.is_deleted:
                    continue
                if not record.path.startswith(old_path):
                    continue
                new_path = new_path_prefix + record.path[len(old_path) :]
                self._departments[key] = replace(
                    record, path=new_path, level=record.level + level_delta, updated_at=_now_iso()
                )
                affected += 1
        return affected

    async def soft_delete_department(self, tenant_id: str, department_id: str) -> bool:
        """软删除部门。

        Args:
            tenant_id: 租户 ID。
            department_id: 部门 ID。

        Returns:
            bool: 删除成功返回 True。
        """
        async with self._lock:
            key = (tenant_id, department_id)
            current = self._departments.get(key)
            if current is None or current.is_deleted:
                return False
            self._departments[key] = replace(current, deleted_at=_now_iso(), updated_at=_now_iso())
        return True

    async def count_children(self, tenant_id: str, department_id: str) -> int:
        """统计未删除的子部门数。

        Args:
            tenant_id: 租户 ID。
            department_id: 部门 ID。

        Returns:
            int: 子部门数。
        """
        async with self._lock:
            return sum(
                1
                for (tid, _), record in self._departments.items()
                if tid == tenant_id and record.parent_id == department_id and not record.is_deleted
            )

    async def link_member(self, link: MemberLink) -> None:
        """写入成员-部门关联（幂等）。

        Args:
            link: 关联记录。
        """
        payload = link if link.joined_at else replace(link, joined_at=_now_iso())
        async with self._lock:
            self._links[(link.tenant_id, link.department_id, link.user_id)] = payload

    async def unlink_member(self, tenant_id: str, department_id: str, user_id: str) -> bool:
        """删除成员-部门关联。

        Args:
            tenant_id: 租户 ID。
            department_id: 部门 ID。
            user_id: 用户 ID。

        Returns:
            bool: 存在并删除返回 True。
        """
        async with self._lock:
            return self._links.pop((tenant_id, department_id, user_id), None) is not None

    async def clear_primary(self, tenant_id: str, user_id: str) -> None:
        """清除该用户的主部门标记。

        Args:
            tenant_id: 租户 ID。
            user_id: 用户 ID。
        """
        async with self._lock:
            for key, link in list(self._links.items()):
                if key[0] == tenant_id and link.user_id == user_id and link.is_primary:
                    self._links[key] = replace(link, is_primary=False)

    async def set_primary(self, tenant_id: str, department_id: str, user_id: str) -> bool:
        """设置主部门标记。

        Args:
            tenant_id: 租户 ID。
            department_id: 部门 ID。
            user_id: 用户 ID。

        Returns:
            bool: 关联不存在返回 False。
        """
        async with self._lock:
            key = (tenant_id, department_id, user_id)
            link = self._links.get(key)
            if link is None:
                return False
            self._links[key] = replace(link, is_primary=True)
        return True

    async def links_of_user(self, tenant_id: str, user_id: str) -> list[MemberLink]:
        """读取某成员的全部部门关联。

        Args:
            tenant_id: 租户 ID。
            user_id: 用户 ID。

        Returns:
            list[MemberLink]: 关联列表（主部门在前）。
        """
        async with self._lock:
            items = [
                link
                for (tid, _, uid), link in self._links.items()
                if tid == tenant_id and uid == user_id
            ]
        items.sort(key=lambda item: (not item.is_primary, item.department_id))
        return items

    async def links_of_department(self, tenant_id: str, department_id: str) -> list[MemberLink]:
        """读取某部门的全部成员关联。

        Args:
            tenant_id: 租户 ID。
            department_id: 部门 ID。

        Returns:
            list[MemberLink]: 关联列表。
        """
        async with self._lock:
            return [
                link
                for (tid, did, _), link in self._links.items()
                if tid == tenant_id and did == department_id
            ]

    async def user_ids(self, tenant_id: str, org_id: str = "") -> list[str]:
        """列出组织内全部成员用户 ID（去重）。

        Args:
            tenant_id: 租户 ID。
            org_id: 组织 ID（空表示不限）。

        Returns:
            list[str]: 用户 ID 列表。
        """
        async with self._lock:
            department_ids = {
                record.department_id
                for (tid, _), record in self._departments.items()
                if tid == tenant_id
                and (not org_id or record.org_id == org_id)
                and not record.is_deleted
            }
            users = {
                link.user_id
                for (tid, did, _), link in self._links.items()
                if tid == tenant_id and did in department_ids
            }
        return sorted(users)

    async def refresh_member_count(self, tenant_id: str, department_id: str) -> int:
        """按关联表重算并写回成员数。

        Args:
            tenant_id: 租户 ID。
            department_id: 部门 ID。

        Returns:
            int: 成员数。
        """
        async with self._lock:
            count = sum(
                1 for (tid, did, _) in self._links if tid == tenant_id and did == department_id
            )
            key = (tenant_id, department_id)
            current = self._departments.get(key)
            if current is not None:
                self._departments[key] = replace(current, member_count=count)
        return count

    def clear(self) -> None:
        """清空（测试隔离用）。"""
        self._departments.clear()
        self._links.clear()


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


# ===========================================================================
# 进程级单例
# ===========================================================================
_memory_store: InMemoryOrgStore | None = None


def get_org_store(settings: Settings | None = None) -> OrgStore:
    """获取组织架构存储。

    Args:
        settings: 可选配置实例。

    Returns:
        OrgStore: `business_store=postgres` 时为数据库实现，否则为内存实现。
    """
    global _memory_store

    conf = settings or get_settings()
    if conf.uses_postgres:
        return PostgresOrgStore(conf)
    if _memory_store is None:
        _memory_store = InMemoryOrgStore()
    return _memory_store


def reset_org_store() -> None:
    """重置内存后端单例（测试隔离用）。"""
    global _memory_store
    if _memory_store is not None:
        _memory_store.clear()
    _memory_store = None


class OrgUsageDirectory:
    """计量服务所需的组织信息解析器（D-12 部门维度归属）。

    实现 `UsageService` 期望的 `UsageDirectory` 协议：
    主部门用于写 `usage_monthly(scope_type='department')`，展示名用于 BL5 分项。
    """

    def __init__(self, store: OrgStore) -> None:
        """初始化。

        Args:
            store: 组织架构存储。
        """
        self._store = store

    async def primary_department(self, tenant_id: str, user_id: str) -> str:
        """取用户的主部门 ID。

        Args:
            tenant_id: 租户 ID。
            user_id: 用户 ID。

        Returns:
            str: 主部门 ID；无关联返回空串。
        """
        links = await self._store.links_of_user(tenant_id, user_id)
        for link in links:
            if link.is_primary:
                return link.department_id
        return links[0].department_id if links else ""

    async def display_name(self, tenant_id: str, user_id: str) -> str:
        """取用户展示名。

        成员展示名由身份底座提供，本阶段以用户 ID 兜底（不虚构姓名）。

        Args:
            tenant_id: 租户 ID。
            user_id: 用户 ID。

        Returns:
            str: 展示名。
        """
        del tenant_id
        return user_id


def get_org_directory(settings: Settings | None = None) -> OrgUsageDirectory:
    """获取计量服务所需的组织信息解析器。

    Args:
        settings: 可选配置实例。

    Returns:
        OrgUsageDirectory: 组织信息解析器。
    """
    return OrgUsageDirectory(get_org_store(settings))


def normalize_department_id(raw: str) -> str:
    """把部门 ID 归一化为唯一规范形态（`dept_` 前缀，B08 的「一个对象一个 ID」）。

    Args:
        raw: 原始部门 ID（`dept_...` 或裸 ID）。

    Returns:
        str: 规范形态；入参为空返回空串。

    Raises:
        ApiError: 前缀形态非法（`deptxxx` 这类既非规范也不是裸 ID 的写法一律 20001）。
    """
    value = str(raw or "").strip()
    if not value:
        return ""
    if value.startswith(DEPARTMENT_ID_PREFIX):
        return value
    if not value.replace("-", "").replace("_", "").isalnum():
        raise ApiError(ErrorCode.PARAM_INVALID, internal_detail=f"illegal department id: {raw!r}")
    return f"{DEPARTMENT_ID_PREFIX}{value}"


__all__: list[str] = [
    "DEPARTMENT_ID_PREFIX",
    "DEPARTMENT_TABLE",
    "MAX_DEPARTMENT_LEVEL",
    "MEMBER_TABLE",
    "ROOT_PARENT_ID",
    "DepartmentRecord",
    "InMemoryOrgStore",
    "MemberLink",
    "OrgStore",
    "OrgUsageDirectory",
    "PostgresOrgStore",
    "get_org_directory",
    "get_org_store",
    "normalize_department_id",
    "reset_org_store",
]
