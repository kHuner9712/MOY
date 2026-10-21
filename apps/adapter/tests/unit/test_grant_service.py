"""知识库授权业务单元测试（阶段二 B05，架构文档 6.3 / PRD P2-KB-03）。

覆盖点（逐条对应 PRD 验收标准）：

1. 批量授权：重复对象计入 `skipped_count`（验收 3），不产生重复条目；
2. `guest` 角色授 `editor` → `20001` + 文案「访客只能查看，不能上传或管理文档」（验收 5）；
3. 对象级判权：非所有者 / 非管理员 → `20003`；访客 → `20003`；库不存在 → `11001`；
4. 授权后库可见范围收敛为 `restricted`，且缓存立即失效（下一请求即生效）；
5. 授权对象展示名回填（部门 / 角色），成员回落为占位名；
6. 撤销：成功 → `20004` 之后不可重复撤销；对象归属不符 → `20004`；
7. 授权与撤销都写操作记录（验收 6）。

测试运行在内存后端上，**不依赖 PostgreSQL**。
"""

from __future__ import annotations

import pytest

from app.business.audit_service import (
    ACTION_GRANT_CREATE,
    ACTION_GRANT_REVOKE,
    AuditService,
    InMemoryAuditRecorder,
)
from app.business.grant_service import (
    GUEST_EDITOR_MESSAGE,
    MAX_SUBJECTS_PER_REQUEST,
    GrantService,
)
from app.business.policy_service import PolicyService
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.security import AuthContext
from app.db.repositories.grant_repo import GrantRecord, InMemoryGrantStore
from app.db.repositories.knowledge_base_repo import (
    InMemoryKnowledgeBaseStore,
    KnowledgeBaseRecord,
)
from app.schemas.knowledge_ext import CreateGrantRequest

pytestmark = pytest.mark.unit

_TENANT = "tnt_unit"
_OWNER = AuthContext(user_id="usr_owner", tenant_id=_TENANT, roles=["member"])
_ADMIN = AuthContext(user_id="usr_admin", tenant_id=_TENANT, roles=["org_admin"])
_OTHER = AuthContext(user_id="usr_other", tenant_id=_TENANT, roles=["member"])
_GUEST = AuthContext(user_id="usr_guest", tenant_id=_TENANT, roles=["guest"])

_BASE_ID = "kb_unit_001"


class _Clock:
    """可控单调时钟（验证 5s 缓存失效）。"""

    def __init__(self, value: float = 1_000.0) -> None:
        """初始化时钟。

        Args:
            value: 初始时刻。
        """
        self.value = value

    def __call__(self) -> float:
        """读取当前时刻。

        Returns:
            float: 当前时刻。
        """
        return self.value


class _Harness:
    """授权服务测试夹具（内存双存储 + 可控时钟）。"""

    def __init__(self, settings: Settings) -> None:
        """初始化夹具。

        Args:
            settings: 配置实例。
        """
        self.bases = InMemoryKnowledgeBaseStore()
        self.grants = InMemoryGrantStore()
        self.audit = AuditService(settings, recorder=InMemoryAuditRecorder())
        self.clock = _Clock()
        self.policy = PolicyService(
            settings, bases=self.bases, grants=self.grants, clock=self.clock
        )
        self.service = GrantService(
            settings,
            store=self.grants,
            bases=self.bases,
            audit=self.audit,
            policy=self.policy,
        )

    async def seed_base(
        self, owner: str = _OWNER.user_id, visibility: str = "organization"
    ) -> None:
        """写入一个知识库。

        Args:
            owner: 所有者用户 ID。
            visibility: 可见范围。
        """
        await self.bases.create(
            KnowledgeBaseRecord(
                tenant_id=_TENANT,
                base_id=_BASE_ID,
                name="制度库",
                visibility=visibility,
                owner_user_id=owner,
            )
        )


def _settings() -> Settings:
    """构造桩模式配置。

    Returns:
        Settings: 配置实例。
    """
    return Settings(env="test", deploy_mode="private", downstream_enabled=False)


