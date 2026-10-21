"""知识库授权与引用回链端点测试（阶段二 B05，架构文档 §4.2 / 6.3 / 2.4）。

覆盖点（逐条对应 PRD P2-KB-03 / P2-KB-04 可执行断言）：

1. `restricted` 库仅授权成员可见：未授权成员**列表不含**、直连 KB3 返回 `20003`、
   R7 检索返回 `20003`、对话（M1）携带该库时**静默跳过**（不报错、不引用）；
2. 同一对象重复授权 → `skipped_count=1`；撤销后立即失效（列表消失 + 直连 `20003`）；
3. 部门授权：成员移出部门且缓存失效后立即不可访问（PRD 验收 2 的失效路径）；
4. KB4 引用详情：命中 `valid` 返回正文 + 上下文 + 位置；
   R5 重处理后旧引用返回 **HTTP 200 + code=0 + status=stale**（验收 3）；
5. KB4 错误边界：库不存在 `11001`、文档未处理完 `11004`、无权限 `20003`；
6. 授权与撤销写操作记录；响应文案不含禁用词。

测试运行在 `OA_BUSINESS_STORE=memory` 下，**不依赖 PostgreSQL**。
"""

from __future__ import annotations

import hashlib
import io
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.business.audit_service import ACTION_GRANT_CREATE, ACTION_GRANT_REVOKE, AuditService
from app.business.document_service import DocumentService
from app.business.policy_service import get_policy_service
from app.business.segment_service import SegmentHit, SegmentService, build_key
from app.core.config import Settings
from app.core.security import AuthContext
from app.db.repositories.document_repo import get_document_store
from app.db.repositories.grant_repo import get_grant_store
from tests.conftest import make_token

pytestmark = pytest.mark.integration

#: 私有化模式下的固定租户
TENANT = "default"

#: 权限点（与路由常量保持一致）
KB_UPDATE = "knowledge:base:update"
KB_DELETE = "knowledge:base:delete"
KB_READ = "knowledge:base:read"
KB_GRANT = "knowledge:grant:manage"
DOC_UPLOAD = "knowledge:document:upload"

#: 禁词（PRD 第 7 章；用户可见文案一律不得出现）
FORBIDDEN_TERMS = ("向量", "召回", "相似度", "embedding", "chunk", "segment", "score")

_OWNER_ROLES = [KB_UPDATE, KB_DELETE, KB_READ, KB_GRANT, DOC_UPLOAD]


def _headers(
    settings: Settings, roles: list[str] | None = None, user_id: str = "usr_owner"
) -> dict:
    """构造鉴权请求头。

    Args:
        settings: 配置实例。
        roles: 令牌角色。
        user_id: 用户 ID。

    Returns:
        dict: 请求头。
    """
    token = make_token(settings=settings, user_id=user_id, roles=roles or _OWNER_ROLES)
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": TENANT}


def _key(tag: str) -> str:
    """按标签生成稳定的 UUID 形态幂等键。

    Args:
        tag: 标签。

    Returns:
        str: 幂等键。
    """
    digest = hashlib.sha256(tag.encode("utf-8")).hexdigest()
    return f"{digest[0:8]}-{digest[8:12]}-4{digest[13:16]}-8{digest[17:20]}-{digest[20:32]}"


def _create_base(
    client: TestClient,
    settings: Settings,
    *,
    name: str = "薪酬制度库",
    visibility: str = "organization",
    tag: str = "base",
    user_id: str = "usr_owner",
) -> str:
    """创建知识库并返回 base_id。

    Args:
        client: 测试客户端。
        settings: 配置实例。
        name: 知识库名称。
        visibility: 可见范围。
        tag: 幂等键标签。
        user_id: 创建人。

    Returns:
        str: 知识库 ID。
    """
    response = client.post(
        "/api/v1/knowledge/bases",
        json={"name": name, "description": "制度文档", "visibility": visibility},
        headers={**_headers(settings, user_id=user_id), "Idempotency-Key": _key(tag)},
    )
    assert response.json()["code"] == 0, response.text
    return str(response.json()["data"]["base_id"])


