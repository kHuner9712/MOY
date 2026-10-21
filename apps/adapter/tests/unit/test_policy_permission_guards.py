"""B05 判权 fail-closed / ID 归一化 / 5s 快照缓存的单元回归（QA B05-P0-01）。

为什么单独一份文件：

- QA 的验收用例（`tests/integration/test_qa_b05_b06_verification.py`）走 HTTP 入口，
  只能证明「换 ID 写法拿不到数据」；**本文件直接驱动 `PolicyService`**，
  把「为什么拿不到」钉在三个可独立回归的分支上：
  1. **归一化**：`kb_<原生>` 与 `<原生>` 必须落到同一个判定键；
  2. **fail-closed**：本地无元数据 / 跨租户 / 已软删除 → 一律剔除（不报错但绝不透传）；
  3. **缓存语义**：5s 快照窗口内不重查、窗口过后重查、写入方 `invalidate()` 立即生效。

判据来源：架构文档 §4.8（三层校验 + 对象级，禁止「列表过滤了但详情/检索没校验」）、
QA 报告 §5.2（根因：按「元数据是否存在」判权而非按「对象归属」）。
"""

from __future__ import annotations

import pytest

from app.business.policy_service import (
    PolicyService,
    PolicySnapshot,
    has_admin_role,
    is_guest_role,
    normalize_base_id,
)
from app.core.config import Settings
from app.core.security import AuthContext
from app.db.repositories.grant_repo import GrantRecord, get_grant_store
from app.db.repositories.knowledge_base_repo import (
    KnowledgeBaseRecord,
    get_knowledge_base_store,
)

pytestmark = pytest.mark.unit

#: 主租户（本文件自带前缀，避免与其它用例撞键）
TENANT = "tnt_guard"

#: 另一个租户（跨租户用例）
OTHER_TENANT = "tnt_guard_other"

#: 知识库所有者
OWNER = "usr_guard_owner"

#: 普通成员（未授权）
MEMBER = "usr_guard_member"

#: 组织管理员
ADMIN = "usr_guard_admin"


def _record(
    base_id: str,
    *,
    visibility: str = "restricted",
    owner: str = OWNER,
    tenant: str = TENANT,
) -> KnowledgeBaseRecord:
    """构造知识库元数据记录。

    Args:
        base_id: 知识库 ID（规范形态）。
        visibility: 可见范围。
        owner: 所有者用户 ID。
        tenant: 租户 ID。

    Returns:
        KnowledgeBaseRecord: 元数据记录。
    """
    return KnowledgeBaseRecord(
        tenant_id=tenant,
        base_id=base_id,
        name="薪酬保密制度",
        visibility=visibility,
        owner_user_id=owner,
        created_by=owner,
    )


def _auth(
    user_id: str = MEMBER,
    *,
    tenant: str = TENANT,
    roles: tuple[str, ...] = ("knowledge:base:read",),
) -> AuthContext:
    """构造鉴权上下文。

    Args:
        user_id: 用户 ID。
        tenant: 租户 ID。
        roles: 角色 / 权限点列表。

    Returns:
        AuthContext: 鉴权上下文。
    """
    return AuthContext(tenant_id=tenant, user_id=user_id, roles=list(roles))


async def _seed_base(settings: Settings, record: KnowledgeBaseRecord) -> None:
    """写入知识库元数据。

    Args:
        settings: 配置实例（决定内存 / PG 后端）。
        record: 元数据记录。
    """
    await get_knowledge_base_store(settings).create(record)


async def _grant_member(
    settings: Settings,
    base_id: str,
    user_id: str = MEMBER,
    *,
    permission: str = "viewer",
    grant_id: str = "grnt_guard_1",
) -> str:
    """给成员直授知识库权限。

    Args:
        settings: 配置实例。
        base_id: 知识库 ID。
        user_id: 被授权用户 ID。
        permission: 授权级别（`viewer` / `editor`）。
        grant_id: 授权记录 ID。

    Returns:
        str: 授权记录 ID。
    """
    await get_grant_store(settings).create(
        GrantRecord(
            tenant_id=TENANT,
            grant_id=grant_id,
            base_id=base_id,
            subject_type="user",
            subject_id=user_id,
            permission=permission,
            granted_by=OWNER,
        )
    )
    return grant_id


