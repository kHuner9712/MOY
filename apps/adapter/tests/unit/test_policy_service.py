"""可见性判定与判权矩阵单元测试（架构 §4.8 / PRD P2-KB-03 验收 1-2）。

## 这一组用例在防什么风险

1. **越权读到别人的库**：`restricted` / `private` 两种可见性一旦判错就是数据泄漏，
   这是本文件覆盖面最重的一块。
2. **误拒**：部门授权拿不到用户所属部门 → "被授了部门却看不到"，功能形同虚设。
3. **以权限点自造权限**：令牌里塞一个任意 `x:y` 字符串就能拿到一个权限点（白名单外的
   角色必须 fail-closed）。
4. **降级不生效（D-13）**：业务库把 owner 降成 member 后，令牌里的 owner 仍在撑着旧权限。
   业务库是权威源，撤权必须"下一请求即生效"。
5. **重复检索放大用量**：`["kb_x", "x"]` 是同一个对象的两种写法，过滤后必须去重，
   否则单次对话会重复检索、引用条重复、用量虚高。
6. **缓存与失效**：5 秒缓存让"移出部门 ≤5s 失效"，但授权写入方必须能立即失效。

全部走内存仓储（`InMemory*Store`），不连库。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from app.business import policy_service
from app.business.policy_service import (
    BUILTIN_ROLE_PERMISSIONS,
    BUSINESS_PERMISSIONS,
    KNOWN_PERMISSION_POINTS,
    OPS_PERMISSIONS,
    PolicyService,
    get_policy_service,
    has_admin_role,
    is_guest_role,
    normalize_base_id,
    normalized_roles,
    permissions_for_roles,
)
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.security import AuthContext
from app.db.repositories.grant_repo import GrantRecord, InMemoryGrantStore
from app.db.repositories.knowledge_base_repo import (
    InMemoryKnowledgeBaseStore,
    KnowledgeBaseRecord,
)
from app.db.repositories.org_repo import MemberLink, get_org_store, reset_org_store
from app.db.repositories.role_repo import (
    InMemoryRoleStore,
    builtin_role_id,
    get_role_store,
    reset_role_store,
)

pytestmark = pytest.mark.unit

TENANT = "tnt_policy_unit"
OTHER_TENANT = "tnt_policy_other"
USER = "usr_member"
OTHER_USER = "usr_other"
ADMIN = "usr_admin"

_BASE_PARAMS: dict[str, Any] = {
    "env": "test",
    "deploy_mode": "saas",
    "business_store": "memory",
    "auth_mode": "stub",
    "jwt_secret": "policy-unit-secret",
    "downstream_enabled": False,
    "ratelimit_enabled": False,
    "background_jobs_enabled": False,
}


def _settings(**overrides: Any) -> Settings:
    """构造配置实例。

    Args:
        **overrides: 覆盖项。

    Returns:
        Settings: 配置。
    """
    params = dict(_BASE_PARAMS)
    params.update(overrides)
    return Settings(**params)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _isolate_identity_stores() -> Iterator[None]:
    """每个用例前后重置组织域与权限域的内存单例。

    `tests/conftest.py` 的 `_reset_all()` 重置了组织域，但**没有**重置
    `role_repo` 的存储指针——本文件会给成员分角色，不显式重置就会出现
    "上一个用例分的角色漏到下一个用例"（表现为单独跑绿、顺序变了就红）。
    这是用例隔离问题，不是被测代码的问题。

    Yields:
        None: 用例运行期间。
    """
    reset_org_store()
    reset_role_store()
    yield
    reset_org_store()
    reset_role_store()


def _auth(
    user_id: str = USER, roles: list[str] | None = None, tenant_id: str = TENANT
) -> AuthContext:
    """构造鉴权上下文。

    Args:
        user_id: 用户 ID。
        roles: 令牌角色。
        tenant_id: 租户 ID。

    Returns:
        AuthContext: 上下文。
    """
    return AuthContext(user_id=user_id, tenant_id=tenant_id, roles=roles or ["member"])


def _base(
    base_id: str,
    *,
    visibility: str = "organization",
    owner: str = OTHER_USER,
    deleted: str | None = None,
    tenant_id: str = TENANT,
) -> KnowledgeBaseRecord:
    """构造知识库记录。

    Args:
        base_id: 知识库 ID。
        visibility: 可见范围。
        owner: 所有者。
        deleted: 软删除时间。
        tenant_id: 租户 ID。

    Returns:
        KnowledgeBaseRecord: 记录。
    """
    return KnowledgeBaseRecord(
        tenant_id=tenant_id,
        base_id=base_id,
        name=f"库 {base_id}",
        visibility=visibility,
        owner_user_id=owner,
        deleted_at=deleted,
    )


class LoggerSpy:
    """记录 structlog 调用的替身（断言"确实记了这条业务日志"）。"""

    def __init__(self) -> None:
        """初始化空记录。"""
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    def debug(self, event: str, **fields: Any) -> None:
        """DEBUG 级。"""
        self.events.append(("debug", str(event), dict(fields)))

    def info(self, event: str, **fields: Any) -> None:
        """INFO 级。"""
        self.events.append(("info", str(event), dict(fields)))

    def warning(self, event: str, **fields: Any) -> None:
        """WARNING 级。"""
        self.events.append(("warning", str(event), dict(fields)))

    def error(self, event: str, **fields: Any) -> None:
        """ERROR 级。"""
        self.events.append(("error", str(event), dict(fields)))

    def named(self, event: str) -> list[dict[str, Any]]:
        """取某事件的全部字段。

        Args:
            event: 事件名。

        Returns:
            list[dict[str, Any]]: 字段列表。
        """
        return [fields for _level, name, fields in self.events if name == event]


async def _grant(
    grants: InMemoryGrantStore,
    base_id: str,
    *,
    subject_type: str,
    subject_id: str,
    permission: str = "viewer",
) -> None:
    """写入一条授权（幂等）。

    Args:
        grants: 授权存储。
        base_id: 知识库 ID。
        subject_type: `user` / `department` / `role`。
        subject_id: 授权对象 ID。
        permission: `viewer` / `editor`。
    """
    await grants.create(
        GrantRecord(
            tenant_id=TENANT,
            grant_id=f"grant_{base_id}_{subject_type}_{subject_id}",
            base_id=base_id,
            subject_type=subject_type,
            subject_id=subject_id,
            permission=permission,
        )
    )


# ===========================================================================
# 角色 → 权限点（矩阵展开）
# ===========================================================================
class TestPermissionsForRoles:
    """`permissions_for_roles`：令牌角色展开为权限点。"""

    def test_builtin_roles_map_to_matrix(self) -> None:
        """内置角色按架构 §4.8 矩阵展开（owner/org_admin 是全集，guest 最少）。"""
        assert permissions_for_roles(["owner"]) == BUSINESS_PERMISSIONS
        assert permissions_for_roles(["org_admin"]) == BUSINESS_PERMISSIONS
        assert permissions_for_roles(["member"]) == BUILTIN_ROLE_PERMISSIONS["member"]
        assert permissions_for_roles(["guest"]) == BUILTIN_ROLE_PERMISSIONS["guest"]
        assert permissions_for_roles(["ops_operator"]) == OPS_PERMISSIONS
        # 访客不得拥有任何写权限点
        assert "team:role:manage" not in permissions_for_roles(["guest"])

    def test_wildcard_role_grants_everything(self) -> None:
        """平台通配角色 → 业务 + 运营全集（运营后台专用）。"""
        assert permissions_for_roles(["*"]) == BUSINESS_PERMISSIONS | OPS_PERMISSIONS

    def test_roles_are_normalized(self) -> None:
        """角色键大小写/空白被归一化，空串被忽略。"""
        assert permissions_for_roles([" OWNER ", "", "  "]) == BUSINESS_PERMISSIONS

    def test_permission_point_as_role_is_accepted_only_from_catalog(self) -> None:
        """以权限点当角色签发只认目录内的值；自造串一分不得（fail-closed）。"""
        assert permissions_for_roles(["tool:execute"]) == frozenset({"tool:execute"})
        assert permissions_for_roles(["admin:*"]) == frozenset()
        assert permissions_for_roles(["totally:made:up"]) == frozenset()
        assert "tool:execute" in KNOWN_PERMISSION_POINTS

    def test_unknown_role_contributes_nothing(self) -> None:
        """未知角色不贡献任何权限（拼错角色名不该变成"有权限"）。"""
        assert permissions_for_roles(["MEMBERR"]) == frozenset()


class TestRolePredicates:
    """`has_admin_role` / `is_guest_role` / `normalized_roles`。"""

    def test_admin_roles_include_wildcard(self) -> None:
        """管理员 = owner/org_admin/admin 或通配。"""
        assert has_admin_role(_auth(roles=["owner"])) is True
        assert has_admin_role(_auth(roles=["ADMIN"])) is True
        assert has_admin_role(_auth(roles=["*"])) is True
        assert has_admin_role(_auth(roles=["member"])) is False

    def test_guest_excludes_admins(self) -> None:
        """访客判定必须先排除管理员（owner+guest 混签时不算访客）。"""
        assert is_guest_role(_auth(roles=["guest"])) is True
        assert is_guest_role(_auth(roles=["guest", "owner"])) is False
        assert is_guest_role(_auth(roles=["member"])) is False

    def test_normalized_roles_lowercases_and_strips(self) -> None:
        """角色归一化是小写 + 去空白（令牌签发方风格不一不能造成漏判）。"""
        assert normalized_roles(_auth(roles=[" Owner ", "Member"])) == {"owner", "member"}


class TestNormalizeBaseId:
    """`normalize_base_id`：一个对象只能有一个判定键。"""

    def test_prefixes_native_id(self) -> None:
        """底座原生 ID 补 `kb_` 前缀，已是规范形态的原样返回。"""
        assert normalize_base_id("raw123") == "kb_raw123"
        assert normalize_base_id("kb_raw123") == "kb_raw123"
        assert normalize_base_id("  kb_x  ") == "kb_x"

    def test_empty_is_empty(self) -> None:
        """空值归一化为空串（调用方据此跳过，而不是拼出 `kb_`）。"""
        assert normalize_base_id("") == ""
        assert normalize_base_id(None) == ""  # type: ignore[arg-type]


# ===========================================================================
# 快照：缓存、部门/角色解析、降级
# ===========================================================================
class TestSnapshot:
    """`PolicyService.snapshot` 与它的缓存。"""

    async def test_snapshot_is_cached_within_ttl(self) -> None:
        """TTL 内复用同一快照对象（否则每个请求两次授权查询）。"""
        clock = [1000.0]
        service = PolicyService(
            _settings(permission_cache_ttl_seconds=5.0),
            bases=InMemoryKnowledgeBaseStore(),
            grants=InMemoryGrantStore(),
            clock=lambda: clock[0],
        )
        first = await service.snapshot(_auth())
        clock[0] += 4.9
        second = await service.snapshot(_auth())
        assert second is first

    async def test_snapshot_is_rebuilt_after_ttl(self) -> None:
        """超过 TTL 重建（"成员移出部门 ≤5s 失效"靠这一条）。"""
        clock = [1000.0]
        service = PolicyService(
            _settings(permission_cache_ttl_seconds=5.0),
            bases=InMemoryKnowledgeBaseStore(),
            grants=InMemoryGrantStore(),
            clock=lambda: clock[0],
        )
        first = await service.snapshot(_auth())
        clock[0] += 5.1
        second = await service.snapshot(_auth())
        assert second is not first

    async def test_department_and_role_grants_are_resolved(self) -> None:
        """部门授权与角色授权都必须命中（否则"授了看不到"）。

        `_user_departments` / `_user_role_ids` 读的是业务库的组织域与权限域，
        这里用内存仓储把成员放进部门、分到角色，再断言两个库都进了可见集合。
        """
        settings = _settings()
        bases = InMemoryKnowledgeBaseStore()
        grants = InMemoryGrantStore()
        await bases.create(_base("kb_by_dept", visibility="restricted"))
        await bases.create(_base("kb_by_role", visibility="restricted"))
        await _grant(grants, "kb_by_dept", subject_type="department", subject_id="dept_qa")
        await _grant(
            grants, "kb_by_role", subject_type="role", subject_id=builtin_role_id("member")
        )

        org = get_org_store(settings)
        await org.link_member(MemberLink(tenant_id=TENANT, department_id="dept_qa", user_id=USER))
        roles = get_role_store(settings)
        assert isinstance(roles, InMemoryRoleStore)
        await roles.ensure_builtin_roles(TENANT, "org_1")
        await roles.assign_role(TENANT, "org_1", USER, builtin_role_id("member"))

        service = PolicyService(settings, bases=bases, grants=grants)
        snapshot = await service.snapshot(_auth())

        assert snapshot.granted_base_ids == frozenset({"kb_by_dept", "kb_by_role"})
        assert snapshot.business_role == "member"
        assert snapshot.is_admin is False

    async def test_business_role_overrides_token_role_for_points(self) -> None:
        """业务库是权威源：令牌写着 owner，但业务库是 member → 权限点按 member 算。

        这是 D-13 的核心：并集取值会让降级永远不生效（业务库只能加不能减）。
        """
        settings = _settings()
        roles = get_role_store(settings)
        assert isinstance(roles, InMemoryRoleStore)
        await roles.ensure_builtin_roles(TENANT, "org_1")
        await roles.assign_role(TENANT, "org_1", USER, builtin_role_id("member"))
        service = PolicyService(
            settings, bases=InMemoryKnowledgeBaseStore(), grants=InMemoryGrantStore()
        )

        snapshot = await service.snapshot(_auth(roles=["owner"]))

        assert snapshot.business_role == "member"
        assert snapshot.is_admin is False
        assert "team:role:manage" not in snapshot.permission_points
        assert snapshot.permission_points == BUILTIN_ROLE_PERMISSIONS["member"]

    async def test_wildcard_token_is_not_downgraded(self) -> None:
        """平台运维令牌不受租户内角色降级影响（它是"平台视角专线"）。"""
        settings = _settings()
        roles = get_role_store(settings)
        assert isinstance(roles, InMemoryRoleStore)
        await roles.ensure_builtin_roles(TENANT, "org_1")
        await roles.assign_role(TENANT, "org_1", USER, builtin_role_id("guest"))
        service = PolicyService(
            settings, bases=InMemoryKnowledgeBaseStore(), grants=InMemoryGrantStore()
        )

        snapshot = await service.snapshot(_auth(roles=["*"]))

        assert snapshot.is_admin is True
        assert snapshot.permission_points == BUSINESS_PERMISSIONS | OPS_PERMISSIONS

    async def test_snapshot_without_business_role_falls_back_to_token(self) -> None:
        """业务库未登记该成员 → 回落令牌角色矩阵（外部账号 / 未播种租户）。"""
        service = PolicyService(
            _settings(), bases=InMemoryKnowledgeBaseStore(), grants=InMemoryGrantStore()
        )
        snapshot = await service.snapshot(_auth(roles=["owner"]))
        assert snapshot.business_role == ""
        assert snapshot.is_admin is True
        assert snapshot.permission_points == BUSINESS_PERMISSIONS

    async def test_admin_of_reads_snapshot(self) -> None:
        """`admin_of` 与管理视图同源（都是快照的 `is_admin`）。

        两个断言用**不同用户**：快照缓存键是 `(tenant_id, user_id)` 不含角色，
        同一用户在同一 TTL 内换角色不会重新判定（角色变更由 `invalidate` 负责）。
        """
        service = PolicyService(
            _settings(), bases=InMemoryKnowledgeBaseStore(), grants=InMemoryGrantStore()
        )
        assert await service.admin_of(_auth(user_id=ADMIN, roles=["org_admin"])) is True
        assert await service.admin_of(_auth(user_id=USER, roles=["member"])) is False


# ===========================================================================
# 权限点判定
# ===========================================================================
class TestPermissionPoints:
    """`has_point` / `require_point`（唯一判权出口）。"""

    async def test_empty_permission_always_passes(self) -> None:
        """空权限点表示"无需权限"（不少只读端点用它当默认值）。"""
        service = PolicyService(
            _settings(), bases=InMemoryKnowledgeBaseStore(), grants=InMemoryGrantStore()
        )
        assert await service.has_point(_auth(roles=[]), "") is True

    async def test_has_point_follows_matrix(self) -> None:
        """会员有 tool:execute，没有 team:role:manage。"""
        service = PolicyService(
            _settings(), bases=InMemoryKnowledgeBaseStore(), grants=InMemoryGrantStore()
        )
        auth = _auth(roles=["member"])
        assert await service.has_point(auth, "tool:execute") is True
        assert await service.has_point(auth, "team:role:manage") is False

    async def test_require_point_raises_forbidden(self) -> None:
        """不足即抛 20003，且内部诊断里带上权限点与角色（排障不用猜）。"""
        service = PolicyService(
            _settings(), bases=InMemoryKnowledgeBaseStore(), grants=InMemoryGrantStore()
        )
        with pytest.raises(ApiError) as exc_info:
            await service.require_point(_auth(roles=["guest"]), "team:role:manage")
        assert exc_info.value.code == ErrorCode.FORBIDDEN
        assert "team:role:manage" in (exc_info.value.internal_detail or "")

    async def test_require_point_passes_silently_when_granted(self) -> None:
        """有权限时不抛异常（`require_point` 是"要么通过要么 20003"）。"""
        service = PolicyService(
            _settings(), bases=InMemoryKnowledgeBaseStore(), grants=InMemoryGrantStore()
        )
        await service.require_point(_auth(roles=["member"]), "knowledge:base:read")


# ===========================================================================
# 单个库的可见性
# ===========================================================================
class TestIsVisible:
    """`is_visible` / `can_read`：三种可见范围 × 三类身份。"""

    async def test_organization_is_visible_to_everyone(self) -> None:
        """全企业可见：任何成员都能读。"""
        service = PolicyService(
            _settings(), bases=InMemoryKnowledgeBaseStore(), grants=InMemoryGrantStore()
        )
        assert service.is_visible(_base("kb_org"), _auth(roles=["guest"])) is True

    async def test_deleted_base_is_invisible_even_to_admin(self) -> None:
        """软删除的库对所有人不可见（包括管理员）——否则列表外仍能检索到。"""
        service = PolicyService(
            _settings(), bases=InMemoryKnowledgeBaseStore(), grants=InMemoryGrantStore()
        )
        record = _base("kb_gone", deleted="2026-01-01T00:00:00Z")
        assert service.is_visible(record, _auth(roles=["owner"])) is False

    async def test_private_base_visible_only_to_owner_and_admin(self) -> None:
        """仅自己可见：所有者与管理员可见，普通成员不可见。"""
        service = PolicyService(
            _settings(), bases=InMemoryKnowledgeBaseStore(), grants=InMemoryGrantStore()
        )
        record = _base("kb_priv", visibility="private", owner=OTHER_USER)
        assert service.is_visible(record, _auth(roles=["member"])) is False
        assert service.is_visible(record, _auth(roles=["owner"])) is True
        assert service.is_visible(record, _auth(user_id=OTHER_USER, roles=["member"])) is True

    async def test_restricted_base_needs_grant(self) -> None:
        """指定成员可见：无授权不可见，授权后可见（`can_read` 带快照）。"""
        service = PolicyService(
            _settings(), bases=InMemoryKnowledgeBaseStore(), grants=InMemoryGrantStore()
        )
        record = _base("kb_res", visibility="restricted")
        assert await service.can_read(_auth(roles=["member"]), record) is False

    async def test_restricted_base_visible_after_user_grant(self) -> None:
        """用户直授后立即可读，且 `grant_permission` 给出授权级别。"""
        grants = InMemoryGrantStore()
        await _grant(grants, "kb_res", subject_type="user", subject_id=USER, permission="editor")
        service = PolicyService(_settings(), bases=InMemoryKnowledgeBaseStore(), grants=grants)
        record = _base("kb_res", visibility="restricted")

        assert await service.can_read(_auth(roles=["member"]), record) is True
        assert await service.grant_permission(_auth(roles=["member"]), "kb_res") == "editor"
        assert await service.visible_base_ids(_auth(roles=["member"])) == frozenset({"kb_res"})


# ===========================================================================
# 批量过滤（M1/M2）
# ===========================================================================
class TestFilterAuthorized:
    """`filter_authorized`：静默剔除 + 归一化去重。"""

    async def test_none_auth_passes_through_deduped(self) -> None:
        """无鉴权信息时原样返回（去重但**不归一化**——不替调用方改语义）。"""
        service = PolicyService(
            _settings(), bases=InMemoryKnowledgeBaseStore(), grants=InMemoryGrantStore()
        )
        assert await service.filter_authorized(None, ["a", "a", "b"]) == ["a", "b"]
        assert await service.filter_authorized(None, []) == []

    async def test_two_spellings_of_one_base_are_deduped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`["kb_x", "x"]` 是同一个对象：返回一个 `kb_x`，顺序保持首次出现。

        先归一化再判权（而不是先按原始串去重），否则重复项会漏到检索层 →
        重复检索 → 引用条重复 → 单次对话用量虚高。
        """
        monkeypatch.setattr(policy_service, "logger", LoggerSpy())
        bases = InMemoryKnowledgeBaseStore()
        await bases.create(_base("kb_x"))
        service = PolicyService(_settings(), bases=bases, grants=InMemoryGrantStore())

        allowed = await service.filter_authorized(_auth(), ["kb_x", "x", "kb_x"])

        assert allowed == ["kb_x"]

    async def test_missing_metadata_is_dropped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """本地查不到元数据 → 剔除（fail-closed，不"无法判定就放行"）。"""
        spy = LoggerSpy()
        monkeypatch.setattr(policy_service, "logger", spy)
        service = PolicyService(
            _settings(), bases=InMemoryKnowledgeBaseStore(), grants=InMemoryGrantStore()
        )

        allowed = await service.filter_authorized(_auth(), ["kb_unknown", ""])

        assert allowed == []
        skipped = spy.named("policy_bases_silently_skipped")
        assert skipped and skipped[0]["skipped"] == 2

    async def test_deleted_and_unauthorized_are_dropped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """软删除的、以及 `restricted` 未授权的库都剔除；可见的保留原顺序。"""
        monkeypatch.setattr(policy_service, "logger", LoggerSpy())
        bases = InMemoryKnowledgeBaseStore()
        await bases.create(_base("kb_org"))
        await bases.create(_base("kb_gone", deleted="2026-01-01T00:00:00Z"))
        await bases.create(_base("kb_res", visibility="restricted"))
        service = PolicyService(_settings(), bases=bases, grants=InMemoryGrantStore())

        allowed = await service.filter_authorized(
            _auth(), ["kb_gone", "kb_org", "kb_res", "kb_missing"]
        )

        assert allowed == ["kb_org"]


