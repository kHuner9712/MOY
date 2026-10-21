"""知识库元数据业务能力单元测试（阶段二 B04，PRD P2-KB-01）。

覆盖点（逐条对应 PRD 验收与架构文档）：

1. 库级 `chunk_policy` 持久化，`PATCH` 改名 / 改精细度 / 改可见范围；
2. `PATCH` 空名称、无字段 → `20001`；库不存在 → `11001`；无权限 → `20003`；
3. 删除判权四档：创建者 / 组织管理员可删；成员删他人库 `20003`；访客 `20003`；
4. 删除为软删除：列表不可见、文档一并移除、操作记录保留；并发重复删除 `20005`；
5. `my_role` **由数据 + 令牌推导**（`owner_user_id` / 角色 / 授权级别），
   用例用「同一条记录 + 不同观察者」证明没有写死。

测试全部在 `OA_BUSINESS_STORE=memory` 下运行，**不依赖 PostgreSQL**。
"""

from __future__ import annotations

import pytest

from app.business.audit_service import (
    ACTION_KB_CREATE,
    ACTION_KB_DELETE,
    ACTION_KB_UPDATE,
    AuditService,
    InMemoryAuditRecorder,
)
from app.business.knowledge_base_service import (
    KnowledgeBaseService,
    is_guest,
    is_org_admin,
)
from app.core.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.core.security import AuthContext
from app.db.repositories.document_repo import DocumentRecord, InMemoryDocumentStore
from app.db.repositories.knowledge_base_repo import (
    InMemoryKnowledgeBaseStore,
    KnowledgeBaseRecord,
)
from app.schemas.knowledge_ext import UpdateBaseRequest
from app.schemas.rag import (
    ChunkPolicy,
    CreateBaseRequest,
    KnowledgeBaseRole,
    ListBasesQuery,
    Visibility,
)

pytestmark = pytest.mark.unit

TENANT = "tnt_kb"
OWNER = "usr_owner"
OTHER = "usr_other"
ADMIN = "usr_admin"
GUEST = "usr_guest"

_ROLE_MEMBER = ["member"]
_ROLE_ADMIN = ["org_admin"]
_ROLE_GUEST = ["guest"]


def _settings() -> Settings:
    """内存后端配置。"""
    return Settings(env="test", deploy_mode="private", business_store="memory")


def _auth(user_id: str = OWNER, roles: list[str] | None = None) -> AuthContext:
    """构造鉴权上下文。"""
    return AuthContext(user_id=user_id, tenant_id=TENANT, roles=roles or _ROLE_MEMBER)


class _ServiceBundle:
    """服务与内存存储的组合（用例内可见，便于断言）。"""

    def __init__(self) -> None:
        """初始化一组全新的内存存储与服务。"""
        self.settings = _settings()
        self.bases = InMemoryKnowledgeBaseStore()
        self.documents = InMemoryDocumentStore()
        self.audit_recorder = InMemoryAuditRecorder()
        self.audit = AuditService(self.settings, recorder=self.audit_recorder)
        self.service = KnowledgeBaseService(
            self.settings,
            store=self.bases,
            documents=self.documents,
            audit=self.audit,
        )


@pytest.fixture
def bundle() -> _ServiceBundle:
    """全新的服务组合（用例间互不污染）。"""
    return _ServiceBundle()


async def _create_base(
    bundle: _ServiceBundle,
    *,
    name: str = "人事制度库",
    owner: str = OWNER,
    chunk_policy: ChunkPolicy = ChunkPolicy.DEFAULT,
    visibility: Visibility = Visibility.ORGANIZATION,
) -> str:
    """创建一个知识库并返回其 ID。"""
    data = await bundle.service.create(
        CreateBaseRequest(
            name=name,
            description="制度文档",
            visibility=visibility,
            chunk_policy=chunk_policy,
        ),
        _auth(owner),
    )
    return data.base_id


# ===========================================================================
# 1. 创建与库级设置持久化
# ===========================================================================
async def test_create_persists_owner_and_chunk_policy(bundle: _ServiceBundle) -> None:
    """创建时落库库级内容切分精细度与所有者（删除判权的依据）。"""
    base_id = await _create_base(bundle, chunk_policy=ChunkPolicy.FINE)

    record = await bundle.bases.get(TENANT, base_id)
    assert record is not None
    assert record.owner_user_id == OWNER
    assert record.chunk_policy == "fine"
    assert record.created_by == OWNER
    assert not record.is_deleted


