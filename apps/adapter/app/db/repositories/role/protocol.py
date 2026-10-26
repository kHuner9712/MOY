"""角色与权限点仓储 · 协议层（内存 / PostgreSQL 双实现的唯一契约）。

**为什么协议单独成文件**：`memory.py` 与 `postgres.py` 都要实现 `RoleStore`，
若协议定义在任一侧，另一侧就必须 import 对侧模块，形成 `memory ↔ postgres` 的
双向依赖（拆分前它们同处一个上帝文件，看不出来；一拆就立刻变成环）。
独立成文件后依赖方向是 `records ← protocol ← {memory, postgres} ← __init__`，
单向且无环，G-38 门禁据此判定「协议方法 ⊆ 两侧实现」。
"""

from __future__ import annotations

from typing import Protocol

from app.db.repositories.role.records import PermissionCatalogEntry, RoleRecord


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


__all__: list[str] = ["RoleStore"]