# ===========================================================================
# 失效
# ===========================================================================
class TestInvalidation:
    """`invalidate` / `clear`：授权写入方必须能立即生效。"""

    async def test_invalidate_single_user(self) -> None:
        """按用户失效：只有该用户下次重建快照。"""
        service = PolicyService(
            _settings(), bases=InMemoryKnowledgeBaseStore(), grants=InMemoryGrantStore()
        )
        mine = await service.snapshot(_auth())
        other = await service.snapshot(_auth(user_id=OTHER_USER))

        service.invalidate(TENANT, USER)

        assert await service.snapshot(_auth()) is not mine
        assert await service.snapshot(_auth(user_id=OTHER_USER)) is other

    async def test_invalidate_whole_tenant(self) -> None:
        """按租户失效：该租户全部用户重建，别的租户不受影响。"""
        service = PolicyService(
            _settings(), bases=InMemoryKnowledgeBaseStore(), grants=InMemoryGrantStore()
        )
        mine = await service.snapshot(_auth())
        foreign = await service.snapshot(_auth(tenant_id=OTHER_TENANT))

        service.invalidate(TENANT)

        assert await service.snapshot(_auth()) is not mine
        assert await service.snapshot(_auth(tenant_id=OTHER_TENANT)) is foreign

    async def test_clear_drops_everything(self) -> None:
        """清空全部缓存（测试隔离用）。"""
        service = PolicyService(
            _settings(), bases=InMemoryKnowledgeBaseStore(), grants=InMemoryGrantStore()
        )
        cached = await service.snapshot(_auth())
        service.clear()
        assert await service.snapshot(_auth()) is not cached


class TestSingleton:
    """`get_policy_service`：必须是单例（否则失效通知打不到列表/检索/问答三处）。"""

    def test_singleton_is_stable(self) -> None:
        """同一进程内两次取到同一个实例。"""
        settings = _settings()
        assert get_policy_service(settings) is get_policy_service(settings)