async def test_create_records_audit_entry(bundle: _ServiceBundle) -> None:
    """创建知识库写入操作记录（业务事实，可供客户查询）。"""
    base_id = await _create_base(bundle)

    entries = await bundle.audit.recent(TENANT, action=ACTION_KB_CREATE)
    assert [item.resource_id for item in entries] == [base_id]
    assert entries[0].display_name == "创建知识库"


async def test_create_rejects_blank_name(bundle: _ServiceBundle) -> None:
    """空白名称返回 20001。"""
    with pytest.raises(ApiError) as exc_info:
        await bundle.service.create(CreateBaseRequest(name="   "), _auth())
    assert exc_info.value.code == ErrorCode.PARAM_INVALID


# ===========================================================================
# 2. my_role 推导（重点：证明没有写死）
# ===========================================================================
@pytest.mark.parametrize(
    ("viewer", "roles", "expected"),
    [
        (OWNER, _ROLE_MEMBER, KnowledgeBaseRole.OWNER),
        (OTHER, _ROLE_ADMIN, KnowledgeBaseRole.EDITOR),
        (OTHER, ["admin"], KnowledgeBaseRole.EDITOR),
        (OTHER, ["*"], KnowledgeBaseRole.EDITOR),
        (OTHER, _ROLE_MEMBER, KnowledgeBaseRole.VIEWER),
        (OTHER, _ROLE_GUEST, KnowledgeBaseRole.VIEWER),
    ],
)
async def test_my_role_is_derived_from_data_and_claims(
    bundle: _ServiceBundle,
    viewer: str,
    roles: list[str],
    expected: KnowledgeBaseRole,
) -> None:
    """同一条库记录 + 不同观察者 → 角色随「所有者 + 令牌角色」变化（未写死）。

    这是 B04 最容易被写成常量的一处：若实现把 `my_role` 固定为 `owner`，
    除第一行外全部会红。
    """
    await _create_base(bundle, owner=OWNER)

    result = await bundle.service.list(ListBasesQuery(page=1, page_size=20), _auth(viewer, roles))
    assert len(result.items) == 1
    assert result.items[0].my_role == expected


async def test_my_role_uses_grant_permission_when_provided(bundle: _ServiceBundle) -> None:
    """B05 授权表接入后传入 `grant_permission` 即生效（本批预留同一入口）。"""
    base_id = await _create_base(bundle, owner=OWNER)
    record = await bundle.bases.get(TENANT, base_id)
    assert record is not None

    assert (
        bundle.service.resolve_role(record, _auth(OTHER), grant_permission="editor")
        == KnowledgeBaseRole.EDITOR
    )
    assert (
        bundle.service.resolve_role(record, _auth(OTHER), grant_permission="viewer")
        == KnowledgeBaseRole.VIEWER
    )


# ===========================================================================
# 3. 列表与可见范围
# ===========================================================================
async def test_list_returns_chunk_policy_and_timestamps(bundle: _ServiceBundle) -> None:
    """列表条目含 `chunk_policy` / `updated_at`（PRD R2 扩展字段）。"""
    await _create_base(bundle, name="制度库", chunk_policy=ChunkPolicy.FINE)

    result = await bundle.service.list(ListBasesQuery(page=1, page_size=20), _auth())
    item = result.items[0]
    assert item.chunk_policy == ChunkPolicy.FINE
    assert item.updated_at
    assert item.created_at
    assert item.document_count == 0


async def test_list_keyword_filter_is_case_insensitive(bundle: _ServiceBundle) -> None:
    """关键字筛选大小写不敏感。"""
    await _create_base(bundle, name="Employee Handbook")
    await _create_base(bundle, name="财务制度")

    result = await bundle.service.list(
        ListBasesQuery(page=1, page_size=20, keyword="handbook"), _auth()
    )
    assert [item.name for item in result.items] == ["Employee Handbook"]
    assert result.meta.total == 1


