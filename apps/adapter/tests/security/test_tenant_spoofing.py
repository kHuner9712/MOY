"""AR-017：`X-Tenant-Id` 租户伪造（跨租户越权）修复用例。

**缺陷**：`resolve_tenant_id` 让 `X-Tenant-Id` 请求头**无条件优先**于令牌的
`tenant_id` 声明，而 `get_request_context` 拿到这个值后直接 `replace()` 回写，
从不校验「这个人是不是目标租户的成员」。结果：持 A 租户令牌 + `X-Tenant-Id: B`
即可对 B 租户做任意读写。

**修复口径**（`get_request_context`）：非私有化模式下，若「生效租户 ≠ 令牌租户」，
只放行两种情形：

1. 平台运维令牌（租户为 `__platform__`，或角色含 `*`）——职责就是跨租户；
2. 该用户在目标租户里确有组织归属（`department_member`）或角色归属（`member_role`）。

**本文件的六条用例**覆盖：拒绝伪造、同租户零开销、平台放行、私有化不误伤、
两种合法成员关系放行，以及「只在自己租户有归属」时仍拒绝（防止条件写反）。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from typing import Any

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import security as security_module
from app.core.config import Settings
from app.core.errors import ErrorCode
from app.db.repositories.org import MemberLink, get_org_store
from app.db.repositories.role import get_role_store
from app.main import create_app

pytestmark = [pytest.mark.security, pytest.mark.integration]

#: 测试密钥（oidc 模式下用于签发合法令牌）
REAL_SECRET = "tenant-spoofing-real-secret"

#: 两个互不相关的租户
TENANT_A = "tnt_spoof_a"
TENANT_B = "tnt_spoof_b"

#: 分别属于两个租户的用户
USER_A = "usr_spoof_a"
USER_B = "usr_spoof_b"

#: 平台租户哨兵（与 `app.core.security.OPS_TENANT_ID` 同值）
OPS_TENANT = "__platform__"

#: 受保护端点：只依赖 `get_request_context`，不依赖权限点，最适合观察租户收敛结果
PROFILE_PATH = "/api/v1/identity/profile"

#: 私有化模式的强制租户
PRIVATE_TENANT = "default"

#: 造数用的部门与角色 ID
DEPT_B = "dept_spoof_b"
ORG_B = "org_spoof_b"
ROLE_B = "role_spoof_b"


def _settings(**overrides: Any) -> Settings:
    """构造测试配置（默认 SaaS 模式 + 真实验签）。

    Args:
        **overrides: 需要覆盖的配置项。

    Returns:
        Settings: 配置实例。
    """
    defaults: dict[str, Any] = {
        "env": "test",
        "deploy_mode": "saas",
        "downstream_enabled": False,
        "auth_mode": "oidc",
        "jwt_secret": REAL_SECRET,
        "jwt_algorithm": "HS256",
        "jwt_audience": "officeai-adapter",
        "jwt_issuer": "officeai",
        "ratelimit_enabled": False,
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def _issue_token(
    *,
    tenant_id: str = TENANT_A,
    user_id: str = USER_A,
    roles: list[str] | None = None,
    expires_in: int = 3600,
    secret: str = REAL_SECRET,
    include_tenant_id: bool = True,
) -> str:
    """签发一张合法令牌。

    Args:
        tenant_id: 令牌声明的租户。
        user_id: 用户 ID。
        roles: 角色列表（None 表示使用默认 `member`）。
        expires_in: 有效期（秒）。
        secret: 签名密钥（PG 用例用独立配置，故可覆盖）。
        include_tenant_id: 是否写入 `tenant_id` 声明（False 用于构造「声明缺失」令牌）。

    Returns:
        str: JWT。
    """
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": user_id,
        "roles": roles if roles is not None else ["member"],
        "email": f"{user_id}@example.com",
        "aud": "officeai-adapter",
        "iss": "officeai",
        "iat": now,
        "exp": now + expires_in,
    }
    if include_tenant_id:
        payload["tenant_id"] = tenant_id
    return jwt.encode(payload, secret, algorithm="HS256")


def _headers(token: str, tenant: str = "") -> dict[str, str]:
    """构造鉴权请求头。

    Args:
        token: 令牌。
        tenant: `X-Tenant-Id` 请求头的值（空表示不带该头）。

    Returns:
        dict[str, str]: 请求头。
    """
    headers = {"Authorization": f"Bearer {token}"}
    if tenant:
        headers["X-Tenant-Id"] = tenant
    return headers


@pytest.fixture
def saas_app() -> FastAPI:
    """SaaS 模式的测试应用（成员关系为空）。

    Returns:
        FastAPI: 应用实例。
    """
    return create_app(_settings())


@pytest.fixture
def saas_client(saas_app: FastAPI) -> Iterator[TestClient]:
    """SaaS 模式的测试客户端。

    Args:
        saas_app: 应用实例。

    Yields:
        Iterator[TestClient]: 客户端。
    """
    with TestClient(saas_app) as client:
        yield client


def _link_member(settings: Settings, tenant_id: str, user_id: str, dept_id: str) -> None:
    """在指定租户内写入一条组织归属（`department_member`）。

    Args:
        settings: 配置实例（决定存储后端）。
        tenant_id: 租户 ID。
        user_id: 用户 ID。
        dept_id: 部门 ID。
    """
    link = MemberLink(tenant_id=tenant_id, department_id=dept_id, user_id=user_id, is_primary=True)
    asyncio.run(get_org_store(settings).link_member(link))


def _assign_role(settings: Settings, tenant_id: str, user_id: str, role_id: str) -> None:
    """在指定租户内写入一条角色归属（`member_role`）。

    Args:
        settings: 配置实例（决定存储后端）。
        tenant_id: 租户 ID。
        user_id: 用户 ID。
        role_id: 角色 ID。
    """
    asyncio.run(
        get_role_store(settings).assign_role(tenant_id, ORG_B, user_id, role_id, assigned_by=USER_B)
    )


# ===========================================================================
# 用例 1：伪造请求头必须被拒绝（AR-017 的核心判据）
# ===========================================================================


def test_cross_tenant_header_without_membership_is_denied(saas_client: TestClient) -> None:
    """持 A 租户令牌 + `X-Tenant-Id: B`（非 B 成员）→ 403 / 20003。"""
    response = saas_client.get(
        PROFILE_PATH,
        headers=_headers(_issue_token(tenant_id=TENANT_A, user_id=USER_A), TENANT_B),
    )
    assert response.status_code == 403, "伪造租户头必须被拒绝，实际放行了"
    body = response.json()
    assert body["code"] == int(ErrorCode.FORBIDDEN)
    assert body["data"] is None
    # 拒绝文案必须是业务语言，不得回显内部细节（表名/权限点/用户 ID）
    assert body["message"] == "当前账号没有该操作的权限"
    assert TENANT_B not in body["message"]
    assert USER_A not in body["message"]


def test_membership_in_own_tenant_does_not_authorize_other_tenant() -> None:
    """用户只在**自己**租户有组织归属时，切到别人的租户仍然被拒绝。

    这条用例专门防止「成员校验条件写反」——查到了归属就放行，却不看归属在哪个租户。
    """
    settings = _settings()
    _link_member(settings, TENANT_A, USER_A, "dept_spoof_a")
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get(
            PROFILE_PATH,
            headers=_headers(_issue_token(tenant_id=TENANT_A, user_id=USER_A), TENANT_B),
        )
    assert response.status_code == 403
    assert response.json()["code"] == int(ErrorCode.FORBIDDEN)


# ===========================================================================
# 用例 2：同租户请求不受影响，且**不触发**成员查询（性能红线）
# ===========================================================================


def test_same_tenant_request_passes_without_membership_lookup(
    saas_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """持 A 租户令牌 + `X-Tenant-Id: A` → 200，且不查询成员关系。"""
    calls: list[tuple[str, str]] = []

    async def _spy(auth: Any, tenant_id: str, settings: Settings) -> None:
        calls.append((auth.user_id, tenant_id))
        raise AssertionError("同租户请求不得触发成员查询")

    monkeypatch.setattr(security_module, "assert_tenant_membership", _spy)
    response = saas_client.get(
        PROFILE_PATH,
        headers=_headers(_issue_token(tenant_id=TENANT_A, user_id=USER_A), TENANT_A),
    )
    assert response.status_code == 200
    assert response.json()["code"] == 0
    assert response.json()["data"]["tenant"]["tenant_id"] == TENANT_A
    assert calls == [], "同租户请求不得产生额外的成员关系查询"


def test_request_without_tenant_header_uses_token_tenant(saas_client: TestClient) -> None:
    """不带 `X-Tenant-Id` 时按令牌声明生效，且同样不触发成员查询。"""
    response = saas_client.get(
        PROFILE_PATH, headers=_headers(_issue_token(tenant_id=TENANT_A, user_id=USER_A))
    )
    assert response.status_code == 200
    assert response.json()["data"]["tenant"]["tenant_id"] == TENANT_A


# ===========================================================================
# 用例 3：平台运维令牌可跨租户
# ===========================================================================


@pytest.mark.parametrize(
    ("token_kwargs", "label"),
    [
        # SEC-02 收紧后，「平台哨兵租户」单独不再等于跨租户特权：必须**同时**
        # 带平台运维角色（`ops_operator` / `*`）。这里原本用的是
        # `roles=["ops:console:access"]`（把权限点当角色），收紧后它不再被认作
        # 运营角色——这是判权口径的**预期变化**，不是回归：判据改成
        # 「`__platform__` + 运营角色」后，用最典型的运营角色表达同一意图。
        ({"tenant_id": OPS_TENANT, "roles": ["ops_operator"]}, "平台哨兵租户"),
        ({"tenant_id": TENANT_A, "roles": ["*"]}, "通配角色"),
    ],
)
def test_platform_token_can_switch_tenant(
    saas_client: TestClient, token_kwargs: dict[str, Any], label: str
) -> None:
    """平台运维令牌（哨兵租户或通配角色）+ `X-Tenant-Id: B` → 放行。

    Args:
        saas_client: 测试客户端。
        token_kwargs: 令牌构造参数。
        label: 用例标签（失败时便于定位是哪一种特权形态）。
    """
    token = _issue_token(user_id=USER_B, **token_kwargs)
    response = saas_client.get(PROFILE_PATH, headers=_headers(token, TENANT_B))
    assert response.status_code == 200, f"平台令牌必须在「{label}」形态下跨租户放行"
    assert response.json()["data"]["tenant"]["tenant_id"] == TENANT_B


# ===========================================================================
# 用例 4：私有化模式不受影响（单租户恒为 default）
# ===========================================================================


def test_private_mode_ignores_tenant_header_without_membership() -> None:
    """私有化模式 + 任意 `X-Tenant-Id` → 生效租户为 `default`，不报 20003。"""
    app = create_app(_settings(deploy_mode="private"))
    with TestClient(app) as client:
        response = client.get(
            PROFILE_PATH,
            headers=_headers(_issue_token(tenant_id=TENANT_A, user_id=USER_A), TENANT_B),
        )
    assert response.status_code == 200, "私有化模式的租户收敛不是跨租户访问，不得拒绝"
    assert response.json()["data"]["tenant"]["tenant_id"] == PRIVATE_TENANT
    assert response.json()["data"]["tenant"]["tenant_id"] != TENANT_B


def test_private_mode_does_not_trigger_membership_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """私有化模式下连成员查询都不该发生（没有跨租户这回事）。"""

    async def _spy(auth: Any, tenant_id: str, settings: Settings) -> None:
        raise AssertionError("私有化模式不得触发成员查询")

    monkeypatch.setattr(security_module, "assert_tenant_membership", _spy)
    app = create_app(_settings(deploy_mode="private"))
    with TestClient(app) as client:
        response = client.get(
            PROFILE_PATH,
            headers=_headers(_issue_token(tenant_id=TENANT_A, user_id=USER_A), TENANT_B),
        )
    assert response.status_code == 200


# ===========================================================================
# 用例 5：在目标租户确有归属时可切换（多租户成员的真实场景）
# ===========================================================================


def test_member_of_target_tenant_can_switch_via_org_link() -> None:
    """用户已在 B 租户有部门归属（`department_member`）+ 令牌 A + 头 B → 放行。"""
    settings = _settings()
    _link_member(settings, TENANT_B, USER_A, DEPT_B)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get(
            PROFILE_PATH,
            headers=_headers(_issue_token(tenant_id=TENANT_A, user_id=USER_A), TENANT_B),
        )
    assert response.status_code == 200, "目标租户确有组织归属时必须放行"
    assert response.json()["data"]["tenant"]["tenant_id"] == TENANT_B


def test_member_of_target_tenant_can_switch_via_role_assignment() -> None:
    """用户已在 B 租户有角色归属（`member_role`）+ 令牌 A + 头 B → 放行。"""
    settings = _settings()
    _assign_role(settings, TENANT_B, USER_A, ROLE_B)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get(
            PROFILE_PATH,
            headers=_headers(_issue_token(tenant_id=TENANT_A, user_id=USER_A), TENANT_B),
        )
    assert response.status_code == 200, "目标租户确有角色归属时必须放行"
    assert response.json()["data"]["tenant"]["tenant_id"] == TENANT_B


# ===========================================================================
# 用例 6：`__platform__` 请求头必须在查库前拒掉（否则 PG 下会变 500）
# ===========================================================================


def test_enterprise_token_cannot_forge_platform_header(
    saas_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """企业令牌带 `X-Tenant-Id: __platform__` → 20003，且**不查任何仓储**。

    平台哨兵不是真实租户，流入 `tenant_scope("__platform__")` 会被 RLS 拒绝并
    抛成 HTTP 500——那会让"伪造请求头"看起来像"服务故障"。因此必须在任何
    仓储查询之前就返回 20003。
    """

    async def _forbidden_lookup(auth: Any, tenant_id: str, settings: Settings) -> None:
        raise AssertionError("平台哨兵请求头必须在查库之前被拒绝")

    monkeypatch.setattr(security_module, "assert_tenant_membership", _forbidden_lookup)
    response = saas_client.get(
        PROFILE_PATH, headers=_headers(_issue_token(tenant_id=TENANT_A, user_id=USER_A), OPS_TENANT)
    )
    assert response.status_code == 403
    assert response.json()["code"] == int(ErrorCode.FORBIDDEN)
    assert response.json()["data"] is None


# ===========================================================================
# 用例 7：回退开关（`OA_TENANT_MEMBERSHIP_CHECK`）
# ===========================================================================


def test_membership_check_off_skips_validation() -> None:
    """开关置 `off`（紧急回退）时跳过校验：与令牌租户不一致的请求头被放过。

    这是**故意的安全降级**：开关存在的意义是"校验逻辑本身导致线上不可用"时止血，
    所以这里断言"确实放过了"，好让开关失效这件事在测试里也是可见的
    （而不是某天被人删掉后没人发现）。
    """
    settings = _settings(tenant_membership_check="off")
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get(
            PROFILE_PATH,
            headers=_headers(_issue_token(tenant_id=TENANT_A, user_id=USER_A), TENANT_B),
        )
    assert response.status_code == 200
    assert response.json()["data"]["tenant"]["tenant_id"] == TENANT_B


def test_membership_check_auto_skips_stub_mode() -> None:
    """`auto` 档在 stub 模式（本地联调）下不校验，避免手编令牌联调被挡。"""
    settings = _settings(auth_mode="stub")
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get(
            PROFILE_PATH,
            headers=_headers(_issue_token(tenant_id=TENANT_A, user_id=USER_A), TENANT_B),
        )
    assert response.status_code == 200


def test_membership_check_always_enforced_even_in_private_mode() -> None:
    """`always` 档连私有化模式也校验（灰度期收紧口径用）。"""
    settings = _settings(deploy_mode="private", tenant_membership_check="always")
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get(
            PROFILE_PATH,
            headers=_headers(_issue_token(tenant_id=TENANT_A, user_id=USER_A), TENANT_B),
        )
    # 私有化模式下生效租户被收敛为 default，用户在 default 无任何归属 → 拒绝
    assert response.status_code == 403
    assert response.json()["code"] == int(ErrorCode.FORBIDDEN)


# ===========================================================================
# 用例 8：PostgreSQL 后端下的同一拒绝（`--run-pg` 启用）
#
# **为什么必须有这条**（QA 复测结论）：上面所有用例都跑在内存后端。把早期的
# `__platform__` 拒绝删掉之后，内存后端会被 membership 查库兜底成 403 ——
# 也就是说它们在「防线被削掉」时**仍然全绿**。只有 PG 后端会暴露差异：
# `tenant_scope("__platform__")` 触发 `assert_tenant_allowed` 抛编码缺陷码 30003，
# 请求变成 500。这条用例锁的正是"拒绝发生在查库之前"这个位置。
#
# 不需要建表：拒绝发生在任何仓储访问之前（若断言挂了，才说明它被挪到了后面）。
# ===========================================================================


@pytest.mark.pg
def test_forged_platform_header_rejected_on_pg(
    pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PG 后端：企业令牌伪造 `__platform__` 头必须 403/20003，**且未触达成员校验**。

    Args:
        pg_dsn: 真实测试库连接串（未加 `--run-pg` 时用例被跳过）。
        monkeypatch: pytest 补丁工具。
    """
    import base64
    import contextlib

    from app.db.engine import reset_engine_for_test

    test_key = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()
    settings = Settings(
        env="test",
        deploy_mode="saas",
        downstream_enabled=False,
        # 生产口径：`auto` 档在 oidc 下才开启成员校验（stub 会整段跳过，
        # 那样即使删掉早期拒绝，用例也会被端点级守卫"顺带"救绿——测不出问题）
        auth_mode="oidc",
        jwt_secret="pg-tenant-spoofing",
        ratelimit_enabled=False,
        business_store="postgres",
        database_url=pg_dsn,
        credential_encryption_key=test_key,
        pay_channel="fake",
    )

    # 一旦早期拒绝被挪到仓储查询之后，这里就会被调用 —— 用哨兵把它变成硬红，
    # 而不是依赖"RLS 恰好报 30003"这种间接症状。
    async def _must_not_be_reached(auth: Any, tenant_id: str, cfg: Settings) -> None:
        raise AssertionError("平台哨兵请求头必须在任何仓储查询之前被拒绝")

    monkeypatch.setattr(security_module, "assert_tenant_membership", _must_not_be_reached)

    token = _issue_token(
        tenant_id=TENANT_A, user_id=USER_A, roles=["owner"], secret="pg-tenant-spoofing"
    )
    try:
        with TestClient(create_app(settings)) as client:
            response = client.get(
                "/api/v1/ops/tenants",
                headers=_headers(token, OPS_TENANT),
            )
    finally:
        # 引擎单例不能跨用例复用（每个用例的事件循环不同）
        with contextlib.suppress(Exception):
            asyncio.run(reset_engine_for_test())

    assert response.status_code == 403, (
        f"PG 后端未在查库前拒绝平台哨兵头：{response.status_code} / "
        f"{response.json().get('code')}（30003 说明请求流进了 tenant_scope）"
    )
    assert response.json()["code"] == int(ErrorCode.FORBIDDEN)


