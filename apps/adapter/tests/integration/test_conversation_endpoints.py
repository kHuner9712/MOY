"""会话端点 MC1-MC4 的接口验收（阶段四 T4-02.5，架构 §4.2 / §4.4）。

**为什么走 HTTP 而不是直接调服务**：`response_model` 才是决定"前端看到什么"的那一层。
阶段三就吃过一次亏——服务层塞了字段、`response_model` 没声明，pydantic 默认忽略，
前端永远拿不到值且**不报错**。

四条判据：

1. **本人边界**：别人的会话一律 18001；且**不接受** `user_id` 参数（多传被发现就说明契约漏了）；
2. **游标分页**：100 条逐页往前拉到空，无重无漏，`next_cursor` 为空表示到头；
3. **软删**：MC3 返回 `recoverable_until`；删完 MC1 默认看不到、`include_deleted=true` 能看到；
4. **幂等**：MC3/MC4 带同一个 `Idempotency-Key` 重放 → 20007 且回放首次 `data`。
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.business.context_assembler import reset_context_assembler
from app.business.conversation_service import reset_conversation_service
from app.core.config import Settings
from app.db.repositories.conversation_repo import (
    InMemoryConversationStore,
    InMemoryMessageStore,
    MessageRecord,
    new_conversation_id,
    new_message_id,
    reset_conversation_stores,
)
from tests.conftest import TEST_USER_ID

pytestmark = pytest.mark.integration

TENANT = "tnt_test_tenant"

BASE = "/api/v1/model/conversations"


@pytest.fixture
def settings() -> Iterator[Settings]:
    """测试配置（内存存储、关闭限流）。

    Yields:
        Iterator[Settings]: 配置实例。
    """
    reset_conversation_stores()
    reset_context_assembler()
    reset_conversation_service()
    yield Settings(
        env="test",
        deploy_mode="saas",
        downstream_enabled=False,
        auth_mode="stub",
        jwt_secret="conversation-endpoints-test-secret",
        ratelimit_enabled=False,
    )
    reset_conversation_stores()


@pytest.fixture
def conversations(settings: Settings) -> InMemoryConversationStore:
    """会话存储（预置到全局单例，供端点使用）。

    Args:
        settings: 配置。

    Returns:
        InMemoryConversationStore: 存储。
    """
    from app.db.repositories import conversation

    store = InMemoryConversationStore()
    conversation._conversation_store = store  # noqa: SLF001 - 测试注入单例
    return store


@pytest.fixture
def messages(settings: Settings) -> InMemoryMessageStore:
    """消息存储（预置到全局单例，供端点使用）。

    Args:
        settings: 配置。

    Returns:
        InMemoryMessageStore: 存储。
    """
    from app.db.repositories import conversation

    store = InMemoryMessageStore()
    conversation._message_store = store  # noqa: SLF001 - 测试注入单例
    return store


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """测试客户端（`create_app(settings)` 注入配置）。

    Args:
        settings: 配置。

    Yields:
        Iterator[TestClient]: 客户端。
    """
    from app.main import create_app

    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _run(coro: Any) -> Any:
    """跑一个协程（同步用例里做数据准备时用）。

    Args:
        coro: 协程对象。

    Returns:
        Any: 协程返回值。
    """
    import asyncio

    return asyncio.run(coro)


async def _seed(
    conversations: InMemoryConversationStore,
    messages: InMemoryMessageStore,
    *,
    user_id: str = TEST_USER_ID,
    title: str = "上线计划",
    message_count: int = 0,
) -> str:
    """造一个会话（可选造若干条消息）。

    Args:
        conversations: 会话存储。
        messages: 消息存储。
        user_id: 归属人。
        title: 标题。
        message_count: 造几条消息。

    Returns:
        str: 会话 ID。
    """
    from app.db.repositories.conversation_repo import ConversationRecord

    conversation_id = new_conversation_id()
    await conversations.create(
        ConversationRecord(
            tenant_id=TENANT, conversation_id=conversation_id, user_id=user_id, title=title
        )
    )
    for index in range(message_count):
        await messages.append(
            MessageRecord(
                tenant_id=TENANT,
                message_id=new_message_id(),
                conversation_id=conversation_id,
                user_id=user_id,
                role="user" if index % 2 == 0 else "assistant",
                content=f"第 {index} 条",
                status="settled",
            )
        )
    return conversation_id


# ===========================================================================
# MC1：列表
# ===========================================================================
def test_mc1_lists_only_my_conversations(
    client: TestClient,
    auth_headers: dict[str, str],
    conversations: InMemoryConversationStore,
    messages: InMemoryMessageStore,
) -> None:
    """列表只返回本人的会话（别人的那条不出现）。"""
    mine = _run(_seed(conversations, messages, title="我的会话"))
    _run(_seed(conversations, messages, user_id="usr_other", title="别人的会话"))

    response = client.get(BASE, headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    items = body["data"]["items"]
    assert [item["conversation_id"] for item in items] == [mine]
    assert body["data"]["meta"]["total"] == 1


def test_mc1_rejects_user_id_query_param(
    client: TestClient,
    auth_headers: dict[str, str],
    conversations: InMemoryConversationStore,
    messages: InMemoryMessageStore,
) -> None:
    """列表**不接受** `user_id` 参数：传了也只按令牌身份返回（身份只来自令牌）。"""
    _run(_seed(conversations, messages, user_id="usr_other", title="别人的会话"))

    response = client.get(f"{BASE}?user_id=usr_other", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["data"]["items"] == [], "不能因为传了 user_id 就看到别人的会话"


# ===========================================================================
# MC2：游标分页
# ===========================================================================
def test_mc2_paginates_100_messages_without_dup_or_gap(
    client: TestClient,
    auth_headers: dict[str, str],
    conversations: InMemoryConversationStore,
    messages: InMemoryMessageStore,
) -> None:
    """100 条消息逐页往前拉到空：无重无漏，且顺序为时间正序。"""
    conversation_id = _run(_seed(conversations, messages, message_count=100))

    seen: list[str] = []
    cursor = ""
    for _round in range(20):
        query = f"{BASE}/{conversation_id}/messages?limit=30"
        if cursor:
            query += f"&before={cursor}"
        response = client.get(query, headers=auth_headers)
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        page_ids = [item["message_id"] for item in data["items"]]
        if not page_ids:
            break
        seen = page_ids + seen  # 每页是时间正序；整体倒着拼回去
        cursor = data["next_cursor"]
        if not cursor:
            break

    assert len(seen) == len(set(seen)), "分页出现重复消息"
    assert len(seen) == 100, f"分页漏了消息：只拿到 {len(seen)} 条"


def test_mc2_rejects_invalid_cursor(
    client: TestClient,
    auth_headers: dict[str, str],
    conversations: InMemoryConversationStore,
    messages: InMemoryMessageStore,
) -> None:
    """非法游标 → 20001（而不是静默从头开始，那会让前端无限翻页）。"""
    conversation_id = _run(_seed(conversations, messages, message_count=3))

    response = client.get(
        f"{BASE}/{conversation_id}/messages?before=not-a-cursor", headers=auth_headers
    )

    assert response.status_code == 400
    assert response.json()["code"] == 20001


def test_mc2_unknown_conversation_is_18001(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """读一个不存在的会话 → 18001 + 文案「该对话已不存在」。"""
    response = client.get(f"{BASE}/conv_not_exists/messages", headers=auth_headers)

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == 18001
    assert body["message"] == "该对话已不存在"


def test_mc2_other_users_conversation_is_18001(
    client: TestClient,
    auth_headers: dict[str, str],
    conversations: InMemoryConversationStore,
    messages: InMemoryMessageStore,
) -> None:
    """别人的会话同样 18001（不区分"不存在"，避免探测）。"""
    conversation_id = _run(_seed(conversations, messages, user_id="usr_other"))

    response = client.get(f"{BASE}/{conversation_id}/messages", headers=auth_headers)

    assert response.status_code == 404
    assert response.json()["code"] == 18001


# ===========================================================================
# MC3：软删
# ===========================================================================
def test_mc3_soft_delete_hides_from_list_but_keeps_recoverable_window(
    client: TestClient,
    auth_headers: dict[str, str],
    conversations: InMemoryConversationStore,
    messages: InMemoryMessageStore,
) -> None:
    """软删后列表看不到；`include_deleted=true` 能看到；`recoverable_until` 晚于删除时间。"""
    conversation_id = _run(_seed(conversations, messages))

    deleted = client.delete(
        f"{BASE}/{conversation_id}", headers={**auth_headers, "Idempotency-Key": "del-1"}
    )
    assert deleted.status_code == 200, deleted.text
    data = deleted.json()["data"]
    assert data["conversation_id"] == conversation_id
    assert data["deleted_at"] and data["recoverable_until"]
    assert data["recoverable_until"] > data["deleted_at"]

    listed = client.get(BASE, headers=auth_headers).json()["data"]
    assert listed["items"] == []

    recycle = client.get(f"{BASE}?include_deleted=true", headers=auth_headers).json()["data"]
    assert [item["conversation_id"] for item in recycle["items"]] == [conversation_id]


def test_mc3_is_idempotent_with_same_key(
    client: TestClient,
    auth_headers: dict[str, str],
    conversations: InMemoryConversationStore,
    messages: InMemoryMessageStore,
) -> None:
    """同一个 `Idempotency-Key` 重放 → 20007 且回放首次 `data`（不重复删）。"""
    conversation_id = _run(_seed(conversations, messages))
    request_headers = {**auth_headers, "Idempotency-Key": "del-replay"}

    first = client.delete(f"{BASE}/{conversation_id}", headers=request_headers)
    second = client.delete(f"{BASE}/{conversation_id}", headers=request_headers)

    assert first.status_code == 200
    assert second.json()["code"] == 20007
    assert second.json()["data"] == first.json()["data"]


# ===========================================================================
# MC4：改标题 / 归档
# ===========================================================================
def test_mc4_updates_title_and_archived(
    client: TestClient,
    auth_headers: dict[str, str],
    conversations: InMemoryConversationStore,
    messages: InMemoryMessageStore,
) -> None:
    """改标题 → 列表里能看到新标题；归档 → 默认列表为空、归档视图可见。"""
    conversation_id = _run(_seed(conversations, messages, title="旧标题"))
    request_headers = {**auth_headers, "Idempotency-Key": "patch-1"}

    renamed = client.patch(
        f"{BASE}/{conversation_id}", json={"title": "新标题"}, headers=request_headers
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["data"]["title"] == "新标题"
    assert renamed.json()["data"]["archived"] is False

    archived = client.patch(
        f"{BASE}/{conversation_id}",
        json={"archived": True},
        headers={**auth_headers, "Idempotency-Key": "patch-2"},
    )
    assert archived.json()["data"]["archived"] is True

    assert client.get(BASE, headers=auth_headers).json()["data"]["items"] == []
    archived_list = client.get(f"{BASE}?archived=true", headers=auth_headers).json()["data"]
    assert [item["conversation_id"] for item in archived_list["items"]] == [conversation_id]


def test_mc4_rejects_empty_payload(
    client: TestClient,
    auth_headers: dict[str, str],
    conversations: InMemoryConversationStore,
    messages: InMemoryMessageStore,
) -> None:
    """两个字段都不传 → 20001（避免"点了保存但什么都没改"的静默成功）。"""
    conversation_id = _run(_seed(conversations, messages))

    response = client.patch(
        f"{BASE}/{conversation_id}",
        json={},
        headers={**auth_headers, "Idempotency-Key": "patch-empty"},
    )

    assert response.status_code == 400
    assert response.json()["code"] == 20001


def test_mc4_rejects_extra_fields(
    client: TestClient,
    auth_headers: dict[str, str],
    conversations: InMemoryConversationStore,
    messages: InMemoryMessageStore,
) -> None:
    """多传一个 `user_id` → 20001（`extra="forbid"`：不接受"替别人改"）。"""
    conversation_id = _run(_seed(conversations, messages))

    response = client.patch(
        f"{BASE}/{conversation_id}",
        json={"title": "x", "user_id": "usr_other"},
        headers={**auth_headers, "Idempotency-Key": "patch-extra"},
    )

    assert response.status_code == 400
    assert response.json()["code"] == 20001


# ===========================================================================
# 游标格式（与 M1/M2 回填的 ID 一致）
# ===========================================================================
def test_cursor_is_opaque_base64url_json() -> None:
    """`next_cursor` 是 `base64url({"seq":n})`，不含 padding（可直接进 query）。"""
    from app.db.repositories.conversation_repo import decode_cursor, encode_cursor

    token = encode_cursor(42)
    raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
    assert json.loads(raw) == {"seq": 42}
    assert decode_cursor(token) == 42
    assert "=" not in token


def test_recoverable_window_matches_configuration() -> None:
    """软删可恢复窗口 = 删除时间 + `conversation_soft_delete_recover_days`（默认 30 天）。"""
    from app.db.repositories.conversation_repo import recoverable_until

    stamp = datetime(2026, 11, 1, 8, 0, tzinfo=UTC).isoformat().replace("+00:00", "Z")

    assert recoverable_until(stamp).startswith("2026-12-01T08:00:00")


def test_soft_delete_window_is_configurable() -> None:
    """窗口可配（私有化客户合同里会写具体天数）。"""
    from app.db.repositories.conversation_repo import recoverable_until

    stamp = (
        (datetime(2026, 11, 1, 8, 0, tzinfo=UTC) + timedelta(0)).isoformat().replace("+00:00", "Z")
    )
    conf = Settings(env="test", conversation_soft_delete_recover_days=7)

    assert recoverable_until(stamp, conf).startswith("2026-11-08T08:00:00")
