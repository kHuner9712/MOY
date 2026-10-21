"""B05 判权/归一化/授权计数的接口级回归（QA B05-P0-01 + B05-P2-02）。

与 `tests/integration/test_qa_b05_b06_verification.py` 的关系：那是 QA 的独立验收集，
本文件是**工程师侧回归**，多做的三件事：

1. **把「换 ID 写法」的绕过路径逐入口钉死**：R7 / KB3 / KB4 / M1 四个入口 ×
   (`kb_<原生>` / `<原生>`) 两种写法 × (未授权 / 已授权 / 跨租户 / 已删除) 四种状态；
2. **R3 与 KB3 的 ID 一致性**：用原生写法上传的文档，必须能在规范写法的清单里看到
   （否则「一个对象一个 ID」只做了一半）；
3. **深防御分支**（schema 已经拦住、服务层仍要自己拦的那些）：批量授权超 50 个对象、
   知识库名称/描述超长、存储层枚举值漂移的兜底、KB4 片段归属不符的降级。

判据来源：架构文档 §4.8（三层校验 + 对象级）、§4.2（KB6 `subject_ids` 重复计入
`skipped_count`）、§9.2 B05 可验证 ①③④。
"""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.business.document_service import DocumentService
from app.business.grant_service import GrantService
from app.business.knowledge_base_service import KnowledgeBaseService
from app.business.policy_service import get_policy_service
from app.business.segment_service import (
    SegmentHit,
    SegmentService,
    build_key,
    get_segment_service,
)
from app.core.config import Settings
from app.core.errors import ApiError
from app.core.security import AuthContext
from app.db.repositories.grant_repo import GrantRecord, get_grant_store
from app.db.repositories.knowledge_base_repo import (
    KnowledgeBaseRecord,
    get_knowledge_base_store,
)
from app.db.repositories.segment_repo import (
    STATUS_DELETED,
    STATUS_STALE,
    SegmentMapRecord,
    SegmentStore,
    get_segment_store,
)
from app.schemas.knowledge_ext import (
    CreateGrantRequest,
    GrantPermission,
    GrantSubjectType,
    UpdateBaseRequest,
)
from app.schemas.rag import (
    ChunkPolicy,
    CreateBaseRequest,
    ListBasesQuery,
    SearchRequest,
    Visibility,
)
from app.schemas.segment import SegmentStatus
from app.services.rag_service import RagService
from tests.conftest import make_token

pytestmark = pytest.mark.integration

#: 私有化模式固定租户：`deploy_mode=private` 时运行时租户恒为 `default`
TENANT = "default"

#: 另一个租户（跨租户用例）
OTHER_TENANT = "tnt_guard_other"

#: 知识库所有者 / 管理员视角的用户
OWNER = "usr_guard_owner"

#: 被授权 / 未授权成员
MEMBER = "usr_guard_member"

#: 权限点
KB_UPDATE = "knowledge:base:update"
KB_DELETE = "knowledge:base:delete"
KB_READ = "knowledge:base:read"
KB_GRANT = "knowledge:grant:manage"

_OWNER_ROLES = [KB_UPDATE, KB_DELETE, KB_READ, KB_GRANT]
_READER_ROLES = [KB_READ]


def _key(tag: str) -> str:
    """按标签生成稳定 UUID 形态幂等键。

    Args:
        tag: 标签。

    Returns:
        str: 幂等键。
    """
    digest = hashlib.sha256(tag.encode("utf-8")).hexdigest()
    return f"{digest[0:8]}-{digest[8:12]}-4{digest[13:16]}-8{digest[17:20]}-{digest[20:32]}"


def _headers(
    settings: Settings,
    roles: list[str] | None = None,
    *,
    user_id: str = OWNER,
    tenant: str = TENANT,
) -> dict[str, str]:
    """构造鉴权请求头。

    Args:
        settings: 配置实例。
        roles: 权限点列表。
        user_id: 用户 ID。
        tenant: 租户 ID。

    Returns:
        dict[str, str]: 请求头。
    """
    token = make_token(
        settings=settings,
        user_id=user_id,
        tenant_id=tenant,
        roles=_OWNER_ROLES if roles is None else roles,
    )
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant}