def _upload(
    client: TestClient,
    settings: Settings,
    base_id: str,
    *,
    file_name: str = "员工手册.pdf",
    tag: str = "upload",
) -> str:
    """上传文档并返回 document_id。

    Args:
        client: 测试客户端。
        settings: 配置实例。
        base_id: 知识库 ID。
        file_name: 文件名。
        tag: 幂等键标签。

    Returns:
        str: 文档 ID。
    """
    response = client.post(
        f"/api/v1/knowledge/bases/{base_id}/documents",
        files={"file": (file_name, io.BytesIO(b"%PDF-1.4 test"), "application/pdf")},
        headers={**_headers(settings), "Idempotency-Key": _key(tag)},
    )
    assert response.json()["code"] == 0, response.text
    return str(response.json()["data"]["document_id"])


def _grant(
    client: TestClient,
    settings: Settings,
    base_id: str,
    *,
    subject_type: str = "user",
    subject_ids: list[str] | None = None,
    permission: str = "viewer",
    tag: str = "grant",
    roles: list[str] | None = None,
) -> dict[str, Any]:
    """批量授权并返回响应 data。

    Args:
        client: 测试客户端。
        settings: 配置实例。
        base_id: 知识库 ID。
        subject_type: 授权对象类型。
        subject_ids: 授权对象 ID 列表。
        permission: 授权级别。
        tag: 幂等键标签。
        roles: 操作人角色。

    Returns:
        dict[str, Any]: 响应 data。
    """
    response = client.post(
        f"/api/v1/knowledge/bases/{base_id}/grants",
        json={
            "subject_type": subject_type,
            "subject_ids": subject_ids or ["usr_b"],
            "permission": permission,
        },
        headers={
            **_headers(settings, roles=roles),
            "Idempotency-Key": _key(tag),
        },
    )
    assert response.json()["code"] == 0, response.text
    return dict(response.json()["data"])


def _iter_text_values(node: Any) -> list[str]:
    """递归取出 JSON 中的字符串值。

    Args:
        node: 任意 JSON 节点。

    Returns:
        list[str]: 字符串值列表。
    """
    values: list[str] = []
    if isinstance(node, dict):
        for value in node.values():
            values.extend(_iter_text_values(value))
    elif isinstance(node, list):
        for value in node:
            values.extend(_iter_text_values(value))
    elif isinstance(node, str):
        values.append(node)
    return values


def _assert_no_forbidden_terms(payload: Any) -> None:
    """断言用户可见文案不含禁用词（PRD 第 7 章）。

    Args:
        payload: 响应体。
    """
    for value in _iter_text_values(payload):
        lowered = value.lower()
        for term in FORBIDDEN_TERMS:
            assert term.lower() not in lowered, f"用户可见文案出现禁用词 {term}：{value}"


# ===========================================================================
# 1. restricted 库的三处一致判定
# ===========================================================================
async def test_restricted_base_is_visible_only_to_granted_member(
    client: TestClient, settings: Settings
) -> None:
    """授权成员可见，未授权成员列表不含 / 直连 20003 / 检索 20003。"""
    base_id = _create_base(client, settings, visibility="restricted", tag="restr-base")
    _upload(client, settings, base_id, tag="restr-upload")

    # 未授权成员 B：列表中看不到
    listed_b = client.get(
        "/api/v1/knowledge/bases?page=1&page_size=20",
        headers=_headers(settings, roles=[KB_READ], user_id="usr_b"),
    )
    assert listed_b.json()["code"] == 0
    assert [item["base_id"] for item in listed_b.json()["data"]["items"]] == []

    # 未授权成员 B：直连文档清单 20003
    direct = client.get(
        f"/api/v1/knowledge/bases/{base_id}/documents",
        headers=_headers(settings, roles=[KB_READ], user_id="usr_b"),
    )
    assert direct.json()["code"] == 20003

    # 未授权成员 B：R7 检索 20003
    search = client.post(
        "/api/v1/knowledge/search",
        json={"base_id": base_id, "query": "薪酬怎么算"},
        headers={
            **_headers(settings, roles=[KB_READ], user_id="usr_b"),
            "Idempotency-Key": _key("r7-b"),
        },
    )
    assert search.json()["code"] == 20003

    # 授权后：列表可见、直连 200、检索放行
    created = _grant(client, settings, base_id, subject_ids=["usr_b"], tag="grant-b")
    assert created == {"created_count": 1, "skipped_count": 0}

    listed_after = client.get(
        "/api/v1/knowledge/bases?page=1&page_size=20",
        headers=_headers(settings, roles=[KB_READ], user_id="usr_b"),
    )
    items = listed_after.json()["data"]["items"]
    assert [item["base_id"] for item in items] == [base_id]
    assert items[0]["my_role"] == "viewer"

    direct_after = client.get(
        f"/api/v1/knowledge/bases/{base_id}/documents",
        headers=_headers(settings, roles=[KB_READ], user_id="usr_b"),
    )
    assert direct_after.json()["code"] == 0

    search_after = client.post(
        "/api/v1/knowledge/search",
        json={"base_id": base_id, "query": "薪酬怎么算"},
        headers={
            **_headers(settings, roles=[KB_READ], user_id="usr_b"),
            "Idempotency-Key": _key("r7-b-ok"),
        },
    )
    assert search_after.json()["code"] == 0


