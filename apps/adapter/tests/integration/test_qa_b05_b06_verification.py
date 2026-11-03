"""QA 独立验证用例（B05 知识库授权与回链 / B06 工具三款与调用记录）。

本文件的定位与 `test_grant_and_backlink.py` / `test_tool_executions.py` **不同**：

- 那两份是**工程师自测**，本文件是**验收方独立复跑**，不复用其辅助函数，
  也不改动其断言（测试方案 v1.3 §1「不修改既有用例断言」）；
- 每条用例对应 `docs/architecture-phase2.md` §9.2 的「完成后可验证」条目
  或 `docs/qa-phase2-test-plan.md` 的 PERM-01 / PERM-07 硬判据；
- 对**口径偏差**（`restricted` 判定对「无本地元数据的历史库」放行）
  按方案口径**断言应有行为**，用于给出可复现证据而非迁就实现。

判据来源：
- 架构 §4.8 第 885 行：三层校验 + 对象级，「**禁止**列表过滤了但详情接口没校验」；
- 架构 §9.2 B05 可验证 ①：未授权成员列表不含、直连 `20003`、R7 `20003`、对话静默跳过；
- 测试方案 §7.1 P0 定义 ③：无权限账号通过详情/检索入口拿到数据（PERM-01/07）。
"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.business.document_service import DocumentService
from app.business.policy_service import PolicyService, get_policy_service
from app.business.segment_service import SegmentHit, SegmentService, build_key
from app.core.config import Settings
from app.core.security import AuthContext
from app.db.repositories.grant_repo import GrantRecord, get_grant_store
from app.db.repositories.knowledge_base_repo import KnowledgeBaseRecord, get_knowledge_base_store
from tests.conftest import make_token

pytestmark = pytest.mark.integration

#: 私有化模式固定租户（与 conftest 的 TEST_TENANT_ID 一致）
TENANT = "default"

#: 权限点
KB_UPDATE = "knowledge:base:update"
KB_READ = "knowledge:base:read"
KB_GRANT = "knowledge:grant:manage"
TOOL_MANAGE = "tool:connection:manage"
TOOL_EXECUTE = "tool:execute"

_OWNER_ROLES = [KB_UPDATE, KB_READ, KB_GRANT]
_READER_ROLES = [KB_READ]

#: 用户可见文案禁词（PRD 第 7 章 + G-10/G-14 口径）
FORBIDDEN_TERMS = (
    "向量",
    "召回",
    "相似度",
    "embedding",
    "chunk",
    "segment",
    "score",
    "dify",
    "milvus",
    "litellm",
    "chat-messages",
    "5001",
)

#: 内部字段（响应体一律不得出现）
INTERNAL_KEYS = (
    "tenant_id",
    "owner_user_id",
    "dify_dataset_id",
    "native_segment_id",
    "content_sha256",
    "credential_ref",
    "credential_value",
    "api_key",
    "encrypted_value",
)


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
    settings: Settings, roles: list[str] | None = None, user_id: str = "usr_owner"
) -> dict[str, str]:
    """构造鉴权请求头。

    Args:
        settings: 配置实例。
        roles: 令牌权限点。
        user_id: 用户 ID。

    Returns:
        dict[str, str]: 请求头。
    """
    token = make_token(settings=settings, user_id=user_id, roles=roles or _OWNER_ROLES)
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": TENANT}


def _create_base(
    client: TestClient,
    settings: Settings,
    *,
    visibility: str = "restricted",
    tag: str = "qa-base",
    user_id: str = "usr_owner",
) -> str:
    """经 R1 创建知识库（阶段二路径，必然落本地元数据）。

    Args:
        client: 测试客户端。
        settings: 配置实例。
        visibility: 可见范围。
        tag: 幂等键标签。
        user_id: 创建人。

    Returns:
        str: 知识库 ID。
    """
    response = client.post(
        "/api/v1/knowledge/bases",
        json={"name": f"制度库-{tag}", "description": "制度", "visibility": visibility},
        headers={**_headers(settings, user_id=user_id), "Idempotency-Key": _key(tag)},
    )
    body = response.json()
    assert body["code"] == 0, response.text
    return str(body["data"]["base_id"])


def _grant(
    client: TestClient,
    settings: Settings,
    base_id: str,
    subject_ids: list[str],
    *,
    subject_type: str = "user",
    tag: str = "qa-grant",
    key_tag: str | None = None,
) -> dict[str, Any]:
    """经 KB6 批量授权。

    Args:
        client: 测试客户端。
        settings: 配置实例。
        base_id: 知识库 ID。
        subject_ids: 授权对象 ID 列表。
        subject_type: 授权对象类型。
        tag: 标签。
        key_tag: 幂等键标签覆盖（用于幂等指纹用例）。

    Returns:
        dict[str, Any]: 响应 JSON。
    """
    return client.post(
        f"/api/v1/knowledge/bases/{base_id}/grants",
        json={"subject_type": subject_type, "subject_ids": subject_ids, "permission": "viewer"},
        headers={**_headers(settings), "Idempotency-Key": _key(key_tag or tag)},
    ).json()


def _auth_context(user_id: str = "usr_b", roles: list[str] | None = None) -> AuthContext:
    """构造鉴权上下文（单位级调用 `PolicyService` 用）。

    Args:
        user_id: 用户 ID。
        roles: 权限点列表。

    Returns:
        AuthContext: 鉴权上下文。
    """
    return AuthContext(
        tenant_id=TENANT,
        user_id=user_id,
        roles=roles or _READER_ROLES,
    )


def _walk_keys(node: Any) -> set[str]:
    """递归收集 JSON 中的全部键名。

    Args:
        node: 任意 JSON 节点。

    Returns:
        set[str]: 键名集合。
    """
    keys: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            keys.add(str(key))
            keys |= _walk_keys(value)
    elif isinstance(node, list):
        for item in node:
            keys |= _walk_keys(item)
    return keys


def _walk_text(node: Any) -> list[str]:
    """递归收集 JSON 中的全部字符串值。

    Args:
        node: 任意 JSON 节点。

    Returns:
        list[str]: 字符串值列表。
    """
    texts: list[str] = []
    if isinstance(node, dict):
        for value in node.values():
            texts.extend(_walk_text(value))
    elif isinstance(node, list):
        for item in node:
            texts.extend(_walk_text(item))
    elif isinstance(node, str):
        texts.append(node)
    return texts


# ===========================================================================
# A 组：B05 授权（架构 §9.2 B05 可验证 ①③④）
# ===========================================================================
async def test_a2b_downstream_call_proves_restricted_data_exposure(
    settings: Settings,
) -> None:
    """在下游可用形态下证明：去前缀 ID 会真实发起检索并取回受限库正文。

    与 `test_a2` 互补：`test_a2` 走桩模式（下游不调用，仅看错误码），本用例
    注入 `httpx.MockTransport` 让下游「真实返回」，直接把请求 URL 与返回正文
    打印出来 —— 若判定短路，未授权成员的响应里会出现 `restricted` 库的原文。
    """
    import httpx

    from app.clients.dify_client import DifyClient
    from app.core.errors import ApiError
    from app.schemas.rag import SearchRequest
    from app.services.rag_service import RagService

    online = Settings(
        env="test",
        deploy_mode="private",
        downstream_enabled=True,
        auth_mode="stub",
        jwt_secret="unit-test-secret",
        ratelimit_enabled=False,
        dify_base_url="http://dify.test",
        dify_default_app_key="test-key",
    )
    captured: dict[str, str] = {}
    secret = "第十五条 高管薪酬明细（受限库原文）"

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "records": [
                    {
                        "document_id": "doc_secret",
                        "score": 0.92,
                        "segment": {
                            "content": secret,
                            "document_name": "薪酬保密制度.pdf",
                            "position": {"page": 2, "paragraph": 1},
                        },
                    }
                ]
            },
        )

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_handler), base_url="http://dify.test"
    )
    dify = DifyClient("http://dify.test", "test-key", settings=online, client=http_client)

    bases = get_knowledge_base_store(online)
    base_id = "kb_qaA2B"
    native_id = base_id.removeprefix("kb_")
    await bases.create(
        KnowledgeBaseRecord(
            tenant_id=TENANT,
            base_id=base_id,
            name="薪酬保密库",
            visibility="restricted",
            owner_user_id="usr_owner",
        )
    )

    policy = PolicyService(online)
    service = RagService(online, dify=dify, policy=policy)
    member = _auth_context("usr_member_not_granted")

    # 形态一：正确 ID → 对象级判权拦截
    with pytest.raises(ApiError) as denied:
        await service.search(SearchRequest(base_id=base_id, query="薪酬"), member)
    assert int(denied.value.code) == 20003

    # 形态二：去前缀的同一对象 → 归一化后同样被拦截，且**不得发起任何下游调用**
    with pytest.raises(ApiError) as native_denied:
        await service.search(SearchRequest(base_id=native_id, query="薪酬"), member)
    await http_client.aclose()
    assert int(native_denied.value.code) == 20003
    assert "url" not in captured, (
        f"去前缀 ID 绕过了 restricted 判权并发起了下游检索：downstream={captured.get('url')}"
    )


def test_a1_restricted_base_hidden_from_unauthorized_member_three_entries(
    client: TestClient, settings: Settings
) -> None:
    """未授权成员：列表不含 + 直连 20003 + 检索 20003（可验证 ①）。"""
    base_id = _create_base(client, settings, tag="qa-a1")

    listed = client.get(
        "/api/v1/knowledge/bases", headers=_headers(settings, roles=_READER_ROLES, user_id="usr_b")
    )
    assert listed.json()["code"] == 0
    assert base_id not in {item["base_id"] for item in listed.json()["data"]["items"]}

    direct = client.get(
        f"/api/v1/knowledge/bases/{base_id}/documents",
        headers=_headers(settings, roles=_READER_ROLES, user_id="usr_b"),
    )
    assert direct.json()["code"] == 20003

    search = client.post(
        "/api/v1/knowledge/search",
        json={"base_id": base_id, "query": "年假"},
        headers={
            **_headers(settings, roles=_READER_ROLES, user_id="usr_b"),
            "Idempotency-Key": _key("qa-a1-search"),
        },
    )
    assert search.json()["code"] == 20003


def test_a2_search_denies_unauthorized_member_using_native_base_id(
    client: TestClient, settings: Settings
) -> None:
    """PERM-01/07 硬判据：换一个 ID 写法不得绕过 `restricted` 判定。

    阶段二 `base_id = "kb_" + 底座 dataset id`，而 R7 下发下游时执行
    `base_id.removeprefix("kb_")`，即**同一个底座对象存在两种可达 ID 形态**。
    若判权依赖「本地元数据是否存在」而非「对象归属」，未授权成员只要去掉
    `kb_` 前缀即可让判定整体短路 —— 这正是架构 §4.8 第 885 行禁止的
    「列表过滤了但详情接口没校验」，属测试方案 §7.1 P0 定义 ③。
    """
    base_id = _create_base(client, settings, tag="qa-a2")
    native_id = base_id.removeprefix("kb_")
    assert native_id != base_id

    denied = client.post(
        "/api/v1/knowledge/search",
        json={"base_id": base_id, "query": "年假"},
        headers={
            **_headers(settings, roles=_READER_ROLES, user_id="usr_b"),
            "Idempotency-Key": _key("qa-a2-1"),
        },
    )
    assert denied.json()["code"] == 20003

    bypass = client.post(
        "/api/v1/knowledge/search",
        json={"base_id": native_id, "query": "年假"},
        headers={
            **_headers(settings, roles=_READER_ROLES, user_id="usr_b"),
            "Idempotency-Key": _key("qa-a2-2"),
        },
    )
    assert bypass.json()["code"] == 20003, (
        f"无本地元数据的 ID 形态绕过了 restricted 判权：native_id={native_id} 返回 {bypass.json()}"
    )


async def test_a3_conversation_drops_unauthorized_base_using_native_id(
    client: TestClient, settings: Settings
) -> None:
    """对话（M1/M2）静默跳过必须对**同一对象的任一 ID 形态**成立。"""
    base_id = _create_base(client, settings, tag="qa-a3")
    native_id = base_id.removeprefix("kb_")
    policy: PolicyService = get_policy_service(settings)
    policy.clear()

    authorized = await policy.filter_authorized(_auth_context("usr_b"), [base_id, native_id])
    assert authorized == [], f"未授权库未被剔除：{authorized}"


def test_a4_duplicate_grant_increments_skipped_count(
    client: TestClient, settings: Settings
) -> None:
    """同对象重复授权 → `skipped_count=1`（可验证 ③）。"""
    base_id = _create_base(client, settings, tag="qa-a4")

    first = _grant(client, settings, base_id, ["usr_b"], tag="qa-a4-g1")
    assert first["code"] == 0, first
    assert first["data"]["created_count"] == 1
    assert first["data"]["skipped_count"] == 0

    second = _grant(client, settings, base_id, ["usr_b"], tag="qa-a4-g2")
    assert second["code"] == 0, second
    assert second["data"]["created_count"] == 0
    assert second["data"]["skipped_count"] == 1


def test_a4b_intra_batch_duplicate_counts_as_skipped(
    client: TestClient, settings: Settings
) -> None:
    """同批 `subject_ids` 内重复对象也应计入 `skipped_count`（架构 §4.2 要点）。

    「KB6 `subject_ids` 中重复对象计入 `skipped_count`」是架构对 KB6 的
    明文契约，本用例按契约断言 `created=1 + skipped=1`。
    """
    base_id = _create_base(client, settings, tag="qa-a4b")
    body = _grant(client, settings, base_id, ["usr_c", "usr_c"], tag="qa-a4b-g1")
    assert body["code"] == 0, body
    assert body["data"]["created_count"] == 1
    assert body["data"]["skipped_count"] == 1, (
        f"同批重复对象未计入 skipped_count（架构 §4.2 要点）：{body['data']}"
    )


async def test_a5_department_grant_removal_expires_within_five_seconds(settings: Settings) -> None:
    """部门授权移出成员后 ≤5s 失效，且 TTL 上限为 5s（可验证 ②）。

    直接驱动 `PolicyService` 并注入单调时钟，避免依赖真实等待。
    """
    assert settings.permission_cache_ttl_seconds <= 5.0

    clock = {"value": 1000.0}
    bases = get_knowledge_base_store(settings)
    grants = get_grant_store(settings)
    base_id = "kb_qa_a5"

    await bases.create(
        KnowledgeBaseRecord(
            tenant_id=TENANT,
            base_id=base_id,
            name="部门授权库",
            visibility="restricted",
            owner_user_id="usr_owner",
        )
    )
    await grants.create(
        GrantRecord(
            tenant_id=TENANT,
            grant_id="grnt_qa_a5",
            base_id=base_id,
            subject_type="department",
            subject_id="dept_qa",
            permission="viewer",
            granted_by="usr_owner",
        )
    )
    grants.set_user_departments(TENANT, "usr_b", ["dept_qa"])  # type: ignore[attr-defined]

    policy = PolicyService(settings, clock=lambda: clock["value"])
    auth = _auth_context("usr_b")
    record = await bases.get(TENANT, base_id)
    assert record is not None
    assert await policy.can_read(auth, record)

    # 移出部门后：≤ TTL 内属于缓存窗口，超过 TTL 必须失效
    grants.set_user_departments(TENANT, "usr_b", [])  # type: ignore[attr-defined]
    clock["value"] += settings.permission_cache_ttl_seconds
    assert not await policy.can_read(auth, record), "超过 5s 仍可读"


async def test_a6_kb4_stale_returns_200_without_error_semantics(
    client: TestClient, settings: Settings
) -> None:
    """重处理后旧引用 → HTTP 200 + code=0 + status=stale，无错误码外露（可验证 ④）。"""
    base_id = _create_base(client, settings, tag="qa-a6", visibility="organization")
    upload = client.post(
        f"/api/v1/knowledge/bases/{base_id}/documents",
        files={"file": ("制度.pdf", b"%PDF-1.4 qa\n%%EOF", "application/pdf")},
        headers={**_headers(settings), "Idempotency-Key": _key("qa-a6-up")},
    )
    assert upload.json()["code"] == 0, upload.text
    document_id = str(upload.json()["data"]["document_id"])

    documents = DocumentService(settings)
    await documents.refresh_progress(
        tenant_id=TENANT, document_id=document_id, native_status="completed", progress=100
    )
    content = "第十条 报销需在五个工作日内提交。"
    segments = SegmentService(settings)
    await segments.record_hits(
        TENANT,
        [
            SegmentHit(
                base_id=base_id,
                document_id=document_id,
                segment_index=0,
                content=content,
                position_page=3,
                position_paragraph=1,
            )
        ],
        schedule=False,
    )
    segment_key = build_key(document_id, 0, content)
    url = f"/api/v1/knowledge/bases/{base_id}/documents/{document_id}/segments/{segment_key}"

    valid = client.get(url, headers=_headers(settings, roles=_READER_ROLES))
    assert valid.status_code == 200
    assert valid.json()["code"] == 0
    assert valid.json()["data"]["status"] == "valid"

    await segments.mark_stale(TENANT, document_id=document_id, keep_run_id="")
    stale = client.get(url, headers=_headers(settings, roles=_READER_ROLES))
    body = stale.json()
    assert stale.status_code == 200
    assert body["code"] == 0, f"stale 不得用错误码：{body}"
    assert body["data"]["status"] == "stale"
    texts = " ".join(_walk_text(body)).lower()
    for term in FORBIDDEN_TERMS:
        assert term not in texts, f"KB4 响应出现禁词 {term}"
    assert not any(part.isdigit() and len(part) == 5 for part in texts.split())


def test_a7_responses_do_not_expose_internal_fields(client: TestClient, settings: Settings) -> None:
    """新增响应模型不得回传内部字段（租户 / 底座 / 凭据 / 哈希）。"""
    base_id = _create_base(client, settings, tag="qa-a7")
    _grant(client, settings, base_id, ["usr_b"], tag="qa-a7-grant")

    payloads = [
        client.get(f"/api/v1/knowledge/bases/{base_id}/grants", headers=_headers(settings)).json(),
        _grant(client, settings, base_id, ["usr_c"], tag="qa-a7-grant2"),
        client.get("/api/v1/knowledge/bases", headers=_headers(settings)).json(),
    ]
    for payload in payloads:
        keys = _walk_keys(payload)
        for internal in INTERNAL_KEYS:
            assert internal not in keys, f"响应体泄露内部字段 {internal}：{payload}"


def test_a8_kb6_idempotency_fingerprint_includes_body(
    client: TestClient, settings: Settings
) -> None:
    """幂等指纹含请求体摘要：同键换请求体不得静默回放首次结果。"""
    base_id = _create_base(client, settings, tag="qa-a8")
    key = _key("qa-a8-shared")

    first = client.post(
        f"/api/v1/knowledge/bases/{base_id}/grants",
        json={"subject_type": "user", "subject_ids": ["usr_b"], "permission": "viewer"},
        headers={**_headers(settings), "Idempotency-Key": key},
    ).json()
    assert first["code"] == 0, first

    second = client.post(
        f"/api/v1/knowledge/bases/{base_id}/grants",
        json={"subject_type": "user", "subject_ids": ["usr_c"], "permission": "viewer"},
        headers={**_headers(settings), "Idempotency-Key": key},
    ).json()
    assert second["code"] == 20005, f"同键不同体被静默回放：{second}"

    grants = client.get(
        f"/api/v1/knowledge/bases/{base_id}/grants", headers=_headers(settings)
    ).json()
    granted = {item["subject_id"] for item in grants["data"]["items"]}
    assert "usr_c" not in granted, "键复用请求改变了授权状态（既未拒绝也未回放）"


# ===========================================================================
# B 组：B06 工具三款与调用记录（架构 §9.2 B06 可验证 ①②③④）
# ===========================================================================
def test_b1_t1_returns_five_tools_with_chinese_operations(
    client: TestClient, settings: Settings
) -> None:
    """T1 返回 5 款，`operations` 全为中文业务语言（可验证 ①）。"""
    response = client.get("/api/v1/tools/registry", headers=_headers(settings))
    body = response.json()
    assert body["code"] == 0
    tools = body["data"]["tools"]
    assert len(tools) == 5
    assert {tool["tool_type"] for tool in tools} == {
        "email",
        "github",
        "wecom",
        "feishu",
        "wechat_mp",
    }
    for tool in tools:
        assert tool["display_name"]
        for operation in tool["operations"]:
            label = operation["display_name"]
            assert label, f"{tool['tool_type']} 存在空 operation 展示名"
            assert any("\u4e00" <= ch <= "\u9fff" for ch in label), f"非中文业务语言：{label}"
            for term in FORBIDDEN_TERMS:
                assert term not in label.lower(), f"操作名出现技术词 {term}：{label}"


def test_b2_tl1_returns_latest_twenty_without_stack_or_codes(
    client: TestClient, settings: Settings
) -> None:
    """TL1 返回最近 20 条，失败项无堆栈无错误码（可验证 ③）。"""
    connect = client.post(
        "/api/v1/tools/connections",
        json={"tool_type": "wecom", "auth_code": "qa-auth-code"},
        headers={**_headers(settings, roles=[TOOL_MANAGE]), "Idempotency-Key": _key("qa-b2-c")},
    )
    assert connect.json()["code"] == 0, connect.text

    for index in range(22):
        client.post(
            "/api/v1/tools/execute",
            json={"tool_type": "wecom", "operation": "list_members", "params": {}},
            headers={
                **_headers(settings, roles=[TOOL_MANAGE, TOOL_EXECUTE]),
                "Idempotency-Key": _key(f"qa-b2-e{index}"),
            },
        )

    body = client.get(
        "/api/v1/tools/executions", headers=_headers(settings, roles=[TOOL_MANAGE])
    ).json()
    assert body["code"] == 0
    items = body["data"]["items"]
    assert len(items) == 20
    assert body["data"]["meta"]["total"] == 22
    executed_at = [item["executed_at"] for item in items]
    assert executed_at == sorted(executed_at, reverse=True)
    for item in items:
        message = item.get("error_message") or ""
        assert "Traceback" not in message
        assert 'File "' not in message
        assert not any(part.isdigit() and len(part) == 5 for part in message.split())
        for term in FORBIDDEN_TERMS:
            assert term not in message.lower()


def test_b3_execute_without_connection_returns_13002(
    client: TestClient, settings: Settings
) -> None:
    """未连接调用 T5 → 13002（可验证 ④）。"""
    response = client.post(
        "/api/v1/tools/execute",
        json={"tool_type": "wecom", "operation": "send_message", "params": {"content": "hi"}},
        headers={
            **_headers(settings, roles=[TOOL_MANAGE, TOOL_EXECUTE]),
            "Idempotency-Key": _key("qa-b3"),
        },
    )
    assert response.json()["code"] == 13002
    assert "尚未连接" in response.json()["message"]


def test_b2b_tl1_failed_item_shows_business_reason_only(
    client: TestClient, settings: Settings
) -> None:
    """失败调用在 TL1 只展示业务原因：无堆栈、无错误码（可验证 ③）。"""
    connect = client.post(
        "/api/v1/tools/connections",
        json={"tool_type": "wecom", "auth_code": "qa-auth-code"},
        headers={**_headers(settings, roles=[TOOL_MANAGE]), "Idempotency-Key": _key("qa-b2b-c")},
    )
    assert connect.json()["code"] == 0, connect.text

    failed = client.post(
        "/api/v1/tools/execute",
        json={
            "tool_type": "wecom",
            "operation": "send_message",
            "params": {"content": "内容", "members": ["usr_a"], "simulate": "member_missing"},
        },
        headers={
            **_headers(settings, roles=[TOOL_MANAGE, TOOL_EXECUTE]),
            "Idempotency-Key": _key("qa-b2b-e"),
        },
    )
    assert failed.json()["code"] == 13006, failed.text

    response = client.get(
        "/api/v1/tools/executions", headers=_headers(settings, roles=[TOOL_MANAGE])
    )
    body = response.json()
    assert body["code"] == 0
    items = body["data"]["items"]
    assert len(items) == 1
    item = items[0]
    assert item["status"] == "failed"
    assert item["tool_display_name"] == "企业微信"
    assert item["operation_display_name"] == "发送消息"
    assert item["error_message"], "失败项必须有业务原因文案"
    assert "13006" not in response.text
    assert "Traceback" not in response.text
    assert 'File "' not in response.text


async def test_a2c_cross_tenant_search_is_denied(settings: Settings) -> None:
    """跨租户检索必须 `20003`（架构 §4.8 租户层第一判据 / 测试方案 P0 定义 ①）。"""
    import httpx

    from app.clients.dify_client import DifyClient
    from app.services.rag_service import RagService

    online = Settings(
        env="test",
        deploy_mode="private",
        downstream_enabled=True,
        auth_mode="stub",
        jwt_secret="unit-test-secret",
        ratelimit_enabled=False,
        dify_base_url="http://dify.test",
        dify_default_app_key="test-key",
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "records": [
                    {
                        "document_id": "doc_other_tenant",
                        "score": 0.9,
                        "segment": {"content": "A 企业受限资料", "position": {"page": 1}},
                    }
                ]
            },
        )

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_handler), base_url="http://dify.test"
    )
    dify = DifyClient("http://dify.test", "test-key", settings=online, client=http_client)

    bases = get_knowledge_base_store(online)
    await bases.create(
        KnowledgeBaseRecord(
            tenant_id="tnt_other",
            base_id="kb_qaCrossTenant",
            name="A 企业库",
            visibility="restricted",
            owner_user_id="usr_a",
        )
    )
    service = RagService(online, dify=dify, policy=PolicyService(online))
    attacker = AuthContext(
        tenant_id="tnt_attacker", user_id="usr_evil", roles=["knowledge:base:read"]
    )

    from app.core.errors import ApiError
    from app.schemas.rag import SearchRequest

    try:
        leaked = await service.search(
            SearchRequest(base_id="kb_qaCrossTenant", query="资料"), attacker
        )
    except ApiError as error:
        assert int(error.code) == 20003
        leaked = None
    finally:
        await http_client.aclose()

    assert leaked is None or leaked.segments == [], (
        "跨租户检索取回了对方租户数据："
        f"{[segment.segment_content for segment in (leaked.segments if leaked else [])]}"
    )
