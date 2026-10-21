# ruff: noqa: S608 - 表名均为模块常量，值全部使用命名参数绑定。
"""知识库授权仓储（B05，内存与 PostgreSQL 双实现）。

授权行以 ``(tenant_id, base_id, subject_type, subject_id)`` 为有效唯一键；撤销只写
``revoked_at``，保留操作痕迹。部门与角色授权在每次可见性判断时解析当前成员关系，
因此成员加入或离开部门后无需改授权行即可生效。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Final, Protocol

from app.core.config import Settings, get_settings
from app.db.repositories.base import BaseRepository, assert_identifier
from app.db.session import tenant_scope

GRANT_TABLE: Final[str] = "knowledge_base_grant"
_DEPARTMENT_TABLE: Final[str] = "department"
_DEPARTMENT_MEMBER_TABLE: Final[str] = "department_member"
_MEMBER_ROLE_TABLE: Final[str] = "member_role"
_ROLE_TABLE: Final[str] = "role"

#: 授权对象匹配条件（用户直授 / 部门授权含子部门 / 角色授权），表别名固定为 `g`。
#:
#: 部门与角色一律**实时解析**当前成员关系（架构文档 2.2 第 4 条）：
#: 成员加入或离开部门后无需改授权行即可生效，这也是「移出部门 ≤5s 失效」的基础。
_SUBJECT_MATCH_SQL: Final[str] = f"""
                  (
                    (g.subject_type = 'user' AND g.subject_id = :user_id)
                    OR (
                      g.subject_type = 'department' AND (
                        g.subject_id = ANY(string_to_array(CAST(:department_ids AS text), ','))
                        OR EXISTS (
                          SELECT 1
                          FROM {_DEPARTMENT_MEMBER_TABLE} dm
                          JOIN {_DEPARTMENT_TABLE} child
                            ON child.tenant_id = dm.tenant_id
                           AND child.department_id = dm.department_id
                          JOIN {_DEPARTMENT_TABLE} parent
                            ON parent.tenant_id = child.tenant_id
                           AND parent.department_id = g.subject_id
                          WHERE dm.tenant_id = :tenant_id AND dm.user_id = :user_id
                            AND child.deleted_at IS NULL AND parent.deleted_at IS NULL
                            AND (child.department_id = parent.department_id
                                 OR child.path LIKE parent.path || '%')
                        )
                      )
                    )
                    OR (
                      g.subject_type = 'role' AND (
                        g.subject_id = ANY(string_to_array(CAST(:role_ids AS text), ','))
                        OR EXISTS (
                          SELECT 1 FROM {_MEMBER_ROLE_TABLE} mr
                          WHERE mr.tenant_id = :tenant_id AND mr.user_id = :user_id
                            AND mr.role_id = g.subject_id
                        )
                      )
                    )
                  )