def _create_base(
    client: TestClient,
    settings: Settings,
    *,
    visibility: str = "restricted",
    tag: str = "guard-base",
    user_id: str = OWNER,
) -> str:
    """经 R1 创建知识库（必然落本地元数据）。

    Args:
        client: 测试客户端。
        settings: 配置实例。
        visibility: 可见范围。
        tag: 幂等键标签。
        user_id: 创建人。

    Returns:
        str: 知识库 ID（规范形态）。
    """
    body = client.post(
        "/api/v1/knowledge/bases",
        json={"name": f"制度库-{tag}", "description": "制度", "visibility": visibility},
        headers={**_headers(settings, user_id=user_id), "Idempotency-Key": _key(tag)},
    ).json()
    assert body["code"] == 0, body
    return str(body["data"]["base_id"])


def _grant(
    client: TestClient,
    settings: Settings,
    base_id: str,
    subject_ids: list[str],
    *,
    permission: str = "viewer",
    tag: str = "guard-grant",
) -> dict[str, object]:
    """经 KB6 批量授权。

    Args:
        client: 测试客户端。
        settings: 配置实例。
        base_id: 知识库 ID。
        subject_ids: 授权对象 ID 列表。
        permission: 授权级别。
        tag: 幂等键标签。

    Returns:
        dict[str, object]: 响应 JSON。
    """
    return client.post(
        f"/api/v1/knowledge/bases/{base_id}/grants",
        json={"subject_type": "user", "subject_ids": subject_ids, "permission": permission},
        headers={**_headers(settings), "Idempotency-Key": _key(tag)},
    ).json()


def _search(
    client: TestClient,
    settings: Settings,
    base_id: str,
    *,
    tag: str,
    roles: list[str] | None = None,
    user_id: str = MEMBER,
) -> dict[str, object]:
    """经 R7 检索（返回完整响应体）。

    Args:
        client: 测试客户端。
        settings: 配置实例。
        base_id: 知识库 ID。
        tag: 幂等键标签。
        roles: 权限点列表。
        user_id: 用户 ID。

    Returns:
        dict[str, object]: 响应 JSON。
    """
    return client.post(
        "/api/v1/knowledge/search",
        json={"base_id": base_id, "query": "年假"},
        headers={
            **_headers(settings, roles, user_id=user_id),
            "Idempotency-Key": _key(tag),
        },
    ).json()


def _auth(user_id: str = OWNER, *, tenant: str = TENANT) -> AuthContext:
    """构造鉴权上下文（服务层直调用）。

    Args:
        user_id: 用户 ID。
        tenant: 租户 ID。

    Returns:
        AuthContext: 鉴权上下文。
    """
    return AuthContext(tenant_id=tenant, user_id=user_id, roles=list(_OWNER_ROLES))


async def _create_completed_document(
    client: TestClient, settings: Settings, base_id: str, *, tag: str
) -> str:
    """上传并「处理完成」一份文档（KB4 只对已完成文档返回详情）。

    Args:
        client: 测试客户端。
        settings: 配置实例。
        base_id: 知识库 ID。
        tag: 幂等键标签。

    Returns:
        str: 文档 ID。
    """
    upload = client.post(
        f"/api/v1/knowledge/bases/{base_id}/documents",
        files={"file": ("制度.pdf", b"%PDF-1.4 guard", "application/pdf")},
        headers={**_headers(settings), "Idempotency-Key": _key(tag)},
    )
    assert upload.json()["code"] == 0, upload.text
    document_id = str(upload.json()["data"]["document_id"])
    await DocumentService(settings).refresh_progress(
        tenant_id=TENANT, document_id=document_id, native_status="completed", progress=100
    )
    return document_id


# ===========================================================================
# 1. R7 检索：判权 fail-closed + ID 归一化（P0 核心）
# ===========================================================================
def test_r7_unregistered_base_returns_20003(client: TestClient, settings: Settings) -> None:
    """本地无元数据 → `20003`（不再「查不到就放行」，且不泄露存在性）。

    Args:
        client: 测试客户端。
        settings: 配置实例。
    """
    body = _search(client, settings, "kb_01M5FT0G0NS7MX62CTQTWE0H12", tag="guard-r7-unknown")

    assert body["code"] == 20003, body


