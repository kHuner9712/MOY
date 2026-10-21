"""KB1 / KB2 / KB3 端点契约测试（阶段二 B04，架构文档 §4.2）。

覆盖点（逐条对应 PRD P2-KB-01 / P2-KB-02 可执行断言）：

1. `PATCH /api/v1/knowledge/bases/{base_id}`：改名 / 改精细度 / 改可见范围；
   空名称 `20001`；无字段 `20001`；库不存在 `11001`；无权限 `20003`；
   同键异体 `20005`；同键同体 `20007`。
2. `DELETE /api/v1/knowledge/bases/{base_id}`：软删除，返回
   `{base_id, deleted, deleted_document_count}`；删除后列表不可见、
   `GET .../documents` 返回 `11001`、操作记录有「删除知识库」条目；
   成员删他人库 `20003`。
3. `GET /api/v1/knowledge/bases/{base_id}/documents`：分页 + 关键字 + 阶段筛选 +
   排序；5000 条文档下第 250 页响应体 < 200KB；库已删除 `11001`。
4. 库级 `chunk_policy` 落库后，上传不带该字段时按库级处理（PRD 验收 1）。
5. 响应体与日志中**不出现**禁用词（PRD 第 7 章）。

测试运行在 `OA_BUSINESS_STORE=memory` 下，**不依赖 PostgreSQL**。
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.business.audit_service import ACTION_KB_DELETE, AuditService
from app.business.document_service import DocumentService
from app.core.config import Settings
from app.core.security import AuthContext
from app.db.repositories.document_repo import DocumentRecord
from app.schemas.rag import ChunkPolicy, DocumentStage, IndexStatus
from tests.conftest import make_token

pytestmark = pytest.mark.integration

#: 私有化模式下的固定租户
TENANT = "default"

#: 权限点（与路由常量保持一致）
KB_UPDATE = "knowledge:base:update"
KB_DELETE = "knowledge:base:delete"
KB_READ = "knowledge:base:read"

#: 禁词（PRD 第 7 章；后端响应与对外文案一律不得出现）
FORBIDDEN_TERMS = (
    "索引",
    "分块",
    "切片",
    "向量",
    "召回",
    "chunk",
    "token",
    "embedding",
    "segment",
    "相似度",
    "indexing_status",
)


def _headers(
    settings: Settings, roles: list[str] | None = None, user_id: str = "usr_owner"
) -> dict[str, str]:
    """构造鉴权请求头。"""
    token = make_token(
        settings=settings, user_id=user_id, roles=roles or [KB_UPDATE, KB_DELETE, KB_READ]
    )
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": TENANT}


def _create_base(
    client: TestClient,
    settings: Settings,
    make_idempotency_headers: Any,
    *,
    name: str = "制度库",
    chunk_policy: str = "default",
    tag: str = "kb-create",
) -> str:
    """创建一个知识库并返回 base_id。"""
    response = client.post(
        "/api/v1/knowledge/bases",
        json={"name": name, "description": "制度文档", "chunk_policy": chunk_policy},
        headers={**_headers(settings), **make_idempotency_headers(tag)},
    )
    assert response.json()["code"] == 0, response.text
    return str(response.json()["data"]["base_id"])


def _upload(
    client: TestClient,
    settings: Settings,
    make_idempotency_headers: Any,
    base_id: str,
    *,
    file_name: str = "员工手册.pdf",
    tag: str = "doc-upload",
    chunk_policy: str | None = None,
) -> dict[str, Any]:
    """上传一个文档并返回响应 data。"""
    data = {} if chunk_policy is None else {"chunk_policy": chunk_policy}
    response = client.post(
        f"/api/v1/knowledge/bases/{base_id}/documents",
        files={"file": (file_name, io.BytesIO(b"%PDF-1.4 test"), "application/pdf")},
        data=data,
        headers={**_headers(settings), **make_idempotency_headers(tag)},
    )
    assert response.json()["code"] == 0, response.text
    return dict(response.json()["data"])


def _iter_text_values(node: Any) -> list[str]:
    """递归取出 JSON 中的**值**（不含字段名）用于禁词扫描。

    字段名受契约约束（`chunk_policy` / `segment_count` 由架构文档 §4.2 固定，
    前端展示名才是「内容切分精细度 / 内容片段数」），禁词红线约束的是
    **用户可见文案**，故只扫描值。

    Args:
        node: 任意 JSON 节点。

    Returns:
        list[str]: 全部字符串值。
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
    """断言响应中的**用户可见文案**不含禁用词（PRD 第 7 章）。"""
    for value in _iter_text_values(payload):
        lowered = value.lower()
        for term in FORBIDDEN_TERMS:
            assert term.lower() not in lowered, f"用户可见文案出现禁用词 {term}：{value}"


