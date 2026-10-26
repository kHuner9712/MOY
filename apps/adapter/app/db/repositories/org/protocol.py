"""组织架构仓储 · 协议层（内存 / PostgreSQL 双实现的唯一契约）。

**为什么协议单独成文件**：`memory.py` 与 `postgres.py` 都要实现 `OrgStore`，
若协议定义在任一侧，另一侧就必须 import 对侧模块，形成 `memory ↔ postgres` 的
双向依赖（拆分前它们同处一个上帝文件，看不出来；一拆就立刻变成环）。
独立成文件后依赖方向是 `records ← protocol ← {memory, postgres} ← __init__`，
单向且无环，G-38 门禁据此判定「协议方法 ⊆ 两侧实现」。
"""

from __future__ import annotations

from typing import Protocol

from app.db.repositories.org.records import DepartmentRecord, MemberLink


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


__all__: list[str] = ["OrgStore"]
