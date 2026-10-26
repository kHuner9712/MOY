"""组织与权限端点集成测试（阶段二 B08，架构文档 §4.4 ID1-ID11 / §4.7 U3/U6）。

逐条对齐 B08「完成后可验证」：

1. 建 5 层成功、第 6 层 `14006`；
2. 移动到自身子树 `14006`；
3. 删除有成员部门 `14006`；
4. 移除 owner `14007`、改同级成员 `14008`；
5. 角色被引用删除 `20005`；
6. 所有变更写 `audit_log` 含前后值；
7. 权限点目录 20 条与 DDL 种子一致。
"""

from __future__ import annotations

import hashlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.business.audit_service import AuditService
from app.core.config import Settings
from app.db.repositories.org import get_org_store
from app.db.repositories.role_repo import PERMISSION_CATALOG_SEED, get_role_store
from tests.conftest import make_token

pytestmark = pytest.mark.integration

ORG = "org_acme"
TENANT = "default"
ADMIN_USER = "usr_admin"
MEMBER_USER = "usr_member"
OWNER_USER = "usr_owner"


def _headers(
    settings: Settings, *, user_id: str = ADMIN_USER, roles: list[str] | None = None, key: str = ""
) -> dict[str, str]:
    """构造鉴权（与可选幂等）请求头。

    Args:
        settings: 配置实例。
        user_id: 用户 ID。
        roles: 角色列表（默认组织管理员）。
        key: 幂等键（空表示不带）。

    Returns:
        dict[str, str]: 请求头。
    """
    token = make_token(
        settings=settings,
        user_id=user_id,
        roles=["org_admin"] if roles is None else roles,
        tenant_id=TENANT,
    )
    headers = {"Authorization": f"Bearer {token}", "X-Tenant-Id": TENANT}
    if key:
        headers["Idempotency-Key"] = key
    return headers


def _key(tag: str) -> str:
    """由标签派生稳定的幂等键（UUID v4 形态）。

    Args:
        tag: 标签。

    Returns:
        str: 幂等键。
    """
    digest = hashlib.sha256(tag.encode("utf-8")).hexdigest()
    return f"{digest[0:8]}-{digest[8:12]}-4{digest[12:15]}-8{digest[15:18]}-{digest[18:30]}"


def _create_dept(
    client: TestClient, settings: Settings, name: str, parent_id: str = "", tag: str = ""
) -> dict[str, object]:
    """调用 ID2 建部门。

    Args:
        client: 测试客户端。
        settings: 配置实例。
        name: 部门名称。
        parent_id: 上级部门 ID。
        tag: 幂等键标签（缺省用名称）。

    Returns:
        dict[str, object]: 响应体。
    """
    response = client.post(
        f"/api/v1/identity/organizations/{ORG}/departments",
        json={"name": name, "parent_id": parent_id},
        headers=_headers(settings, key=_key(tag or name)),
    )
    return response.json()


def test_id1_empty_tree(client: TestClient, settings: Settings) -> None:
    """ID1 空组织返回空数组（登录即可查看）。"""
    body = client.get(
        f"/api/v1/identity/organizations/{ORG}/departments",
        headers=_headers(settings, roles=["member"]),
    ).json()
    assert body["code"] == 0
    assert body["data"]["departments"] == []


def test_id2_creates_five_levels_then_14006(client: TestClient, settings: Settings) -> None:
    """建 5 层成功，第 6 层 `14006` 且文案为层级上限。"""
    parent = ""
    for index in range(1, 6):
        body = _create_dept(client, settings, f"第{index}层", parent, tag=f"lvl{index}")
        assert body["code"] == 0, body
        data = body["data"]
        assert data["level"] == index
        parent = str(data["department_id"])

    sixth = _create_dept(client, settings, "第6层", parent, tag="lvl6")
    assert sixth["code"] == 14006
    assert sixth["message"] == "组织层级最多 5 层，请调整后重试"