async def test_list_hides_private_base_from_other_member(bundle: _ServiceBundle) -> None:
    """仅自己可见的库对其他成员不可见（fail-closed）。"""
    await _create_base(bundle, name="私人库", owner=OWNER, visibility=Visibility.PRIVATE)

    own = await bundle.service.list(ListBasesQuery(page=1, page_size=20), _auth(OWNER))
    other = await bundle.service.list(ListBasesQuery(page=1, page_size=20), _auth(OTHER))
    admin = await bundle.service.list(
        ListBasesQuery(page=1, page_size=20), _auth(ADMIN, _ROLE_ADMIN)
    )
    assert len(own.items) == 1
    assert other.items == []
    assert len(admin.items) == 1


async def test_list_hides_restricted_base_before_grants(bundle: _ServiceBundle) -> None:
    """指定成员可见的库在授权表落地前仅对所有者与组织管理员可见。"""
    await _create_base(bundle, name="薪酬库", owner=OWNER, visibility=Visibility.RESTRICTED)

    own = await bundle.service.list(ListBasesQuery(page=1, page_size=20), _auth(OWNER))
    other = await bundle.service.list(ListBasesQuery(page=1, page_size=20), _auth(OTHER))
    assert len(own.items) == 1
    assert other.items == []


async def test_list_excludes_soft_deleted_base(bundle: _ServiceBundle) -> None:
    """删除后列表立即不可见。"""
    base_id = await _create_base(bundle)
    await bundle.service.soft_delete(base_id, _auth())

    result = await bundle.service.list(ListBasesQuery(page=1, page_size=20), _auth())
    assert result.items == []
    assert result.meta.total == 0


# ===========================================================================
# 4. KB1 更新
# ===========================================================================
async def test_update_name_and_policy(bundle: _ServiceBundle) -> None:
    """改名 + 改内容切分精细度，响应含更新时间与精细度。"""
    base_id = await _create_base(bundle, name="旧名称")

    data = await bundle.service.update(
        base_id,
        UpdateBaseRequest(name="新名称", chunk_policy=ChunkPolicy.FINE),
        _auth(),
    )
    assert data.base_id == base_id
    assert data.name == "新名称"
    assert data.chunk_policy == ChunkPolicy.FINE
    assert data.updated_at

    record = await bundle.bases.get(TENANT, base_id)
    assert record is not None
    assert record.chunk_policy == "fine"


async def test_update_visibility_to_restricted(bundle: _ServiceBundle) -> None:
    """可见范围可改为 `restricted`（枚举加宽，允许）。"""
    base_id = await _create_base(bundle)

    data = await bundle.service.update(
        base_id, UpdateBaseRequest(visibility=Visibility.RESTRICTED), _auth()
    )
    assert data.visibility == Visibility.RESTRICTED


async def test_update_empty_name_returns_20001(bundle: _ServiceBundle) -> None:
    """名称为空字符串返回 20001（PRD 验收 2）。"""
    base_id = await _create_base(bundle)

    with pytest.raises(ApiError) as exc_info:
        await bundle.service.update(base_id, UpdateBaseRequest(name=""), _auth())
    assert exc_info.value.code == ErrorCode.PARAM_INVALID


async def test_update_blank_name_returns_20001(bundle: _ServiceBundle) -> None:
    """名称只有空白字符同样返回 20001。"""
    base_id = await _create_base(bundle)

    with pytest.raises(ApiError) as exc_info:
        await bundle.service.update(base_id, UpdateBaseRequest(name="   "), _auth())
    assert exc_info.value.code == ErrorCode.PARAM_INVALID


async def test_update_without_any_field_returns_20001(bundle: _ServiceBundle) -> None:
    """未提供任何修改项返回 20001（至少一项）。"""
    base_id = await _create_base(bundle)

    with pytest.raises(ApiError) as exc_info:
        await bundle.service.update(base_id, UpdateBaseRequest(), _auth())
    assert exc_info.value.code == ErrorCode.PARAM_INVALID


async def test_update_unknown_base_returns_11001(bundle: _ServiceBundle) -> None:
    """库不存在返回 11001。"""
    with pytest.raises(ApiError) as exc_info:
        await bundle.service.update("kb_missing", UpdateBaseRequest(name="x"), _auth())
    assert exc_info.value.code == ErrorCode.KNOWLEDGE_BASE_NOT_FOUND


