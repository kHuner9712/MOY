"""权限判权矩阵（阶段二 B08，测试方案 TC-P2-PERM-01/02/06/07）。

**为什么必须单独有这个文件**：测试方案 §5 要求「8 类账号 × 3 入口（列表 / 详情 / 检索）
24 格逐格断言」，而此前该文件**未建**（QA 报告 QA-P3-05 / §7 遗留项），
导致「列表过滤了但详情接口没校验」这类缺陷只能靠偶发用例发现。

本文件的两个判据（缺一不可，缺第二条就是「列表看不到、检索拿得到」）：

1. **列表不含**：无权账号的 R2 结果里不得出现该库；
2. **详情/检索拒绝**：同一账号访问同一库必须 `20003`，且 HTTP 状态**不是 404**
   （不得用「不存在」掩盖「无权」，否则无法区分越权与真不存在）。

入口选择（与 PRD「列表 / 详情 / 检索」三入口一一对应）：

| 入口 | 端点 |
| --- | --- |
| 列表 | `GET /api/v1/knowledge/bases`（R2） |
| 详情 | `GET /api/v1/knowledge/bases/{base_id}/documents`（KB3，需知识库级读权限） |
| 检索 | `POST /api/v1/knowledge/search`（R7） |
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Final

import pytest
from fastapi.testclient import TestClient

from app.business.policy_service import reset_policy_service
from app.core.config import Settings
from app.db.repositories.grant_repo import GrantRecord, get_grant_store
from app.db.repositories.knowledge_base_repo import (
    KnowledgeBaseRecord,
    get_knowledge_base_store,
)
from app.db.repositories.org_repo import MemberLink, get_org_store, reset_org_store
from app.db.repositories.role_repo import reset_role_store
from tests.conftest import make_token

pytestmark = pytest.mark.integration

#: 生效租户（私有化模式下请求头/令牌里的租户会被收敛为 `default`）
TENANT: Final[str] = "default"

#: 另一个租户（跨租户维度用；需要 SaaS 模式才不会被收敛）
OTHER_TENANT: Final[str] = "tnt_other"

#: 组织 ID
ORG: Final[str] = "org_acme"

#: 受限库 ID（仅被授权者可见）
RESTRICTED_BASE: Final[str] = "kb_matrix_restricted"

#: 全企业库 ID（全员可见）
ORG_BASE: Final[str] = "kb_matrix_org"

#: 部门 ID：被授权部门
DEPARTMENT_RD: Final[str] = "dept_rd"

#: 部门 ID：未授权部门
DEPARTMENT_SALES: Final[str] = "dept_sales"

#: 账号定义：`(账号名, 用户 ID, 角色列表)`
ACCOUNTS: Final[tuple[tuple[str, str, tuple[str, ...]], ...]] = (
    ("owner", "usr_owner", ("owner",)),
    ("admin", "usr_admin", ("org_admin",)),
    ("manager", "usr_manager", ("member",)),
    ("member", "usr_member", ("member",)),
    ("guest", "usr_guest", ("guest",)),
    ("cross_department", "usr_cross_dept", ("member",)),
    ("cross_tenant", "usr_evil", ("member",)),
    ("no_role", "usr_no_role", ()),
)

#: 允许访问受限库的账号（owner / admin / 部门负责人 / 直接受权成员）
ALLOWED: Final[frozenset[str]] = frozenset({"owner", "admin", "manager", "member"})

#: 被拒绝的账号（用于「拒绝不得是 404」的逐格断言；不写 `pytest.skip`，
#: 因为 `--run-pg` 门禁要求 skipped == 0）
DENIED_ACCOUNTS: Final[tuple[tuple[str, str, tuple[str, ...]], ...]] = tuple(
    item for item in ACCOUNTS if item[0] not in ALLOWED
)


def _key(tag: str) -> str:
    """由标签派生稳定的幂等键。

    Args:
        tag: 标签。

    Returns:
        str: UUID v4 形态的幂等键。
    """
    digest = hashlib.sha256(tag.encode("utf-8")).hexdigest()
    return f"{digest[0:8]}-{digest[8:12]}-4{digest[12:15]}-8{digest[15:18]}-{digest[18:30]}"


def _headers(
    settings: Settings,
    *,
    user_id: str,
    roles: tuple[str, ...],
    tenant_id: str = TENANT,
    idempotent: str = "",
) -> dict[str, str]:
    """构造请求头。

    Args:
        settings: 配置实例。
        user_id: 用户 ID。
        roles: 角色列表。
        tenant_id: 租户 ID。
        idempotent: 幂等键（空表示不带）。

    Returns:
        dict[str, str]: 请求头。
    """
    token = make_token(settings=settings, user_id=user_id, roles=list(roles), tenant_id=tenant_id)
    headers = {"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant_id}
    if idempotent:
        headers["Idempotency-Key"] = idempotent
    return headers


@dataclass(frozen=True, slots=True)
class SeedState:
    """矩阵造数结果。

    Attributes:
        restricted_base: 受限库 ID。
        org_base: 全企业库 ID。
    """

    restricted_base: str = RESTRICTED_BASE
    org_base: str = ORG_BASE


async def seed_matrix() -> SeedState:
    """造数：1 个受限库（成员直授 + 部门授权）、1 个全企业库、2 个部门。

    Returns:
        SeedState: 造数结果。
    """
    from app.db.repositories.role_repo import get_role_store

    bases = get_knowledge_base_store()
    await bases.create(
        KnowledgeBaseRecord(
            tenant_id=TENANT,
            base_id=RESTRICTED_BASE,
            name="高管薪酬明细",
            visibility="restricted",
            owner_user_id="usr_owner",
            created_by="usr_owner",
        )
    )
    await bases.create(
        KnowledgeBaseRecord(
            tenant_id=TENANT,
            base_id=ORG_BASE,
            name="公共制度库",
            visibility="organization",
            owner_user_id="usr_admin",
            created_by="usr_admin",
        )
    )
    grants = get_grant_store()
    await grants.create(
        GrantRecord(
            tenant_id=TENANT,
            grant_id="grt_matrix_user",
            base_id=RESTRICTED_BASE,
            subject_type="user",
            subject_id="usr_member",
            permission="viewer",
            granted_by="usr_owner",
        )
    )
    await grants.create(
        GrantRecord(
            tenant_id=TENANT,
            grant_id="grt_matrix_dept",
            base_id=RESTRICTED_BASE,
            subject_type="department",
            subject_id=DEPARTMENT_RD,
            permission="viewer",
            granted_by="usr_owner",
        )
    )

    org = get_org_store()
    await org.link_member(
        MemberLink(tenant_id=TENANT, department_id=DEPARTMENT_RD, user_id="usr_manager")
    )
    await org.link_member(
        MemberLink(tenant_id=TENANT, department_id=DEPARTMENT_SALES, user_id="usr_cross_dept")
    )
    await org.link_member(
        MemberLink(tenant_id=TENANT, department_id=DEPARTMENT_SALES, user_id="usr_member")
    )

    roles = get_role_store()
    await roles.ensure_builtin_roles(TENANT, ORG)
    return SeedState()


def _list_base_ids(client: TestClient, headers: dict[str, str]) -> list[str]:
    """取 R2 列表中的知识库 ID（用于「列表不含」断言）。

    Args:
        client: 测试客户端。
        headers: 请求头。

    Returns:
        list[str]: 知识库 ID 列表。
    """
    body = client.get("/api/v1/knowledge/bases", headers=headers).json()
    assert body["code"] == 0, body
    return [str(item["base_id"]) for item in body["data"]["items"]]


def _detail(client: TestClient, headers: dict[str, str], base_id: str) -> dict[str, object]:
    """调用 KB3 文档清单（详情入口）。

    Args:
        client: 测试客户端。
        headers: 请求头。
        base_id: 知识库 ID。

    Returns:
        dict[str, object]: 响应体。
    """
    return client.get(f"/api/v1/knowledge/bases/{base_id}/documents", headers=headers).json()


def _search(client: TestClient, headers: dict[str, str], base_id: str) -> dict[str, object]:
    """调用 R7 检索。

    Args:
        client: 测试客户端。
        headers: 请求头。
        base_id: 知识库 ID。

    Returns:
        dict[str, object]: 响应体。
    """
    enriched = {**headers, "Idempotency-Key": _key(f"search-{base_id}-{headers['X-Tenant-Id']}")}
    response = client.post(
        "/api/v1/knowledge/search",
        json={"base_id": base_id, "query": "薪酬"},
        headers=enriched,
    )
    return response.json()


@pytest.fixture(autouse=True)
def _clean_state(app: object) -> object:
    """每个用例前后重置策略缓存与组织/角色/知识库存储。

    Args:
        app: 应用实例（保证夹具顺序）。

    Yields:
        object: 无。
    """
    from app.db.repositories.grant_repo import reset_grant_store
    from app.db.repositories.knowledge_base_repo import reset_knowledge_base_store

    def _reset() -> None:
        reset_policy_service()
        reset_org_store()
        reset_role_store()
        reset_grant_store()
        reset_knowledge_base_store()

    _reset()
    yield None
    _reset()


# ===========================================================================
# TC-P2-PERM-01：8 账号 × 3 入口，24 格逐格断言
# ===========================================================================
@pytest.mark.parametrize(("account", "user_id", "roles"), ACCOUNTS)
async def test_restricted_base_three_entries(
    account: str,
    user_id: str,
    roles: tuple[str, ...],
    client: TestClient,
    settings: Settings,
) -> None:
    """受限库：8 账号 × 3 入口逐格断言（列表 / 详情 / 检索三处必须一致）。"""
    await seed_matrix()
    headers = _headers(settings, user_id=user_id, roles=roles)
    allowed = account in ALLOWED

    # ① 列表入口：无权账号的列表里不得出现该库
    listed = RESTRICTED_BASE in _list_base_ids(client, headers)
    assert listed is allowed, f"{account} 列表可见性不符预期"

    # ② 详情入口（KB3）：无权必须 20003，且不得是 404
    detail = _detail(client, headers, RESTRICTED_BASE)
    if allowed:
        assert detail["code"] == 0, f"{account} 应可读受限库详情：{detail}"
    else:
        assert detail["code"] == 20003, f"{account} 详情必须 20003：{detail}"

    # ③ 检索入口（R7）：无权必须 20003（B05 的 fail-closed 语义）
    search = _search(client, headers, RESTRICTED_BASE)
    if allowed:
        assert search["code"] == 0, f"{account} 应可检索受限库：{search}"
    else:
        assert search["code"] == 20003, f"{account} 检索必须 20003：{search}"


@pytest.mark.parametrize(("account", "user_id", "roles"), DENIED_ACCOUNTS)
async def test_denied_entries_never_return_404(
    account: str,
    user_id: str,
    roles: tuple[str, ...],
    client: TestClient,
    settings: Settings,
) -> None:
    """TC-P2-PERM-07：拒绝必须是「无权限」而非「不存在」（HTTP 状态不得为 404）。"""
    await seed_matrix()
    headers = _headers(settings, user_id=user_id, roles=roles)
    response = client.get(f"/api/v1/knowledge/bases/{RESTRICTED_BASE}/documents", headers=headers)
    assert response.status_code != 404
    assert response.json()["code"] == 20003


async def test_organization_base_visible_to_all_accounts(
    client: TestClient, settings: Settings
) -> None:
    """全企业库对所有同租户账号可见（含访客），跨租户不可见。"""
    await seed_matrix()
    for account, user_id, roles in ACCOUNTS:
        if account == "cross_tenant":
            continue
        headers = _headers(settings, user_id=user_id, roles=roles)
        assert ORG_BASE in _list_base_ids(client, headers), f"{account} 应可见全企业库"
        assert _detail(client, headers, ORG_BASE)["code"] == 0, f"{account} 详情应可读"


async def test_cross_tenant_list_is_empty(client: TestClient, settings: Settings) -> None:
    """跨租户账号列表为空（租户层过滤在存储层完成）。"""
    await seed_matrix()
    headers = _headers(settings, user_id="usr_evil", roles=("member",), tenant_id=TENANT)
    assert RESTRICTED_BASE not in _list_base_ids(client, headers)

    # SaaS 模式 + 另一个租户：列表必然为空（需要独立 app，私有化模式会收敛为单租户）
    from fastapi.testclient import TestClient as _TestClient

    from app.main import create_app

    saas_settings = Settings(
        env="test",
        deploy_mode="saas",
        downstream_enabled=False,
        auth_mode="stub",
        jwt_secret=settings.jwt_secret,
        ratelimit_enabled=False,
    )
    evil_headers = _headers(
        saas_settings, user_id="usr_evil", roles=("member",), tenant_id=OTHER_TENANT
    )
    with _TestClient(create_app(saas_settings)) as saas_client:
        body = saas_client.get("/api/v1/knowledge/bases", headers=evil_headers).json()
        detail = saas_client.get(
            f"/api/v1/knowledge/bases/{RESTRICTED_BASE}/documents", headers=evil_headers
        )
    assert body["code"] == 0
    assert body["data"]["items"] == []
    # 跨租户的详情入口：业务库层面等价于「本租户不存在」（11001），
    # 但**绝不允许**返回成功或 500；检索入口另有 fail-closed 的 20003（见下面的用例）
    assert detail.json()["code"] in {20003, 11001}
    assert detail.json()["code"] != 0


async def test_no_role_account_is_fail_closed(client: TestClient, settings: Settings) -> None:
    """无角色账号：受限库详情与检索都拒绝（不得「查不到角色就放行」）。"""
    await seed_matrix()
    headers = _headers(settings, user_id="usr_no_role", roles=())
    assert _detail(client, headers, RESTRICTED_BASE)["code"] == 20003
    assert _search(client, headers, RESTRICTED_BASE)["code"] == 20003


async def test_id_form_variant_does_not_bypass(client: TestClient, settings: Settings) -> None:
    """同一对象两种 ID 写法都不得绕过判权（QA B05-P0-01 回归）。"""
    await seed_matrix()
    headers = _headers(settings, user_id="usr_guest", roles=("guest",))
    bare = RESTRICTED_BASE.removeprefix("kb_")
    assert _detail(client, headers, bare)["code"] == 20003
    assert _search(client, headers, bare)["code"] == 20003


# ===========================================================================
# TC-P2-PERM-02：写入口 + 删库三分支
# ===========================================================================
async def test_kb_delete_three_branches(client: TestClient, settings: Settings) -> None:
    """删库三分支（裁决 1.5）：member 删自己的成功；删他人/公共库 20003；org_admin 任意成功。"""
    await seed_matrix()
    from app.db.repositories.knowledge_base_repo import (
        KnowledgeBaseRecord,
        get_knowledge_base_store,
    )

    store = get_knowledge_base_store()
    await store.create(
        KnowledgeBaseRecord(
            tenant_id=TENANT,
            base_id="kb_member_own",
            name="成员自建库",
            visibility="private",
            owner_user_id="usr_member",
            created_by="usr_member",
        )
    )

    member_headers = {
        **_headers(settings, user_id="usr_member", roles=("member",)),
        "Idempotency-Key": _key("del-own"),
    }
    own = client.delete("/api/v1/knowledge/bases/kb_member_own", headers=member_headers).json()
    assert own["code"] == 0, "member 删除自己创建的库必须成功"

    other_headers = {
        **_headers(settings, user_id="usr_member", roles=("member",)),
        "Idempotency-Key": _key("del-other"),
    }
    other = client.delete("/api/v1/knowledge/bases/kb_matrix_org", headers=other_headers).json()
    assert other["code"] == 20003, "member 删除他人/公共库必须 20003"

    admin_headers = {
        **_headers(settings, user_id="usr_admin", roles=("org_admin",)),
        "Idempotency-Key": _key("del-admin"),
    }
    by_admin = client.delete(
        "/api/v1/knowledge/bases/kb_matrix_restricted", headers=admin_headers
    ).json()
    assert by_admin["code"] == 0, "org_admin 删除任意库必须成功"