# ===========================================================================
# 用例 7（W-05）：租户来源**全部缺失**时必须拒绝，而不是静默收敛到 default
#
# 这一格此前没有机械防线：`resolve_tenant_id` 的最后一行是 `return _default_tenant(conf)`，
# 只要把 `:222` 的 raise 改成 pass，SaaS 模式下「无头 + 无声明」的请求就会安静地
# 落进 `default` 租户——单租户私有化里 `default` 是唯一租户，SaaS 里它仍然存在，
# 于是未声明租户的调用方可以读到 `default` 里的数据（越权读的隐形态）。
# ===========================================================================


def test_saas_missing_tenant_header_and_claim_is_rejected(saas_client: TestClient) -> None:
    """SaaS + 无 `X-Tenant-Id` + 令牌无 `tenant_id` 声明 → 400 / 20001。

    **负样本（回归证明）**：把 `app/core/security.py` 中 `resolve_tenant_id` 的
    `raise ApiError(ErrorCode.PARAM_INVALID, ...)` 改成 `pass`（即让函数继续走到
    末尾的 `return _default_tenant(conf)`），本用例立刻变红：
    状态码 200、`code` 变 0、租户变成 `default`。

    **文案红线**：拒绝文案必须是既有业务语言「请求内容不完整，请检查后重试」，
    不得出现 `header` / `claim` / `tenant_id` 这类内部词——调用方缺的是一个业务
    概念（企业归属），不是「你没给我某个技术字段」。
    """
    token = _issue_token(user_id=USER_A, include_tenant_id=False)

    response = saas_client.get(PROFILE_PATH, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 400, "两个租户来源都缺失时必须拒绝，不得静默收敛"
    body = response.json()
    assert body["code"] == int(ErrorCode.PARAM_INVALID)
    assert body["message"] == "请求内容不完整，请检查后重试"
    assert body["data"] is None
    serialized = f"{body}".lower()
    for leak in ("header", "claim", "tenant_id", "x-tenant-id"):
        assert leak not in serialized, f"拒绝文案泄露内部词「{leak}」：{serialized}"


def test_private_converges_to_default_and_skips_membership_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """私有化 + 头 B + 令牌声明 A → 200，生效租户 `default`，且不查成员仓储。

    私有化是单租户交付：头与声明**都被忽略**，一切收敛到 `default`。
    这里刻意用「头 B + 声明 A」这种互不一致的组合，确保收敛不是因为某一侧生效；
    同时把成员查询替换成必抛断言，证明这条路径上根本没有跨租户校验（没有可校验的对象）。
    """

    async def _spy(auth: Any, tenant_id: str, settings: Settings) -> None:
        raise AssertionError("私有化模式不得触发成员查询（单租户没有跨租户这回事）")

    monkeypatch.setattr(security_module, "assert_tenant_membership", _spy)
    app = create_app(_settings(deploy_mode="private"))
    with TestClient(app) as client:
        response = client.get(
            PROFILE_PATH,
            headers=_headers(_issue_token(tenant_id=TENANT_A, user_id=USER_A), TENANT_B),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["tenant"]["tenant_id"] == PRIVATE_TENANT
    assert body["data"]["tenant"]["tenant_id"] not in {TENANT_A, TENANT_B}