# ===========================================================================
# 1. 批量授权与幂等
# ===========================================================================
async def test_create_grants_counts_created_and_skipped() -> None:
    """同对象连续授权两次：第二次 `skipped_count=1`（PRD 验收 3）。"""
    harness = _Harness(_settings())
    await harness.seed_base()

    first = await harness.service.create_grants(
        _BASE_ID,
        CreateGrantRequest(
            subject_type="user", subject_ids=["usr_b", "usr_c"], permission="viewer"
        ),
        _OWNER,
    )
    assert (first.created_count, first.skipped_count) == (2, 0)

    second = await harness.service.create_grants(
        _BASE_ID,
        CreateGrantRequest(subject_type="user", subject_ids=["usr_b"], permission="viewer"),
        _OWNER,
    )
    assert (second.created_count, second.skipped_count) == (0, 1)

    listed = await harness.service.list_grants(_BASE_ID, 1, 20, _OWNER)
    assert listed.meta.total == 2


async def test_create_grants_deduplicates_within_single_request() -> None:
    """同一请求内重复对象只创建一条，其余计入 `skipped_count`（架构 §4.2 要点）。"""
    harness = _Harness(_settings())
    await harness.seed_base()

    result = await harness.service.create_grants(
        _BASE_ID,
        CreateGrantRequest(
            subject_type="user", subject_ids=["usr_b", "usr_b", "  usr_b  "], permission="editor"
        ),
        _OWNER,
    )
    assert result.created_count == 1
    assert result.skipped_count == 2


async def test_create_grants_rejects_more_than_fifty_subjects() -> None:
    """单次授权对象超过 50 个由请求模型拦截（20001 的入口层）。"""
    with pytest.raises(ValueError):
        CreateGrantRequest(
            subject_type="user",
            subject_ids=[f"usr_{index}" for index in range(MAX_SUBJECTS_PER_REQUEST + 1)],
            permission="viewer",
        )


async def test_create_grants_empty_subject_ids_rejected() -> None:
    """对象 ID 全为空白 → 20001。"""
    harness = _Harness(_settings())
    await harness.seed_base()
    with pytest.raises(ApiError) as excinfo:
        await harness.service.create_grants(
            _BASE_ID,
            CreateGrantRequest(subject_type="user", subject_ids=["   "], permission="viewer"),
            _OWNER,
        )
    assert excinfo.value.code == ErrorCode.PARAM_INVALID


# ===========================================================================
# 2. guest 不可授 editor（PRD 验收 5）
# ===========================================================================
async def test_guest_role_editor_returns_20001_with_prd_message() -> None:
    """`guest` 角色授 `editor` → 20001 且文案为 PRD 原文。"""
    harness = _Harness(_settings())
    await harness.seed_base()

    with pytest.raises(ApiError) as excinfo:
        await harness.service.create_grants(
            _BASE_ID,
            CreateGrantRequest(subject_type="role", subject_ids=["guest"], permission="editor"),
            _OWNER,
        )
    assert excinfo.value.code == ErrorCode.PARAM_INVALID
    assert excinfo.value.message == GUEST_EDITOR_MESSAGE


async def test_guest_role_viewer_is_allowed() -> None:
    """`guest` 角色授 `viewer` 合法。"""
    harness = _Harness(_settings())
    await harness.seed_base()

    result = await harness.service.create_grants(
        _BASE_ID,
        CreateGrantRequest(subject_type="role", subject_ids=["guest"], permission="viewer"),
        _OWNER,
    )
    assert result.created_count == 1


# ===========================================================================
# 3. 对象级判权
# ===========================================================================
async def test_member_cannot_manage_grants_of_others_base() -> None:
    """非所有者成员管理授权 → 20003。"""
    harness = _Harness(_settings())
    await harness.seed_base()

    with pytest.raises(ApiError) as excinfo:
        await harness.service.create_grants(
            _BASE_ID,
            CreateGrantRequest(subject_type="user", subject_ids=["usr_b"], permission="viewer"),
            _OTHER,
        )
    assert excinfo.value.code == ErrorCode.FORBIDDEN


