"""仓储批量查询单测（审计 P2-6：N+1 收口后新增的批量接口）。

## 为什么单独立一组用例

批量接口是**为性能而加的第二条实现路径**：`catalogs_of` / `count_catalog_by_category` /
`counts_occupying` 一旦与逐条接口口径不一致（漏下架行、把零值也塞进字典、算错
占用状态），症状是「已安装列表少了一个插件」或「在跑任务数少 1」——而逐条接口
的用例**全绿**，因为逐条实现根本没被改动。

所以这里不做"新增接口自己跑通"这种弱断言，而是逐个方法与逐条接口做**等价断言**：
同一批 id 上，批量结果的每一项必须等于对应的逐条结果。另外钉住三件容易写错的事：

1. `catalogs_of` **含下架行**（`get_catalog` 的既有语义：由服务层判 `16002`，
   批量版若偷偷过滤掉下架行，前端会看到"插件凭空消失"而不是"该插件已下架"）；
2. `catalogs_of` 对**缺失 id 不入字典**（调用方靠 `dict.get()` 回落，而不是靠 KeyError）；
3. `counts_occupying` **只回非零项**（与 `count_occupying` 返回 0 等价，避免把
   全部插件都塞进字典让调用方分不清"0 个在跑"与"没有这个插件"）。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from typing import Any

import pytest

from app.db.repositories.plugin_repo import CATALOG_AVAILABLE, InMemoryPluginStore
from app.db.repositories.task_repo import InMemoryTaskStore, TaskRecord
from app.plugins.loader import PluginRegistry

pytestmark = pytest.mark.unit

TENANT = "tnt_repo_batch"
OTHER_TENANT = "tnt_repo_batch_other"


def _store() -> tuple[InMemoryPluginStore, list[str]]:
    """构造已播种的插件存储。

    Returns:
        tuple[InMemoryPluginStore, list[str]]: `(存储, 目录里的插件 id 列表)`。
    """
    registry = PluginRegistry()
    assert registry.load_all() == []
    store = InMemoryPluginStore(registry)
    plugin_ids = [manifest.plugin_id for manifest in registry.all_manifests()]
    assert len(plugin_ids) >= 2, "样本太少无法验证批量语义"
    return store, plugin_ids


async def test_catalogs_of_equals_get_catalog_and_keeps_hidden_rows() -> None:
    """批量读目录 == 逐条读目录，且**含下架行**（服务层靠它判 16002）。"""
    store, plugin_ids = _store()
    expected: dict[str, Any] = {}
    for plugin_id in plugin_ids:
        expected[plugin_id] = await store.get_catalog(plugin_id)

    hidden_id = plugin_ids[0]
    record = await store.get_catalog(hidden_id)
    assert record is not None
    # 造一条下架行：批量接口若按 `status='available'` 过滤，这里就会缺键。
    # 直接改内存字典是这条用例唯一可行的造数方式（内存后端不暴露状态写入口）；
    # SLF001 显式豁免，且只改测试自己的实例。
    store._catalog[hidden_id] = replace(record, status="hidden")  # noqa: SLF001 - 造下架行

    batch = await store.catalogs_of(plugin_ids)

    assert set(batch) == set(plugin_ids), "批量结果必须每个 id 都有键（含下架行）"
    assert batch[hidden_id].status == "hidden"
    assert batch[hidden_id].installable is False
    for plugin_id in plugin_ids[1:]:
        assert batch[plugin_id] == expected[plugin_id]


async def test_catalogs_of_ignores_missing_ids_and_empty_input() -> None:
    """缺失 id 不入字典；空入参直接返回空字典（不打库）。"""
    store, plugin_ids = _store()

    assert await store.catalogs_of([]) == {}
    batch = await store.catalogs_of([plugin_ids[0], "plg_nosuchplugin", plugin_ids[0]])
    assert set(batch) == {plugin_ids[0]}, "缺失 id 不得进字典，重复 id 不得重复计数"


async def test_count_catalog_by_category_equals_list_catalog_grouping() -> None:
    """分类计数 == `list_catalog()` 的分组结果（含排除下架行的口径）。"""
    store, plugin_ids = _store()
    listed = await store.list_catalog()
    assert listed, "目录不应为空"
    expected = Counter(record.category for record in listed)

    counts = await store.count_catalog_by_category()
    assert counts == dict(expected)
    assert sum(counts.values()) == len(listed)

    # 下架一个插件：两个接口必须同步减少（都按 `status='available'` 口径）
    record = await store.get_catalog(plugin_ids[0])
    assert record is not None
    assert record.status == CATALOG_AVAILABLE
    store._catalog[plugin_ids[0]] = replace(record, status="hidden")  # noqa: SLF001 - 造下架行

    after = await store.count_catalog_by_category()
    assert sum(after.values()) == len(await store.list_catalog()) == len(listed) - 1
    assert after.get(record.category, 0) == expected[record.category] - 1


async def test_counts_occupying_equals_per_plugin_counts() -> None:
    """批量占用计数 == 逐条计数（且只回非零项）。"""
    store, plugin_ids = _store()
    del store  # 本用例只用 id 清单
    first, second = plugin_ids[0], plugin_ids[1]
    tasks = InMemoryTaskStore()
    seeded = (
        ("task_batch_running", first, "running"),
        ("task_batch_waiting", first, "waiting_confirm"),
        ("task_batch_done", first, "completed"),  # 不占额度
        ("task_batch_second", second, "running"),
    )
    for task_id, plugin_id, status in seeded:
        await tasks.create_task(
            TaskRecord(
                tenant_id=TENANT,
                task_id=task_id,
                plugin_id=plugin_id,
                conversation_id="conv_batch",
                initiator_id="usr_owner",
                status=status,
            )
        )
    # 别的租户的占用任务绝不能算进来（租户隔离红线）
    await tasks.create_task(
        TaskRecord(
            tenant_id=OTHER_TENANT,
            task_id="task_batch_other",
            plugin_id=first,
            conversation_id="conv_batch",
            initiator_id="usr_owner",
            status="running",
        )
    )

    counts = await tasks.counts_occupying(TENANT, [first, second, "plg_nosuchplugin"])

    assert counts == {first: 2, second: 1}, "只回非零项，且不跨租户"
    for plugin_id in (first, second, "plg_nosuchplugin"):
        assert counts.get(plugin_id, 0) == await tasks.count_occupying(TENANT, plugin_id)
    assert await tasks.counts_occupying(TENANT, []) == {}