def test_r7_denies_unauthorized_member_using_native_id(
    client: TestClient, settings: Settings
) -> None:
    """`restricted` 库：`kb_<原生>` 与 `<原生>` 两种写法都必须 `20003`（P0 回归）。

    Args:
        client: 测试客户端。
        settings: 配置实例。
    """
    base_id = _create_base(client, settings, tag="guard-r7-native")
    native_id = base_id.removeprefix("kb_")

    canonical = _search(client, settings, base_id, tag="guard-r7-canonical")
    bypass = _search(client, settings, native_id, tag="guard-r7-bypass")

    assert canonical["code"] == 20003, canonical
    assert bypass["code"] == 20003, f"去前缀写法绕过了 restricted 判权：{bypass}"


def test_r7_allows_granted_member_using_native_id(client: TestClient, settings: Settings) -> None:
    """已授权成员用原生写法也能检索（归一化的正向证明）。

    Args:
        client: 测试客户端。
        settings: 配置实例。
    """
    base_id = _create_base(client, settings, tag="guard-r7-granted")
    granted = _grant(client, settings, base_id, [MEMBER], tag="guard-r7-granted-g")
    assert granted["code"] == 0, granted

    body = _search(client, settings, base_id.removeprefix("kb_"), tag="guard-r7-granted-s")

    assert body["code"] == 0, body


async def test_r7_cross_tenant_base_returns_20003(client: TestClient, settings: Settings) -> None:
    """对方租户的 `base_id` 直接拿来检索 → `20003`（租户层第一判据）。

    Args:
        client: 测试客户端。
        settings: 配置实例。
    """
    await get_knowledge_base_store(settings).create(
        KnowledgeBaseRecord(
            tenant_id=OTHER_TENANT,
            base_id="kb_guardCrossTenant",
            name="A 企业库",
            visibility="restricted",
            owner_user_id="usr_a",
        )
    )

    body = _search(client, settings, "kb_guardCrossTenant", tag="guard-r7-cross")

    assert body["code"] == 20003, f"跨租户检索被放行：{body}"


def test_r7_soft_deleted_base_returns_20003(client: TestClient, settings: Settings) -> None:
    """软删除后立即不可检索（列表与检索口径一致）。

    Args:
        client: 测试客户端。
        settings: 配置实例。
    """
    base_id = _create_base(client, settings, visibility="organization", tag="guard-r7-deleted")
    removed = client.delete(
        f"/api/v1/knowledge/bases/{base_id}",
        headers={**_headers(settings), "Idempotency-Key": _key("guard-r7-deleted-d")},
    )
    assert removed.json()["code"] == 0, removed.text

    body = _search(client, settings, base_id, tag="guard-r7-deleted-s")

    assert body["code"] == 20003, body


async def test_r7_rejects_empty_base_id_at_service_layer(settings: Settings) -> None:
    """归一化后为空 → `20001`（服务层兜底，不把空键送进存储层）。

    Args:
        settings: 配置实例。
    """
    request = SearchRequest.model_construct(base_id="   ", query="年假")

    with pytest.raises(ApiError) as error:
        await RagService(settings).search(request, _auth())

    assert int(error.value.code) == 20001


# ===========================================================================
# 2. KB3 文档清单：归一化 + 与 R3 的 ID 一致性
# ===========================================================================
def test_kb3_denies_unauthorized_member_using_native_id(
    client: TestClient, settings: Settings
) -> None:
    """`restricted` 库：原生写法同样 `20003`（列表过滤 ≠ 详情校验）。

    Args:
        client: 测试客户端。
        settings: 配置实例。
    """
    base_id = _create_base(client, settings, tag="guard-kb3-native")

    body = client.get(
        f"/api/v1/knowledge/bases/{base_id.removeprefix('kb_')}/documents",
        headers=_headers(settings, _READER_ROLES, user_id=MEMBER),
    ).json()

    assert body["code"] == 20003, body


