"""部门树业务服务单测（阶段二 B08，架构文档 §4.4 ID1-ID4 / 2.6 D-13）。

逐条对应 B08「完成后可验证」：

1. 建 5 层成功、第 6 层 `14006`（文案「组织层级最多 5 层，请调整后重试」）；
2. 移动到自身子树 `14006`（文案「不能把部门移动到自己的下级部门」）；
3. 删除有成员/子部门的部门 `14006`；
4. 所有变更写 `audit_log` 且含前后值；
5. `path_names` 由物化路径一次组装（无 N+1），`level` 由 DB 存值返回。
"""

from __future__ import annotations

import pytest

from app.business.audit_service import AuditService, InMemoryAuditRecorder
from app.business.department_service import (
    MESSAGE_LEVEL_EXCEEDED,
    MESSAGE_MOVE_INTO_SUBTREE,
    DepartmentService,
)
from app.business.policy_service import PolicyService
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.security import AuthContext
from app.db.repositories.grant_repo import InMemoryGrantStore
from app.db.repositories.knowledge_base_repo import InMemoryKnowledgeBaseStore
from app.db.repositories.org_repo import InMemoryOrgStore, MemberLink
from app.schemas.identity_ext import CreateDepartmentRequest, UpdateDepartmentRequest

pytestmark = pytest.mark.unit

TENANT = "tnt_org"
ORG = "org_acme"
ADMIN = AuthContext(user_id="usr_admin", tenant_id=TENANT, roles=["org_admin"])
MEMBER = AuthContext(user_id="usr_member", tenant_id=TENANT, roles=["member"])


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


def _service() -> tuple[DepartmentService, InMemoryOrgStore, InMemoryAuditRecorder]:
    """构造使用独立内存存储的部门服务。

    Returns:
        tuple: (服务, 组织存储, 审计存储)。
    """
    settings = _settings()
    org = InMemoryOrgStore()
    audit = InMemoryAuditRecorder()
    policy = PolicyService(
        settings,
        bases=InMemoryKnowledgeBaseStore(),
        grants=InMemoryGrantStore(),
    )
    service = DepartmentService(
        settings, store=org, audit=AuditService(settings, recorder=audit), policy=policy
    )
    return service, org, audit


async def test_create_five_levels_and_reject_sixth() -> None:
    """建 5 层成功，第 6 层报 `14006` 且文案为层级上限。"""
    service, _org, _audit = _service()
    parent_id = ""
    for expected_level in range(1, 6):
        created = await service.create(
            tenant_id=TENANT,
            org_id=ORG,
            request=CreateDepartmentRequest(name=f"第{expected_level}层", parent_id=parent_id),
            auth=ADMIN,
        )
        assert created.level == expected_level
        parent_id = created.department_id

    with pytest.raises(ApiError) as exc_info:
        await service.create(
            tenant_id=TENANT,
            org_id=ORG,
            request=CreateDepartmentRequest(name="第6层", parent_id=parent_id),
            auth=ADMIN,
        )
    assert exc_info.value.code == ErrorCode.DEPARTMENT_STRUCTURE_CONFLICT
    assert exc_info.value.message == MESSAGE_LEVEL_EXCEEDED


async def test_create_under_missing_parent_returns_14005() -> None:
    """上级部门不存在 → `14005`。"""
    service, _org, _audit = _service()
    with pytest.raises(ApiError) as exc_info:
        await service.create(
            tenant_id=TENANT,
            org_id=ORG,
            request=CreateDepartmentRequest(name="研发部", parent_id="dept_missing"),
            auth=ADMIN,
        )
    assert exc_info.value.code == ErrorCode.DEPARTMENT_NOT_FOUND


async def test_create_requires_permission_point() -> None:
    """普通成员无 `team:structure:manage` → `20003`（fail-closed）。"""
    service, _org, _audit = _service()
    with pytest.raises(ApiError) as exc_info:
        await service.create(
            tenant_id=TENANT,
            org_id=ORG,
            request=CreateDepartmentRequest(name="研发部"),
            auth=MEMBER,
        )
    assert exc_info.value.code == ErrorCode.FORBIDDEN


async def test_tree_returns_path_names_and_children() -> None:
    """树结构：`path_names` 为从根到本部门的名称链，子部门挂在父节点下。"""
    service, _org, _audit = _service()
    root = await service.create(
        tenant_id=TENANT, org_id=ORG, request=CreateDepartmentRequest(name="总部"), auth=ADMIN
    )
    child = await service.create(
        tenant_id=TENANT,
        org_id=ORG,
        request=CreateDepartmentRequest(name="研发部", parent_id=root.department_id),
        auth=ADMIN,
    )
    tree = await service.tree(tenant_id=TENANT, org_id=ORG)
    assert len(tree.departments) == 1
    assert tree.departments[0].name == "总部"
    assert tree.departments[0].children[0].department_id == child.department_id
    assert tree.departments[0].children[0].path_names == ["总部", "研发部"]
    assert tree.departments[0].children[0].level == 2