# ===========================================================================
# KB1 修改知识库设置
# ===========================================================================
async def test_kb1_updates_name_and_policy(
    client: TestClient,
    settings: Settings,
    make_idempotency_headers: Any,
) -> None:
    """KB1 改名 + 改内容切分精细度，响应字段与契约一致。"""
    base_id = _create_base(client, settings, make_idempotency_headers, chunk_policy="default")

    response = client.patch(
        f"/api/v1/knowledge/bases/{base_id}",
        json={"name": "人事制度库（2026）", "chunk_policy": "fine"},
        headers={**_headers(settings), **make_idempotency_headers("kb1-ok")},
    )
    body = response.json()
    assert body["code"] == 0
    assert set(body["data"]) == {
        "base_id",
        "name",
        "description",
        "chunk_policy",
        "visibility",
        "updated_at",
    }
    assert body["data"]["name"] == "人事制度库（2026）"
    assert body["data"]["chunk_policy"] == "fine"
    assert body["data"]["updated_at"]
    _assert_no_forbidden_terms(body)

    # 列表里立即生效
    listed = client.get("/api/v1/knowledge/bases?page=1&page_size=20", headers=_headers(settings))
    item = listed.json()["data"]["items"][0]
    assert item["chunk_policy"] == "fine"
    assert item["my_role"] == "owner"
    assert item["updated_at"]


async def test_kb1_empty_name_returns_20001(
    client: TestClient, settings: Settings, make_idempotency_headers: Any
) -> None:
    """空名称返回 20001（PRD 验收 2）。"""
    base_id = _create_base(client, settings, make_idempotency_headers)

    response = client.patch(
        f"/api/v1/knowledge/bases/{base_id}",
        json={"name": ""},
        headers={**_headers(settings), **make_idempotency_headers("kb1-empty-name")},
    )
    assert response.json()["code"] == 20001


async def test_kb1_without_any_field_returns_20001(
    client: TestClient, settings: Settings, make_idempotency_headers: Any
) -> None:
    """未提供任何修改项返回 20001。"""
    base_id = _create_base(client, settings, make_idempotency_headers)

    response = client.patch(
        f"/api/v1/knowledge/bases/{base_id}",
        json={},
        headers={**_headers(settings), **make_idempotency_headers("kb1-no-field")},
    )
    assert response.json()["code"] == 20001


async def test_kb1_unknown_base_returns_11001(
    client: TestClient, settings: Settings, make_idempotency_headers: Any
) -> None:
    """库不存在返回 11001。"""
    response = client.patch(
        "/api/v1/knowledge/bases/kb_not_exist",
        json={"name": "新名称"},
        headers={**_headers(settings), **make_idempotency_headers("kb1-missing")},
    )
    assert response.json()["code"] == 11001


async def test_kb1_requires_idempotency_key(client: TestClient, settings: Settings) -> None:
    """缺少 Idempotency-Key 返回 20001。"""
    response = client.patch(
        "/api/v1/knowledge/bases/kb_1", json={"name": "x"}, headers=_headers(settings)
    )
    assert response.json()["code"] == 20001