def test_kb3_lists_documents_uploaded_with_native_id(
    client: TestClient, settings: Settings
) -> None:
    """用原生写法上传的文档，必须能在规范写法的清单里查到（一个对象一个 ID）。

    Args:
        client: 测试客户端。
        settings: 配置实例。
    """
    base_id = _create_base(client, settings, visibility="organization", tag="guard-kb3-upload")
    upload = client.post(
        f"/api/v1/knowledge/bases/{base_id.removeprefix('kb_')}/documents",
        files={"file": ("制度.pdf", b"%PDF-1.4 guard", "application/pdf")},
        headers={**_headers(settings), "Idempotency-Key": _key("guard-kb3-upload-d")},
    )
    assert upload.json()["code"] == 0, upload.text

    body = client.get(
        f"/api/v1/knowledge/bases/{base_id}/documents",
        headers=_headers(settings, _READER_ROLES),
    ).json()

    assert body["code"] == 0, body
    assert [item["file_name"] for item in body["data"]["items"]] == ["制度.pdf"]


def test_kb3_unregistered_base_returns_11001(client: TestClient, settings: Settings) -> None:
    """未登记元数据的库在详情入口是「不存在」（11001），不泄露也不放行。

    Args:
        client: 测试客户端。
        settings: 配置实例。
    """
    body = client.get(
        "/api/v1/knowledge/bases/kb_guardNoSuchBase/documents",
        headers=_headers(settings, _READER_ROLES),
    ).json()

    assert body["code"] == 11001, body


# ===========================================================================
# 3. KB4 引用详情：归一化 + 片段归属不符的降级（stale，不是错误）
# ===========================================================================
async def test_kb4_returns_detail_for_native_id(client: TestClient, settings: Settings) -> None:
    """KB4 用原生写法也能取到片段详情（归一化覆盖第四个入口）。

    Args:
        client: 测试客户端。
        settings: 配置实例。
    """
    base_id = _create_base(client, settings, visibility="organization", tag="guard-kb4-native")
    document_id = await _create_completed_document(
        client, settings, base_id, tag="guard-kb4-native-doc"
    )
    content = "第十条 报销需在五个工作日内提交。"
    await SegmentService(settings).record_hits(
        TENANT,
        [
            _hit(base_id, document_id, content),
        ],
        schedule=False,
    )
    segment_key = build_key(document_id, 0, content)

    body = client.get(
        f"/api/v1/knowledge/bases/{base_id.removeprefix('kb_')}"
        f"/documents/{document_id}/segments/{segment_key}",
        headers=_headers(settings, _READER_ROLES),
    ).json()

    assert body["code"] == 0, body
    assert body["data"]["status"] == "valid"
    assert body["data"]["content"] == content


async def test_kb4_degrades_when_segment_belongs_to_another_base(
    client: TestClient, settings: Settings
) -> None:
    """片段映射的 `base_id` 与请求库不符 → 降级 stale 且不返回正文。

    Args:
        client: 测试客户端。
        settings: 配置实例。
    """
    base_a = _create_base(client, settings, visibility="organization", tag="guard-kb4-a")
    base_b = _create_base(client, settings, visibility="organization", tag="guard-kb4-b")
    doc_b = await _create_completed_document(client, settings, base_b, tag="guard-kb4-b-doc")
    content = "第九条 差旅标准。"
    segment_key = build_key(doc_b, 0, content)
    await get_segment_store(settings).upsert(
        SegmentMapRecord(
            tenant_id=TENANT,
            segment_key=segment_key,
            base_id=base_a,  # 故意写错：映射登记在 A 库
            document_id=doc_b,
            segment_index=0,
            content_excerpt=content,
            status="valid",
        )
    )

    body = client.get(
        f"/api/v1/knowledge/bases/{base_b}/documents/{doc_b}/segments/{segment_key}",
        headers=_headers(settings, _READER_ROLES),
    ).json()

    assert body["code"] == 0, body
    assert body["data"]["status"] == "stale"
    assert body["data"]["content"] is None


async def test_kb4_degrades_when_segment_belongs_to_another_document(
    client: TestClient, settings: Settings
) -> None:
    """片段映射的 `document_id` 与请求文档不符 → 降级 stale。

    Args:
        client: 测试客户端。
        settings: 配置实例。
    """
    base_id = _create_base(client, settings, visibility="organization", tag="guard-kb4-doc")
    doc_one = await _create_completed_document(client, settings, base_id, tag="guard-kb4-doc-1")
    doc_two = await _create_completed_document(client, settings, base_id, tag="guard-kb4-doc-2")
    content = "第八条 采购流程。"
    segment_key = build_key(doc_two, 0, content)
    await get_segment_store(settings).upsert(
        SegmentMapRecord(
            tenant_id=TENANT,
            segment_key=segment_key,
            base_id=base_id,
            document_id=doc_two,  # 映射属于 doc_two
            segment_index=0,
            content_excerpt=content,
            status="valid",
        )
    )

    body = client.get(
        f"/api/v1/knowledge/bases/{base_id}/documents/{doc_one}/segments/{segment_key}",
        headers=_headers(settings, _READER_ROLES),
    ).json()

    assert body["code"] == 0, body
    assert body["data"]["status"] == "stale"