async def test_update_by_other_member_returns_20003(bundle: _ServiceBundle) -> None:
    """成员修改他人创建的库返回 20003。"""
    base_id = await _create_base(bundle, owner=OWNER)

    with pytest.raises(ApiError) as exc_info:
        await bundle.service.update(base_id, UpdateBaseRequest(name="x"), _auth(OTHER))
    assert exc_info.value.code == ErrorCode.FORBIDDEN


async def test_update_by_org_admin_succeeds(bundle: _ServiceBundle) -> None:
    """组织管理员可修改任意库。"""
    base_id = await _create_base(bundle, owner=OWNER)

    data = await bundle.service.update(
        base_id, UpdateBaseRequest(name="管理员改名"), _auth(ADMIN, _ROLE_ADMIN)
    )
    assert data.name == "管理员改名"


async def test_update_by_guest_returns_20003(bundle: _ServiceBundle) -> None:
    """访客修改自己创建的库也返回 20003（只读角色）。"""
    base_id = await _create_base(bundle, owner=GUEST)

    with pytest.raises(ApiError) as exc_info:
        await bundle.service.update(base_id, UpdateBaseRequest(name="x"), _auth(GUEST, _ROLE_GUEST))
    assert exc_info.value.code == ErrorCode.FORBIDDEN


async def test_update_records_audit_entry(bundle: _ServiceBundle) -> None:
    """修改写入操作记录，摘要只含变更字段。"""
    base_id = await _create_base(bundle)

    await bundle.service.update(base_id, UpdateBaseRequest(name="新名称"), _auth())
    entries = await bundle.audit.recent(TENANT, action=ACTION_KB_UPDATE)
    assert entries[0].resource_id == base_id
    assert entries[0].change_summary == {"name": "新名称"}


# ===========================================================================
# 5. KB2 删除
# ===========================================================================
async def _seed_documents(bundle: _ServiceBundle, base_id: str, count: int) -> None:
    """在指定库下造若干文档。"""
    for index in range(count):
        await bundle.documents.create(
            DocumentRecord(
                tenant_id=TENANT,
                document_id=f"doc_{index:04d}",
                base_id=base_id,
                file_name=f"文档{index}.pdf",
                file_size=1024,
                uploader_user_id=OWNER,
                uploader_name=OWNER,
            )
        )


async def test_soft_delete_by_owner_removes_documents(bundle: _ServiceBundle) -> None:
    """创建者删除：返回一并移除的文档数，库与文档均不可见。"""
    base_id = await _create_base(bundle)
    await _seed_documents(bundle, base_id, 3)

    data = await bundle.service.soft_delete(base_id, _auth())
    assert data.base_id == base_id
    assert data.deleted is True
    assert data.deleted_document_count == 3

    assert await bundle.bases.get(TENANT, base_id) is None
    assert await bundle.documents.count_by_base(TENANT, base_id) == 0


async def test_soft_delete_by_org_admin_succeeds(bundle: _ServiceBundle) -> None:
    """组织管理员可删除任意库（PRD 验收 5 第 4 条）。"""
    base_id = await _create_base(bundle, owner=OWNER)

    data = await bundle.service.soft_delete(base_id, _auth(ADMIN, _ROLE_ADMIN))
    assert data.deleted is True


async def test_soft_delete_other_members_base_returns_20003(bundle: _ServiceBundle) -> None:
    """成员 A 删除成员 B 创建的库 → 20003（PRD 验收 5 第 2 条）。"""
    base_id = await _create_base(bundle, owner=OWNER)

    with pytest.raises(ApiError) as exc_info:
        await bundle.service.soft_delete(base_id, _auth(OTHER))
    assert exc_info.value.code == ErrorCode.FORBIDDEN
    # 拒绝后库仍在
    assert await bundle.bases.get(TENANT, base_id) is not None