class _StubBaseStore:
    """只实现 `get` 的最小桩存储。

    真实存储（内存 / PG）的 `get()` 默认都把已软删除行过滤掉，因此
    「元数据已软删除」的兜底分支只能靠桩存储触发（防止有人把
    `record.is_deleted` 判断删掉后测试仍然全绿）。
    """

    def __init__(self, record: KnowledgeBaseRecord) -> None:
        """初始化桩存储。

        Args:
            record: 无论查什么都返回的记录。
        """
        self._record = record

    async def get(
        self, tenant_id: str, base_id: str, *, include_deleted: bool = False
    ) -> KnowledgeBaseRecord | None:
        """返回预置记录（忽略入参）。

        Args:
            tenant_id: 租户 ID。
            base_id: 知识库 ID。
            include_deleted: 是否包含已删除（桩实现不区分）。

        Returns:
            KnowledgeBaseRecord: 预置记录。
        """
        del tenant_id, base_id, include_deleted
        return self._record


# ===========================================================================
# 1. ID 归一化：一个对象只能有一个判定键
# ===========================================================================
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("kb_01M5FT0G0NS7MX62CTQTWE0H12", "kb_01M5FT0G0NS7MX62CTQTWE0H12"),
        ("01M5FT0G0NS7MX62CTQTWE0H12", "kb_01M5FT0G0NS7MX62CTQTWE0H12"),
        ("  kb_01M5FT0G0NS7MX62CTQTWE0H12  ", "kb_01M5FT0G0NS7MX62CTQTWE0H12"),
        ("  01M5FT0G0NS7MX62CTQTWE0H12 ", "kb_01M5FT0G0NS7MX62CTQTWE0H12"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_normalize_base_id_collapses_both_spellings(raw: str, expected: str) -> None:
    """底座原生 ID 与 `kb_<原生>` 归一化为同一形态；空值返回空串。

    Args:
        raw: 原始写法。
        expected: 期望的规范形态。
    """
    assert normalize_base_id(raw) == expected


# ===========================================================================
# 2. fail-closed：判不出来就剔除（绝不透传）
# ===========================================================================
async def test_filter_authorized_drops_base_without_metadata(settings: Settings) -> None:
    """本地无元数据 → 两种写法都必须剔除（QA 证据 D 的回归点）。

    Args:
        settings: 配置实例。
    """
    policy = PolicyService(settings)
    native = "01M5FT0G0NS7MX62CTQTWE0H12"

    allowed = await policy.filter_authorized(_auth(), [f"kb_{native}", native])

    assert allowed == [], f"无元数据的库被放行（fail-open 复发）：{allowed}"


async def test_filter_authorized_drops_cross_tenant_base(settings: Settings) -> None:
    """跨租户 → `store.get()` 天然查不到 → 剔除（QA 证据 C 的回归点）。

    Args:
        settings: 配置实例。
    """
    await _seed_base(settings, _record("kb_crossA", tenant=OTHER_TENANT))
    policy = PolicyService(settings)

    allowed = await policy.filter_authorized(_auth(), ["kb_crossA", "crossA"])

    assert allowed == [], f"跨租户知识库被放行：{allowed}"


async def test_filter_authorized_drops_soft_deleted_metadata(settings: Settings) -> None:
    """元数据已软删除 → 剔除（即使存储层没过滤 deleted 行）。

    Args:
        settings: 配置实例。
    """
    deleted = KnowledgeBaseRecord(
        tenant_id=TENANT,
        base_id="kb_soft_deleted",
        name="已删除的库",
        visibility="organization",
        deleted_at="2026-10-01T00:00:00Z",
    )
    policy = PolicyService(settings, bases=_StubBaseStore(deleted))

    allowed = await policy.filter_authorized(_auth(), ["kb_soft_deleted"])

    assert allowed == [], "已软删除的知识库被放行"


async def test_filter_authorized_ignores_blank_entries(settings: Settings) -> None:
    """空串 / 纯空白条目直接剔除，不抛异常、不误判为「无元数据即放行」。

    Args:
        settings: 配置实例。
    """
    policy = PolicyService(settings)

    assert await policy.filter_authorized(_auth(), ["", "   ", "\t"]) == []


async def test_filter_authorized_passes_through_without_auth(settings: Settings) -> None:
    """无鉴权上下文（服务层内部调用）与空列表 → 原样返回（仅去重）。

    Args:
        settings: 配置实例。
    """
    policy = PolicyService(settings)

    assert await policy.filter_authorized(None, ["kb_a", "kb_a"]) == ["kb_a"]
    assert await policy.filter_authorized(_auth(), []) == []


async def test_filter_authorized_keeps_authorized_base_and_normalizes_id(
    settings: Settings,
) -> None:
    """已授权成员用**原生写法**也必须拿到库，且返回规范形态（正向用例）。

    Args:
        settings: 配置实例。
    """
    await _seed_base(settings, _record("kb_granted"))
    await _grant_member(settings, "kb_granted")
    policy = PolicyService(settings)

    allowed = await policy.filter_authorized(_auth(), ["granted"])

    assert allowed == ["kb_granted"]


# ===========================================================================
# 2.1 QA-P3-07：同一对象的两种写法必须先归一化再去重
#
# 现象：`filter_authorized(["kb_x", "x"])` 曾返回 `["kb_x", "kb_x"]`——先按原始串
# 去重、再逐个归一化，两个写法各自存活。后果不是越权，而是 M1/M2 的
# `knowledge_base_ids` 出现重复项 → 可能重复检索、引用条重复 →
# **单次对话用量虚高**（与 D-12 的单次口径冲突）。四条断言覆盖：
# ① 顺序与规范形态；② 任意组合不重复；③ 去重不得绕过 fail-closed；
# ④ 空输入的透传契约（M1/M2 无知识库对话依赖它）。
# ===========================================================================
async def test_filter_authorized_normalizes_before_dedup(settings: Settings) -> None:
    """断言 ①：`["kb_X", "X"]` 只剩一个规范形态，且保持首次出现顺序。

    Args:
        settings: 配置实例。
    """
    await _seed_base(settings, _record("kb_dup_x", visibility="organization"))
    policy = PolicyService(settings)

    allowed = await policy.filter_authorized(_auth(), ["kb_dup_x", "dup_x"])

    assert allowed == ["kb_dup_x"], f"同一对象的两种写法未收敛：{allowed}"


async def test_filter_authorized_never_returns_duplicates(settings: Settings) -> None:
    """断言 ②：任意混合写法下返回值无重复（前置 `out` 防恒真）。

    Args:
        settings: 配置实例。
    """
    await _seed_base(settings, _record("kb_dup_y", visibility="organization"))
    policy = PolicyService(settings)

    out = await policy.filter_authorized(_auth(), ["kb_dup_y", "dup_y", "kb_dup_y", "kb_dup_y"])

    assert out, "已可见的库被全部剔除，用例失效"
    assert len(out) == len(set(out)), f"返回值出现重复项（P3-07 复发）：{out}"
    assert out == ["kb_dup_y"]


async def test_dedup_does_not_bypass_fail_closed(settings: Settings) -> None:
    """断言 ③：未授权库的两种写法去重后仍然整体剔除（不得因去重而放行）。

    Args:
        settings: 配置实例。
    """
    await _seed_base(settings, _record("kb_dup_z", visibility="organization", tenant=OTHER_TENANT))
    policy = PolicyService(settings)

    allowed = await policy.filter_authorized(_auth(), ["kb_dup_z", "dup_z"])

    assert allowed == [], f"跨租户库通过重复写法被放行：{allowed}"


async def test_empty_input_still_passes_through(settings: Settings) -> None:
    """断言 ④：空输入原样返回（M1/M2 无知识库对话依赖该早退分支）。

    Args:
        settings: 配置实例。
    """
    policy = PolicyService(settings)

    assert await policy.filter_authorized(_auth(), []) == []
    assert await policy.filter_authorized(None, ["kb_dup_x", "dup_x"]) == [
        "kb_dup_x",
        "dup_x",
    ], "无鉴权上下文必须原样透传（不做归一化，保持既有契约）"


async def test_filter_authorized_keeps_organization_base(settings: Settings) -> None:
    """全企业可见的库对普通成员始终保留。

    Args:
        settings: 配置实例。
    """
    await _seed_base(settings, _record("kb_org", visibility="organization"))
    policy = PolicyService(settings)

    assert await policy.filter_authorized(_auth(), ["kb_org"]) == ["kb_org"]


async def test_filter_authorized_drops_restricted_base_without_grant(settings: Settings) -> None:
    """`restricted` 库对未授权成员剔除，对所有者保留（对话静默跳过的核心判据）。

    Args:
        settings: 配置实例。
    """
    await _seed_base(settings, _record("kb_restricted"))
    policy = PolicyService(settings)

    assert await policy.filter_authorized(_auth(), ["kb_restricted"]) == []
    assert await policy.filter_authorized(_auth(OWNER), ["kb_restricted"]) == ["kb_restricted"]


# ===========================================================================
# 3. 静态可见性规则（is_visible 全分支）
# ===========================================================================
def test_is_visible_static_rules_cover_all_roles(settings: Settings) -> None:
    """已删除 / 全企业 / 管理员 / 通配 / 所有者 / 快照授权 / 快照缺省 七条分支。

    Args:
        settings: 配置实例。
    """
    policy = PolicyService(settings)
    member = _auth()

    deleted = KnowledgeBaseRecord(
        tenant_id=TENANT,
        base_id="kb_deleted",
        name="已删除",
        deleted_at="2026-10-01T00:00:00Z",
        owner_user_id=OWNER,
    )
    assert policy.is_visible(deleted, member) is False
    assert policy.is_visible(_record("kb_o", visibility="organization"), member) is True
    assert policy.is_visible(_record("kb_a"), _auth(ADMIN, roles=("admin",))) is True
    assert policy.is_visible(_record("kb_wild"), _auth(ADMIN, roles=("*",))) is True
    assert policy.is_visible(_record("kb_own", owner=MEMBER), member) is True
    # restricted + 无快照：静态规则判不出来，必须 False（不是 True）
    assert policy.is_visible(_record("kb_r"), member) is False

    snapshot = PolicySnapshot(
        tenant_id=TENANT,
        user_id=MEMBER,
        granted_base_ids=frozenset({"kb_r"}),
        permissions={"kb_r": "viewer"},
        expires_at=1e12,
    )
    assert policy.is_visible(_record("kb_r"), member, snapshot) is True


def test_role_helpers_are_single_source_of_truth() -> None:
    """管理员 / 访客判定：管理员不算访客，通配角色视为管理员。"""
    assert has_admin_role(_auth(ADMIN, roles=("owner",))) is True
    assert has_admin_role(_auth(ADMIN, roles=("*",))) is True
    assert has_admin_role(_auth(MEMBER)) is False
    assert is_guest_role(_auth(MEMBER, roles=("guest",))) is True
    assert is_guest_role(_auth(ADMIN, roles=("guest", "admin"))) is False


# ===========================================================================
# 4. 快照缓存：5s 窗口 + 立即失效
# ===========================================================================
async def test_snapshot_cache_honours_ttl_then_reloads(settings: Settings) -> None:
    """TTL 内不重查；超过 TTL 后重新加载（授权变更 ≤5s 生效的机制证明）。

    Args:
        settings: 配置实例。
    """
    clock = {"now": 500.0}
    bases = get_knowledge_base_store(settings)
    await _seed_base(settings, _record("kb_cache"))
    record = await bases.get(TENANT, "kb_cache")
    assert record is not None

    auth = _auth()
    policy = PolicyService(settings, clock=lambda: clock["now"])
    assert await policy.can_read(auth, record) is False

    await _grant_member(settings, "kb_cache", grant_id="grnt_guard_ttl")
    # 缓存窗口内：仍是旧快照（这正是「≤5s」的上界来源）
    assert await policy.can_read(auth, record) is False

    clock["now"] += settings.permission_cache_ttl_seconds
    assert await policy.can_read(auth, record) is True


async def test_snapshot_invalidate_makes_grant_effective_immediately(settings: Settings) -> None:
    """写入方调用 `invalidate` → 下一请求即生效（不必等 5s）。

    Args:
        settings: 配置实例。
    """
    clock = {"now": 900.0}
    bases = get_knowledge_base_store(settings)
    grants = get_grant_store(settings)
    await _seed_base(settings, _record("kb_invalidate"))
    record = await bases.get(TENANT, "kb_invalidate")
    assert record is not None

    auth = _auth()
    policy = PolicyService(settings, clock=lambda: clock["now"])
    grant_id = await _grant_member(settings, "kb_invalidate", grant_id="grnt_guard_inv")
    policy.invalidate(TENANT, MEMBER)
    assert await policy.can_read(auth, record) is True

    await grants.revoke(TENANT, grant_id)
    assert await policy.can_read(auth, record) is True  # 仍是缓存窗口内的快照
    policy.invalidate(TENANT)  # 按租户整体失效
    assert await policy.can_read(auth, record) is False


async def test_snapshot_clear_forces_reload(settings: Settings) -> None:
    """`clear()` 清空全部缓存后必须重新加载（测试隔离与运维应急路径）。

    Args:
        settings: 配置实例。
    """
    bases = get_knowledge_base_store(settings)
    grants = get_grant_store(settings)
    await _seed_base(settings, _record("kb_clear"))
    record = await bases.get(TENANT, "kb_clear")
    assert record is not None

    auth = _auth()
    policy = PolicyService(settings)
    grant_id = await _grant_member(settings, "kb_clear", grant_id="grnt_guard_clear")
    assert await policy.can_read(auth, record) is True
    await grants.revoke(TENANT, grant_id)
    assert await policy.can_read(auth, record) is True  # 快照仍在
    policy.clear()
    assert await policy.can_read(auth, record) is False  # 重新加载后看到撤销


async def test_grant_permission_and_visible_base_ids_expose_snapshot(
    settings: Settings,
) -> None:
    """`grant_permission` / `visible_base_ids` 直接读快照（`my_role` 的数据来源）。

    Args:
        settings: 配置实例。
    """
    await _seed_base(settings, _record("kb_perm"))
    await _grant_member(settings, "kb_perm", permission="editor")
    policy = PolicyService(settings)
    auth = _auth()

    assert await policy.grant_permission(auth, "kb_perm") == "editor"
    assert await policy.grant_permission(auth, "kb_other") is None
    assert await policy.visible_base_ids(auth) == frozenset({"kb_perm"})