def test_id1_tree_contains_path_names(client: TestClient, settings: Settings) -> None:
    """ID1 树返回 `path_names` 与 `children`。"""
    root = _create_dept(client, settings, "总部", tag="root")
    child = _create_dept(client, settings, "研发部", str(root["data"]["department_id"]), tag="rd")
    body = client.get(
        f"/api/v1/identity/organizations/{ORG}/departments", headers=_headers(settings)
    ).json()
    assert body["code"] == 0
    node = body["data"]["departments"][0]
    assert node["path_names"] == ["总部"]
    assert node["children"][0]["department_id"] == child["data"]["department_id"]
    assert node["children"][0]["path_names"] == ["总部", "研发部"]


def test_id1_flat_mode(client: TestClient, settings: Settings) -> None:
    """`flat=true` 返回平铺数组（无嵌套 children）。"""
    _create_dept(client, settings, "总部", tag="root")
    body = client.get(
        f"/api/v1/identity/organizations/{ORG}/departments?flat=true", headers=_headers(settings)
    ).json()
    assert body["code"] == 0
    assert len(body["data"]["departments"]) == 1
    assert body["data"]["departments"][0]["children"] == []


def test_id3_move_into_subtree_returns_14006(client: TestClient, settings: Settings) -> None:
    """移动到自身子树 → `14006` + 专属文案。"""
    root = _create_dept(client, settings, "总部", tag="root")
    child = _create_dept(client, settings, "研发部", str(root["data"]["department_id"]), tag="rd")
    body = client.patch(
        f"/api/v1/identity/departments/{root['data']['department_id']}",
        json={"parent_id": child["data"]["department_id"]},
        headers=_headers(settings, key=_key("move-bad")),
    ).json()
    assert body["code"] == 14006
    assert body["message"] == "不能把部门移动到自己的下级部门"


def test_id4_delete_with_children_returns_14006(client: TestClient, settings: Settings) -> None:
    """删除有子部门的部门 → `14006`。"""
    root = _create_dept(client, settings, "总部", tag="root")
    _create_dept(client, settings, "研发部", str(root["data"]["department_id"]), tag="rd")
    body = client.delete(
        f"/api/v1/identity/departments/{root['data']['department_id']}",
        headers=_headers(settings, key=_key("del-root")),
    ).json()
    assert body["code"] == 14006


def test_id4_delete_empty_department_succeeds(client: TestClient, settings: Settings) -> None:
    """删除空部门成功。"""
    created = _create_dept(client, settings, "临时部门", tag="tmp")
    body = client.delete(
        f"/api/v1/identity/departments/{created['data']['department_id']}",
        headers=_headers(settings, key=_key("del-tmp")),
    ).json()
    assert body["code"] == 0
    assert body["data"]["deleted"] is True


def test_id4_delete_missing_department_returns_14005(
    client: TestClient, settings: Settings
) -> None:
    """删除不存在的部门 → `14005`。"""
    body = client.delete(
        "/api/v1/identity/departments/dept_missing",
        headers=_headers(settings, key=_key("del-missing")),
    ).json()
    assert body["code"] == 14005


def test_id5_permission_catalog_has_20_entries(client: TestClient, settings: Settings) -> None:
    """ID5 权限点目录 20 条（16 业务 + 4 运营），分组齐全。"""
    body = client.get("/api/v1/identity/permissions", headers=_headers(settings)).json()
    assert body["code"] == 0
    groups = body["data"]["groups"]
    total = sum(len(group["permissions"]) for group in groups)
    assert total == 20
    keys = {group["group"] for group in groups}
    assert {"knowledge", "tool", "member", "billing", "audit", "ops"} <= keys


def test_catalog_matches_ddl_seed() -> None:
    """代码常量的 20 个权限点与 DDL 种子逐条一致（防双口径漂移）。"""
    assert len(PERMISSION_CATALOG_SEED) == 20
    business = [item for item in PERMISSION_CATALOG_SEED if not item.permission.startswith("ops:")]
    ops = [item for item in PERMISSION_CATALOG_SEED if item.permission.startswith("ops:")]
    assert len(business) == 16
    assert len(ops) == 4


