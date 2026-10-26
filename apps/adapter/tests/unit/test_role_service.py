"""角色服务单测（阶段二 B08 ID5/ID6/ID7 + D14-1 补测）。

补齐的分支（此前 0 覆盖）：

- `RoleService.permission_catalog` / `list_roles`（ID5 / ID6 读路径）；
- `RoleService.delete_role` 的三条拒绝分支 + 成功分支（含审计与策略缓存失效）；
- `resolve_role_id`（按 `role_key` / `role_id` 双形态命中）与 `role_rank`。

**验收口径**（架构文档 §4.4 ID7 + §7.1 同码多文案）：

| 场景 | 期望 |
| --- | --- |
| 角色不存在 | `14003` |
| 内置角色 | `20005` + 文案「内置角色不可删除，请调整为自定义角色」 |
| 仍被成员引用 | `20005` + 文案含成员数（「该角色下还有 N 名成员，请先调整成员角色」）|
| 先移出成员再删除 | 成功，并写 `role.delete` 审计 |

> **补测发现（已报 team-lead，不在本批修复）**：`RoleStore` 没有 `create_role`，
> 真实链路上只可能存在 4 个内置角色，因此「自定义角色被引用/删除成功」两条分支
> 在生产代码里**不可达**（PRD P0-09 的「自定义角色」缺少创建入口）。
> 为覆盖这两条分支，本文件用 `CustomRoleStore`（测试替身，继承内存实现）
> 叠加一个自定义角色——**只加测试、不改实现**。
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.business.audit_service import ACTION_ROLE_DELETE, AuditService, InMemoryAuditRecorder
from app.business.policy_service import PolicyService
from app.business.role_service import (
    MESSAGE_ROLE_BUILTIN,
    MESSAGE_ROLE_IN_USE,
    RoleService,
)
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.security import AuthContext
from app.db.repositories.grant_repo import InMemoryGrantStore
from app.db.repositories.knowledge_base_repo import InMemoryKnowledgeBaseStore
from app.db.repositories.role import InMemoryRoleStore, RoleRecord

pytestmark = pytest.mark.unit

TENANT = "tnt_role"
ORG = "org_role"
ADMIN = AuthContext(user_id="usr_admin", tenant_id=TENANT, roles=["org_admin"])

#: 测试替身里的自定义角色 ID
CUSTOM_ROLE_ID = "role_custom_reviewer"


class CustomRoleStore(InMemoryRoleStore):
    """在内存角色存储之上叠加一个「自定义角色」，用于覆盖 ID7 的删除分支。

    仅作为**测试替身**存在：生产实现不支持创建自定义角色（见模块 docstring 的
    补测发现），因此这两条分支只能在此替身上验证。

    Attributes:
        members: 引用该自定义角色的成员集合（模拟 `count_members`）。
    """

    def __init__(self) -> None:
        """初始化自定义角色与成员集合。"""
        super().__init__()
        self.members: set[str] = set()
        self.deleted = False
        self._custom = RoleRecord(
            tenant_id=TENANT,
            role_id=CUSTOM_ROLE_ID,
            org_id=ORG,
            role_key="reviewer",
            display_name="审核员",
            is_builtin=False,
            permissions=frozenset({"knowledge:base:read"}),
        )

    async def list_roles(self, tenant_id: str, org_id: str) -> list[RoleRecord]:
        """列出内置角色 + 自定义角色。

        Args:
            tenant_id: 租户 ID。
            org_id: 组织 ID。

        Returns:
            list[RoleRecord]: 角色列表。
        """
        roles = await super().list_roles(tenant_id, org_id)
        if self.deleted:
            return roles
        return [*roles, replace(self._custom, member_count=len(self.members))]

    async def get_role(self, tenant_id: str, role_id: str) -> RoleRecord | None:
        """按 ID 读取角色（含自定义角色）。

        Args:
            tenant_id: 租户 ID。
            role_id: 角色 ID。

        Returns:
            RoleRecord | None: 角色；不存在或已删除返回 None。
        """
        if role_id == CUSTOM_ROLE_ID:
            return None if self.deleted else self._custom
        return await super().get_role(tenant_id, role_id)

    async def count_members(self, tenant_id: str, role_id: str) -> int:
        """统计引用该角色的成员数。

        Args:
            tenant_id: 租户 ID。
            role_id: 角色 ID。

        Returns:
            int: 成员数。
        """
        if role_id == CUSTOM_ROLE_ID:
            return len(self.members)
        return await super().count_members(tenant_id, role_id)

    async def delete_role(self, tenant_id: str, role_id: str) -> bool:
        """删除角色（自定义角色在无引用时成功）。

        Args:
            tenant_id: 租户 ID。
            role_id: 角色 ID。

        Returns:
            bool: 删除成功返回 True。
        """
        if role_id == CUSTOM_ROLE_ID:
            if self.members:
                return False
            self.deleted = True
            return True
        return await super().delete_role(tenant_id, role_id)


def _settings() -> Settings:
    """构造测试配置（桩模式、内存后端）。

    Returns:
        Settings: 配置实例。
    """
    return Settings(
        env="test",
        deploy_mode="private",
        downstream_enabled=False,
        auth_mode="stub",
        jwt_secret="unit-test-secret",
        ratelimit_enabled=False,
    )


def _service() -> tuple[RoleService, CustomRoleStore, InMemoryAuditRecorder]:
    """构造使用独立内存存储的角色服务。

    Returns:
        tuple: (服务, 角色存储, 审计存储)。
    """
    settings = _settings()
    store = CustomRoleStore()
    audit = InMemoryAuditRecorder()
    policy = PolicyService(
        settings,
        bases=InMemoryKnowledgeBaseStore(),
        grants=InMemoryGrantStore(),
    )
    service = RoleService(
        settings, store=store, audit=AuditService(settings, recorder=audit), policy=policy
    )
    return service, store, audit


@pytest.mark.asyncio
async def test_permission_catalog_groups_and_lists_points() -> None:
    """ID5：权限点目录按分组返回，且每组都有展示名与权限点清单。"""
    service, _store, _audit = _service()
    data = await service.permission_catalog(tenant_id=TENANT)
    assert data.groups, "权限点目录不得为空"
    total = sum(len(group.permissions) for group in data.groups)
    assert total >= 16, f"业务权限点至少 16 个，实际 {total}"
    for group in data.groups:
        assert group.group_name, "分组必须有业务语言名称"
        for item in group.permissions:
            assert item.permission and item.display_name


@pytest.mark.asyncio
async def test_list_roles_includes_builtin_with_member_counts() -> None:
    """ID6：内置 4 角色 + 自定义角色，`is_builtin` / 权限点 / 成员数齐备。"""
    service, store, _audit = _service()
    store.members.add("usr_reviewer")
    data = await service.list_roles(tenant_id=TENANT, org_id=ORG, auth=ADMIN)
    keys = {item.role_key for item in data.items}
    assert {"owner", "org_admin", "member", "guest"} <= keys
    assert "reviewer" in keys
    owner = next(item for item in data.items if item.role_key == "owner")
    assert owner.is_builtin is True
    assert owner.permissions, "内置角色必须带权限点"
    custom = next(item for item in data.items if item.role_id == CUSTOM_ROLE_ID)
    assert custom.is_builtin is False
    assert custom.member_count == 1


@pytest.mark.asyncio
async def test_delete_role_rejects_unknown() -> None:
    """ID7 角色不存在 → `14003`。"""
    service, _store, _audit = _service()
    with pytest.raises(ApiError) as excinfo:
        await service.delete_role(
            tenant_id=TENANT, org_id=ORG, role_id="role_not_exists", auth=ADMIN
        )
    assert excinfo.value.code is ErrorCode.ROLE_NOT_FOUND


@pytest.mark.asyncio
@pytest.mark.p0
async def test_delete_role_rejects_builtin() -> None:
    """ID7 内置角色不可删 → `20005` + 内置文案。"""
    service, store, _audit = _service()
    roles = await store.ensure_builtin_roles(TENANT, ORG)
    member_role = next(item for item in roles if item.role_key == "member")
    with pytest.raises(ApiError) as excinfo:
        await service.delete_role(
            tenant_id=TENANT, org_id=ORG, role_id=member_role.role_id, auth=ADMIN
        )
    assert excinfo.value.code is ErrorCode.CONFLICT
    assert excinfo.value.message == MESSAGE_ROLE_BUILTIN


@pytest.mark.asyncio
@pytest.mark.p0
async def test_delete_role_rejects_when_referenced_then_succeeds_after_unassign() -> None:
    """ID7 角色被引用 → `20005`（含成员数）；先移出成员后删除成功并写审计。"""
    service, store, audit = _service()
    store.members.update({"usr_a", "usr_b"})

    with pytest.raises(ApiError) as excinfo:
        await service.delete_role(tenant_id=TENANT, org_id=ORG, role_id=CUSTOM_ROLE_ID, auth=ADMIN)
    assert excinfo.value.code is ErrorCode.CONFLICT
    assert excinfo.value.message == MESSAGE_ROLE_IN_USE.format(count=2)
    assert "还有 2 名成员" in excinfo.value.message
    assert await store.get_role(TENANT, CUSTOM_ROLE_ID) is not None, "被拒时不得删除"

    # 反向用例：先移出成员，再删角色 → 成功
    store.members.clear()
    deleted = await service.delete_role(
        tenant_id=TENANT, org_id=ORG, role_id=CUSTOM_ROLE_ID, auth=ADMIN
    )
    assert deleted.deleted is True
    assert await store.get_role(TENANT, CUSTOM_ROLE_ID) is None
    entries = await audit.recent(TENANT, action=ACTION_ROLE_DELETE)
    assert len(entries) == 1, "删除角色必须写审计"
    assert entries[0].actor_id == ADMIN.user_id
    assert entries[0].change_summary["before"]["role_id"] == CUSTOM_ROLE_ID


@pytest.mark.asyncio
async def test_resolve_role_id_by_key_and_id() -> None:
    """`resolve_role_id` 支持 `role_key` 与 `role_id` 两种写法。"""
    service, store, _audit = _service()
    roles = await store.ensure_builtin_roles(TENANT, ORG)
    owner = next(item for item in roles if item.role_key == "owner")
    assert await service.resolve_role_id(TENANT, ORG, "owner") == owner.role_id
    assert await service.resolve_role_id(TENANT, ORG, owner.role_id) == owner.role_id
    assert await service.resolve_role_id(TENANT, ORG, "no_such_role") == ""


async def test_role_rank_orders_builtin_roles() -> None:
    """`role_rank`：owner > org_admin/admin > member > guest，自定义与 member 同级。"""
    service, _store, _audit = _service()
    ranks = {
        key: await service.role_rank(TENANT, ORG, key)
        for key in ("owner", "org_admin", "admin", "member", "guest", "reviewer")
    }
    assert ranks["owner"] > ranks["org_admin"] == ranks["admin"]
    assert ranks["org_admin"] > ranks["member"]
    assert ranks["member"] > ranks["guest"]
    assert ranks["reviewer"] == ranks["member"]


@pytest.mark.asyncio
async def test_get_role_reports_real_member_count() -> None:
    """P1-8b：内存实现 `get_role` 的 `member_count` 必须是**真实计数**（此前恒 0）。

    双实现漂移的机械证据见 `deploy/validate_repository_parity.py`（G-38 的 R4：
    `RoleRecord` 的构造点关键字集合在内存侧与 PG 侧不对称——PG 的 `get_role` 写了
    `member_count=await self.count_members(...)`，内存侧整字段缺席，于是恒为默认值 0）。
    本地单测只跑内存实现、PG 用例默认 skip，所以这个漂移**任何用例都不会红**。

    本用例把语义钉成三条：
    1. 没有任何成员时是 `0`（不是 None、不是缺省哨兵）；
    2. 分配两名成员后与 `count_members` 逐个相等（同一个口径，不是一个看起来像的数字）；
    3. 只统计本租户（跨租户串味是"看起来对"的最常见形态）。
    """
    store = InMemoryRoleStore()
    roles = await store.ensure_builtin_roles(TENANT, ORG)
    member_role = next(item for item in roles if item.role_key == "member")

    fresh = await store.get_role(TENANT, member_role.role_id)
    assert fresh is not None
    assert fresh.member_count == 0
    assert fresh.member_count == await store.count_members(TENANT, member_role.role_id)

    await store.assign_role(TENANT, ORG, "usr_a", member_role.role_id)
    await store.assign_role(TENANT, ORG, "usr_b", member_role.role_id)
    # 另一租户引用同一个 role_id 不得被计入本租户的计数。
    await store.ensure_builtin_roles("tnt_role_other", ORG)
    await store.assign_role("tnt_role_other", ORG, "usr_c", member_role.role_id)

    record = await store.get_role(TENANT, member_role.role_id)
    assert record is not None
    counted = await store.count_members(TENANT, member_role.role_id)
    assert counted == 2
    assert record.member_count == counted


@pytest.mark.asyncio
async def test_ensure_builtin_roles_reports_real_member_count() -> None:
    """P1-8b 第二处：`ensure_builtin_roles` 的 `member_count` 也必须是**真实计数**。

    QA 实测同一个 Protocol 方法的三个出口答案不一致：内存实现
    `ensure_builtin_roles` 返回 0、`list_roles` / `get_role` 返回真实计数；PG 实现的
    `ensure_builtin_roles` 走 `list_roles`，返回真实计数。单测只跑内存实现，所以
    "同一状态、多个出口、各算各的"这类漂移**任何用例都不会红**。

    本用例把四个出口钉在同一口径（`_count_locked` 是它们的唯一实现）：
    `ensure_builtin_roles` / `list_roles` / `get_role` / `count_members` 同数；
    无成员时为 0（不是 None）；跨租户引用同一 `role_id` 不计入。
    """
    store = InMemoryRoleStore()
    await store.ensure_builtin_roles(TENANT, ORG)
    # 先分配再播种：角色已存在时 ensure 走的是"已存在"分支，正是漏算的那条路径。
    await store.assign_role(TENANT, ORG, "usr_a", "role_member")
    await store.assign_role(TENANT, ORG, "usr_b", "role_member")
    await store.ensure_builtin_roles("tnt_role_other2", ORG)
    await store.assign_role("tnt_role_other2", ORG, "usr_c", "role_member")

    seeded = {
        item.role_id: item.member_count for item in await store.ensure_builtin_roles(TENANT, ORG)
    }
    listed = {item.role_id: item.member_count for item in await store.list_roles(TENANT, ORG)}
    counted = await store.count_members(TENANT, "role_member")
    record = await store.get_role(TENANT, "role_member")

    assert counted == 2
    assert seeded["role_member"] == counted
    assert listed["role_member"] == counted
    assert record is not None
    assert record.member_count == counted

    # 无成员的角色四个出口同为 0（不是 None、不是缺省哨兵）。
    assert seeded["role_owner"] == 0
    assert listed["role_owner"] == 0
    assert await store.count_members(TENANT, "role_owner") == 0