@pytest.mark.parametrize("stale_at", [None, "", "not-a-timestamp"])
async def test_kb4_returns_old_content_when_retention_cannot_be_judged(
    client: TestClient, settings: Settings, stale_at: str | None
) -> None:
    """`stale_at` 缺失或不可解析 → 视为仍在保留期：返回旧正文 + `status=stale`。

    判据：KB4 的 stale 是**成功响应**（HTTP 200 + `code=0`），正文是否可看重在
    「保留期」而不是「时间戳能否解析」——解析不了就按保留期内处理（宁多显示不误删）。

    注入方式：`SegmentStore.upsert()` 会强制把行刷成 `valid`（写入语义），
    历史 stale 行只能从读取侧构造，故这里用存储包装改写 `status` / `stale_at`。

    Args:
        client: 测试客户端。
        settings: 配置实例。
        stale_at: 注入的 stale 时间戳（None / 空串 / 非法格式）。
    """
    base_id = _create_base(client, settings, visibility="organization", tag="guard-kb4-ret")
    document_id = await _create_completed_document(
        client, settings, base_id, tag="guard-kb4-ret-doc"
    )
    content = "第七条 用印审批。"
    segment_key = build_key(document_id, 0, content)
    await get_segment_store(settings).upsert(
        SegmentMapRecord(
            tenant_id=TENANT,
            segment_key=segment_key,
            base_id=base_id,
            document_id=document_id,
            segment_index=0,
            content_excerpt=content,
        )
    )
    service = SegmentService(
        settings,
        store=_StatusOverrideStore(
            get_segment_store(settings), status=STATUS_STALE, stale_at=stale_at
        ),
    )

    detail = await service.get_detail(
        base_id=base_id,
        document_id=document_id,
        segment_key=segment_key,
        with_context=False,
        auth=_auth(),
    )

    assert detail.status is SegmentStatus.STALE
    assert detail.content == content


async def test_kb4_deleted_segment_hides_content(client: TestClient, settings: Settings) -> None:
    """映射已随文档删除 → `status=stale` 且正文为 None（不是错误码）。

    Args:
        client: 测试客户端。
        settings: 配置实例。
    """
    base_id = _create_base(client, settings, visibility="organization", tag="guard-kb4-del")
    document_id = await _create_completed_document(
        client, settings, base_id, tag="guard-kb4-del-doc"
    )
    content = "第六条 合同归档。"
    segment_key = build_key(document_id, 0, content)
    await get_segment_store(settings).upsert(
        SegmentMapRecord(
            tenant_id=TENANT,
            segment_key=segment_key,
            base_id=base_id,
            document_id=document_id,
            segment_index=0,
            content_excerpt=content,
        )
    )
    service = SegmentService(
        settings,
        store=_StatusOverrideStore(
            get_segment_store(settings), status=STATUS_DELETED, stale_at=None
        ),
    )

    detail = await service.get_detail(
        base_id=base_id,
        document_id=document_id,
        segment_key=segment_key,
        with_context=False,
        auth=_auth(),
    )

    assert detail.status is SegmentStatus.STALE
    assert detail.content is None


# ===========================================================================
# 4. M1 对话：静默跳过对两种写法都成立
# ===========================================================================
def test_m1_silently_skips_unauthorized_base_in_native_form(
    client: TestClient, settings: Settings
) -> None:
    """对话携带未授权库（原生写法）→ `code=0` 且不产生引用（静默跳过）。

    Args:
        client: 测试客户端。
        settings: 配置实例。
    """
    base_id = _create_base(client, settings, tag="guard-m1")

    body = client.post(
        "/api/v1/model/generate",
        json={
            "conversation_id": "conv_guard",
            "profile": "general",
            "messages": [{"role": "user", "content": "薪酬制度是什么"}],
            "knowledge_base_ids": [base_id.removeprefix("kb_")],
        },
        headers={
            **_headers(settings, _READER_ROLES, user_id=MEMBER),
            "Idempotency-Key": _key("guard-m1-generate"),
        },
    ).json()

    assert body["code"] == 0, body
    assert body["data"]["citations"] == []