async def test_guest_cannot_manage_grants() -> None:
    """访客即使令牌角色命中也不可管理授权 → 20003。"""
    harness = _Harness(_settings())
    await harness.seed_base(owner="usr_guest")

    with pytest.raises(ApiError) as excinfo:
        await harness.service.list_grants(_BASE_ID, 1, 20, _GUEST)
    assert excinfo.value.code == ErrorCode.FORBIDDEN


async def test_admin_can_manage_any_base() -> None:
    """组织管理员可管理任意库的授权。"""
    harness = _Harness(_settings())
    await harness.seed_base(owner="usr_someone")

    result = await harness.service.create_grants(
        _BASE_ID,
        CreateGrantRequest(subject_type="user", subject_ids=["usr_b"], permission="viewer"),
        _ADMIN,
    )
    assert result.created_count == 1


async def test_unknown_base_returns_11001() -> None:
    """库不存在 → 11001。"""
    harness = _Harness(_settings())
    with pytest.raises(ApiError) as excinfo:
        await harness.service.list_grants("kb_missing", 1, 20, _OWNER)
    assert excinfo.value.code == ErrorCode.KNOWLEDGE_BASE_NOT_FOUND


# ===========================================================================
# 4. 可见范围收敛 + 缓存失效
# ===========================================================================
async def test_grant_marks_base_restricted_and_invalidates_cache() -> None:
    """授权后：可见范围收敛为 restricted，且列表判定立即生效（不等 TTL）。"""
    harness = _Harness(_settings())
    await harness.seed_base(visibility="organization")

    # 授权前 usr_b 的快照（organization 全员可见）
    before = await harness.policy.snapshot(_context_b())
    assert before.is_admin is False

    await harness.service.create_grants(
        _BASE_ID,
        CreateGrantRequest(subject_type="user", subject_ids=["usr_b"], permission="viewer"),
        _OWNER,
    )

    record = await harness.bases.get(_TENANT, _BASE_ID)
    assert record is not None
    assert record.visibility == "restricted"

    after = await harness.policy.snapshot(_context_b())
    assert _BASE_ID in after.granted_base_ids
    assert after.permission_of(_BASE_ID) == "viewer"


async def test_permission_cache_expires_within_five_seconds() -> None:
    """缓存 TTL 5s：过期后重新计算，成员关系变化无需广播即可生效（PRD 验收 2）。"""
    settings = _settings()
    harness = _Harness(settings)
    await harness.seed_base(visibility="restricted")
    await harness.grants.create(
        GrantRecord(
            tenant_id=_TENANT,
            grant_id="grnt_dept",
            base_id=_BASE_ID,
            subject_type="department",
            subject_id="dept_x",
            permission="viewer",
            granted_by=_OWNER.user_id,
        )
    )
    harness.grants.set_user_departments(_TENANT, "usr_b", ["dept_x"])

    user_b = _context_b()
    first = await harness.policy.snapshot(user_b)
    assert _BASE_ID in first.granted_base_ids

    # 成员移出部门，但缓存未过期 → 仍按旧结果（≤5s 窗口）
    harness.grants.set_user_departments(_TENANT, "usr_b", [])
    cached = await harness.policy.snapshot(user_b)
    assert _BASE_ID in cached.granted_base_ids

    # 推进 5s 后自然失效
    harness.clock.value += settings.permission_cache_ttl_seconds
    refreshed = await harness.policy.snapshot(user_b)
    assert _BASE_ID not in refreshed.granted_base_ids


# ===========================================================================
# 5. 授权对象展示名
# ===========================================================================
async def test_list_grants_resolves_subject_names() -> None:
    """部门 / 角色回填展示名，成员回落为业务语言占位名。"""
    harness = _Harness(_settings())
    await harness.seed_base()
    harness.grants.set_subject_name(_TENANT, "department", "dept_x", "研发部")
    harness.grants.set_subject_name(_TENANT, "role", "role_x", "知识库管理员")

    await harness.service.create_grants(
        _BASE_ID,
        CreateGrantRequest(subject_type="department", subject_ids=["dept_x"], permission="viewer"),
        _OWNER,
    )
    await harness.service.create_grants(
        _BASE_ID,
        CreateGrantRequest(subject_type="role", subject_ids=["role_x"], permission="editor"),
        _OWNER,
    )
    await harness.service.create_grants(
        _BASE_ID,
        CreateGrantRequest(subject_type="user", subject_ids=["usr_b"], permission="viewer"),
        _OWNER,
    )

    listed = await harness.service.list_grants(_BASE_ID, 1, 20, _OWNER)
    names = {item.subject_id: item.subject_name for item in listed.items}
    assert names["dept_x"] == "研发部"
    assert names["role_x"] == "知识库管理员"
    assert names["usr_b"] == "成员"
    permissions = {item.subject_id: item.permission.value for item in listed.items}
    assert permissions["role_x"] == "editor"


