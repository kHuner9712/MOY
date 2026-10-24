"""LED-045 回归：单条任务声明缺失不得拖垮 `GET /api/v1/agent/tasks` 列表。

**缺陷**（QA 证实的真实根因）：一条 `plugin_id` 指向不存在插件的任务行
（`test_attack_object_scope.py` 的造数曾泄漏成这样）进入内存库后，列表装配
路径 `_manifest_of` 抛 16002，**整个列表 404**——单独跑列表用例全绿，与造数
用例连跑才红（顺序敏感）。

**修复口径**（产品级，双轨）：

- 列表是**聚合视图**：单条任务声明缺失时，该条 `goal` 退化为插件标识
  （中性兜底，不编造名称），列表照常返回 200 + code=0；
- 详情路径保持**显性 16002**：没有声明无法渲染步骤/确认语义/核查清单，
  静默兜底会给出错误结果，必须显性失败。

**回归判据**：先造脏任务，再调列表——列表成功返回、脏任务仍在列、
`goal` 为中性兜底、全文无 16002；同一条任务的详情仍为 16002。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.repositories.task_repo import (
    InMemoryTaskStore,
    TaskRecord,
    get_task_store,
)
from tests.conftest import TEST_USER_ID, make_token

pytestmark = pytest.mark.integration

#: 私有化模式的生效租户（`resolve_runtime_tenant` 在 private 模式恒返回 `default`）
TENANT = "default"

#: 复刻泄漏形态的脏插件 ID（注册表里不存在——正是 QA 复现用的那个）
MISSING_PLUGIN_ID = "office_doc"

#: 脏任务 ID（固定值：造数、列表断言与详情请求都要引用）
DIRTY_TASK_ID = "task_led045_missing_manifest"

#: 被测端点
LIST_PATH = "/api/v1/agent/tasks"


@pytest.fixture
async def dirty_task(settings: Settings) -> AsyncIterator[str]:
    """造一条 `plugin_id` 指向不存在插件的任务（直写存储单例，复刻泄漏形态）。

    任务存储是进程级单例且**不随用例重置**——这正是缺陷能跨用例传染的原因。
    这里显式用例内造数、用例后卸数，把回归判据钉在"列表必须容忍"上。

    Args:
        settings: 桩模式配置（内存后端）。

    Yields:
        AsyncIterator[str]: 脏任务 ID。
    """
    store = get_task_store(settings)
    assert isinstance(store, InMemoryTaskStore), "本回归默认跑内存后端"
    await store.create_task(
        TaskRecord(
            tenant_id=TENANT,
            task_id=DIRTY_TASK_ID,
            plugin_id=MISSING_PLUGIN_ID,
            plugin_version="1.0.0",
            initiator_id=TEST_USER_ID,
            status="queued",
            idem_key="idem-led045-missing-manifest",
        )
    )
    try:
        yield DIRTY_TASK_ID
    finally:
        # 只卸本用例造的这一行（内存存储无 delete，直卸键；与 QA 验证脚本同体例）
        store._tasks.pop((TENANT, DIRTY_TASK_ID), None)  # noqa: SLF001 — 测试直卸造数


def _headers(settings: Settings) -> dict[str, str]:
    """构造鉴权请求头（发起人与脏任务一致，保证详情路径可过对象级判权）。

    Args:
        settings: 配置实例（取签发密钥）。

    Returns:
        dict[str, str]: 请求头。
    """
    token = make_token(settings=settings, user_id=TEST_USER_ID, tenant_id=TENANT, roles=["member"])
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": TENANT}


class TestListToleratesMissingManifest:
    """列表容忍 / 详情显性失败的双轨回归（LED-045）。"""

    def test_list_succeeds_with_neutral_fallback(
        self, client: TestClient, settings: Settings, dirty_task: str
    ) -> None:
        """脏任务在位 → 列表 200 + code=0；该条 goal 退化为插件标识；全文无 16002。"""
        response = client.get(f"{LIST_PATH}?page=1&page_size=50", headers=_headers(settings))

        assert response.status_code == 200
        body = response.json()
        assert body["code"] == 0
        assert "16002" not in response.text
        items = body["data"]["items"]
        dirty = next((item for item in items if item["task_id"] == dirty_task), None)
        assert dirty is not None, "脏任务必须仍在列表里（容忍 ≠ 悄悄过滤掉）"
        assert dirty["goal"] == MISSING_PLUGIN_ID
        assert dirty["plugin_id"] == MISSING_PLUGIN_ID
        assert dirty["status"] == "queued"

    async def test_detail_of_dirty_task_still_raises_16002(
        self, client: TestClient, settings: Settings, dirty_task: str
    ) -> None:
        """同一条脏任务的详情仍走显性 16002（没有声明无法渲染，必须显性失败）。"""
        response = client.get(f"{LIST_PATH}/{dirty_task}", headers=_headers(settings))

        assert response.status_code == 404
        body = response.json()
        assert body["code"] == 16002
        assert body["data"] is None