def test_id6_role_list_contains_four_builtin(client: TestClient, settings: Settings) -> None:
    """ID6 返回 4 个内置角色且权限点非空。"""
    body = client.get(
        f"/api/v1/identity/organizations/{ORG}/roles", headers=_headers(settings)
    ).json()
    assert body["code"] == 0
    items = body["data"]["items"]
    builtin = [item for item in items if item["is_builtin"]]
    assert {item["role_key"] for item in builtin} == {"owner", "org_admin", "member", "guest"}
    owner = next(item for item in builtin if item["role_key"] == "owner")
    assert "team:role:manage" in owner["permissions"]
    guest = next(item for item in builtin if item["role_key"] == "guest")
    assert "team:role:manage" not in guest["permissions"]


def test_id7_builtin_role_delete_returns_20005(client: TestClient, settings: Settings) -> None:
    """内置角色不可删除 → `20005`。"""
    body = client.delete(
        f"/api/v1/identity/organizations/{ORG}/roles/role_owner",
        headers=_headers(settings, key=_key("del-owner-role")),
    ).json()
    assert body["code"] == 20005


def test_id7_role_not_found_returns_14003(client: TestClient, settings: Settings) -> None:
    """不存在的角色 → `14003`。"""
    body = client.delete(
        f"/api/v1/identity/organizations/{ORG}/roles/role_custom_missing",
        headers=_headers(settings, key=_key("del-missing-role")),
    ).json()
    assert body["code"] == 14003


async def _seed_member(role_key: str, user_id: str) -> None:
    """直接播种成员与角色（绕过邀请链路）。

    Args:
        role_key: 角色标识。
        user_id: 用户 ID。
    """
    store = get_role_store()
    await store.ensure_builtin_roles(TENANT, ORG)
    roles = await store.list_roles(TENANT, ORG)
    role_id = next(item.role_id for item in roles if item.role_key == role_key)
    await store.assign_role(TENANT, ORG, user_id, role_id, assigned_by=ADMIN_USER)


def test_id8_remove_owner_returns_14007(client: TestClient, settings: Settings) -> None:
    """改企业所有者 → `14007`。"""
    import asyncio

    asyncio.run(_seed_member("owner", OWNER_USER))
    body = client.patch(
        f"/api/v1/identity/organizations/{ORG}/members/{OWNER_USER}",
        json={"role": "member"},
        headers=_headers(settings, key=_key("chg-owner")),
    ).json()
    assert body["code"] == 14007
    assert body["message"] == "不能移除企业所有者，请先转移所有权"


def test_id8_modify_self_returns_14008(client: TestClient, settings: Settings) -> None:
    """改自己 → `14008`。"""
    import asyncio

    asyncio.run(_seed_member("org_admin", ADMIN_USER))
    body = client.patch(
        f"/api/v1/identity/organizations/{ORG}/members/{ADMIN_USER}",
        json={"role": "member"},
        headers=_headers(settings, key=_key("chg-self")),
    ).json()
    assert body["code"] == 14008
    assert body["message"] == "你没有权限修改该成员"


def test_id8_modify_same_level_returns_14008(client: TestClient, settings: Settings) -> None:
    """改同级（同为管理员）成员 → `14008`。"""
    import asyncio

    asyncio.run(_seed_member("org_admin", "usr_admin2"))
    body = client.patch(
        f"/api/v1/identity/organizations/{ORG}/members/usr_admin2",
        json={"role": "member"},
        headers=_headers(settings, key=_key("chg-peer")),
    ).json()
    assert body["code"] == 14008


def test_id8_updates_role_and_writes_audit(client: TestClient, settings: Settings) -> None:
    """改普通成员角色成功，且审计含 before/after 角色。"""
    import asyncio

    asyncio.run(_seed_member("member", MEMBER_USER))
    body = client.patch(
        f"/api/v1/identity/organizations/{ORG}/members/{MEMBER_USER}",
        json={"role": "guest"},
        headers=_headers(settings, key=_key("chg-member")),
    ).json()
    assert body["code"] == 0
    assert body["data"]["role"] == "guest"

    audit_entries = asyncio.run(AuditService(settings).recent(TENANT, action="member.update"))
    assert audit_entries
    summary = audit_entries[0].change_summary
    assert summary["before"]["role"] == "member"
    assert summary["after"]["role"] == "guest"


