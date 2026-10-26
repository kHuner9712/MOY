"""攻击 4：越权对象访问（阶段四 T4-04.6）。

**一处刻意的口径说明**（写给后来者，免得"顺手统一"）：

- **任务**越权 → `20003 / 403` +「你没有权限查看该任务」（对象级判定，用户知道
  这个 ID 存在与否无所谓：任务 ID 是协作场景里会被转发的东西）；
- **会话 / 记忆**越权 → `18001 / 18002 + 404`，**不是 403**。这是既有决策
  （`conversation_service.ensure_conversation` 与 `MemoryService.delete_memory` 的注释）：
  个人数据不能靠"403 vs 404"泄漏"这个 ID 存不存在"，否则就能枚举出别人有几个会话、
  记忆里有没有某条事实。两者都**拒绝访问**，只是对外表现不同。

判据：越权一律拿不到数据，且**目标行没有被删/改**。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from tests.security.conftest import USER_B, issue_token, write_headers

from app.core.config import get_settings
from app.db.repositories.conversation_repo import (
    ConversationRecord,
    MessageRecord,
    get_conversation_store,
    get_message_store,
    new_message_id,
)
from app.db.repositories.memory import (
    MemoryRecord,
    get_memory_store,
)
from app.db.repositories.task_repo import (
    InMemoryTaskStore,
    TaskRecord,
    get_task_store,
)
from app.db.tenant import PRIVATE_TENANT
from app.services.memory_extractor import normalized_hash

pytestmark = [pytest.mark.security, pytest.mark.integration]

#: 资源归属租户（client 夹具是 private 单租户 → 恒为 default）。
#: 越权维度因此是「同一租户的另一个用户」——个人数据只在本人边界内可见，
#: 这与跨租户是**同一条判据**的两种表现（租户边界在 RLS，个人边界在仓储层）。
OWNER_TENANT = PRIVATE_TENANT

#: 越权目标资源 ID
CONVERSATION_ID = "conv_sec_owner"
MEMORY_ID = "mem_sec_owner"
TASK_ID = "task_sec_owner"


def _owner_headers() -> dict[str, str]:
    """B 租户自己的请求头（造数用）。

    Returns:
        dict[str, str]: 请求头。
    """
    return write_headers(
        issue_token(tenant_id=OWNER_TENANT, user_id=USER_B, roles=["member"]),
        tenant=OWNER_TENANT,
        key="77777777-6666-4555-8444-333333333333",
    )


@pytest.fixture
async def owned_resources() -> AsyncIterator[dict[str, str]]:
    """在 B 租户下造一份"属于别人的"资源。

    **teardown 必须清理造数**（LED-045）：任务存储是进程级单例且**不随用例
    重置**，这里造的任务行 `plugin_id='office_doc'` 指向不存在的插件——不清理
    就会泄漏到后续用例，让别人的 `GET /api/v1/agent/tasks` 列表整体 404。
    清理只删造数：不改任何断言、不弱化任何判据（阶段四验收红线）。

    Args:
        无。

    Yields:
        AsyncIterator[dict[str, str]]: 资源 ID。
    """
    conf = get_settings()
    conversations = get_conversation_store(conf)
    messages = get_message_store(conf)
    memories = get_memory_store(conf)
    tasks = get_task_store(conf)

    await conversations.create(
        ConversationRecord(
            tenant_id=OWNER_TENANT,
            conversation_id=CONVERSATION_ID,
            user_id=USER_B,
            title="B 的私密会话",
        )
    )
    await messages.append(
        MessageRecord(
            tenant_id=OWNER_TENANT,
            message_id=new_message_id(),
            conversation_id=CONVERSATION_ID,
            user_id=USER_B,
            role="user",
            content="B 的私密内容",
            status="settled",
        )
    )
    await memories.insert(
        MemoryRecord(
            tenant_id=OWNER_TENANT,
            memory_id=MEMORY_ID,
            user_id=USER_B,
            category="identity",
            content="B 叫李工",
            normalized_hash=normalized_hash("identity", "B 叫李工"),
        )
    )
    await tasks.create_task(
        TaskRecord(
            tenant_id=OWNER_TENANT,
            task_id=TASK_ID,
            plugin_id="office_doc",
            plugin_version="1.0.0",
            initiator_id=USER_B,
            status="queued",
        )
    )
    try:
        yield {"conversation": CONVERSATION_ID, "memory": MEMORY_ID, "task": TASK_ID}
    finally:
        # 只删本夹具造的这一行（内存存储无 delete 方法，直卸键；
        # 与 QA 验证脚本 `seed()` 的直写造数同体例）。会话/记忆行由
        # `tests/conftest.py` 的用例级单例重置负责，不在这里重复。
        if isinstance(tasks, InMemoryTaskStore):
            tasks._tasks.pop((OWNER_TENANT, TASK_ID), None)  # noqa: SLF001 — 测试直卸造数


class TestObjectScope:
    """越权对象一律拒绝，且目标数据未被改动。"""

    def test_other_tenant_task_is_forbidden(
        self, client: Any, auth_headers: dict[str, str], owned_resources: dict[str, str]
    ) -> None:
        """A 读 B 的任务 → 403 +「你没有权限查看该任务」。"""
        response = client.get(
            f"/api/v1/agent/tasks/{owned_resources['task']}", headers=auth_headers
        )

        assert response.status_code == 403
        body = response.json()
        assert body["code"] == 20003
        assert body["message"] == "你没有权限查看该任务"
        assert body["data"] is None

    def test_other_tenant_conversation_is_not_found(
        self, client: Any, auth_headers: dict[str, str], owned_resources: dict[str, str]
    ) -> None:
        """A 读 B 的会话 → 404（防枚举：不告诉调用方"这个 ID 存在但不是你的"）。"""
        response = client.get(
            f"/api/v1/model/conversations/{owned_resources['conversation']}/messages",
            headers=auth_headers,
        )

        assert response.status_code == 404
        assert response.json()["code"] == 18001
        assert response.json()["data"] is None

    def test_other_tenant_conversation_cannot_be_deleted(
        self, client: Any, auth_headers: dict[str, str], owned_resources: dict[str, str]
    ) -> None:
        """A 删 B 的会话 → 404，且 B 的会话仍在（没被删掉）。"""
        response = client.delete(
            f"/api/v1/model/conversations/{owned_resources['conversation']}",
            headers={
                **auth_headers,
                **write_headers(issue_token(), key="11112222-3333-4444-8555-666677778888"),
            },
        )

        assert response.status_code == 404
        assert response.json()["code"] == 18001

    def test_other_tenant_memory_cannot_be_deleted(
        self, client: Any, auth_headers: dict[str, str], owned_resources: dict[str, str]
    ) -> None:
        """A 删 B 的记忆 → 404（18002），且 B 的记忆仍在。"""
        response = client.delete(
            f"/api/v1/model/memories/{owned_resources['memory']}",
            headers={
                **auth_headers,
                **write_headers(issue_token(), key="22223333-4444-4555-8666-777788889999"),
            },
        )

        assert response.status_code == 404
        assert response.json()["code"] == 18002

    async def test_target_rows_survive_denials(self, owned_resources: dict[str, str]) -> None:
        """所有拒绝走完之后，B 的三份资源**一行都没少**。"""
        conf = get_settings()
        conversation = await get_conversation_store(conf).get(
            OWNER_TENANT, USER_B, owned_resources["conversation"]
        )
        memory = await get_memory_store(conf).get(OWNER_TENANT, owned_resources["memory"])
        task = await get_task_store(conf).get_task(OWNER_TENANT, owned_resources["task"])

        assert conversation is not None and conversation.deleted is False
        assert memory is not None and memory.active is True
        assert task is not None