async def test_kb1_without_permission_returns_20003(
    client: TestClient, settings: Settings, make_idempotency_headers: Any
) -> None:
    """无 `knowledge:base:update` 权限返回 20003（PRD 验收 3）。"""
    base_id = _create_base(client, settings, make_idempotency_headers)

    response = client.patch(
        f"/api/v1/knowledge/bases/{base_id}",
        json={"name": "改名"},
        headers={
            **_headers(settings, roles=["member"]),
            **make_idempotency_headers("kb1-forbidden"),
        },
    )
    assert response.status_code == 403
    assert response.json()["code"] == 20003


async def test_kb1_same_key_different_body_conflicts(
    client: TestClient, settings: Settings
) -> None:
    """同 Idempotency-Key 不同请求体 → 20005（指纹含请求体摘要）。"""
    base_id = _create_base(client, settings, lambda tag: {"Idempotency-Key": _key(tag)})
    idem = {"Idempotency-Key": "cccccccc-1111-4222-8333-444444444444"}

    first = client.patch(
        f"/api/v1/knowledge/bases/{base_id}",
        json={"name": "名称 A"},
        headers={**_headers(settings), **idem},
    )
    second = client.patch(
        f"/api/v1/knowledge/bases/{base_id}",
        json={"name": "名称 B"},
        headers={**_headers(settings), **idem},
    )
    assert first.json()["code"] == 0
    assert second.json()["code"] == 20005
    assert second.json()["data"] is None


async def test_kb1_same_key_same_body_replays(client: TestClient, settings: Settings) -> None:
    """同键同体重放 → 20007 且数据与首次一致。"""
    base_id = _create_base(client, settings, lambda tag: {"Idempotency-Key": _key(tag)})
    idem = {"Idempotency-Key": "dddddddd-1111-4222-8333-444444444444"}
    payload = {"name": "同名"}

    first = client.patch(
        f"/api/v1/knowledge/bases/{base_id}",
        json=payload,
        headers={**_headers(settings), **idem},
    )
    replay = client.patch(
        f"/api/v1/knowledge/bases/{base_id}",
        json=payload,
        headers={**_headers(settings), **idem},
    )
    assert first.json()["code"] == 0
    assert replay.json()["code"] == 20007
    assert replay.json()["data"] == first.json()["data"]


def _key(tag: str) -> str:
    """按标签生成稳定的 UUID 形态幂等键。"""
    import hashlib

    digest = hashlib.sha256(tag.encode("utf-8")).hexdigest()
    return f"{digest[0:8]}-{digest[8:12]}-4{digest[13:16]}-8{digest[17:20]}-{digest[20:32]}"


# ===========================================================================
# KB2 删除知识库
# ===========================================================================
async def test_kb2_soft_delete_flow(
    client: TestClient, settings: Settings, make_idempotency_headers: Any
) -> None:
    """删除后：列表不可见、文档接口 11001、操作记录有「删除知识库」条目。"""
    base_id = _create_base(client, settings, make_idempotency_headers, name="待删除库")
    _upload(client, settings, make_idempotency_headers, base_id, tag="doc-1")
    _upload(
        client,
        settings,
        make_idempotency_headers,
        base_id,
        file_name="制度.pdf",
        tag="doc-2",
    )

    response = client.delete(
        f"/api/v1/knowledge/bases/{base_id}",
        headers={**_headers(settings), **make_idempotency_headers("kb2-delete")},
    )
    body = response.json()
    assert body["code"] == 0
    assert body["data"] == {
        "base_id": base_id,
        "deleted": True,
        "deleted_document_count": 2,
    }

    listed = client.get("/api/v1/knowledge/bases?page=1&page_size=20", headers=_headers(settings))
    assert listed.json()["data"]["meta"]["total"] == 0
    assert listed.json()["data"]["items"] == []

    documents = client.get(
        f"/api/v1/knowledge/bases/{base_id}/documents?page=1&page_size=20",
        headers={**_headers(settings, roles=[KB_READ])},
    )
    assert documents.json()["code"] == 11001

    entries = await AuditService(settings).recent(TENANT, action=ACTION_KB_DELETE)
    assert [item.resource_id for item in entries] == [base_id]
    assert entries[0].display_name == "删除知识库"
    assert entries[0].change_summary["deleted_document_count"] == 2