# ===========================================================================
# 5. KB6 / KB7 授权计数（P2-02）
# ===========================================================================
def test_kb6_intra_batch_duplicates_count_as_skipped(
    client: TestClient, settings: Settings
) -> None:
    """同批 `subject_ids` 内重复 → `created=1 / skipped=1`（架构 §4.2 要点）。

    Args:
        client: 测试客户端。
        settings: 配置实例。
    """
    base_id = _create_base(client, settings, tag="guard-kb6-dup")

    body = _grant(client, settings, base_id, ["usr_c", "usr_c"], tag="guard-kb6-dup-g")

    assert body["code"] == 0, body
    assert body["data"] == {"created_count": 1, "skipped_count": 1}, body


def test_kb6_cross_batch_duplicates_count_as_skipped(
    client: TestClient, settings: Settings
) -> None:
    """跨批次重复 → 第二次 `created=0 / skipped=1`（幂等不报错）。

    Args:
        client: 测试客户端。
        settings: 配置实例。
    """
    base_id = _create_base(client, settings, tag="guard-kb6-twice")

    first = _grant(client, settings, base_id, ["usr_b"], tag="guard-kb6-twice-1")
    second = _grant(client, settings, base_id, ["usr_b"], tag="guard-kb6-twice-2")

    assert first["data"] == {"created_count": 1, "skipped_count": 0}, first
    assert second["data"] == {"created_count": 0, "skipped_count": 1}, second


def test_kb6_rejects_more_than_fifty_subjects(client: TestClient, settings: Settings) -> None:
    """单次最多 50 个对象（schema 层直接 `20001`）。

    Args:
        client: 测试客户端。
        settings: 配置实例。
    """
    base_id = _create_base(client, settings, tag="guard-kb6-many")

    body = _grant(
        client,
        settings,
        base_id,
        [f"usr_{index}" for index in range(51)],
        tag="guard-kb6-many-g",
    )

    assert body["code"] == 20001, body


def test_kb6_guest_cannot_manage_grants(client: TestClient, settings: Settings) -> None:
    """访客即使持有授权管理权限点也不能改授权（`20003`）。

    Args:
        client: 测试客户端。
        settings: 配置实例。
    """
    base_id = _create_base(client, settings, tag="guard-kb6-guest")

    body = client.post(
        f"/api/v1/knowledge/bases/{base_id}/grants",
        json={"subject_type": "user", "subject_ids": ["usr_x"], "permission": "viewer"},
        headers={
            **_headers(settings, [KB_GRANT, "guest"], user_id="usr_guest"),
            "Idempotency-Key": _key("guard-kb6-guest-g"),
        },
    ).json()

    assert body["code"] == 20003, body


def test_kb7_unknown_grant_returns_20004(client: TestClient, settings: Settings) -> None:
    """撤销不存在的授权 → `20004`（不误报成功）。

    Args:
        client: 测试客户端。
        settings: 配置实例。
    """
    base_id = _create_base(client, settings, tag="guard-kb7")

    body = client.delete(
        f"/api/v1/knowledge/bases/{base_id}/grants/grnt_guard_missing",
        headers={**_headers(settings), "Idempotency-Key": _key("guard-kb7-d")},
    ).json()

    assert body["code"] == 20004, body


async def test_kb5_falls_back_for_unknown_stored_subject_type(
    client: TestClient, settings: Settings
) -> None:
    """存储层出现未知的对象类型 / 级别时展示兜底值（不 500、不泄露原值）。

    Args:
        client: 测试客户端。
        settings: 配置实例。
    """
    base_id = _create_base(client, settings, tag="guard-kb5-fallback")
    await get_grant_store(settings).create(
        GrantRecord(
            tenant_id=TENANT,
            grant_id="grnt_guard_unknown",
            base_id=base_id,
            subject_type="legacy_group",
            subject_id="grp_legacy",
            permission="owner_level",
            granted_by=OWNER,
        )
    )

    body = client.get(
        f"/api/v1/knowledge/bases/{base_id}/grants", headers=_headers(settings)
    ).json()

    assert body["code"] == 0, body
    item = body["data"]["items"][0]
    assert item["subject_type"] == GrantSubjectType.USER.value
    assert item["permission"] == GrantPermission.VIEWER.value
    assert item["subject_name"] == "成员"