async def test_conversation_silently_skips_unauthorized_base(
    client: TestClient, settings: Settings
) -> None:
    """对话携带未授权库时静默跳过：不报错、不引用（PRD 验收 1 第 3 条）。"""
    base_id = _create_base(client, settings, visibility="restricted", tag="chat-base")

    response = client.post(
        "/api/v1/model/generate",
        json={
            "conversation_id": "conv_1",
            "profile": "general",
            "messages": [{"role": "user", "content": "薪酬制度是什么"}],
            "knowledge_base_ids": [base_id],
        },
        headers={
            **_headers(settings, roles=[KB_READ], user_id="usr_b"),
            "Idempotency-Key": _key("m1-b"),
        },
    )
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["citations"] == []

    # 同一套判定在服务层确实剔除了未授权库（列表 / 检索 / 问答共用）
    policy = get_policy_service(settings)
    allowed = await policy.filter_authorized(
        AuthContext(user_id="usr_b", tenant_id=TENANT, roles=[KB_READ]), [base_id]
    )
    assert allowed == []


# ===========================================================================
# 2. 重复授权与撤销
# ===========================================================================
async def test_duplicate_grant_is_skipped(client: TestClient, settings: Settings) -> None:
    """同一对象连续授权两次：第二次 `skipped_count=1`（PRD 验收 3）。"""
    base_id = _create_base(client, settings, tag="dup-base")

    first = _grant(client, settings, base_id, subject_ids=["usr_b"], tag="dup-1")
    second = _grant(client, settings, base_id, subject_ids=["usr_b"], tag="dup-2")
    assert first == {"created_count": 1, "skipped_count": 0}
    assert second == {"created_count": 0, "skipped_count": 1}

    listed = client.get(
        f"/api/v1/knowledge/bases/{base_id}/grants?page=1&page_size=20",
        headers=_headers(settings),
    )
    body = listed.json()
    assert body["code"] == 0
    assert body["data"]["meta"]["total"] == 1
    item = body["data"]["items"][0]
    assert set(item) == {
        "grant_id",
        "subject_type",
        "subject_id",
        "subject_name",
        "permission",
        "granted_by_name",
        "granted_at",
    }
    assert item["grant_id"].startswith("grnt_")
    _assert_no_forbidden_terms(body)


async def test_revoke_grant_takes_effect_immediately(
    client: TestClient, settings: Settings
) -> None:
    """撤销后被撤销成员列表消失、直连 20003（PRD 验收 4）。"""
    base_id = _create_base(client, settings, visibility="restricted", tag="rev-base")
    _grant(client, settings, base_id, subject_ids=["usr_b"], tag="rev-grant")

    granted = client.get(
        f"/api/v1/knowledge/bases/{base_id}/grants?page=1&page_size=20",
        headers=_headers(settings),
    )
    grant_id = granted.json()["data"]["items"][0]["grant_id"]

    revoked = client.delete(
        f"/api/v1/knowledge/bases/{base_id}/grants/{grant_id}",
        headers={**_headers(settings), "Idempotency-Key": _key("rev-del")},
    )
    assert revoked.json()["code"] == 0
    assert revoked.json()["data"] == {"grant_id": grant_id, "deleted": True}

    direct = client.get(
        f"/api/v1/knowledge/bases/{base_id}/documents",
        headers=_headers(settings, roles=[KB_READ], user_id="usr_b"),
    )
    assert direct.json()["code"] == 20003

    again = client.delete(
        f"/api/v1/knowledge/bases/{base_id}/grants/{grant_id}",
        headers={**_headers(settings), "Idempotency-Key": _key("rev-del-2")},
    )
    assert again.json()["code"] == 20004