async def test_kb2_other_member_returns_20003(
    client: TestClient, settings: Settings, make_idempotency_headers: Any
) -> None:
    """成员删除他人创建的库 → 20003（PRD 验收 5 第 2 条）。"""
    base_id = _create_base(client, settings, make_idempotency_headers)

    response = client.delete(
        f"/api/v1/knowledge/bases/{base_id}",
        headers={
            **_headers(settings, roles=[KB_DELETE], user_id="usr_other"),
            **make_idempotency_headers("kb2-other-member"),
        },
    )
    assert response.status_code == 403
    assert response.json()["code"] == 20003


async def test_kb2_guest_returns_20003(
    client: TestClient, settings: Settings, make_idempotency_headers: Any
) -> None:
    """访客删除自己创建的库也返回 20003。"""
    base_id = _create_base(
        client, settings, make_idempotency_headers, name="访客库", tag="kb-guest"
    )

    response = client.delete(
        f"/api/v1/knowledge/bases/{base_id}",
        headers={
            **_headers(settings, roles=[KB_DELETE, "guest"], user_id="usr_owner"),
            **make_idempotency_headers("kb2-guest"),
        },
    )
    assert response.json()["code"] == 20003


async def test_kb2_requires_idempotency_key(client: TestClient, settings: Settings) -> None:
    """缺少 Idempotency-Key 返回 20001。"""
    response = client.delete("/api/v1/knowledge/bases/kb_1", headers=_headers(settings))
    assert response.json()["code"] == 20001


async def test_kb2_unknown_base_returns_11001(
    client: TestClient, settings: Settings, make_idempotency_headers: Any
) -> None:
    """库不存在返回 11001。"""
    response = client.delete(
        "/api/v1/knowledge/bases/kb_not_exist",
        headers={**_headers(settings), **make_idempotency_headers("kb2-missing")},
    )
    assert response.json()["code"] == 11001


# ===========================================================================
# KB3 文档清单
# ===========================================================================
async def test_kb3_lists_documents_with_stage(
    client: TestClient, settings: Settings, make_idempotency_headers: Any
) -> None:
    """清单条目字段完整，五态与三态同时返回且语义一致。"""
    base_id = _create_base(client, settings, make_idempotency_headers)
    _upload(client, settings, make_idempotency_headers, base_id)

    response = client.get(
        f"/api/v1/knowledge/bases/{base_id}/documents?page=1&page_size=20",
        headers={**_headers(settings, roles=[KB_READ])},
    )
    body = response.json()
    assert body["code"] == 0
    assert set(body["data"]["meta"]) == {"page", "page_size", "total", "total_pages"}
    item = body["data"]["items"][0]
    assert item["stage"] == "uploaded"
    assert item["index_status"] == "processing"
    assert item["stage_display_name"] == "已上传"
    assert item["file_name"] == "员工手册.pdf"
    assert item["uploader_name"]
    assert item["uploaded_at"]
    assert item["estimated_remaining_seconds"] is None
    _assert_no_forbidden_terms(body)