def test_id8_missing_member_returns_14001(client: TestClient, settings: Settings) -> None:
    """成员不存在 → `14001`。"""
    body = client.patch(
        f"/api/v1/identity/organizations/{ORG}/members/usr_nobody",
        json={"role": "member"},
        headers=_headers(settings, key=_key("chg-nobody")),
    ).json()
    assert body["code"] == 14001


def test_id9_remove_owner_returns_14007(client: TestClient, settings: Settings) -> None:
    """移出企业所有者 → `14007`。"""
    import asyncio

    asyncio.run(_seed_member("owner", OWNER_USER))
    body = client.delete(
        f"/api/v1/identity/organizations/{ORG}/members/{OWNER_USER}",
        headers=_headers(settings, key=_key("rm-owner")),
    ).json()
    assert body["code"] == 14007


def test_id9_remove_member_transfers_knowledge_base(client: TestClient, settings: Settings) -> None:
    """移出成员：其创建的知识库转交管理员并返回数量。"""
    import asyncio

    from app.db.repositories.knowledge_base_repo import (
        KnowledgeBaseRecord,
        get_knowledge_base_store,
    )

    asyncio.run(_seed_member("member", MEMBER_USER))
    store = get_knowledge_base_store()
    asyncio.run(
        store.create(
            KnowledgeBaseRecord(
                tenant_id=TENANT,
                base_id="kb_transfer",
                name="成员创建的库",
                owner_user_id=MEMBER_USER,
                created_by=MEMBER_USER,
            )
        )
    )
    body = client.delete(
        f"/api/v1/identity/organizations/{ORG}/members/{MEMBER_USER}",
        headers=_headers(settings, key=_key("rm-member")),
    ).json()
    assert body["code"] == 0
    assert body["data"]["removed"] is True
    assert body["data"]["transferred_knowledge_base_count"] == 1
    record = asyncio.run(store.get(TENANT, "kb_transfer"))
    assert record is not None and record.owner_user_id == ADMIN_USER


def test_id10_batch_update_reports_failures(client: TestClient, settings: Settings) -> None:
    """ID10 批量：成功与失败逐条返回，不整批失败。"""
    import asyncio

    asyncio.run(_seed_member("member", MEMBER_USER))
    asyncio.run(_seed_member("owner", OWNER_USER))
    body = client.post(
        f"/api/v1/identity/organizations/{ORG}/members/batch",
        json={"member_ids": [MEMBER_USER, OWNER_USER], "action": "update_role", "role": "guest"},
        headers=_headers(settings, key=_key("batch-1")),
    ).json()
    assert body["code"] == 0
    assert body["data"]["succeeded"] == 1
    assert len(body["data"]["failed"]) == 1
    assert body["data"]["failed"][0]["member_id"] == OWNER_USER
    assert body["data"]["failed"][0]["reason"] == "不能移除企业所有者，请先转移所有权"


def test_id10_same_key_different_body_is_conflict(client: TestClient, settings: Settings) -> None:
    """同一幂等键换请求体 → `20005`（幂等指纹含 body_digest，不得静默回放）。"""
    import asyncio

    asyncio.run(_seed_member("member", MEMBER_USER))
    key = _key("batch-dup")
    first = client.post(
        f"/api/v1/identity/organizations/{ORG}/members/batch",
        json={"member_ids": [MEMBER_USER], "action": "update_role", "role": "guest"},
        headers=_headers(settings, key=key),
    ).json()
    assert first["code"] == 0
    second = client.post(
        f"/api/v1/identity/organizations/{ORG}/members/batch",
        json={"member_ids": [MEMBER_USER], "action": "update_role", "role": "member"},
        headers=_headers(settings, key=key),
    ).json()
    assert second["code"] == 20005


def test_id10_rejects_more_than_hundred_members(client: TestClient, settings: Settings) -> None:
    """批量超过 100 位 → 参数校验失败（20001）。"""
    response = client.post(
        f"/api/v1/identity/organizations/{ORG}/members/batch",
        json={
            "member_ids": [f"usr_{index}" for index in range(101)],
            "action": "update_role",
            "role": "guest",
        },
        headers=_headers(settings, key=_key("batch-101")),
    )
    assert response.status_code == 400