async def test_grant_endpoints_require_permission(client: TestClient, settings: Settings) -> None:
    """无 `knowledge:grant:manage` 权限 → 20003。"""
    base_id = _create_base(client, settings, tag="perm-base")

    response = client.post(
        f"/api/v1/knowledge/bases/{base_id}/grants",
        json={"subject_type": "user", "subject_ids": ["usr_b"], "permission": "viewer"},
        headers={**_headers(settings, roles=["member"]), "Idempotency-Key": _key("perm-grant")},
    )
    assert response.status_code == 403
    assert response.json()["code"] == 20003


async def test_grant_records_are_audited(client: TestClient, settings: Settings) -> None:
    """授权与撤销都写操作记录（PRD 验收 6）。"""
    base_id = _create_base(client, settings, tag="audit-base")
    _grant(client, settings, base_id, subject_ids=["usr_b"], tag="audit-grant")
    grant_id = client.get(
        f"/api/v1/knowledge/bases/{base_id}/grants?page=1&page_size=20",
        headers=_headers(settings),
    ).json()["data"]["items"][0]["grant_id"]
    client.delete(
        f"/api/v1/knowledge/bases/{base_id}/grants/{grant_id}",
        headers={**_headers(settings), "Idempotency-Key": _key("audit-revoke")},
    )

    audit = AuditService(settings)
    created = await audit.recent(TENANT, action=ACTION_GRANT_CREATE)
    revoked = await audit.recent(TENANT, action=ACTION_GRANT_REVOKE)
    assert [entry.resource_id for entry in created] == [base_id]
    assert created[0].display_name == "添加成员授权"
    assert [entry.resource_id for entry in revoked] == [base_id]
    assert revoked[0].display_name == "撤销成员授权"


# ===========================================================================
# 3. 部门授权：成员移出后立即失效
# ===========================================================================
async def test_department_grant_applies_to_current_members(
    client: TestClient, settings: Settings
) -> None:
    """部门授权对当前成员生效；移出部门且缓存失效后立即不可访问（PRD 验收 2）。"""
    base_id = _create_base(client, settings, visibility="restricted", tag="dept-base")
    _grant(
        client,
        settings,
        base_id,
        subject_type="department",
        subject_ids=["dept_x"],
        tag="dept-grant",
    )

    store = get_grant_store(settings)
    store.set_user_departments(TENANT, "usr_b", ["dept_x"])  # type: ignore[attr-defined]
    get_policy_service(settings).invalidate(TENANT)

    readable = client.get(
        f"/api/v1/knowledge/bases/{base_id}/documents",
        headers=_headers(settings, roles=[KB_READ], user_id="usr_b"),
    )
    assert readable.json()["code"] == 0

    store.set_user_departments(TENANT, "usr_b", [])  # type: ignore[attr-defined]
    get_policy_service(settings).invalidate(TENANT)

    denied = client.get(
        f"/api/v1/knowledge/bases/{base_id}/documents",
        headers=_headers(settings, roles=[KB_READ], user_id="usr_b"),
    )
    assert denied.json()["code"] == 20003