# ===========================================================================
# 6. 服务层深防御分支（schema 已拦住，服务层仍要自己拦）
# ===========================================================================
async def test_grant_service_rejects_oversized_batch(settings: Settings) -> None:
    """服务层二次校验 50 个上限（绕过 schema 直调用也不放过）。

    Args:
        settings: 配置实例。
    """
    base_id = await _seed_base(settings, "kb_guard_oversize")
    request = CreateGrantRequest.model_construct(
        subject_type=GrantSubjectType.USER,
        subject_ids=[f"usr_{index}" for index in range(51)],
        permission=GrantPermission.VIEWER,
    )

    with pytest.raises(ApiError) as error:
        await GrantService(settings).create_grants(base_id, request, _auth())

    assert int(error.value.code) == 20001


async def test_create_base_rejects_oversized_metadata(settings: Settings) -> None:
    """名称 / 描述超长由服务层拒绝（`20001`），不写库。

    Args:
        settings: 配置实例。
    """
    service = KnowledgeBaseService(settings)

    with pytest.raises(ApiError) as name_error:
        await service.create(_base_request(name="名" * 129), _auth())
    assert int(name_error.value.code) == 20001

    with pytest.raises(ApiError) as desc_error:
        await service.create(_base_request(description="述" * 1025), _auth())
    assert int(desc_error.value.code) == 20001


async def test_update_base_rejects_oversized_metadata(settings: Settings) -> None:
    """KB1 的名称 / 描述超长同样由服务层拒绝。

    Args:
        settings: 配置实例。
    """
    base_id = await _seed_base(settings, "kb_guard_update_oversize")
    service = KnowledgeBaseService(settings)

    with pytest.raises(ApiError) as name_error:
        await service.update(
            base_id,
            UpdateBaseRequest.model_construct(
                name="名" * 129, description=None, chunk_policy=None, visibility=None
            ),
            _auth(),
        )
    assert int(name_error.value.code) == 20001

    with pytest.raises(ApiError) as desc_error:
        await service.update(
            base_id,
            UpdateBaseRequest.model_construct(
                name=None, description="述" * 1025, chunk_policy=None, visibility=None
            ),
            _auth(),
        )
    assert int(desc_error.value.code) == 20001


def test_kb1_updates_description_only(client: TestClient, settings: Settings) -> None:
    """只改描述：其余字段保持原值（部分更新语义）。

    Args:
        client: 测试客户端。
        settings: 配置实例。
    """
    base_id = _create_base(client, settings, tag="guard-kb1-desc")

    body = client.patch(
        f"/api/v1/knowledge/bases/{base_id}",
        json={"description": "新的制度说明"},
        headers={**_headers(settings), "Idempotency-Key": _key("guard-kb1-desc-patch")},
    ).json()

    assert body["code"] == 0, body
    assert body["data"]["description"] == "新的制度说明"
    assert body["data"]["name"] == "制度库-guard-kb1-desc"
    assert body["data"]["visibility"] == "restricted"


def test_kb1_empty_body_returns_20001(client: TestClient, settings: Settings) -> None:
    """一个字段都没给 → `20001`（避免「看起来成功但没改任何东西」）。

    Args:
        client: 测试客户端。
        settings: 配置实例。
    """
    base_id = _create_base(client, settings, tag="guard-kb1-empty")

    body = client.patch(
        f"/api/v1/knowledge/bases/{base_id}",
        json={},
        headers={**_headers(settings), "Idempotency-Key": _key("guard-kb1-empty-patch")},
    ).json()

    assert body["code"] == 20001, body


