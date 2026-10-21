"""组织仓储与部门边界补测（阶段二 B08 + D14-1 补测）。

补齐的分支（此前 0 覆盖）：

- **部门层级边界值**：父部门已在第 5 层时建子部门 → `14006`（文案为层级上限，
  而不是等建到第 6 层才拦）；
- `InMemoryOrgStore` 的读写方法：`update_department`（改名/改负责人/移动）、
  `move_subtree`（子树 `path` 与 `level` 原子迁移）、`count_children`、
  `soft_delete_department`、`links_of_department`、`user_ids(org_id)` 过滤、
  `refresh_member_count`、`include_deleted` 语义；
- `normalize_department_id` 的三种输入形态；
- `OrgUsageDirectory`：主部门解析与展示名兜底（计量域 `group_by=member` 依赖它）。

> 只加用例不改实现（D14-1 约束）。
"""

from __future__ import annotations

import pytest

from app.business.audit_service import AuditService, InMemoryAuditRecorder
from app.business.department_service import MESSAGE_LEVEL_EXCEEDED, DepartmentService
from app.business.policy_service import PolicyService
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.security import AuthContext
from app.db.repositories.grant_repo import InMemoryGrantStore
from app.db.repositories.knowledge_base_repo import InMemoryKnowledgeBaseStore
from app.db.repositories.org_repo import (
    MAX_DEPARTMENT_LEVEL,
    DepartmentRecord,
    InMemoryOrgStore,
    MemberLink,
    OrgUsageDirectory,
    normalize_department_id,
)
from app.schemas.identity_ext import CreateDepartmentRequest

pytestmark = pytest.mark.unit

TENANT = "tnt_org_repo"
ORG = "org_repo"
ADMIN = AuthContext(user_id="usr_admin", tenant_id=TENANT, roles=["org_admin"])