async def test_kb3_filters_failed_stage(
    client: TestClient, settings: Settings, make_idempotency_headers: Any
) -> None:
    """阶段筛选只返回「处理未成功」的文档，且给出业务语言原因。"""
    base_id = _create_base(client, settings, make_idempotency_headers)
    uploaded = _upload(client, settings, make_idempotency_headers, base_id)

    service = DocumentService(settings)
    await service.refresh_progress(
        tenant_id=TENANT,
        document_id=uploaded["document_id"],
        native_status="error",
        progress=10,
        error_message="文档解析失败，请确认文件内容后重新上传",
    )

    response = client.get(
        f"/api/v1/knowledge/bases/{base_id}/documents?stage=failed&page=1&page_size=20",
        headers={**_headers(settings, roles=[KB_READ])},
    )
    body = response.json()
    assert body["data"]["meta"]["total"] == 1
    item = body["data"]["items"][0]
    assert item["stage"] == "failed"
    assert item["index_status"] == "failed"
    assert item["stage_display_name"] == "处理未成功"
    assert item["error_message"] == "文档解析失败，请确认文件内容后重新上传"

    empty = client.get(
        f"/api/v1/knowledge/bases/{base_id}/documents?stage=completed",
        headers={**_headers(settings, roles=[KB_READ])},
    )
    assert empty.json()["data"]["items"] == []


async def test_kb3_keyword_is_case_insensitive(
    client: TestClient, settings: Settings, make_idempotency_headers: Any
) -> None:
    """关键字筛选大小写不敏感。"""
    base_id = _create_base(client, settings, make_idempotency_headers)
    _upload(
        client,
        settings,
        make_idempotency_headers,
        base_id,
        file_name="Employee Handbook.pdf",
        tag="doc-en",
    )
    _upload(
        client,
        settings,
        make_idempotency_headers,
        base_id,
        file_name="财务制度.pdf",
        tag="doc-cn",
    )

    response = client.get(
        f"/api/v1/knowledge/bases/{base_id}/documents?keyword=handbook",
        headers={**_headers(settings, roles=[KB_READ])},
    )
    items = response.json()["data"]["items"]
    assert [item["file_name"] for item in items] == ["Employee Handbook.pdf"]


async def test_kb3_sort_ascending(
    client: TestClient, settings: Settings, make_idempotency_headers: Any
) -> None:
    """`sort=uploaded_at:asc` 生效。"""
    base_id = _create_base(client, settings, make_idempotency_headers)
    _upload(client, settings, make_idempotency_headers, base_id, file_name="a.pdf", tag="doc-a")
    _upload(client, settings, make_idempotency_headers, base_id, file_name="b.pdf", tag="doc-b")

    ascending = client.get(
        f"/api/v1/knowledge/bases/{base_id}/documents?sort=uploaded_at:asc",
        headers={**_headers(settings, roles=[KB_READ])},
    )
    descending = client.get(
        f"/api/v1/knowledge/bases/{base_id}/documents?sort=uploaded_at:desc",
        headers={**_headers(settings, roles=[KB_READ])},
    )
    asc_names = [item["file_name"] for item in ascending.json()["data"]["items"]]
    desc_names = [item["file_name"] for item in descending.json()["data"]["items"]]
    assert asc_names == list(reversed(desc_names))


async def test_kb3_deleted_base_returns_11001(
    client: TestClient, settings: Settings, make_idempotency_headers: Any
) -> None:
    """库被删除后文档清单返回 11001（PRD 验收 4）。"""
    base_id = _create_base(client, settings, make_idempotency_headers)
    client.delete(
        f"/api/v1/knowledge/bases/{base_id}",
        headers={**_headers(settings), **make_idempotency_headers("kb2-doc-gone")},
    )

    response = client.get(
        f"/api/v1/knowledge/bases/{base_id}/documents",
        headers={**_headers(settings, roles=[KB_READ])},
    )
    assert response.json()["code"] == 11001


async def test_kb3_without_permission_returns_20003(
    client: TestClient, settings: Settings, make_idempotency_headers: Any
) -> None:
    """无 `knowledge:base:read` 权限返回 20003。"""
    base_id = _create_base(client, settings, make_idempotency_headers)

    response = client.get(
        f"/api/v1/knowledge/bases/{base_id}/documents",
        headers={**_headers(settings, roles=["member"])},
    )
    assert response.status_code == 403
    assert response.json()["code"] == 20003