async def test_flat_listing_is_sorted_by_level() -> None:
    """`flat=true` 返回平铺且按层级排序。"""
    service, _org, _audit = _service()
    root = await service.create(
        tenant_id=TENANT, org_id=ORG, request=CreateDepartmentRequest(name="总部"), auth=ADMIN
    )
    await service.create(
        tenant_id=TENANT,
        org_id=ORG,
        request=CreateDepartmentRequest(name="研发部", parent_id=root.department_id),
        auth=ADMIN,
    )
    flat = await service.tree(tenant_id=TENANT, org_id=ORG, flat=True)
    levels = [item.level for item in flat.departments]
    assert levels == sorted(levels)
    assert len(flat.departments) == 2


async def test_move_into_own_subtree_returns_14006() -> None:
    """移动到自身子树 → `14006` 且文案为「不能把部门移动到自己的下级部门」。"""
    service, _org, _audit = _service()
    root = await service.create(
        tenant_id=TENANT, org_id=ORG, request=CreateDepartmentRequest(name="总部"), auth=ADMIN
    )
    child = await service.create(
        tenant_id=TENANT,
        org_id=ORG,
        request=CreateDepartmentRequest(name="研发部", parent_id=root.department_id),
        auth=ADMIN,
    )
    with pytest.raises(ApiError) as exc_info:
        await service.update(
            tenant_id=TENANT,
            department_id=root.department_id,
            request=UpdateDepartmentRequest(parent_id=child.department_id),
            auth=ADMIN,
        )
    assert exc_info.value.code == ErrorCode.DEPARTMENT_STRUCTURE_CONFLICT
    assert exc_info.value.message == MESSAGE_MOVE_INTO_SUBTREE


async def test_move_to_self_returns_14006() -> None:
    """把部门移动到自己身上同样非法。"""
    service, _org, _audit = _service()
    root = await service.create(
        tenant_id=TENANT, org_id=ORG, request=CreateDepartmentRequest(name="总部"), auth=ADMIN
    )
    with pytest.raises(ApiError) as exc_info:
        await service.update(
            tenant_id=TENANT,
            department_id=root.department_id,
            request=UpdateDepartmentRequest(parent_id=root.department_id),
            auth=ADMIN,
        )
    assert exc_info.value.code == ErrorCode.DEPARTMENT_STRUCTURE_CONFLICT


async def test_move_subtree_rewrites_paths_and_levels() -> None:
    """移动子树：路径前缀与层级原子更新。"""
    service, org, _audit = _service()
    a = await service.create(
        tenant_id=TENANT, org_id=ORG, request=CreateDepartmentRequest(name="A"), auth=ADMIN
    )
    b = await service.create(
        tenant_id=TENANT,
        org_id=ORG,
        request=CreateDepartmentRequest(name="B", parent_id=a.department_id),
        auth=ADMIN,
    )
    c = await service.create(
        tenant_id=TENANT, org_id=ORG, request=CreateDepartmentRequest(name="C"), auth=ADMIN
    )
    await service.update(
        tenant_id=TENANT,
        department_id=b.department_id,
        request=UpdateDepartmentRequest(parent_id=c.department_id),
        auth=ADMIN,
    )
    moved = await org.get_department(TENANT, b.department_id)
    parent = await org.get_department(TENANT, c.department_id)
    assert moved is not None and parent is not None
    assert moved.parent_id == c.department_id
    assert moved.level == 2
    assert moved.path.startswith(parent.path)


async def test_move_deep_subtree_beyond_five_levels_returns_14006() -> None:
    """把 3 层子树挂到第 4 层下 → 第 6 层，`14006`（层级上限优先于其他校验）。"""
    service, _org, _audit = _service()
    branch_a: list[str] = [""]
    for index in range(3):
        created = await service.create(
            tenant_id=TENANT,
            org_id=ORG,
            request=CreateDepartmentRequest(name=f"A{index + 1}", parent_id=branch_a[-1]),
            auth=ADMIN,
        )
        branch_a.append(created.department_id)
    branch_b: list[str] = [""]
    for index in range(4):
        created = await service.create(
            tenant_id=TENANT,
            org_id=ORG,
            request=CreateDepartmentRequest(name=f"B{index + 1}", parent_id=branch_b[-1]),
            auth=ADMIN,
        )
        branch_b.append(created.department_id)

    with pytest.raises(ApiError) as exc_info:
        await service.update(
            tenant_id=TENANT,
            department_id=branch_a[1],
            request=UpdateDepartmentRequest(parent_id=branch_b[4]),
            auth=ADMIN,
        )
    assert exc_info.value.code == ErrorCode.DEPARTMENT_STRUCTURE_CONFLICT
    assert exc_info.value.message == MESSAGE_LEVEL_EXCEEDED