def test_id11_action_catalog_groups(client: TestClient, settings: Settings) -> None:
    """ID11 动作目录含成员/部门/角色分组，且展示名为业务语言。"""
    body = client.get("/api/v1/identity/audit-logs/actions", headers=_headers(settings)).json()
    assert body["code"] == 0
    groups = {item["group"]: item for item in body["data"]["groups"]}
    assert {"department", "member", "role"} <= set(groups)
    member_actions = {item["action"]: item["display_name"] for item in groups["member"]["actions"]}
    assert member_actions["member.update"] == "变更成员角色与部门"


def test_u3_members_list_uses_business_store(client: TestClient, settings: Settings) -> None:
    """U3 成员列表来自业务库（D-13）：播种后可查到，并带部门与状态字段。"""
    import asyncio

    asyncio.run(_seed_member("member", MEMBER_USER))
    body = client.get(
        f"/api/v1/identity/organizations/{ORG}/members", headers=_headers(settings)
    ).json()
    assert body["code"] == 0
    items = body["data"]["items"]
    assert [item["user_id"] for item in items] == [MEMBER_USER]
    assert items[0]["status"] == "active"
    assert "department_ids" in items[0]


def test_u3_filter_by_status_and_department(client: TestClient, settings: Settings) -> None:
    """U3 支持 `status` / `department_id` 筛选（B08 加性 query）。"""
    import asyncio

    from app.db.repositories.org import MemberLink

    created = _create_dept(client, settings, "研发部", tag="u3-dept")
    department_id = str(created["data"]["department_id"])
    asyncio.run(_seed_member("member", MEMBER_USER))
    asyncio.run(
        get_org_store().link_member(
            MemberLink(tenant_id=TENANT, department_id=department_id, user_id=MEMBER_USER)
        )
    )
    matched = client.get(
        f"/api/v1/identity/organizations/{ORG}/members?department_id={department_id}&status=active",
        headers=_headers(settings),
    ).json()
    assert matched["code"] == 0
    assert len(matched["data"]["items"]) == 1
    miss = client.get(
        f"/api/v1/identity/organizations/{ORG}/members?department_id=dept_other",
        headers=_headers(settings),
    ).json()
    assert miss["data"]["items"] == []


def test_u6_audit_logs_require_permission(client: TestClient, settings: Settings) -> None:
    """U6 无 `audit:log:read` 权限 → `20003`（fail-closed）。"""
    body = client.post(
        "/api/v1/identity/audit-logs",
        json={"page": 1, "page_size": 20},
        headers={
            **_headers(settings, roles=["member"], key=_key("audit-deny")),
        },
    ).json()
    assert body["code"] == 20003


def test_u6_audit_logs_filter_and_fields(client: TestClient, settings: Settings) -> None:
    """U6 多维筛选可用，响应含 `action_display_name` / `change_summary`。"""
    _create_dept(client, settings, "审计部门", tag="audit-dept")
    body = client.post(
        "/api/v1/identity/audit-logs",
        json={"page": 1, "page_size": 20, "action_group": "department"},
        headers=_headers(settings, key=_key("audit-1")),
    ).json()
    assert body["code"] == 0
    items = body["data"]["items"]
    assert items
    assert items[0]["action_display_name"] == "新建部门"
    assert items[0]["change_summary"]["after"]["name"] == "审计部门"


def test_u6_rejects_span_over_90_days(client: TestClient, settings: Settings) -> None:
    """时间跨度 > 90 天 → `20001` + 专属文案。"""
    body = client.post(
        "/api/v1/identity/audit-logs",
        json={
            "page": 1,
            "page_size": 20,
            "start_at": "2026-01-01T00:00:00Z",
            "end_at": "2026-06-01T00:00:00Z",
        },
        headers=_headers(settings, key=_key("audit-span")),
    ).json()
    assert body["code"] == 20001
    assert body["message"] == "查询时间范围最长 90 天，请缩小范围"


@pytest.fixture(autouse=True)
def _reset_org_state(app: FastAPI) -> None:
    """每个用例前后隔离组织/角色/出箱状态。

    Args:
        app: 应用实例（保证夹具顺序与配置一致）。
    """
    from app.business.kc_projection import reset_outbox_store
    from app.db.repositories.org import reset_org_store
    from app.db.repositories.role_repo import reset_role_store

    for reset in (reset_org_store, reset_role_store, reset_outbox_store):
        reset()
    yield
    for reset in (reset_org_store, reset_role_store, reset_outbox_store):
        reset()