async def test_kb3_deep_page_payload_is_small(
    client: TestClient, settings: Settings, make_idempotency_headers: Any
) -> None:
    """5000 条文档下第 250 页返回正确且响应体 < 200KB（PRD 验收 4）。"""
    base_id = _create_base(client, settings, make_idempotency_headers)
    service = DocumentService(settings)
    base_time = datetime(2026, 10, 1, 0, 0, 0, tzinfo=UTC)
    for index in range(5000):
        await service.store.create(
            DocumentRecord(
                tenant_id=TENANT,
                document_id=f"doc_{index:05d}",
                base_id=base_id,
                file_name=f"文档_{index:05d}.pdf",
                file_size=1024 + index,
                uploader_user_id="usr_owner",
                uploader_name="usr_owner",
                uploaded_at=(base_time + timedelta(seconds=index))
                .isoformat()
                .replace("+00:00", "Z"),
            )
        )

    response = client.get(
        f"/api/v1/knowledge/bases/{base_id}/documents?page=250&page_size=20",
        headers={**_headers(settings, roles=[KB_READ])},
    )
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["meta"]["total"] == 5000
    assert body["data"]["meta"]["total_pages"] == 250
    assert len(body["data"]["items"]) == 20
    assert body["data"]["items"][0]["document_id"] == "doc_00019"
    assert len(response.content) < 200 * 1024, f"响应体过大：{len(response.content)} 字节"


# ===========================================================================
# R1 / R3 扩展：库级内容切分精细度
# ===========================================================================
async def test_base_chunk_policy_applies_to_upload_without_override(
    client: TestClient, settings: Settings, make_idempotency_headers: Any
) -> None:
    """库级 `fine` + 上传不带精细度 → 按 `fine` 处理（PRD 验收 1）。"""
    base_id = _create_base(client, settings, make_idempotency_headers, chunk_policy="fine")

    data = _upload(client, settings, make_idempotency_headers, base_id, tag="doc-inherit")
    assert data["chunk_policy"] == "fine"
    assert data["stage"] == "uploaded"
    assert data["index_status"] == "processing"


async def test_upload_override_does_not_change_base_policy(
    client: TestClient, settings: Settings, make_idempotency_headers: Any
) -> None:
    """单次覆盖不改变库级设置。"""
    base_id = _create_base(client, settings, make_idempotency_headers, chunk_policy="fine")

    data = _upload(
        client,
        settings,
        make_idempotency_headers,
        base_id,
        tag="doc-override",
        chunk_policy="default",
    )
    assert data["chunk_policy"] == "default"

    listed = client.get("/api/v1/knowledge/bases?page=1&page_size=20", headers=_headers(settings))
    assert listed.json()["data"]["items"][0]["chunk_policy"] == "fine"


async def test_r4_status_exposes_stage_and_file_meta(
    client: TestClient, settings: Settings, make_idempotency_headers: Any
) -> None:
    """R4 扩展字段：阶段、文档大小、上传时间（PRD P2-KB-02 R4 扩展）。"""
    base_id = _create_base(client, settings, make_idempotency_headers)
    uploaded = _upload(client, settings, make_idempotency_headers, base_id)

    response = client.get(
        f"/api/v1/knowledge/bases/{base_id}/documents/{uploaded['document_id']}",
        headers=_headers(settings),
    )
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["stage"] == "uploaded"
    assert body["data"]["file_size"] > 0
    assert body["data"]["uploaded_at"]
    assert "indexing_status" not in response.text
    _assert_no_forbidden_terms(body)