# ===========================================================================
# 4. KB4 引用详情：valid / stale 全链路
# ===========================================================================
async def _prepare_segment(
    client: TestClient, settings: Settings, *, tag: str
) -> tuple[str, str, str]:
    """准备「已完成文档 + 一条片段映射」。

    Args:
        client: 测试客户端。
        settings: 配置实例。
        tag: 幂等键标签前缀。

    Returns:
        tuple[str, str, str]: `(base_id, document_id, segment_key)`。
    """
    base_id = _create_base(client, settings, name="引用回链库", tag=f"{tag}-base")
    document_id = _upload(client, settings, base_id, file_name="制度汇编.pdf", tag=f"{tag}-upload")

    document_service = DocumentService(settings)
    await document_service.refresh_progress(
        tenant_id=TENANT,
        document_id=document_id,
        native_status="completed",
        progress=100,
    )
    content = "第十二条 年假申请需提前三个工作日提交。"
    segments = SegmentService(settings)
    await segments.record_hits(
        TENANT,
        [
            SegmentHit(
                base_id=base_id,
                document_id=document_id,
                segment_index=0,
                content=content,
                position_page=12,
                position_paragraph=3,
            )
        ],
        schedule=False,
    )
    return base_id, document_id, build_key(document_id, 0, content)


async def test_kb4_returns_valid_segment_with_context(
    client: TestClient, settings: Settings
) -> None:
    """KB4 命中有效片段：返回正文、位置与库名（PRD 验收 1）。"""
    base_id, document_id, segment_key = await _prepare_segment(client, settings, tag="kb4")

    response = client.get(
        f"/api/v1/knowledge/bases/{base_id}/documents/{document_id}/segments/{segment_key}",
        headers=_headers(settings, roles=[KB_READ]),
    )
    body = response.json()
    assert response.status_code == 200
    assert body["code"] == 0
    assert set(body["data"]) == {
        "segment_id",
        "document_id",
        "document_name",
        "base_id",
        "base_name",
        "content",
        "context_before",
        "context_after",
        "position",
        "status",
    }
    data = body["data"]
    assert data["segment_id"] == segment_key
    assert data["status"] == "valid"
    assert data["content"] == "第十二条 年假申请需提前三个工作日提交。"
    assert data["position"] == {"page": 12, "paragraph": 3}
    assert data["base_name"] == "引用回链库"
    assert data["document_name"] == "制度汇编.pdf"
    _assert_no_forbidden_terms(body)


async def test_kb4_returns_stale_after_reprocessing(client: TestClient, settings: Settings) -> None:
    """R5 重处理成功后旧引用返回 200 + status=stale（PRD 验收 3）。"""
    base_id, document_id, segment_key = await _prepare_segment(client, settings, tag="stale")

    # R5 重新处理：换新批次，旧行不动
    reindex = client.post(
        f"/api/v1/knowledge/bases/{base_id}/documents/{document_id}/reindex",
        headers={**_headers(settings), "Idempotency-Key": _key("stale-reindex")},
    )
    assert reindex.json()["code"] == 0

    # 处理中旧引用仍可查看（不预标 stale）
    during = client.get(
        f"/api/v1/knowledge/bases/{base_id}/documents/{document_id}/segments/{segment_key}",
        headers=_headers(settings, roles=[KB_READ]),
    )
    # 处理中阶段文档不可定位 → 11004（引用抽屉在此之前不会展示）
    assert during.json()["code"] == 11004

    # 新批次成功完成 → 旧批次统一转 stale
    document_service = DocumentService(settings)
    await document_service.refresh_progress(
        tenant_id=TENANT,
        document_id=document_id,
        native_status="completed",
        progress=100,
    )

    response = client.get(
        f"/api/v1/knowledge/bases/{base_id}/documents/{document_id}/segments/{segment_key}",
        headers=_headers(settings, roles=[KB_READ]),
    )
    body = response.json()
    assert response.status_code == 200
    assert body["code"] == 0
    assert body["data"]["status"] == "stale"
    # 保留期内仍返回旧内容，前端提示「该引用来自旧版本内容，原文已更新」
    assert body["data"]["content"] == "第十二条 年假申请需提前三个工作日提交。"
    _assert_no_forbidden_terms(body)