def _settings() -> Settings:
    """构造测试配置。

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


def _service() -> tuple[DepartmentService, InMemoryOrgStore]:
    """构造部门服务与其内存存储。

    Returns:
        tuple[DepartmentService, InMemoryOrgStore]: 服务与存储。
    """
    settings = _settings()
    org = InMemoryOrgStore()
    policy = PolicyService(
        settings,
        bases=InMemoryKnowledgeBaseStore(),
        grants=InMemoryGrantStore(),
    )
    service = DepartmentService(
        settings,
        store=org,
        audit=AuditService(settings, recorder=InMemoryAuditRecorder()),
        policy=policy,
    )
    return service, org


@pytest.mark.asyncio
@pytest.mark.p0
async def test_child_of_fifth_level_department_is_rejected_at_boundary() -> None:
    """父部门已在第 5 层时建子部门 → `14006`（边界值，不等第 6 层落地才拦）。"""
    service, _org = _service()
    parent_id = ""
    deepest = ""
    for _ in range(MAX_DEPARTMENT_LEVEL):
        created = await service.create(
            tenant_id=TENANT,
            org_id=ORG,
            request=CreateDepartmentRequest(name=f"L{_ + 1}", parent_id=parent_id),
            auth=ADMIN,
        )
        parent_id = created.department_id
        deepest = created.department_id

    with pytest.raises(ApiError) as excinfo:
        await service.create(
            tenant_id=TENANT,
            org_id=ORG,
            request=CreateDepartmentRequest(name="L6", parent_id=deepest),
            auth=ADMIN,
        )
    assert excinfo.value.code is ErrorCode.DEPARTMENT_STRUCTURE_CONFLICT
    assert excinfo.value.message == MESSAGE_LEVEL_EXCEEDED


@pytest.mark.asyncio
async def test_store_update_and_soft_delete_semantics() -> None:
    """仓储：改名/改负责人、软删除后默认不可见、`include_deleted` 可查。"""
    _service_ignored, org = _service()
    await org.create_department(
        DepartmentRecord(
            tenant_id=TENANT,
            department_id="dept_a",
            org_id=ORG,
            parent_id="",
            name="旧名",
            path="/dept_a/",
            level=1,
        )
    )
    updated = await org.update_department(TENANT, "dept_a", name="新名", manager_user_id="usr_mgr")
    assert updated is not None
    assert updated.name == "新名"
    assert updated.manager_user_id == "usr_mgr"

    assert await org.soft_delete_department(TENANT, "dept_a") is True
    assert await org.get_department(TENANT, "dept_a") is None, "软删除后默认不可见"
    hidden = await org.list_departments(TENANT, ORG)
    assert all(item.department_id != "dept_a" for item in hidden)
    visible = await org.get_department(TENANT, "dept_a", include_deleted=True)
    assert visible is not None
    assert visible.is_deleted is True


@pytest.mark.asyncio
async def test_store_move_subtree_updates_path_and_level() -> None:
    """仓储：移动子树时 `path` 前缀与 `level` 整体迁移。"""
    _ignored, org = _service()
    for dep_id, parent, path, level in (
        ("dept_a", "", "/dept_a/", 1),
        ("dept_b", "dept_a", "/dept_a/dept_b/", 2),
    ):
        await org.create_department(
            DepartmentRecord(
                tenant_id=TENANT,
                department_id=dep_id,
                org_id=ORG,
                parent_id=parent,
                name=dep_id,
                path=path,
                level=level,
            )
        )
    moved = await org.move_subtree(TENANT, ORG, "/dept_a/dept_b/", "/dept_c/dept_b/", 1)
    assert moved == 1
    child = await org.get_department(TENANT, "dept_b")
    assert child is not None
    assert child.path == "/dept_c/dept_b/"
    assert child.level == 3


@pytest.mark.asyncio
async def test_store_member_links_and_counts() -> None:
    """仓储：成员关联、主部门唯一维护、成员数重算、`user_ids` 的 org 过滤。"""
    _ignored, org = _service()
    for dep_id in ("dept_rd", "dept_ops"):
        await org.create_department(
            DepartmentRecord(
                tenant_id=TENANT,
                department_id=dep_id,
                org_id=ORG,
                parent_id="",
                name=dep_id,
                path=f"/{dep_id}/",
                level=1,
            )
        )
    await org.link_member(
        MemberLink(tenant_id=TENANT, department_id="dept_rd", user_id="usr_1", is_primary=True)
    )
    await org.link_member(MemberLink(tenant_id=TENANT, department_id="dept_ops", user_id="usr_1"))
    assert len(await org.links_of_user(TENANT, "usr_1")) == 2
    assert [item.user_id for item in await org.links_of_department(TENANT, "dept_ops")] == ["usr_1"]
    assert await org.refresh_member_count(TENANT, "dept_rd") == 1
    assert await org.count_children(TENANT, "dept_rd") == 0
    assert await org.user_ids(TENANT) == ["usr_1"]
    assert await org.user_ids(TENANT, "org_other") == [], "org 过滤不匹配时应为空"

    await org.clear_primary(TENANT, "usr_1")
    links = await org.links_of_user(TENANT, "usr_1")
    assert all(not link.is_primary for link in links)
    assert await org.set_primary(TENANT, "dept_ops", "usr_1") is True
    primary = [link for link in await org.links_of_user(TENANT, "usr_1") if link.is_primary]
    assert [link.department_id for link in primary] == ["dept_ops"]
    assert await org.unlink_member(TENANT, "dept_ops", "usr_1") is True


@pytest.mark.asyncio
async def test_usage_directory_resolves_primary_department_and_display_name() -> None:
    """计量域组织解析器：主部门命中 / 未登记成员返回空串。"""
    org = InMemoryOrgStore()
    await org.create_department(
        DepartmentRecord(
            tenant_id=TENANT,
            department_id="dept_rd",
            org_id=ORG,
            parent_id="",
            name="研发部",
            path="/dept_rd/",
            level=1,
        )
    )
    await org.link_member(
        MemberLink(tenant_id=TENANT, department_id="dept_rd", user_id="usr_1", is_primary=True)
    )
    directory = OrgUsageDirectory(org)
    assert await directory.primary_department(TENANT, "usr_1") == "dept_rd"
    assert await directory.primary_department(TENANT, "usr_ghost") == ""
    assert await directory.display_name(TENANT, "usr_1") == "usr_1"
    assert await directory.display_name(TENANT, "usr_ghost") == "usr_ghost", (
        "展示名以用户 ID 兜底（不虚构姓名）"
    )


def test_normalize_department_id_forms() -> None:
    """部门 ID 归一化：已带前缀原样返回、裸 ID 补前缀、空值返回空串。"""
    assert normalize_department_id("dept_abc") == "dept_abc"
    assert normalize_department_id("abc") == "dept_abc"
    assert normalize_department_id("") == ""
    assert normalize_department_id("   ") == ""
