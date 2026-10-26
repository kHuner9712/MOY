"""角色与权限点仓储 · 内存实现（development / test）。

内存后端就是生产语义的**可执行规格**：G-38 门禁逐条比对本文件与 `postgres.py` 的
公开方法集、签名与 record 构造点，任何一侧单独改动都会被判红。因此本文件的每个方法
都必须与 PG 侧同名方法**保持同形**（参数名、默认值、同步性），差异只能通过
`TEST_HOOKS`（`clear`）这类显式白名单存在。
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

from app.core.builtin_roles import BUILTIN_ROLE_KEYS, BUILTIN_ROLE_PERMISSIONS
from app.db.repositories.role.records import (
    PERMISSION_CATALOG_SEED,
    PermissionCatalogEntry,
    RoleRecord,
    builtin_role_id,
    role_display_name,
)


class InMemoryRoleStore:
    """进程内角色与权限点存储（development/test）。"""

    def __init__(self) -> None:
        """初始化。"""
        self._roles: dict[tuple[str, str], RoleRecord] = {}
        self._assignments: dict[tuple[str, str], str] = {}
        self._lock = asyncio.Lock()

    def _count_locked(self, tenant_id: str, role_id: str) -> int:
        """统计引用该角色的成员数（**调用方必须已持 `self._lock`**）。

        `asyncio.Lock` 不可重入，所以对外方法之间不能互相嵌套调用；把统计收成锁内私有
        helper，让 `ensure_builtin_roles` / `list_roles` / `get_role` / `count_members`
        四个出口共用**同一份口径**。

        为什么值得抽出来：P1-8b（`get_role` 恒 0）与"`ensure_builtin_roles` 恒 0"是同一个
        病——同一状态有多个出口，其中一个出口忘了算。只要出口之间还有"各算各的"的空间，
        下一次改字段还会重演；四个出口共用一行统计就没有这个空间了。
        PG 侧不需要这个 helper 是因为 `ensure_builtin_roles` 直接复用 `list_roles`、
        `get_role` 直接复用 `count_members`，天然同源。

        Args:
            tenant_id: 租户 ID。
            role_id: 角色 ID。

        Returns:
            int: 成员数（只计本租户；跨租户不串）。
        """
        return sum(
            1
            for (tid, _), assigned in self._assignments.items()
            if tid == tenant_id and assigned == role_id
        )

    async def ensure_builtin_roles(self, tenant_id: str, org_id: str) -> list[RoleRecord]:
        """播种 4 个内置角色（幂等），返回值带**真实成员数**。

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
                    # `member_count=0` 显式写出（而非依赖 dataclass 默认值）：新建角色
                    # 这一刻确实没有成员引用，写出来让 R4「双实现 record 构造点形状
                    # 一致」有据可依——缺项正是 P1-8b 的形态（PG 侧写了、内存侧漏了）。
                    self._roles[key] = RoleRecord(
                        tenant_id=tenant_id,
                        role_id=role_id,
                        org_id=org_id,
                        role_key=role_key,
                        display_name=role_display_name(role_key),
                        is_builtin=True,
                        permissions=BUILTIN_ROLE_PERMISSIONS.get(role_key, frozenset()),
                        member_count=0,
                    )
                created.append(self._roles[key])
            # 返回前补齐真实计数：PG 侧 `ensure_builtin_roles` 走 `list_roles`，本来带真实
            # 计数；内存侧若把存储里的原始记录直接返回，同一 Protocol 方法的两个后端就会
            # 给出不同答案（QA 实测到的"第三处漂移"）。
            created = [
                replace(record, member_count=self._count_locked(tenant_id, record.role_id))
                for record in created
            ]
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
            items = [
                replace(record, member_count=self._count_locked(tenant_id, record.role_id))
                for record in records
            ]
        items.sort(key=lambda item: (not item.is_builtin, item.role_key))
        return items

    async def get_role(self, tenant_id: str, role_id: str) -> RoleRecord | None:
        """按 ID 读取角色（含真实成员数）。

        `member_count` 必须与 `count_members` / `list_roles` / `ensure_builtin_roles`
        同口径（审计 P1-8b）：拆分前这里直接返回存储里的原始记录，`member_count` 恒为
        构造时的 0，而 PG 实现返回 `await self.count_members(...)`——同一份接口在两种
        后端给出不同答案，而本地单测只跑内存实现，这个漂移**任何用例都红不了**。
        现在四个出口共用 `_count_locked`，G-38 的 R4 与之对应的单测共同把它钉住。

        Args:
            tenant_id: 租户 ID。
            role_id: 角色 ID。

        Returns:
            RoleRecord | None: 角色记录或 None（角色不存在）。
        """
        async with self._lock:
            record = self._roles.get((tenant_id, role_id))
            if record is None:
                return None
            member_count = self._count_locked(tenant_id, role_id)
        return replace(record, member_count=member_count)

    async def count_members(self, tenant_id: str, role_id: str) -> int:
        """统计引用该角色的成员数。

        Args:
            tenant_id: 租户 ID。
            role_id: 角色 ID。

        Returns:
            int: 成员数。
        """
        async with self._lock:
            return self._count_locked(tenant_id, role_id)

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
        """清空（测试隔离用）。

        `TEST_HOOKS` 白名单成员：**只允许存在内存侧**，PG 侧出现同名方法会被 G-38
        判红（那意味着测试钩子漏进了生产实现）。
        """
        self._roles.clear()
        self._assignments.clear()


__all__: list[str] = ["InMemoryRoleStore"]