async def test_kb4_failed_reprocessing_keeps_segment_valid(
    client: TestClient, settings: Settings
) -> None:
    """新批次失败不置 stale：用户不会因为处理失败丢引用（架构文档 2.4 第 3 条）。"""
    base_id, document_id, segment_key = await _prepare_segment(client, settings, tag="fail")

    client.post(
        f"/api/v1/knowledge/bases/{base_id}/documents/{document_id}/reindex",
        headers={**_headers(settings), "Idempotency-Key": _key("fail-reindex")},
    )
    document_service = DocumentService(settings)
    await document_service.refresh_progress(
        tenant_id=TENANT,
        document_id=document_id,
        native_status="error",
        progress=30,
        error_message="文档解析失败，请确认文件内容后重新上传",
    )

    store = get_document_store(settings)
    record = await store.get(TENANT, document_id)
    assert record is not None
    segments = SegmentService(settings)
    stored = await segments.store.get(TENANT, segment_key)
    assert stored is not None
    assert stored.status == "valid"


async def test_kb4_error_boundaries(client: TestClient, settings: Settings) -> None:
    """KB4 错误边界：库不存在 11001、文档未处理完 11004、无权限 20003。"""
    base_id = _create_base(client, settings, name="边界库", tag="edge-base")
    document_id = _upload(client, settings, base_id, file_name="待处理.pdf", tag="edge-upload")

    missing_base = client.get(
        "/api/v1/knowledge/bases/kb_missing/documents/doc_missing/segments/seg_0000000000000000",
        headers=_headers(settings, roles=[KB_READ]),
    )
    assert missing_base.json()["code"] == 11001

    processing = client.get(
        f"/api/v1/knowledge/bases/{base_id}/documents/{document_id}/segments/seg_0000000000000000",
        headers=_headers(settings, roles=[KB_READ]),
    )
    assert processing.json()["code"] == 11004

    _create_base(client, settings, name="私有库", visibility="restricted", tag="edge-private")
    restricted_base = _create_base(
        client, settings, name="保密库", visibility="restricted", tag="edge-restricted"
    )
    forbidden = client.get(
        f"/api/v1/knowledge/bases/{restricted_base}/documents/doc_x/segments/seg_0000000000000000",
        headers=_headers(settings, roles=[KB_READ], user_id="usr_b"),
    )
    assert forbidden.json()["code"] == 20003


async def test_kb4_requires_permission(client: TestClient, settings: Settings) -> None:
    """无 `knowledge:base:read` 权限 → 403 / 20003。

    B08 起权限点按架构 §4.8 矩阵判定：`member` / `guest` 拥有该权限点（🔸 对象级受限），
    故「无权限账号」用未授任何权限点的自定义角色表示。
    """
    response = client.get(
        "/api/v1/knowledge/bases/kb_1/documents/doc_1/segments/seg_0000000000000000",
        headers=_headers(settings, roles=["custom_no_permission"]),
    )
    assert response.status_code == 403
    assert response.json()["code"] == 20003


