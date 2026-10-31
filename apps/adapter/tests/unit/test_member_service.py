"""成员管理服务单测（阶段二 B08 ID8/ID9/ID10 + D14-1 补测）。

补齐的分支（此前 0 覆盖）：

- `MemberService.update_member`：改角色 / 覆盖部门 / 只调主部门 / 改在册状态；
- 保护规则：操作自己、同级或更高级、把成员设为 owner、成员不存在；
- `MemberService.remove_member`：解绑部门 + 知识库所有权转交 + 审计；所有者保护；
- `MemberService.batch`：批量移出 / 批量改角色 / 单条失败不中断；
- `_sync_departments` / `_set_primary` 的两种非法入参（主部门不在列表、部门不存在）。

> 这些分支此前**完全没有用例**，但都承载 PRD 6.3 的判权红线；
> 补测只加用例，不改实现（D14-1 约束）。
"""

from __future__ import annotations

import pytest

from app.business.audit_service import AuditService, InMemoryAuditRecorder
from app.business.member_service import (
    MESSAGE_MODIFICATION_FORBIDDEN,
    MESSAGE_OWNER_PROTECTED,
    MemberService,
)
from app.business.policy_service import PolicyService
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.security import AuthContext
from app.db.repositories.grant_repo import InMemoryGrantStore
from app.db.repositories.knowledge_base_repo import (
    InMemoryKnowledgeBaseStore,
    KnowledgeBaseRecord,
)
from app.db.repositories.org import DepartmentRecord, InMemoryOrgStore, MemberLink
from app.db.repositories.role import InMemoryRoleStore
from app.schemas.identity import ListMembersQuery
from app.schemas.identity_ext import (
    BatchMemberAction,
    BatchMemberRequest,
    MemberStatus,
    UpdateMemberRequest,
)

pytestmark = pytest.mark.unit

TENANT = "tnt_member"
ORG = "org_member"
OWNER = AuthContext(user_id="usr_owner", tenant_id=TENANT, roles=["owner"])
ADMIN = AuthContext(user_id="usr_admin", tenant_id=TENANT, roles=["org_admin"])
OTHER_ADMIN = AuthContext(user_id="usr_admin2", tenant_id=TENANT, roles=["org_admin"])


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