async def test_soft_delete_organization_base_by_member_returns_20003(
    bundle: _ServiceBundle,
) -> None:
    """组织公共库（非本人创建）成员不可删（PRD 验收 5 第 3 条）。"""
    base_id = await _create_base(bundle, owner=OWNER, visibility=Visibility.ORGANIZATION)

    with pytest.raises(ApiError) as exc_info:
        await bundle.service.soft_delete(base_id, _auth(OTHER))
    assert exc_info.value.code == ErrorCode.FORBIDDEN


async def test_soft_delete_by_guest_returns_20003(bundle: _ServiceBundle) -> None:
    """访客一律不可删。"""
    base_id = await _create_base(bundle, owner=GUEST)

    with pytest.raises(ApiError) as exc_info:
        await bundle.service.soft_delete(base_id, _auth(GUEST, _ROLE_GUEST))
    assert exc_info.value.code == ErrorCode.FORBIDDEN


async def test_soft_delete_unknown_base_returns_11001(bundle: _ServiceBundle) -> None:
    """库不存在返回 11001。"""
    with pytest.raises(ApiError) as exc_info:
        await bundle.service.soft_delete("kb_missing", _auth())
    assert exc_info.value.code == ErrorCode.KNOWLEDGE_BASE_NOT_FOUND


async def test_soft_delete_concurrent_delete_returns_20005(bundle: _ServiceBundle) -> None:
    """并发重复删除（存储层判定已被删除）返回 20005。"""
    base_id = await _create_base(bundle)

    class _RacingStore:
        """模拟「读到库 → 被并发删除 → 写回失败」的存储。"""

        def __init__(self, delegate: InMemoryKnowledgeBaseStore) -> None:
            self._delegate = delegate
            self.soft_delete_calls = 0

        async def get(
            self, tenant_id: str, base_id: str, *, include_deleted: bool = False
        ) -> KnowledgeBaseRecord | None:
            return await self._delegate.get(tenant_id, base_id, include_deleted=include_deleted)

        async def soft_delete(self, tenant_id: str, base_id: str) -> bool:
            self.soft_delete_calls += 1
            return False

        async def count_documents(self, tenant_id: str, base_id: str) -> int:
            return await self._delegate.count_documents(tenant_id, base_id)

    racing = _RacingStore(bundle.bases)
    service = KnowledgeBaseService(
        bundle.settings,
        store=racing,
        documents=bundle.documents,
        audit=bundle.audit,  # type: ignore[arg-type]
    )
    with pytest.raises(ApiError) as exc_info:
        await service.soft_delete(base_id, _auth())
    assert exc_info.value.code == ErrorCode.CONFLICT
    assert racing.soft_delete_calls == 1


async def test_soft_delete_records_audit_entry(bundle: _ServiceBundle) -> None:
    """删除写入操作记录（软删除后记录保留，PRD 验收 4）。"""
    base_id = await _create_base(bundle)
    await _seed_documents(bundle, base_id, 2)

    await bundle.service.soft_delete(base_id, _auth())
    entries = await bundle.audit.recent(TENANT, action=ACTION_KB_DELETE)
    assert entries[0].resource_id == base_id
    assert entries[0].display_name == "删除知识库"
    assert entries[0].change_summary["deleted_document_count"] == 2


# ===========================================================================
# 6. 角色判定辅助函数
# ===========================================================================
@pytest.mark.parametrize(
    ("roles", "expected_admin", "expected_guest"),
    [
        (["member"], False, False),
        (["org_admin"], True, False),
        (["admin"], True, False),
        (["owner"], True, False),
        (["*"], True, False),
        (["guest"], False, True),
        (["member", "guest"], False, True),
        ([], False, False),
    ],
)
def test_role_helpers(roles: list[str], expected_admin: bool, expected_guest: bool) -> None:
    """角色判定：管理员与访客互斥，空角色既非管理员也非访客。"""
    auth = AuthContext(user_id="usr_1", tenant_id=TENANT, roles=roles)
    assert is_org_admin(auth) is expected_admin
    assert is_guest(auth) is expected_guest


def test_org_admin_wins_over_guest() -> None:
    """同时带管理员与访客角色时按管理员处理（不误伤运维令牌）。"""
    auth = AuthContext(user_id="usr_1", tenant_id=TENANT, roles=["guest", "org_admin"])
    assert is_org_admin(auth) is True
    assert is_guest(auth) is False