async def test_list_bases_normalizes_unknown_stored_enum_values(settings: Settings) -> None:
    """存储层出现未知可见范围 / 切分档位时按默认值展示（不 500、不外泄原值）。

    Args:
        settings: 配置实例。
    """
    await get_knowledge_base_store(settings).create(
        KnowledgeBaseRecord(
            tenant_id=TENANT,
            base_id="kb_guard_drift",
            name="口径漂移库",
            visibility="legacy_scope",
            chunk_policy="legacy_policy",
            owner_user_id=OWNER,
        )
    )

    page = await KnowledgeBaseService(settings).list(
        ListBasesQuery(page=1, page_size=20, keyword=""), _auth()
    )

    assert [item.base_id for item in page.items] == ["kb_guard_drift"]
    assert page.items[0].visibility is Visibility.ORGANIZATION
    assert page.items[0].chunk_policy is ChunkPolicy.DEFAULT


async def test_knowledge_base_service_exposes_policy_and_documents(settings: Settings) -> None:
    """服务层属性与同步可见性判定（`my_role` / 删除级联统计的依赖）。

    Args:
        settings: 配置实例。
    """
    service = KnowledgeBaseService(settings)
    record = KnowledgeBaseRecord(
        tenant_id=TENANT,
        base_id="kb_guard_props",
        name="属性库",
        visibility="organization",
        owner_user_id=OWNER,
    )

    assert service.policy is get_policy_service(settings)
    assert service.documents is not None
    assert service.is_visible(record, _auth()) is True


def test_segment_service_singleton_is_reused(settings: Settings) -> None:
    """片段映射服务是进程单例（回填任务与 KB4 共用同一实例/配置）。

    Args:
        settings: 配置实例。
    """
    first = get_segment_service(settings)
    second = get_segment_service(settings)

    assert first is second


class _StatusOverrideStore:
    """读取时改写 `status` / `stale_at` 的存储包装。

    `SegmentStore.upsert()` 是「写入 / 刷新」语义，落库时强制 `status=valid`；
    要覆盖 KB4 的 stale / deleted 降级路径，只能在读取侧改写历史行。
    """

    def __init__(self, inner: SegmentStore, *, status: str, stale_at: str | None) -> None:
        """初始化包装。

        Args:
            inner: 真实存储。
            status: 读取时强制返回的状态。
            stale_at: 读取时强制返回的 stale 时间戳。
        """
        self._inner = inner
        self._status = status
        self._stale_at = stale_at

    async def get(self, tenant_id: str, segment_key: str) -> SegmentMapRecord | None:
        """读取并改写状态。

        Args:
            tenant_id: 租户 ID。
            segment_key: 片段稳定标识。

        Returns:
            SegmentMapRecord | None: 改写后的映射；不存在返回 None。
        """
        record = await self._inner.get(tenant_id, segment_key)
        if record is None:
            return None
        return replace(record, status=self._status, stale_at=self._stale_at)


def _hit(base_id: str, document_id: str, content: str) -> SegmentHit:
    """构造一条命中记录（省去调用方重复拼字段）。

    Args:
        base_id: 知识库 ID。
        document_id: 文档 ID。
        content: 片段正文。

    Returns:
        SegmentHit: 命中记录。
    """
    return SegmentHit(
        base_id=base_id,
        document_id=document_id,
        segment_index=0,
        content=content,
        position_page=1,
        position_paragraph=1,
    )


def _base_request(*, name: str = "制度库", description: str = "制度") -> CreateBaseRequest:
    """构造 R1 创建请求（绕过 schema 校验的服务层用例专用）。

    Args:
        name: 名称。
        description: 描述。

    Returns:
        CreateBaseRequest: 创建请求。
    """
    return CreateBaseRequest.model_construct(
        name=name,
        description=description,
        visibility=Visibility.ORGANIZATION,
        chunk_policy=ChunkPolicy.DEFAULT,
    )


async def _seed_base(settings: Settings, base_id: str) -> str:
    """直接落库一个知识库（服务层用例不需要走 HTTP + 幂等）。

    Args:
        settings: 配置实例。
        base_id: 知识库 ID。

    Returns:
        str: 知识库 ID。
    """
    await get_knowledge_base_store(settings).create(
        KnowledgeBaseRecord(
            tenant_id=TENANT,
            base_id=base_id,
            name="直建库",
            visibility="restricted",
            owner_user_id=OWNER,
        )
    )
    return base_id