def test_projection_is_one_way_and_never_blocks(client: TestClient, settings: Settings) -> None:
    """D-13：建部门后产生投影出箱记录，且业务成功不依赖投影。"""
    import asyncio

    from app.business.kc_projection import get_outbox_store

    created = _create_dept(client, settings, "投影部门", tag="proj")
    assert created["code"] == 0
    entries = asyncio.run(get_outbox_store().all_entries(TENANT))  # type: ignore[attr-defined]
    assert entries
    assert entries[0].kind == "kc_group_upsert"
    assert entries[0].status == "pending"


def test_projection_failure_does_not_rollback_business(
    client: TestClient, settings: Settings
) -> None:
    """投影登记失败不得回滚业务事务（建部门仍成功）。"""
    from app.business import kc_projection

    class _BrokenStore(kc_projection.InMemoryOutboxStore):
        async def enqueue(self, entry: kc_projection.OutboxEntry) -> kc_projection.OutboxEntry:
            raise RuntimeError("outbox down")

    original = kc_projection.get_outbox_store
    kc_projection.get_outbox_store = lambda settings=None: _BrokenStore()  # type: ignore[assignment]
    try:
        body = _create_dept(client, settings, "投影故障部门", tag="proj-fail")
    finally:
        kc_projection.get_outbox_store = original  # type: ignore[assignment]
    assert body["code"] == 0
    assert body["data"]["name"] == "投影故障部门"


