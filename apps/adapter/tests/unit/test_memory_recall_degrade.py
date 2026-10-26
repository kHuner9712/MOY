"""召回与注入的降级验收（阶段四 T4-03.4，PRD §4.7 D-2 / D-3 / §4.4）。

四条判据（每条都是"用户能立刻感知"的形态）：

1. **D-2 超时**：召回超过预算 → **不注入**，回答照常（不是"等它慢慢查完"）；
2. **D-3 向量不可用**：换成"按类别 + 更新时间"照常注入——没有向量不等于没有记忆；
3. **关开关不注入**：关掉后 system 上下文里**不得出现任何 L3 事实**
   （PRD §4.4 明令"不允许关了就偷偷还用"）；
4. **注入内容不进 `data`**：记忆正文只在上游 prompt 里，响应里搜不到。

全部离线：向量用 Fake、记忆服务用内存实现，不依赖网络与真库。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from typing import Any

import pytest

from app.business.context_assembler import MEMORY_HEADER, ContextAssembler
from app.business.memory_service import MemoryService
from app.core.config import Settings
from app.core.metrics import MEMORY_INJECTED_TOTAL, MEMORY_RECALL_DEGRADED_TOTAL
from app.db.repositories.conversation import InMemoryMessageStore, MessageRecord
from app.db.repositories.memory import (
    InMemoryMemoryStore,
    MemoryRecord,
    MemorySettingRecord,
    new_memory_id,
)
from app.services.memory_embedding import NullEmbeddingProvider
from app.services.memory_extractor import MemoryExtractor, normalized_hash
from tests.conftest import TEST_MEMORY_APP_KEY, TEST_TENANT_ID, TEST_USER_ID

pytestmark = [pytest.mark.unit, pytest.mark.memory]

TENANT = TEST_TENANT_ID
USER = TEST_USER_ID
CONVERSATION = "conv_01K4Q8Z2X7M9B3N5P6R8T0V2W4"


class _SilentChat:
    """不会用到的补全替身（召回用例不触发抽取）。"""

    available = True

    async def complete(self, prompt: str) -> str:
        """返回空数组（不发生抽取）。

        Args:
            prompt: 提示词。

        Returns:
            str: `[]`。
        """
        return "[]"


class _SlowEmbedding:
    """会拖过召回预算的向量替身（模拟向量库变慢）。"""

    def __init__(self, delay: float = 0.5) -> None:
        self._delay = delay
        self.calls = 0

    @property
    def available(self) -> bool:
        """恒可用（"可用但慢"正是 D-2 的场景）。

        Returns:
            bool: True。
        """
        return True

    @property
    def model(self) -> str:
        """模型标识。

        Returns:
            str: 标识。
        """
        return "slow-embedding"

    @property
    def dim(self) -> int:
        """维度。

        Returns:
            int: 2。
        """
        return 2

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """延迟后返回向量。

        Args:
            texts: 文本。

        Returns:
            list[list[float]]: 向量。
        """
        self.calls += 1
        await asyncio.sleep(self._delay)
        return [[1.0, 0.0] for _ in texts]


class _FastEmbedding:
    """即时返回的向量替身（模拟向量可用）。"""

    def __init__(self) -> None:
        self.calls = 0

    @property
    def available(self) -> bool:
        """恒可用。

        Returns:
            bool: True。
        """
        return True

    @property
    def model(self) -> str:
        """模型标识。

        Returns:
            str: 标识。
        """
        return "fast-embedding"

    @property
    def dim(self) -> int:
        """维度。

        Returns:
            int: 2。
        """
        return 2

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """返回与事实向量一致的向量。

        Args:
            texts: 文本。

        Returns:
            list[list[float]]: 向量。
        """
        self.calls += 1
        return [[1.0, 0.0] for _ in texts]


def _settings(**overrides: Any) -> Settings:
    """构造召回用例的配置。

    Args:
        **overrides: 覆盖项。

    Returns:
        Settings: 配置实例。
    """
    base: dict[str, Any] = {
        "env": "test",
        "business_store": "memory",
        "dify_memory_app_key": TEST_MEMORY_APP_KEY,
        "memory_recall_timeout_seconds": 0.3,
        "memory_recall_top_k": 8,
    }
    base.update(overrides)
    return Settings(**base)


class _Harness:
    """召回用例装配体。"""

    def __init__(self, *, embedding: Any | None = None, settings: Settings | None = None) -> None:
        self.settings = settings or _settings()
        self.memories = InMemoryMemoryStore()
        self.messages = InMemoryMessageStore()
        self.embedding = embedding or NullEmbeddingProvider()
        self.service = MemoryService(
            self.settings,
            memories=self.memories,
            messages=self.messages,
            extractor=MemoryExtractor(_SilentChat()),
            embedding=self.embedding,
        )

    async def seed_fact(
        self,
        content: str,
        *,
        category: str = "preference",
        confidence: str = "high",
        user_id: str = USER,
        vector: Sequence[float] | None = None,
    ) -> MemoryRecord:
        """写入一条记忆（可选写向量）。

        Args:
            content: 正文。
            category: 类别。
            confidence: 置信度。
            user_id: 归属人。
            vector: 向量（`None` 不写）。

        Returns:
            MemoryRecord: 落库后的记录。
        """
        record = await self.memories.insert(
            MemoryRecord(
                tenant_id=TENANT,
                memory_id=new_memory_id(),
                user_id=user_id,
                category=category,
                content=content,
                confidence=confidence,
                normalized_hash=normalized_hash(category, content),
            )
        )
        assert record is not None
        if vector is not None:
            await self.memories.upsert_embedding(
                TENANT, record.memory_id, model="fake", dim=len(vector), embedding=vector
            )
        return record

    async def seed_message(self, role: str, content: str) -> None:
        """写入一条消息（`role=user` 会被组装器当作历史）。

        Args:
            role: 角色。
            content: 正文。
        """
        await self.messages.append(
            MessageRecord(
                tenant_id=TENANT,
                conversation_id=CONVERSATION,
                user_id=USER,
                role=role,
                content=content,
                status="settled",
            )
        )

    def assembler(self) -> ContextAssembler:
        """构造注入了本装配体记忆服务的组装器。

        Returns:
            ContextAssembler: 组装器。
        """
        return ContextAssembler(self.settings, messages=self.messages, memory=self.service)


class TestTimeoutDegrades:
    """D-2：召回超时 → 不注入、回答照常。"""

    async def test_slow_vector_recall_returns_nothing(self) -> None:
        """500ms 的向量调用 > 300ms 预算 → 召回返回空，并记降级指标。"""
        harness = _Harness(embedding=_SlowEmbedding(delay=0.5))
        await harness.seed_fact("汇报要表格式", vector=[1.0, 0.0])

        started = asyncio.get_running_loop().time()
        records = await harness.service.recall(TENANT, USER, "帮我写周报")
        elapsed = asyncio.get_running_loop().time() - started

        assert records == []
        assert elapsed < 0.45  # 卡在预算上，而不是等满 500ms
        assert MEMORY_RECALL_DEGRADED_TOTAL.value(reason="timeout") == 1
        assert MEMORY_INJECTED_TOTAL.value(result="degraded") == 1

    async def test_assembled_query_has_no_memory_when_recall_times_out(self) -> None:
        """超时后 query 里没有记忆块，`injected_memory_count=0`。"""
        harness = _Harness(embedding=_SlowEmbedding(delay=0.5))
        await harness.seed_fact("汇报要表格式", vector=[1.0, 0.0])
        await harness.seed_message("user", "上次说的那个报表")

        assembled = await harness.assembler().build(
            TENANT,
            USER,
            CONVERSATION,
            current_user_text="帮我写周报",
            current_message_id="",
        )

        assert assembled.injected_memory_count == 0
        assert MEMORY_HEADER not in assembled.query_text
        assert "汇报要表格式" not in assembled.query_text
        # 历史照常在（D-2 只说"不注入 L3"，不是"整条上下文都不要"）
        assert "上次说的那个报表" in assembled.query_text


class TestVectorUnavailableDegrades:
    """D-3：向量不可用 → 按类别召回照常注入。"""

    async def test_falls_back_to_category_recall(self) -> None:
        """没有向量能力时仍能召回（按更新时间取前 N）。"""
        harness = _Harness(embedding=NullEmbeddingProvider())
        await harness.seed_fact("汇报要表格式", category="preference")
        await harness.seed_fact("我叫赵工", category="identity")

        records = await harness.service.recall(TENANT, USER, "帮我写周报")

        assert {item.content for item in records} == {"汇报要表格式", "我叫赵工"}
        assert MEMORY_RECALL_DEGRADED_TOTAL.value(reason="vector_unavailable") == 1
        assert MEMORY_INJECTED_TOTAL.value(result="degraded") == 1

    async def test_vector_available_uses_similarity_order(self) -> None:
        """向量可用时按余弦距离排序，且只取 top_k。"""
        harness = _Harness(embedding=_FastEmbedding())
        await harness.seed_fact("汇报要表格式", vector=[1.0, 0.0])
        await harness.seed_fact("我叫赵工", category="identity", vector=[0.0, 1.0])

        records = await harness.service.recall(TENANT, USER, "周报", top_k=1)

        assert [item.content for item in records] == ["汇报要表格式"]
        assert MEMORY_INJECTED_TOTAL.value(result="ok") == 1


class TestSwitchOff:
    """关开关 → 不注入（"不允许关了就偷偷还用"）。"""

    async def test_global_switch_off_returns_nothing(self) -> None:
        """全局关闭：recall 返回空、query 无记忆块、指标记 empty。"""
        harness = _Harness()
        await harness.seed_fact("汇报要表格式")
        await harness.memories.upsert_setting(
            MemorySettingRecord(tenant_id=TENANT, user_id=USER, enabled=False)
        )

        records = await harness.service.recall(TENANT, USER, "帮我写周报")
        assembled = await harness.assembler().build(
            TENANT, USER, CONVERSATION, current_user_text="帮我写周报"
        )

        assert records == []
        assert assembled.injected_memory_count == 0
        assert MEMORY_HEADER not in assembled.query_text
        # 本用例调了两次 recall（直接一次 + 组装一次），故断言"只记 empty"而不是精确条数
        assert MEMORY_INJECTED_TOTAL.value(result="empty") >= 1
        assert MEMORY_INJECTED_TOTAL.value(result="ok") == 0

    async def test_category_switch_off_excludes_that_category(self) -> None:
        """只关一类：该类不注入，其它类照常。"""
        harness = _Harness()
        await harness.seed_fact("汇报要表格式", category="preference")
        await harness.seed_fact("我叫赵工", category="identity")
        await harness.memories.upsert_setting(
            MemorySettingRecord(
                tenant_id=TENANT,
                user_id=USER,
                enabled=True,
                category_switches={"preference": False},
            )
        )

        records = await harness.service.recall(TENANT, USER, "帮我写周报")

        assert [item.content for item in records] == ["我叫赵工"]


class TestInjectionShape:
    """注入形态：低置信度不注入、内容不进 data、条数上限生效。"""

    async def test_low_confidence_is_not_injected(self) -> None:
        """low 置信度只在管理页显示，不参与回答。"""
        harness = _Harness()
        await harness.seed_fact("我可能是做采购的", category="org", confidence="low")
        await harness.seed_fact("我叫赵工", category="identity")

        records = await harness.service.recall(TENANT, USER, "帮我写周报")

        assert [item.content for item in records] == ["我叫赵工"]

    async def test_top_k_limits_injection(self) -> None:
        """`top_k` 生效（默认 8，可传更小值）。"""
        harness = _Harness()
        for index in range(12):
            await harness.seed_fact(f"第{index}条偏好{index}")

        records = await harness.service.recall(TENANT, USER, "随便问问", top_k=3)

        assert len(records) == 3

    async def test_assembled_query_contains_memory_block_before_history(self) -> None:
        """记忆块在历史之前、本轮问题在最后（模型对末尾注意力最高）。"""
        harness = _Harness()
        await harness.seed_fact("汇报要表格式", category="preference")
        await harness.seed_message("user", "上次说的那个报表")

        assembled = await harness.assembler().build(
            TENANT, USER, CONVERSATION, current_user_text="帮我写周报"
        )

        query = assembled.query_text
        assert assembled.injected_memory_count == 1
        assert "工作习惯：汇报要表格式" in query
        assert query.index(MEMORY_HEADER) < query.index("上次说的那个报表")
        assert query.index("上次说的那个报表") < query.index("帮我写周报")

    async def test_memory_content_never_appears_in_api_data(
        self, client: Any, auth_headers: dict[str, str], make_idempotency_headers: Any
    ) -> None:
        """记忆正文只进上游 prompt，**不出现在用户可见的 `data` 里**。"""
        from app.db.repositories.memory import get_memory_store
        from app.db.tenant import PRIVATE_TENANT

        store = get_memory_store()
        await store.insert(
            MemoryRecord(
                tenant_id=PRIVATE_TENANT,
                memory_id=new_memory_id(),
                user_id=USER,
                category="preference",
                content="汇报要表格式",
                normalized_hash=normalized_hash("preference", "汇报要表格式"),
            )
        )
        response = client.post(
            "/api/v1/model/generate",
            json={
                "conversation_id": "conv_mem_leak",
                "profile": "general",
                "messages": [{"role": "user", "content": "帮我写周报"}],
            },
            headers={**auth_headers, **make_idempotency_headers("mem-leak")},
        )
        assert response.json()["code"] == 0
        assert "汇报要表格式" not in json.dumps(response.json(), ensure_ascii=False)
        assert "记忆" not in json.dumps(response.json()["data"], ensure_ascii=False)