async def test_r6_delete_processing_document_returns_11006(
    client: TestClient, settings: Settings, make_idempotency_headers: Any
) -> None:
    """处理中的文档删除返回 11006（PRD 验收 5 文案）。"""
    base_id = _create_base(client, settings, make_idempotency_headers)
    uploaded = _upload(client, settings, make_idempotency_headers, base_id)

    response = client.delete(
        f"/api/v1/knowledge/bases/{base_id}/documents/{uploaded['document_id']}",
        headers={**_headers(settings), **make_idempotency_headers("r6-processing")},
    )
    body = response.json()
    assert body["code"] == 11006
    assert body["message"] == "文档正在处理，请稍后再删除"


async def test_r5_reindex_resets_stage_to_parsing(
    client: TestClient, settings: Settings, make_idempotency_headers: Any
) -> None:
    """重新处理后阶段回到「正在读取内容」（PRD 验收 2）。"""
    base_id = _create_base(client, settings, make_idempotency_headers)
    uploaded = _upload(client, settings, make_idempotency_headers, base_id)
    document_id = uploaded["document_id"]

    service = DocumentService(settings)
    await service.refresh_progress(
        tenant_id=TENANT,
        document_id=document_id,
        native_status="error",
        progress=10,
        error_message="文档解析失败，请确认文件内容后重新上传",
    )

    response = client.post(
        f"/api/v1/knowledge/bases/{base_id}/documents/{document_id}/reindex",
        headers={**_headers(settings), **make_idempotency_headers("r5-reindex")},
    )
    assert response.json()["code"] == 0
    assert response.json()["data"]["stage"] == "parsing"

    status = client.get(
        f"/api/v1/knowledge/bases/{base_id}/documents/{document_id}",
        headers=_headers(settings),
    )
    assert status.json()["data"]["stage"] == "parsing"


async def test_visibility_restricted_is_accepted(
    client: TestClient, settings: Settings, make_idempotency_headers: Any
) -> None:
    """可见范围枚举新增 `restricted`（只允许加宽）。"""
    base_id = _create_base(client, settings, make_idempotency_headers)

    response = client.patch(
        f"/api/v1/knowledge/bases/{base_id}",
        json={"visibility": "restricted"},
        headers={**_headers(settings), **make_idempotency_headers("kb1-restricted")},
    )
    assert response.json()["code"] == 0
    assert response.json()["data"]["visibility"] == "restricted"


async def test_document_stage_and_status_always_consistent(
    client: TestClient, settings: Settings, make_idempotency_headers: Any
) -> None:
    """全量断言：清单中每个条目的五态与三态语义一致。"""
    base_id = _create_base(client, settings, make_idempotency_headers)
    service = DocumentService(settings)
    auth = AuthContext(user_id="usr_owner", tenant_id=TENANT)
    for native in ("waiting", "parsing", "splitting", "completed", "error"):
        await service.register_document(
            base_id=base_id,
            file_name=f"{native}.pdf",
            file_size=512,
            auth=auth,
            chunk_policy=ChunkPolicy.DEFAULT,
        )
    records, _ = await service.store.list(TENANT, base_id, limit=20)
    for record in records:
        await service.refresh_progress(
            tenant_id=TENANT,
            document_id=record.document_id,
            native_status=native_for(record.document_id),
            progress=0,
        )

    response = client.get(
        f"/api/v1/knowledge/bases/{base_id}/documents?page=1&page_size=50",
        headers={**_headers(settings, roles=[KB_READ])},
    )
    items = response.json()["data"]["items"]
    assert len(items) == 5
    for item in items:
        assert (item["index_status"] == "completed") == (item["stage"] == "completed")
        assert (item["index_status"] == "failed") == (item["stage"] == "failed")
        assert item["stage"] in {stage.value for stage in DocumentStage}
        assert item["index_status"] in {status.value for status in IndexStatus}


def native_for(document_id: str) -> str:
    """按文档 ID 反推其底座状态（用于批量刷新）。"""
    mapping = {
        "waiting": "waiting",
        "parsing": "parsing",
        "splitting": "splitting",
        "completed": "completed",
        "error": "error",
    }
    for key, value in mapping.items():
        if key in document_id:
            return value
    return "waiting"