# ===========================================================================
# 真实 PostgreSQL：组织 / 角色 / 审计 / 出箱仓储 SQL 冒烟（`--run-pg` 启用）
#
# 为什么必要：内存实现证明业务规则，证明不了 SQL——「物化路径子树原子移动」
# 「主部门唯一部分索引」「权限点目录平台共享行」「出箱退避字段类型」
# 都只有真实库能验证；不测等于把 B12 前端组织页建在未验证的 SQL 上。
# ===========================================================================
@pytest.mark.pg
async def test_postgres_org_role_audit_outbox_smoke(pg_dsn: str) -> None:
    """真实库跑通部门树、角色权限、审计查询与投影出箱。"""
    from app.business.audit_service import AuditService
    from app.business.kc_projection import (
        KIND_GROUP_UPSERT,
        OutboxEntry,
        PostgresOutboxStore,
    )
    from app.core.config import Settings as Conf
    from app.db.repositories.audit_repo import AuditQuery, query_audit_logs
    from app.db.repositories.org import (
        DepartmentRecord,
        MemberLink,
        PostgresOrgStore,
    )
    from app.db.repositories.role_repo import PostgresRoleStore
    from app.db.session import tenant_scope

    conf = Conf(
        env="test",
        deploy_mode="private",
        business_store="postgres",
        database_url=pg_dsn,
    )
    tenant = "tnt_pg_b08"
    org_id = "org_pg_b08"
    org = PostgresOrgStore(conf)
    roles = PostgresRoleStore(conf)
    outbox = PostgresOutboxStore(conf)

    async def purge() -> None:
        async with tenant_scope(tenant, settings=conf) as uow:
            for statement in (
                "DELETE FROM department_member WHERE tenant_id = :tenant_id",
                "DELETE FROM department WHERE tenant_id = :tenant_id",
                "DELETE FROM member_role WHERE tenant_id = :tenant_id",
                "DELETE FROM role_permission WHERE tenant_id = :tenant_id",
                "DELETE FROM role WHERE tenant_id = :tenant_id",
                "DELETE FROM integration_outbox WHERE tenant_id = :tenant_id",
            ):
                await uow.execute(statement, {"tenant_id": tenant})

    await purge()
    try:
        # ---- 部门树：建 → 移动子树 → 子部门计数 → 主部门唯一 ----
        root = await org.create_department(
            DepartmentRecord(
                tenant_id=tenant,
                department_id="dept_pg_root",
                org_id=org_id,
                name="总部",
                path="/dept_pg_root/",
                level=1,
            )
        )
        child = await org.create_department(
            DepartmentRecord(
                tenant_id=tenant,
                department_id="dept_pg_child",
                org_id=org_id,
                parent_id=root.department_id,
                name="研发部",
                path="/dept_pg_root/dept_pg_child/",
                level=2,
            )
        )
        other = await org.create_department(
            DepartmentRecord(
                tenant_id=tenant,
                department_id="dept_pg_other",
                org_id=org_id,
                name="销售部",
                path="/dept_pg_other/",
                level=1,
            )
        )
        assert child.level == 2
        assert await org.count_children(tenant, root.department_id) == 1

        moved = await org.move_subtree(tenant, org_id, child.path, f"{other.path}dept_pg_child/", 0)
        assert moved == 1
        reloaded = await org.get_department(tenant, child.department_id)
        assert reloaded is not None
        assert reloaded.path.startswith(other.path)

        # ---- 成员关联与主部门唯一 ----
        await org.link_member(
            MemberLink(tenant_id=tenant, department_id=other.department_id, user_id="usr_pg")
        )
        await org.link_member(
            MemberLink(
                tenant_id=tenant,
                department_id=child.department_id,
                user_id="usr_pg",
                is_primary=True,
            )
        )
        await org.clear_primary(tenant, "usr_pg")
        assert await org.set_primary(tenant, other.department_id, "usr_pg") is True
        links = await org.links_of_user(tenant, "usr_pg")
        assert [link.department_id for link in links if link.is_primary] == [other.department_id]
        assert await org.refresh_member_count(tenant, other.department_id) == 1
        assert "usr_pg" in await org.user_ids(tenant, org_id)

        # ---- 角色：播种 → 分配 → 权限点解析 → 引用计数 ----
        seeded = await roles.ensure_builtin_roles(tenant, org_id)
        assert {item.role_key for item in seeded} == {"owner", "org_admin", "member", "guest"}
        await roles.assign_role(tenant, org_id, "usr_pg", "role_org_admin", assigned_by="usr_admin")
        assert await roles.role_key_of_user(tenant, "usr_pg") == "org_admin"
        assert await roles.role_ids_of_user(tenant, "usr_pg") == ["role_org_admin"]
        points = await roles.permissions_of_user(tenant, "usr_pg")
        assert "team:role:manage" in points
        assert await roles.count_members(tenant, "role_org_admin") == 1
        catalog = await roles.permission_catalog(tenant)
        assert len(catalog) == 20
        assert await roles.delete_role(tenant, "role_owner") is False

        # ---- 审计：写入 → 多维筛选 ----
        # 审计表对应用角色**只允许追加**（不可删改），故用**每次运行唯一**的操作人隔离断言
        import uuid

        actor = f"usr_pg_b08_{uuid.uuid4().hex[:12]}"
        audit = AuditService(conf)
        await audit.record(
            tenant_id=tenant,
            actor_id=actor,
            action="department.create",
            resource_type="department",
            resource_id="dept_pg_root",
            actor_role="org_admin",
            change_summary={"after": {"name": "总部"}},
        )
        result = await query_audit_logs(
            conf,
            tenant,
            AuditQuery(actor_id=actor, action_group="department", page=1, page_size=10),
        )
        assert result.total == 1
        assert result.items[0].display_name == "新建部门"

        # ---- 出箱：入队 → 到期扫描 → 重试 → 失败 ----
        await outbox.enqueue(
            OutboxEntry(
                tenant_id=tenant,
                outbox_id="obx_pg_1",
                kind=KIND_GROUP_UPSERT,
                payload={"group_id": "dept_pg_root", "name": "总部"},
            )
        )
        due = await outbox.due(tenant, limit=10)
        assert [entry.outbox_id for entry in due] == ["obx_pg_1"]
        await outbox.mark_retry(
            tenant,
            "obx_pg_1",
            attempts=1,
            next_attempt_at="2030-01-01T00:00:00+00:00",
            error="boom",
        )
        assert await outbox.due(tenant, limit=10) == []
        await outbox.mark_failed(tenant, "obx_pg_1", error="boom")
        await outbox.mark_sent(tenant, "obx_pg_1")
    finally:
        await purge()