# ===========================================================================
# 6. 撤销
# ===========================================================================
async def test_revoke_grant_then_second_revoke_returns_20004() -> None:
    """撤销成功后再次撤销同一记录 → 20004。"""
    harness = _Harness(_settings())
    await harness.seed_base()
    await harness.service.create_grants(
        _BASE_ID,
        CreateGrantRequest(subject_type="user", subject_ids=["usr_b"], permission="viewer"),
        _OWNER,
    )
    listed = await harness.service.list_grants(_BASE_ID, 1, 20, _OWNER)
    grant_id = listed.items[0].grant_id

    revoked = await harness.service.revoke_grant(_BASE_ID, grant_id, _OWNER)
    assert revoked.grant_id == grant_id
    assert revoked.deleted is True
    assert (await harness.service.list_grants(_BASE_ID, 1, 20, _OWNER)).meta.total == 0

    with pytest.raises(ApiError) as excinfo:
        await harness.service.revoke_grant(_BASE_ID, grant_id, _OWNER)
    assert excinfo.value.code == ErrorCode.RESOURCE_NOT_FOUND


async def test_revoke_grant_of_other_base_returns_20004() -> None:
    """授权记录不属于该库 → 20004（对象归属校验）。"""
    harness = _Harness(_settings())
    await harness.seed_base()
    await harness.bases.create(
        KnowledgeBaseRecord(
            tenant_id=_TENANT,
            base_id="kb_unit_002",
            name="另一个库",
            owner_user_id=_OWNER.user_id,
        )
    )
    await harness.service.create_grants(
        _BASE_ID,
        CreateGrantRequest(subject_type="user", subject_ids=["usr_b"], permission="viewer"),
        _OWNER,
    )
    grant_id = (await harness.service.list_grants(_BASE_ID, 1, 20, _OWNER)).items[0].grant_id

    with pytest.raises(ApiError) as excinfo:
        await harness.service.revoke_grant("kb_unit_002", grant_id, _OWNER)
    assert excinfo.value.code == ErrorCode.RESOURCE_NOT_FOUND


# ===========================================================================
# 7. 操作记录
# ===========================================================================
async def test_grant_changes_are_audited() -> None:
    """授权与撤销都写操作记录（PRD 验收 6）。"""
    harness = _Harness(_settings())
    await harness.seed_base()
    await harness.service.create_grants(
        _BASE_ID,
        CreateGrantRequest(subject_type="user", subject_ids=["usr_b"], permission="viewer"),
        _OWNER,
    )
    grant_id = (await harness.service.list_grants(_BASE_ID, 1, 20, _OWNER)).items[0].grant_id
    await harness.service.revoke_grant(_BASE_ID, grant_id, _OWNER)

    created = await harness.audit.recent(_TENANT, action=ACTION_GRANT_CREATE)
    revoked = await harness.audit.recent(_TENANT, action=ACTION_GRANT_REVOKE)
    assert [entry.resource_id for entry in created] == [_BASE_ID]
    assert created[0].display_name == "添加成员授权"
    assert created[0].change_summary["created_count"] == 1
    assert [entry.resource_id for entry in revoked] == [_BASE_ID]
    assert revoked[0].change_summary["grant_id"] == grant_id


def _context_b() -> AuthContext:
    """构造「成员 B」的鉴权上下文。

    Returns:
        AuthContext: 鉴权上下文。
    """
    return AuthContext(user_id="usr_b", tenant_id=_TENANT, roles=["member"])