"""


def _now_iso() -> str:
    """返回当前 UTC ISO 8601 时间。"""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class GrantRecord:
    """一条知识库授权记录。"""

    tenant_id: str
    grant_id: str
    base_id: str
    subject_type: str
    subject_id: str
    permission: str = "viewer"
    granted_by: str = ""
    granted_at: str = ""
    revoked_at: str | None = None

    @property
    def is_active(self) -> bool:
        """授权是否仍有效。"""
        return self.revoked_at is None


def normalize_department_subject(subject_id: str) -> str:
    """把部门型授权对象的 ID 归一化为唯一规范形态（`dept_` 前缀）。

    **一个对象一个 ID**（W1 §5.5 修复建议 3 的推广）：授权表按字符串相等匹配
    `subject_id`，若写入侧允许 `qa_form` 与 `dept_qa_form` 两种写法并存，同一物理
    部门的两条授权只会命中其中一条 → 「授权建成功但没人拿到权限」的静默失效
    （QA `test_perm_2`）。因此写侧统一收敛为规范形态。

    无法归一化的值（含非法字符）原样返回：授权表不接受「猜」，宁可在写入时保留
    原文并由人工核对，也不静默改写为别的对象。

    Args:
        subject_id: 授权对象 ID（任意写法）。

    Returns:
        str: 规范形态；入参无法识别时原样返回。
    """
    from app.db.repositories.org_repo import DEPARTMENT_ID_PREFIX, normalize_department_id

    value = str(subject_id or "").strip()
    if not value or value.startswith(DEPARTMENT_ID_PREFIX):
        return value
    try:
        return normalize_department_id(value)
    except Exception:  # pragma: no cover - 非法形态保持原样，不做静默改写
        return value


class GrantStore(Protocol):
    """授权存储契约。"""

    async def create(self, record: GrantRecord) -> tuple[GrantRecord, bool]:
        """幂等新增授权，返回记录与是否实际创建。"""
        ...

    async def get(self, tenant_id: str, grant_id: str) -> GrantRecord | None:
        """读取一条有效授权。"""
        ...

    async def list(
        self, tenant_id: str, base_id: str, *, limit: int = 20, offset: int = 0
    ) -> tuple[list[GrantRecord], int]:
        """分页读取知识库的有效授权。"""
        ...

    async def revoke(self, tenant_id: str, grant_id: str) -> GrantRecord | None:
        """撤销授权；不存在或已撤销返回 None。"""
        ...

    async def resolve_permission(
        self,
        tenant_id: str,
        base_id: str,
        user_id: str,
        *,
        department_ids: Sequence[str] = (),
        role_ids: Sequence[str] = (),
    ) -> str | None:
        """按当前用户、部门和角色关系解析最高授权。"""
        ...

    async def resolve_permissions(
        self,
        tenant_id: str,
        user_id: str,
        *,
        department_ids: Sequence[str] = (),
        role_ids: Sequence[str] = (),
    ) -> dict[str, str]:
        """批量解析该用户在当前租户下可访问的全部知识库及授权级别。

        一次查询取回全部可见库，避免「按库逐个查授权」的 N+1（架构文档 6.3）。
        """
        ...

    async def resolve_subject_names(
        self, tenant_id: str, subject_type: str, subject_ids: Sequence[str]
    ) -> dict[str, str]:
        """解析授权对象的展示名（部门名 / 角色名）；无法解析的对象不出现在结果中。"""
        ...


class InMemoryGrantStore:
    """进程内授权存储。"""

    def __init__(self) -> None:
        """初始化空存储。"""
        self._records: dict[tuple[str, str], GrantRecord] = {}
        self._departments: dict[tuple[str, str], set[str]] = {}
        self._roles: dict[tuple[str, str], set[str]] = {}
        self._subject_names: dict[tuple[str, str, str], str] = {}
        self._lock = asyncio.Lock()

    async def create(self, record: GrantRecord) -> tuple[GrantRecord, bool]:
        """幂等新增授权（部门型 subject 先归一化为 `dept_` 前缀）。"""
        if record.subject_type == "department":
            record = replace(record, subject_id=normalize_department_subject(record.subject_id))
        async with self._lock:
            for current in self._records.values():
                if (
                    current.tenant_id == record.tenant_id
                    and current.base_id == record.base_id
                    and current.subject_type == record.subject_type
                    and current.subject_id == record.subject_id
                    and current.is_active
                ):
                    return current, False
            payload = record if record.granted_at else replace(record, granted_at=_now_iso())
            self._records[(payload.tenant_id, payload.grant_id)] = payload
            return payload, True

    async def get(self, tenant_id: str, grant_id: str) -> GrantRecord | None:
        """读取有效授权。"""
        async with self._lock:
            record = self._records.get((tenant_id, grant_id))
        return record if record is not None and record.is_active else None

    async def list(
        self, tenant_id: str, base_id: str, *, limit: int = 20, offset: int = 0
    ) -> tuple[list[GrantRecord], int]:
        """分页读取有效授权。"""
        async with self._lock:
            records = [
                item
                for item in self._records.values()
                if item.tenant_id == tenant_id and item.base_id == base_id and item.is_active
            ]
        records.sort(key=lambda item: (item.granted_at, item.grant_id), reverse=True)
        return records[offset : offset + limit], len(records)

    async def revoke(self, tenant_id: str, grant_id: str) -> GrantRecord | None:
        """撤销一条授权。"""
        async with self._lock:
            key = (tenant_id, grant_id)
            current = self._records.get(key)
            if current is None or not current.is_active:
                return None
            revoked = replace(current, revoked_at=_now_iso())
            self._records[key] = revoked
        return revoked

    async def resolve_permission(
        self,
        tenant_id: str,
        base_id: str,
        user_id: str,
        *,
        department_ids: Sequence[str] = (),
        role_ids: Sequence[str] = (),
    ) -> str | None:
        """按当前成员关系解析最高授权。"""
        permissions = await self.resolve_permissions(
            tenant_id, user_id, department_ids=department_ids, role_ids=role_ids
        )
        return permissions.get(base_id)

    async def resolve_permissions(
        self,
        tenant_id: str,
        user_id: str,
        *,
        department_ids: Sequence[str] = (),
        role_ids: Sequence[str] = (),
    ) -> dict[str, str]:
        """批量解析该用户可见的知识库与授权级别。"""
        effective_departments = set(department_ids)
        effective_roles = set(role_ids)
        async with self._lock:
            effective_departments.update(self._departments.get((tenant_id, user_id), set()))
            effective_roles.update(self._roles.get((tenant_id, user_id), set()))
            matches = [
                item
                for item in self._records.values()
                if item.tenant_id == tenant_id
                and item.is_active
                and (
                    (item.subject_type == "user" and item.subject_id == user_id)
                    or (
                        item.subject_type == "department"
                        and item.subject_id in effective_departments
                    )
                    or (item.subject_type == "role" and item.subject_id in effective_roles)
                )
            ]
        permissions: dict[str, str] = {}
        for item in matches:
            if permissions.get(item.base_id) == "editor":
                continue
            permissions[item.base_id] = item.permission
        return permissions

    async def resolve_subject_names(
        self, tenant_id: str, subject_type: str, subject_ids: Sequence[str]
    ) -> dict[str, str]:
        """解析授权对象展示名。"""
        async with self._lock:
            return {
                subject_id: self._subject_names[(tenant_id, subject_type, subject_id)]
                for subject_id in subject_ids
                if (tenant_id, subject_type, subject_id) in self._subject_names
            }

    def set_user_departments(
        self, tenant_id: str, user_id: str, department_ids: Sequence[str]
    ) -> None:
        """设置测试/内存模式下用户当前所在部门。"""
        self._departments[(tenant_id, user_id)] = set(department_ids)

    def set_user_roles(self, tenant_id: str, user_id: str, role_ids: Sequence[str]) -> None:
        """设置测试/内存模式下用户当前角色。"""
        self._roles[(tenant_id, user_id)] = set(role_ids)

    def set_subject_name(
        self, tenant_id: str, subject_type: str, subject_id: str, display_name: str
    ) -> None:
        """设置测试/内存模式下授权对象的展示名。"""
        self._subject_names[(tenant_id, subject_type, subject_id)] = display_name

    def clear(self) -> None:
        """清空内存数据。"""
        self._records.clear()
        self._departments.clear()
        self._roles.clear()
        self._subject_names.clear()


class _GrantTableRepository(BaseRepository):
    """授权表仓储。"""

    table = GRANT_TABLE


class _NameLookupRepository(BaseRepository):
    """展示名查询仓储（部门 / 角色，仅做名称回填，不参与判权）。"""

    table = _DEPARTMENT_TABLE


class PostgresGrantStore:
    """PostgreSQL 授权存储（每次调用使用一个租户短事务）。"""

    def __init__(self, settings: Settings | None = None) -> None:
        """初始化存储。"""
        self._settings = settings or get_settings()

    async def create(self, record: GrantRecord) -> tuple[GrantRecord, bool]:
        """幂等新增授权（部门型 subject 先归一化为 `dept_` 前缀）。"""
        if record.subject_type == "department":
            record = replace(record, subject_id=normalize_department_subject(record.subject_id))
        async with tenant_scope(record.tenant_id, settings=self._settings) as uow:
            repo = _GrantTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                INSERT INTO {GRANT_TABLE} (
                    tenant_id, grant_id, base_id, subject_type, subject_id,
                    permission, granted_by, granted_at
                ) VALUES (
                    :tenant_id, :grant_id, :base_id, :subject_type, :subject_id,
                    :permission, :granted_by, now()
                )
                ON CONFLICT (tenant_id, base_id, subject_type, subject_id)
                    WHERE revoked_at IS NULL DO NOTHING
                RETURNING tenant_id, grant_id, base_id, subject_type, subject_id,
                          permission, granted_by, granted_at, revoked_at
                """,
                repo.params(
                    grant_id=record.grant_id,
                    base_id=record.base_id,
                    subject_type=record.subject_type,
                    subject_id=record.subject_id,
                    permission=record.permission,
                    granted_by=record.granted_by,
                ),
            )
            if row is not None:
                return _to_record(row), True
            existing = await repo.fetch_one(
                f"""
                SELECT tenant_id, grant_id, base_id, subject_type, subject_id,
                       permission, granted_by, granted_at, revoked_at
                FROM {GRANT_TABLE}
                WHERE {repo.tenant_clause()} AND base_id = :base_id
                  AND subject_type = :subject_type AND subject_id = :subject_id
                  AND revoked_at IS NULL
                """,
                repo.params(
                    base_id=record.base_id,
                    subject_type=record.subject_type,
                    subject_id=record.subject_id,
                ),
            )
        return (_to_record(existing) if existing is not None else record), False

    async def get(self, tenant_id: str, grant_id: str) -> GrantRecord | None:
        """读取有效授权。"""
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _GrantTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                SELECT tenant_id, grant_id, base_id, subject_type, subject_id,
                       permission, granted_by, granted_at, revoked_at
                FROM {GRANT_TABLE}
                WHERE {repo.tenant_clause()} AND grant_id = :grant_id
                  AND revoked_at IS NULL
                """,
                repo.params(grant_id=grant_id),
            )
        return _to_record(row) if row is not None else None

    async def list(
        self, tenant_id: str, base_id: str, *, limit: int = 20, offset: int = 0
    ) -> tuple[list[GrantRecord], int]:
        """分页读取有效授权。"""
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _GrantTableRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT tenant_id, grant_id, base_id, subject_type, subject_id,
                       permission, granted_by, granted_at, revoked_at
                FROM {GRANT_TABLE}
                WHERE {repo.tenant_clause()} AND base_id = :base_id
                  AND revoked_at IS NULL
                ORDER BY granted_at DESC, grant_id DESC
                LIMIT :limit OFFSET :offset
                """,
                repo.params(base_id=base_id, limit=limit, offset=offset),
            )
            total = await repo.fetch_scalar(
                f"""
                SELECT COUNT(*) FROM {GRANT_TABLE}
                WHERE {repo.tenant_clause()} AND base_id = :base_id
                  AND revoked_at IS NULL
                """,
                repo.params(base_id=base_id),
            )
        return [_to_record(row) for row in rows], int(total or 0)

    async def revoke(self, tenant_id: str, grant_id: str) -> GrantRecord | None:
        """撤销授权。"""
        async with tenant_scope(tenant_id, settings=self._settings) as uow:
            repo = _GrantTableRepository(uow)
            row = await repo.fetch_one(
                f"""
                UPDATE {GRANT_TABLE} SET revoked_at = now()
                WHERE {repo.tenant_clause()} AND grant_id = :grant_id
                  AND revoked_at IS NULL
                RETURNING tenant_id, grant_id, base_id, subject_type, subject_id,
                          permission, granted_by, granted_at, revoked_at
                """,
                repo.params(grant_id=grant_id),
            )
        return _to_record(row) if row is not None else None

    async def resolve_permission(
        self,
        tenant_id: str,
        base_id: str,
        user_id: str,
        *,
        department_ids: Sequence[str] = (),
        role_ids: Sequence[str] = (),
    ) -> str | None:
        """实时解析成员、部门（含子部门）及角色授权。"""
        permissions = await self.resolve_permissions(
            tenant_id, user_id, department_ids=department_ids, role_ids=role_ids
        )
        return permissions.get(base_id)

    async def resolve_permissions(
        self,
        tenant_id: str,
        user_id: str,
        *,
        department_ids: Sequence[str] = (),
        role_ids: Sequence[str] = (),
    ) -> dict[str, str]:
        """批量解析该用户可见的知识库与授权级别（单条 SQL，避免 N+1）。"""
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _GrantTableRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT g.base_id,
                       CASE WHEN COUNT(*) FILTER (WHERE g.permission = 'editor') > 0
                            THEN 'editor' ELSE 'viewer' END AS permission
                FROM {GRANT_TABLE} g
                WHERE {repo.tenant_clause("g")} AND g.revoked_at IS NULL
                  AND {_SUBJECT_MATCH_SQL}
                GROUP BY g.base_id
                """,
                repo.params(
                    user_id=user_id,
                    department_ids=",".join(department_ids),
                    role_ids=",".join(role_ids),
                ),
            )
        return {str(row.get("base_id", "")): str(row.get("permission", "viewer")) for row in rows}

    async def resolve_subject_names(
        self, tenant_id: str, subject_type: str, subject_ids: Sequence[str]
    ) -> dict[str, str]:
        """解析授权对象展示名（部门名 / 角色名）。

        成员展示名由身份底座提供，本阶段不落地用户表，故 `user` 类型返回空映射，
        由调用方回落到主体 ID（前端以「成员」占位，待 B08 组织域接入后补齐）。
        """
        if not subject_ids:
            return {}
        if subject_type == "department":
            table, id_column, name_column = _DEPARTMENT_TABLE, "department_id", "name"
        elif subject_type == "role":
            table, id_column, name_column = _ROLE_TABLE, "role_id", "display_name"
        else:
            return {}
        async with tenant_scope(tenant_id, read_only=True, settings=self._settings) as uow:
            repo = _NameLookupRepository(uow)
            rows = await repo.fetch_all(
                f"""
                SELECT {assert_identifier(id_column)} AS subject_id,
                       {assert_identifier(name_column)} AS subject_name
                FROM {assert_identifier(table)}
                WHERE {repo.tenant_clause()}
                  AND {assert_identifier(id_column)}
                      = ANY(string_to_array(CAST(:subject_ids AS text), ','))
                """,
                repo.params(subject_ids=",".join(subject_ids)),
            )
        return {str(row.get("subject_id", "")): str(row.get("subject_name", "")) for row in rows}


def _to_timestamp(value: Any) -> str:
    """规范化数据库时间。"""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)


def _to_record(row: Mapping[str, Any]) -> GrantRecord:
    """把数据库行映射为授权记录。"""
    revoked = row.get("revoked_at")
    return GrantRecord(
        tenant_id=str(row.get("tenant_id", "")),
        grant_id=str(row.get("grant_id", "")),
        base_id=str(row.get("base_id", "")),
        subject_type=str(row.get("subject_type", "")),
        subject_id=str(row.get("subject_id", "")),
        permission=str(row.get("permission", "viewer")),
        granted_by=str(row.get("granted_by", "")),
        granted_at=_to_timestamp(row.get("granted_at")),
        revoked_at=_to_timestamp(revoked) if revoked is not None else None,
    )


_memory_store: InMemoryGrantStore | None = None


def get_grant_store(settings: Settings | None = None) -> GrantStore:
    """按配置获取授权存储。"""
    global _memory_store
    conf = settings or get_settings()
    if conf.uses_postgres:
        return PostgresGrantStore(conf)
    if _memory_store is None:
        _memory_store = InMemoryGrantStore()
    return _memory_store


def reset_grant_store() -> None:
    """重置内存授权存储。"""
    global _memory_store
    if _memory_store is not None:
        _memory_store.clear()
    _memory_store = None


__all__ = [
    "GRANT_TABLE",
    "GrantRecord",
    "GrantStore",
    "InMemoryGrantStore",
    "PostgresGrantStore",
    "get_grant_store",
    "reset_grant_store",
]