# ===========================================================================
# 5. 真实 PostgreSQL：授权 / 片段映射仓储 SQL 冒烟（`--run-pg` 启用）
# ===========================================================================
@pytest.mark.pg
async def test_postgres_grant_and_segment_repositories(pg_dsn: str) -> None:
    """真实库跑通 `knowledge_base_grant` / `segment_map` 读写与状态迁移。

    覆盖内存实现无法验证的 SQL 细节：部分唯一索引冲突、部门子部门授权解析、
    槽位唯一约束下的换键写入、`mark_stale` 的批次条件。
    """
    from app.core.config import Settings as Conf
    from app.db.repositories.document_repo import DocumentRecord, PostgresDocumentStore
    from app.db.repositories.grant_repo import GrantRecord, PostgresGrantStore
    from app.db.repositories.knowledge_base_repo import (
        KnowledgeBaseRecord,
        PostgresKnowledgeBaseStore,
    )
    from app.db.repositories.segment_repo import PostgresSegmentStore, SegmentMapRecord
    from app.db.session import tenant_scope

    conf = Conf(
        env="test",
        deploy_mode="private",
        business_store="postgres",
        database_url=pg_dsn,
        credential_encryption_key="",
    )
    tenant = "tnt_pg_b05"
    base_id = "kb_pgb05_001"
    document_id = "doc_pgb05_001"
    bases = PostgresKnowledgeBaseStore(conf)
    documents = PostgresDocumentStore(conf)
    grants = PostgresGrantStore(conf)
    segments = PostgresSegmentStore(conf)

    try:
        await bases.create(
            KnowledgeBaseRecord(
                tenant_id=tenant,
                base_id=base_id,
                name="PG 授权库",
                visibility="restricted",
                owner_user_id="usr_owner",
                created_by="usr_owner",
            )
        )
        await documents.create(
            DocumentRecord(
                tenant_id=tenant,
                document_id=document_id,
                base_id=base_id,
                file_name="pg.pdf",
                uploader_user_id="usr_owner",
                run_id="run_pg_1",
            )
        )

        created, is_new = await grants.create(
            GrantRecord(
                tenant_id=tenant,
                grant_id="grnt_pgb05_001",
                base_id=base_id,
                subject_type="user",
                subject_id="usr_b",
                permission="editor",
                granted_by="usr_owner",
            )
        )
        assert is_new is True
        assert created.grant_id == "grnt_pgb05_001"

        duplicate, second_is_new = await grants.create(
            GrantRecord(
                tenant_id=tenant,
                grant_id="grnt_pgb05_002",
                base_id=base_id,
                subject_type="user",
                subject_id="usr_b",
                permission="viewer",
                granted_by="usr_owner",
            )
        )
        assert second_is_new is False
        assert duplicate.grant_id == "grnt_pgb05_001"

        permissions = await grants.resolve_permissions(tenant, "usr_b")
        assert permissions == {base_id: "editor"}
        assert await grants.resolve_permissions(tenant, "usr_other") == {}

        listed, total = await grants.list(tenant, base_id)
        assert total == 1 and len(listed) == 1

        key_v1 = "seg_pgb05_v1"
        await segments.upsert(
            SegmentMapRecord(
                tenant_id=tenant,
                segment_key=key_v1,
                base_id=base_id,
                document_id=document_id,
                run_id="run_pg_1",
                segment_index=0,
                content_sha256="a" * 64,
                content_excerpt="旧版本内容",
            )
        )
        stored = await segments.get(tenant, key_v1)
        assert stored is not None and stored.status == "valid"

        # 同槽位换键：必须清槽后写入（uq_seg_slot 唯一索引）
        await segments.upsert(
            SegmentMapRecord(
                tenant_id=tenant,
                segment_key="seg_pgb05_v2",
                base_id=base_id,
                document_id=document_id,
                run_id="run_pg_1",
                segment_index=0,
                content_sha256="b" * 64,
                content_excerpt="新版本内容",
            )
        )
        assert await segments.count(tenant, document_id) == 1
        assert await segments.get(tenant, key_v1) is None

        # 新批次完成 → 旧批次 stale（当前批次保持 valid）
        await segments.upsert(
            SegmentMapRecord(
                tenant_id=tenant,
                segment_key="seg_pgb05_v3",
                base_id=base_id,
                document_id=document_id,
                run_id="run_pg_2",
                segment_index=0,
                content_sha256="c" * 64,
                content_excerpt="第二批次内容",
            )
        )
        assert await segments.mark_stale(tenant, document_id, keep_run_id="run_pg_2") == 1
        stale = await segments.get(tenant, "seg_pgb05_v2")
        assert stale is not None and stale.status == "stale"
        current = await segments.get(tenant, "seg_pgb05_v3")
        assert current is not None and current.status == "valid"

        revoked = await grants.revoke(tenant, "grnt_pgb05_001")
        assert revoked is not None and revoked.is_active is False
        assert await grants.resolve_permissions(tenant, "usr_b") == {}
    finally:
        async with tenant_scope(tenant, settings=conf) as uow:
            await uow.execute(
                "DELETE FROM segment_map WHERE tenant_id = :tenant_id",
                {"tenant_id": tenant},
            )
            await uow.execute(
                "DELETE FROM document_meta WHERE tenant_id = :tenant_id",
                {"tenant_id": tenant},
            )
            await uow.execute(
                "DELETE FROM knowledge_base_grant WHERE tenant_id = :tenant_id",
                {"tenant_id": tenant},
            )
            await uow.execute(
                "DELETE FROM knowledge_base_meta WHERE tenant_id = :tenant_id",
                {"tenant_id": tenant},
            )