async def test_update_without_fields_returns_20001() -> None:
    """三项全空 → `20001`。"""
    service, _org, _audit = _service()
    created = await service.create(
        tenant_id=TENANT, org_id=ORG, request=CreateDepartmentRequest(name="总部"), auth=ADMIN
    )
    with pytest.raises(ApiError) as exc_info:
        await service.update(
            tenant_id=TENANT,
            department_id=created.department_id,
            request=UpdateDepartmentRequest(),
            auth=ADMIN,
        )
    assert exc_info.value.code == ErrorCode.PARAM_INVALID


async def test_delete_department_with_members_returns_14006() -> None:
    """删除有成员的部门 → `14006`。"""
    service, org, _audit = _service()
    created = await service.create(
        tenant_id=TENANT, org_id=ORG, request=CreateDepartmentRequest(name="总部"), auth=ADMIN
    )
    await org.link_member(
        MemberLink(tenant_id=TENANT, department_id=created.department_id, user_id="usr_x")
    )
    with pytest.raises(ApiError) as exc_info:
        await service.delete(tenant_id=TENANT, department_id=created.department_id, auth=ADMIN)
    assert exc_info.value.code == ErrorCode.DEPARTMENT_STRUCTURE_CONFLICT


async def test_delete_department_with_children_returns_14006() -> None:
    """删除有子部门的部门 → `14006`。"""
    service, _org, _audit = _service()
    root = await service.create(
        tenant_id=TENANT, org_id=ORG, request=CreateDepartmentRequest(name="总部"), auth=ADMIN
    )
    await service.create(
        tenant_id=TENANT,
        org_id=ORG,
        request=CreateDepartmentRequest(name="研发部", parent_id=root.department_id),
        auth=ADMIN,
    )
    with pytest.raises(ApiError) as exc_info:
        await service.delete(tenant_id=TENANT, department_id=root.department_id, auth=ADMIN)
    assert exc_info.value.code == ErrorCode.DEPARTMENT_STRUCTURE_CONFLICT


async def test_delete_empty_department_succeeds_and_writes_audit() -> None:
    """删除空部门成功，且写审计含前后值。"""
    service, _org, audit = _service()
    created = await service.create(
        tenant_id=TENANT, org_id=ORG, request=CreateDepartmentRequest(name="临时部门"), auth=ADMIN
    )
    deleted = await service.delete(
        tenant_id=TENANT, department_id=created.department_id, auth=ADMIN
    )
    assert deleted.deleted is True
    entries = await audit.entries_for(TENANT)
    assert entries, "删除部门必须留下操作记录"
    # entries 按时间倒序：第一条是删除，最后一条是新建
    assert entries[0].change_summary["before"]["name"] == "临时部门"
    assert entries[0].display_name == "删除部门"


async def test_delete_missing_department_returns_14005() -> None:
    """删除不存在的部门 → `14005`。"""
    service, _org, _audit = _service()
    with pytest.raises(ApiError) as exc_info:
        await service.delete(tenant_id=TENANT, department_id="dept_missing", auth=ADMIN)
    assert exc_info.value.code == ErrorCode.DEPARTMENT_NOT_FOUND


async def test_create_and_update_write_audit_with_before_after() -> None:
    """新建与重命名都写审计，重命名含 before/after。"""
    service, _org, audit = _service()
    created = await service.create(
        tenant_id=TENANT, org_id=ORG, request=CreateDepartmentRequest(name="旧名"), auth=ADMIN
    )
    await service.update(
        tenant_id=TENANT,
        department_id=created.department_id,
        request=UpdateDepartmentRequest(name="新名"),
        auth=ADMIN,
    )
    entries = await audit.entries_for(TENANT)
    assert len(entries) == 2
    update_entry = next(item for item in entries if item.action == "department.update")
    assert update_entry.change_summary["before"]["name"] == "旧名"
    assert update_entry.change_summary["after"]["name"] == "新名"
    assert update_entry.actor_id == ADMIN.user_id


async def test_department_id_normalization_accepts_bare_id() -> None:
    """裸 ID 视作同一对象（一个对象一个 ID，防两种写法绕过判权）。"""
    service, org, _audit = _service()
    created = await service.create(
        tenant_id=TENANT, org_id=ORG, request=CreateDepartmentRequest(name="总部"), auth=ADMIN
    )
    bare = created.department_id.removeprefix("dept_")
    updated = await service.update(
        tenant_id=TENANT,
        department_id=bare,
        request=UpdateDepartmentRequest(name="总部改名"),
        auth=ADMIN,
    )
    assert updated.department_id == created.department_id
    record = await org.get_department(TENANT, created.department_id)
    assert record is not None and record.name == "总部改名"