class _Fixture:
    """成员服务的测试装配。

    Attributes:
        service: 成员服务。
        org: 组织存储。
        roles: 角色存储。
        bases: 知识库存储。
        audit: 审计存储。
    """

    def __init__(self) -> None:
        """初始化各存储与服务。"""
        settings = _settings()
        self.org = InMemoryOrgStore()
        self.roles = InMemoryRoleStore()
        self.bases = InMemoryKnowledgeBaseStore()
        self.audit = InMemoryAuditRecorder()
        policy = PolicyService(settings, bases=self.bases, grants=InMemoryGrantStore())
        self.service = MemberService(
            settings,
            org=self.org,
            roles=self.roles,
            bases=self.bases,
            audit=AuditService(settings, recorder=self.audit),
            policy=policy,
        )
        self.settings = settings

    async def seed(self) -> None:
        """造两个部门、三个成员（owner / org_admin / member）。"""
        await self.org.create_department(
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
        await self.org.create_department(
            DepartmentRecord(
                tenant_id=TENANT,
                department_id="dept_ops",
                org_id=ORG,
                parent_id="",
                name="运营部",
                path="/dept_ops/",
                level=1,
            )
        )
        await self.roles.ensure_builtin_roles(TENANT, ORG)
        roles = {item.role_key: item.role_id for item in await self.roles.list_roles(TENANT, ORG)}
        await self.roles.assign_role(TENANT, ORG, "usr_owner", roles["owner"], "system")
        await self.roles.assign_role(TENANT, ORG, "usr_admin", roles["org_admin"], "system")
        await self.roles.assign_role(TENANT, ORG, "usr_member", roles["member"], "system")
        for user_id in ("usr_owner", "usr_admin", "usr_member"):
            await self.org.link_member(
                MemberLink(
                    tenant_id=TENANT,
                    department_id="dept_rd",
                    user_id=user_id,
                    is_primary=True,
                )
            )
            await self.org.refresh_member_count(TENANT, "dept_rd")


@pytest.mark.asyncio
async def test_list_members_filters_by_role_department_status_keyword() -> None:
    """U3：角色 / 部门 / 在册状态 / 关键字四类过滤都生效。"""
    fixture = _Fixture()
    await fixture.seed()

    all_members = await fixture.service.list_members(
        tenant_id=TENANT, org_id=ORG, query=ListMembersQuery(page=1, page_size=50)
    )
    assert {item.user_id for item in all_members} == {"usr_owner", "usr_admin", "usr_member"}

    by_department = await fixture.service.list_members(
        tenant_id=TENANT,
        org_id=ORG,
        query=ListMembersQuery(department_id="dept_ops"),
    )
    assert by_department == [], "运营部没有成员，过滤后应为空"

    by_keyword = await fixture.service.list_members(
        tenant_id=TENANT, org_id=ORG, query=ListMembersQuery(keyword="MEMBER")
    )
    assert [item.user_id for item in by_keyword] == ["usr_member"]

    invited_only = await fixture.service.list_members(
        tenant_id=TENANT,
        org_id=ORG,
        query=ListMembersQuery(status=MemberStatus.INVITED.value),
    )
    assert invited_only == [], "三个成员都有角色 → 均为 active"


@pytest.mark.asyncio
async def test_list_members_page_slices_without_department_filter() -> None:
    """U3 分页：无部门过滤时 total 为过滤后总数，页内按用户 ID 升序切片。"""
    fixture = _Fixture()
    await fixture.seed()

    first = await fixture.service.list_members_page(
        tenant_id=TENANT, org_id=ORG, query=ListMembersQuery(page=1, page_size=2)
    )
    assert [item.user_id for item in first.items] == ["usr_admin", "usr_member"]
    assert first.meta.total == 3
    assert first.meta.total_pages == 2

    second = await fixture.service.list_members_page(
        tenant_id=TENANT, org_id=ORG, query=ListMembersQuery(page=2, page_size=2)
    )
    assert [item.user_id for item in second.items] == ["usr_owner"]
    assert second.meta.total == 3

    empty = await fixture.service.list_members_page(
        tenant_id=TENANT, org_id=ORG, query=ListMembersQuery(page=3, page_size=2)
    )
    assert empty.items == []
    assert empty.meta.total == 3


@pytest.mark.asyncio
async def test_list_members_page_loads_links_only_for_current_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """分页下推：无部门过滤时只对当前页用户批量读取部门关联（审计 P0-3）。"""
    fixture = _Fixture()
    await fixture.seed()
    seen: list[list[str]] = []
    original = fixture.org.links_of_users

    async def _record(tenant_id: str, user_ids: list[str]) -> dict[str, list[MemberLink]]:
        seen.append(list(user_ids))
        return await original(tenant_id, user_ids)

    monkeypatch.setattr(fixture.org, "links_of_users", _record)

    page = await fixture.service.list_members_page(
        tenant_id=TENANT, org_id=ORG, query=ListMembersQuery(page=1, page_size=2)
    )
    assert len(page.items) == 2
    assert seen == [["usr_admin", "usr_member"]], "只应查询当前页的 2 个用户，而非全部 3 个"


@pytest.mark.asyncio
async def test_list_members_page_with_department_filter_counts_filtered_total() -> None:
    """部门过滤：需按候选整体读取关联判定归属，total 为过滤后条数。"""
    fixture = _Fixture()
    await fixture.seed()

    in_dept = await fixture.service.list_members_page(
        tenant_id=TENANT,
        org_id=ORG,
        query=ListMembersQuery(page=1, page_size=10, department_id="dept_rd"),
    )
    assert in_dept.meta.total == 3
    assert len(in_dept.items) == 3

    out_dept = await fixture.service.list_members_page(
        tenant_id=TENANT,
        org_id=ORG,
        query=ListMembersQuery(page=1, page_size=10, department_id="dept_ops"),
    )
    assert out_dept.meta.total == 0
    assert out_dept.items == []


@pytest.mark.asyncio
@pytest.mark.p0
async def test_update_member_role_and_departments() -> None:
    """ID8：改角色 + 覆盖部门 + 指定主部门，并写审计。"""
    fixture = _Fixture()
    await fixture.seed()
    data = await fixture.service.update_member(
        tenant_id=TENANT,
        org_id=ORG,
        member_id="usr_member",
        request=UpdateMemberRequest(
            role="org_admin",
            department_ids=["dept_rd", "dept_ops"],
            primary_department_id="dept_ops",
        ),
        auth=ADMIN,
    )
    assert data.member_id == "usr_member"
    assert set(data.department_ids) == {"dept_rd", "dept_ops"}
    assert await fixture.roles.role_key_of_user(TENANT, "usr_member") == "org_admin"
    primary = [
        link for link in await fixture.org.links_of_user(TENANT, "usr_member") if link.is_primary
    ]
    assert [link.department_id for link in primary] == ["dept_ops"], "主部门必须唯一且为指定值"
    entries = await fixture.audit.recent(TENANT, action="member.update")
    assert entries, "变更成员必须写审计"


@pytest.mark.asyncio
async def test_update_member_primary_only_and_status() -> None:
    """ID8：只传 `primary_department_id` 或只改 `status` 也能落库。"""
    fixture = _Fixture()
    await fixture.seed()
    await fixture.org.link_member(
        MemberLink(tenant_id=TENANT, department_id="dept_ops", user_id="usr_member")
    )
    await fixture.service.update_member(
        tenant_id=TENANT,
        org_id=ORG,
        member_id="usr_member",
        request=UpdateMemberRequest(primary_department_id="dept_ops"),
        auth=ADMIN,
    )
    primary = [
        link for link in await fixture.org.links_of_user(TENANT, "usr_member") if link.is_primary
    ]
    assert [link.department_id for link in primary] == ["dept_ops"]

    updated = await fixture.service.update_member(
        tenant_id=TENANT,
        org_id=ORG,
        member_id="usr_member",
        request=UpdateMemberRequest(status=MemberStatus.DISABLED),
        auth=ADMIN,
    )
    assert updated.status == MemberStatus.DISABLED.value


@pytest.mark.asyncio
async def test_update_member_rejects_unknown_member() -> None:
    """ID8：成员不存在 → `14001`。"""
    fixture = _Fixture()
    await fixture.seed()
    with pytest.raises(ApiError) as excinfo:
        await fixture.service.update_member(
            tenant_id=TENANT,
            org_id=ORG,
            member_id="usr_ghost",
            request=UpdateMemberRequest(status=MemberStatus.DISABLED),
            auth=ADMIN,
        )
    assert excinfo.value.code is ErrorCode.USER_NOT_FOUND


@pytest.mark.asyncio
@pytest.mark.p0
async def test_update_member_protection_rules() -> None:
    """ID8：不得操作自己 / 同级 / 所有者，也不得把成员设为 owner（`14007`/`14008`）。"""
    fixture = _Fixture()
    await fixture.seed()

    with pytest.raises(ApiError) as self_op:
        await fixture.service.update_member(
            tenant_id=TENANT,
            org_id=ORG,
            member_id="usr_admin",
            request=UpdateMemberRequest(role="member"),
            auth=ADMIN,
        )
    assert self_op.value.code is ErrorCode.MEMBER_MODIFICATION_FORBIDDEN
    assert self_op.value.message == MESSAGE_MODIFICATION_FORBIDDEN

    with pytest.raises(ApiError) as peer:
        await fixture.service.update_member(
            tenant_id=TENANT,
            org_id=ORG,
            member_id="usr_admin",
            request=UpdateMemberRequest(role="member"),
            auth=OTHER_ADMIN,
        )
    assert peer.value.code is ErrorCode.MEMBER_MODIFICATION_FORBIDDEN

    with pytest.raises(ApiError) as owner_guard:
        await fixture.service.update_member(
            tenant_id=TENANT,
            org_id=ORG,
            member_id="usr_owner",
            request=UpdateMemberRequest(role="member"),
            auth=ADMIN,
        )
    assert owner_guard.value.code is ErrorCode.OWNER_PROTECTED
    assert owner_guard.value.message == MESSAGE_OWNER_PROTECTED

    with pytest.raises(ApiError) as assign_owner:
        await fixture.service.update_member(
            tenant_id=TENANT,
            org_id=ORG,
            member_id="usr_member",
            request=UpdateMemberRequest(role="owner"),
            auth=ADMIN,
        )
    assert assign_owner.value.code is ErrorCode.MEMBER_MODIFICATION_FORBIDDEN
    assert "不能把成员设置为企业所有者" in assign_owner.value.message


@pytest.mark.asyncio
async def test_update_member_rejects_invalid_department_input() -> None:
    """ID8：主部门不在列表内 / 部门不存在都返回 `20001`/`14005`。"""
    fixture = _Fixture()
    await fixture.seed()

    with pytest.raises(ApiError) as not_in_list:
        await fixture.service.update_member(
            tenant_id=TENANT,
            org_id=ORG,
            member_id="usr_member",
            request=UpdateMemberRequest(
                department_ids=["dept_rd"], primary_department_id="dept_ops"
            ),
            auth=ADMIN,
        )
    assert not_in_list.value.code is ErrorCode.PARAM_INVALID
    assert "主部门必须是所属部门之一" in not_in_list.value.message

    with pytest.raises(ApiError) as dept_missing:
        await fixture.service.update_member(
            tenant_id=TENANT,
            org_id=ORG,
            member_id="usr_member",
            request=UpdateMemberRequest(department_ids=["dept_ghost"]),
            auth=ADMIN,
        )
    assert dept_missing.value.code is ErrorCode.DEPARTMENT_NOT_FOUND

    with pytest.raises(ApiError) as primary_missing:
        await fixture.service.update_member(
            tenant_id=TENANT,
            org_id=ORG,
            member_id="usr_member",
            request=UpdateMemberRequest(primary_department_id="dept_ops"),
            auth=ADMIN,
        )
    assert primary_missing.value.code is ErrorCode.PARAM_INVALID


@pytest.mark.asyncio
@pytest.mark.p0
async def test_remove_member_transfers_knowledge_base_ownership() -> None:
    """ID9：移出成员 → 解绑部门 + 其知识库所有权转交操作人 + 写审计。"""
    fixture = _Fixture()
    await fixture.seed()
    await fixture.bases.create(
        KnowledgeBaseRecord(
            tenant_id=TENANT,
            base_id="kb_from_member",
            name="成员自建库",
            owner_user_id="usr_member",
            created_by="usr_member",
        )
    )
    data = await fixture.service.remove_member(
        tenant_id=TENANT, org_id=ORG, member_id="usr_member", auth=ADMIN
    )
    assert data.removed is True
    assert data.transferred_knowledge_base_count == 1
    assert await fixture.org.links_of_user(TENANT, "usr_member") == []
    record = await fixture.bases.get(TENANT, "kb_from_member")
    assert record is not None
    assert record.owner_user_id == ADMIN.user_id, "知识库所有权须转交操作人"
    entries = await fixture.audit.recent(TENANT, action="member.remove")
    assert entries and entries[0].actor_id == ADMIN.user_id


@pytest.mark.asyncio
@pytest.mark.p0
async def test_remove_member_protects_owner_and_self() -> None:
    """ID9：移除所有者 `14007`；移除自己/同级 `14008`。"""
    fixture = _Fixture()
    await fixture.seed()
    with pytest.raises(ApiError) as owner_guard:
        await fixture.service.remove_member(
            tenant_id=TENANT, org_id=ORG, member_id="usr_owner", auth=ADMIN
        )
    assert owner_guard.value.code is ErrorCode.OWNER_PROTECTED

    with pytest.raises(ApiError) as self_op:
        await fixture.service.remove_member(
            tenant_id=TENANT, org_id=ORG, member_id="usr_admin", auth=ADMIN
        )
    assert self_op.value.code is ErrorCode.MEMBER_MODIFICATION_FORBIDDEN


@pytest.mark.asyncio
async def test_batch_mixed_results_do_not_abort() -> None:
    """ID10：批量操作逐条执行，失败项进入 `failed` 而不中断整体。"""
    fixture = _Fixture()
    await fixture.seed()
    result = await fixture.service.batch(
        tenant_id=TENANT,
        org_id=ORG,
        request=BatchMemberRequest(
            member_ids=["usr_member", "usr_ghost"],
            action=BatchMemberAction.REMOVE,
        ),
        auth=ADMIN,
    )
    assert result.succeeded == 1
    assert [item.member_id for item in result.failed] == ["usr_ghost"]


@pytest.mark.asyncio
async def test_batch_update_role_requires_role_field() -> None:
    """ID10：`update_role` 缺 `role` → 该条失败（`20001`），其余照常。"""
    fixture = _Fixture()
    await fixture.seed()
    result = await fixture.service.batch(
        tenant_id=TENANT,
        org_id=ORG,
        request=BatchMemberRequest(member_ids=["usr_member"], action=BatchMemberAction.UPDATE_ROLE),
        auth=ADMIN,
    )
    assert result.succeeded == 0
    assert result.failed and "角色" in result.failed[0].reason


@pytest.mark.asyncio
async def test_batch_update_role_success() -> None:
    """ID10：批量改角色成功路径。"""
    fixture = _Fixture()
    await fixture.seed()
    result = await fixture.service.batch(
        tenant_id=TENANT,
        org_id=ORG,
        request=BatchMemberRequest(
            member_ids=["usr_member"], action=BatchMemberAction.UPDATE_ROLE, role="guest"
        ),
        auth=ADMIN,
    )
    assert result.succeeded == 1
    assert result.failed == []
    assert await fixture.roles.role_key_of_user(TENANT, "usr_member") == "guest"
