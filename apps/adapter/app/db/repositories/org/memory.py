"""组织架构仓储 · 内存实现（development / test）。

内存后端就是生产语义的**可执行规格**：G-38 门禁逐条比对本文件与 `postgres.py` 的
公开方法集、签名与 record 构造点，差异只能通过 `TEST_HOOKS`（`clear`）这类显式
白名单存在。因此本文件的每个方法都必须与 PG 侧同名方法**保持同形**。

**物化路径在这里也要真算**（不能退化成"只存父子"）：`move_subtree` 必须原子地
重写整棵子树的 `path` 前缀，否则内存/ PG 子树查询结果不同——而子树查询正是
判权（部门维度可见范围）的输入。
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from app.db.repositories.org.records import (
    DepartmentRecord,
    MemberLink,
)


def _now_iso() -> str:
    """当前 UTC 时间（ISO 8601）。

    Returns:
        str: 形如 `2026-11-02T09:00:00Z`。
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


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

    async def links_of_users(
        self, tenant_id: str, user_ids: list[str]
    ) -> dict[str, list[MemberLink]]:
        """批量读取多个成员的部门关联。

        Args:
            tenant_id: 租户 ID。
            user_ids: 用户 ID 列表。

        Returns:
            dict[str, list[MemberLink]]: 以用户 ID 为键的关联列表（主部门在前）。
        """
        wanted = set(user_ids)
        async with self._lock:
            grouped: dict[str, list[MemberLink]] = {}
            for (tid, _, uid), link in self._links.items():
                if tid == tenant_id and uid in wanted:
                    grouped.setdefault(uid, []).append(link)
        for items in grouped.values():
            items.sort(key=lambda item: (not item.is_primary, item.department_id))
        return grouped

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


__all__: list[str] = ["InMemoryOrgStore"]
