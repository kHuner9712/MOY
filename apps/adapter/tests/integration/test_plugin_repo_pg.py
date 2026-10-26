"""阶段三持久化补测（B21c）：`plugin_repo` 的 PG 与内存双实现。

**为什么必须单开一个文件**：`plugin_repo` 是插件域唯一的数据入口（目录 / 声明快照 /
安装态双指针 / 授权 / 企业开关），但 `test_task_repo_pg.py` 只顺手覆盖了 4 条主路径
（56.50%）。没被执行的恰恰是**决定业务正确性的分支**：

- `swap_version` 的**乐观锁**（并发升级/回退 → `16010`）与 `create_installation` 的
  **唯一键冲突**（并发安装 → `16010`）——这两条是 B18 专门落地的两个场景，
  不执行就等于"16010 又变回僵尸码"；
- `adjust_install_count` 的**下界**（不能为负）、`list_installations` 的状态过滤；
- `version_records_from_registry`（声明快照的唯一来源，写错会让"版本不可变"失效）；
- 内存实现与 PG 实现的**语义一致性**（两套实现漂移是最难查的一类缺陷）。

用 `--run-pg` 启用；缺 `OA_TEST_DATABASE_URL` 时 skip，**绝不假绿**。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest

from app.core.config import Settings, reset_settings_cache
from app.db.repositories.plugin import (
    STATUS_ENABLED,
    CatalogRecord,
    InMemoryPluginStore,
    InstallationConflictError,
    InstallationRecord,
    PostgresPluginStore,
    VersionRecord,
    get_memory_plugin_store,
    get_plugin_store,
    now_iso,
    reset_plugin_store,
    version_records_from_registry,
)
from app.db.session import platform_scope
from app.plugins.loader import PluginRegistry, default_manifests_dir

pytestmark = [pytest.mark.integration, pytest.mark.pg]

pytest_plugins = ["tests.integration.pg_fixture"]

TENANT_ID = f"tnt_pg_plug_{uuid.uuid4().hex[:8]}"
OTHER_TENANT = f"tnt_pg_plug_other_{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
async def _dispose_engine_after_test() -> AsyncIterator[None]:
    """每个用例结束后释放进程级引擎。

    Yields:
        AsyncIterator[None]: 无值。
    """
    from app.db.engine import dispose_engine

    yield
    await dispose_engine()
    reset_plugin_store()


@pytest.fixture
def pg_settings(pg_dsn: str) -> Settings:
    """PostgreSQL 后端的测试配置。

    Args:
        pg_dsn: 测试库连接串。

    Returns:
        Settings: 配置实例。
    """
    reset_settings_cache()
    return Settings(
        env="test",
        deploy_mode="saas",
        downstream_enabled=False,
        auth_mode="stub",
        jwt_secret="pg-test-secret",
        ratelimit_enabled=False,
        business_store="postgres",
        database_url=pg_dsn,
    )


@pytest.fixture
def memory_settings() -> Settings:
    """内存后端的测试配置。

    Returns:
        Settings: 配置实例。
    """
    reset_settings_cache()
    return Settings(env="test", business_store="memory")


@pytest.fixture
async def store(pg_settings: Settings) -> AsyncIterator[PostgresPluginStore]:
    """PG 插件存储（用例结束清理本次写入的行）。

    Args:
        pg_settings: 配置实例。

    Yields:
        AsyncIterator[PostgresPluginStore]: 插件存储。
    """
    instance = PostgresPluginStore(pg_settings)
    try:
        yield instance
    finally:
        await _cleanup(pg_settings)


async def _cleanup(settings: Settings) -> None:
    """清理本次运行写入的行。

    Args:
        settings: 配置实例。
    """
    async with platform_scope(settings) as uow:
        for table in (
            "plugin_review",
            "plugin_grant",
            "plugin_installation",
            "plugin_capability_policy",
            "plugin_version",
            "plugin_catalog",
        ):
            for tenant in (TENANT_ID, OTHER_TENANT):
                await uow.execute(
                    f"DELETE FROM {table} WHERE tenant_id = :tenant_id",  # noqa: S608
                    {"tenant_id": tenant},
                )
        for table in ("plugin_version", "plugin_catalog"):
            await uow.execute(
                f"DELETE FROM {table} WHERE plugin_id LIKE 'plg_vr%'",  # noqa: S608
            )


def _plugin_id(tag: str = "vr") -> str:
    """生成本用例专用插件标识（`plg_` + 小写字母）。

    Args:
        tag: 标识前缀片段。

    Returns:
        str: 插件标识。
    """
    return f"plg_{tag}{uuid.uuid4().hex[:8]}"


def _version(plugin_id: str, version: str = "1.0.0", **overrides: object) -> VersionRecord:
    """构造声明快照行。

    Args:
        plugin_id: 插件标识。
        version: 版本号。
        **overrides: 其它字段覆盖。

    Returns:
        VersionRecord: 声明快照行。
    """
    payload: dict[str, object] = {
        "plugin_id": plugin_id,
        "version": version,
        "manifest": {"plugin_id": plugin_id, "version": version},
        "manifest_sha256": "0" * 64,
        "min_platform_version": "1.0.0",
        "capabilities": ["personal.tool.use"],
        "actions": ["compose"],
        "step_titles": ["生成内容"],
        "status": "active",
    }
    payload.update(overrides)
    return VersionRecord(**payload)  # type: ignore[arg-type]


def _installation(plugin_id: str, **overrides: object) -> InstallationRecord:
    """构造安装行。

    Args:
        plugin_id: 插件标识。
        **overrides: 其它字段覆盖。

    Returns:
        InstallationRecord: 安装行。
    """
    payload: dict[str, object] = {
        "tenant_id": TENANT_ID,
        "plugin_id": plugin_id,
        "version": "1.0.0",
        "status": STATUS_ENABLED,
        "installed_by": "usr_plug",
    }
    payload.update(overrides)
    return InstallationRecord(**payload)  # type: ignore[arg-type]


# ===========================================================================
# 1. 声明快照：幂等登记 / 版本不可变 / 读回
# ===========================================================================
async def test_ensure_versions_is_idempotent_and_lists(
    store: PostgresPluginStore,
) -> None:
    """同一 `(plugin_id, version)` 只登记一次；`list_versions` 能读回。"""
    plugin_id = _plugin_id()
    record = _version(plugin_id)
    assert await store.ensure_versions([record]) >= 1
    assert await store.ensure_versions([record]) == 0, "版本不可变：重复登记必须是 0"

    fetched = await store.get_version(plugin_id, "1.0.0")
    assert fetched is not None and fetched.plugin_id == plugin_id
    assert [item.version for item in await store.list_versions(plugin_id)] == ["1.0.0"]
    assert await store.get_version(plugin_id, "9.9.9") is None, "版本不存在应返回 None"


async def test_ensure_versions_registers_multiple_versions(
    store: PostgresPluginStore,
) -> None:
    """两个版本各登记一次，`list_versions` 都读得到（回退只保留 2 版的前置）。"""
    plugin_id = _plugin_id()
    await store.ensure_versions([_version(plugin_id, "1.0.0"), _version(plugin_id, "1.1.0")])
    versions = [item.version for item in await store.list_versions(plugin_id)]
    assert sorted(versions) == ["1.0.0", "1.1.0"]


def test_version_records_from_registry_covers_all_manifests() -> None:
    """`version_records_from_registry` 把 6 份磁盘声明转成快照行（唯一来源）。

    写错这里不会报错，只会让"版本不可变"悄悄失效（快照与文件不一致）。
    """
    registry = PluginRegistry()
    registry.load_all(default_manifests_dir())
    records = version_records_from_registry(registry)
    assert len(records) == len(registry) == 6
    for item in records:
        assert len(item.manifest_sha256) == 64, "每份快照都要有摘要"
        assert item.capabilities and item.actions and item.step_titles


# ===========================================================================
# 2. 并发两场景：`16010` 的落地点（唯一键冲突 / 版本指针竞争）
# ===========================================================================
async def test_create_installation_conflicts_on_duplicate(
    store: PostgresPluginStore,
) -> None:
    """并发安装：第二次 `create_installation` 抛 `InstallationConflictError`。

    这是 `16010` 的**唯一来源之一**：不抛就等于并发安装静默覆盖先到者的配置。
    """
    plugin_id = _plugin_id()
    await store.ensure_versions([_version(plugin_id)])
    created = await store.create_installation(_installation(plugin_id))
    assert created.plugin_id == plugin_id
    with pytest.raises(InstallationConflictError):
        await store.create_installation(_installation(plugin_id))


async def test_swap_version_optimistic_lock(store: PostgresPluginStore) -> None:
    """版本指针乐观锁：预期版本不符时返回 None（并发升级 → `16010`）。"""
    plugin_id = _plugin_id()
    await store.ensure_versions([_version(plugin_id, "1.0.0"), _version(plugin_id, "1.1.0")])
    await store.create_installation(_installation(plugin_id))

    swapped = await store.swap_version(
        TENANT_ID, plugin_id, expected_version="1.0.0", target_version="1.1.0"
    )
    assert swapped is not None
    assert swapped.version == "1.1.0" and swapped.previous_version == "1.0.0"
    assert swapped.capability_version >= 2, "切换版本必须推进能力版本（前端缓存失效）"

    stale = await store.swap_version(
        TENANT_ID, plugin_id, expected_version="1.0.0", target_version="1.0.0"
    )
    assert stale is None, "指针已被改：必须返回 None 让服务层翻译成 16010"


async def test_save_installation_upserts_settings_and_status(
    store: PostgresPluginStore,
) -> None:
    """`save_installation` 是 upsert：配置与状态就地更新，不新增行。"""
    plugin_id = _plugin_id()
    await store.ensure_versions([_version(plugin_id)])
    await store.create_installation(_installation(plugin_id))
    updated = await store.save_installation(
        _installation(plugin_id, status="disabled", settings={"default_branch": "main"})
    )
    assert updated.status == "disabled"
    assert updated.settings == {"default_branch": "main"}
    listed = await store.list_installations(TENANT_ID)
    assert [item.plugin_id for item in listed].count(plugin_id) == 1


async def test_list_installations_filters_by_status(store: PostgresPluginStore) -> None:
    """`list_installations(status=...)` 只返回该状态的安装（PL3 的筛选）。"""
    enabled_id, disabled_id = _plugin_id(), _plugin_id()
    await store.ensure_versions([_version(enabled_id), _version(disabled_id)])
    await store.create_installation(_installation(enabled_id))
    await store.create_installation(_installation(disabled_id, status="disabled"))

    enabled = await store.list_installations(TENANT_ID, status=STATUS_ENABLED)
    assert [item.plugin_id for item in enabled] == [enabled_id]
    disabled = await store.list_installations(TENANT_ID, status="disabled")
    assert [item.plugin_id for item in disabled] == [disabled_id]
    assert await store.list_installations(OTHER_TENANT) == []


# ===========================================================================
# 3. 授权与企业开关
# ===========================================================================
async def test_grants_add_and_revoke(store: PostgresPluginStore) -> None:
    """授权集合按成员存取：增、撤、跨租户隔离。"""
    plugin_id = _plugin_id()
    await store.ensure_versions([_version(plugin_id)])
    granted = await store.set_grants(
        TENANT_ID, plugin_id, "usr_plug", {"personal.tool.use", "org.repo.write"}
    )
    assert granted == {"personal.tool.use", "org.repo.write"}
    remaining = await store.set_grants(TENANT_ID, plugin_id, "usr_plug", {"personal.tool.use"})
    assert remaining == {"personal.tool.use"}, "未列出的能力应被撤销"
    assert await store.get_grants(OTHER_TENANT, plugin_id, "usr_plug") == set()


async def test_policies_roundtrip_and_tenant_isolation(store: PostgresPluginStore) -> None:
    """企业能力开关：写入后读回；其他租户读不到本租户的开关。"""
    saved = await store.save_policies(
        TENANT_ID, {"org.repo.write": True, "org.external.publish": False}, "usr_plug"
    )
    assert saved["org.repo.write"] is True
    policies = await store.list_policies(TENANT_ID)
    assert policies["org.repo.write"] is True
    assert policies["org.external.publish"] is False
    other = await store.list_policies(OTHER_TENANT)
    assert not any(other.values()), "其他租户不得继承本租户的开关"


# ===========================================================================
# 4. 目录：评分 / 安装数下界
# ===========================================================================
async def test_catalog_rating_and_install_count_floor(store: PostgresPluginStore) -> None:
    """安装数不得为负（卸载多于安装时停在 0 而不是负数）。"""
    plugin_id = "plg_weekly_report"
    before = await store.get_catalog(plugin_id)
    assert before is not None
    try:
        await store.set_catalog_rating(plugin_id, rating_avg=3.5, rating_count=1)
        await store.adjust_install_count(plugin_id, 2)
        bumped = await store.get_catalog(plugin_id)
        assert bumped is not None
        assert float(bumped.rating_avg) == 3.5 and bumped.rating_count == 1
        assert bumped.install_count == before.install_count + 2

        await store.adjust_install_count(plugin_id, -(before.install_count + 100))
        floored = await store.get_catalog(plugin_id)
        assert floored is not None and floored.install_count == 0, "安装数下界为 0"
    finally:
        await store.set_catalog_rating(
            plugin_id, rating_avg=float(before.rating_avg), rating_count=before.rating_count
        )
        current = await store.get_catalog(plugin_id)
        if current is not None:
            await store.adjust_install_count(
                plugin_id, before.install_count - current.install_count
            )


async def test_get_catalog_missing_returns_none(store: PostgresPluginStore) -> None:
    """目录无此行返回 None（服务层据此报 `16002`，不是抛异常）。"""
    assert await store.get_catalog("plg_nosuchplugin") is None


async def test_catalogs_of_matches_get_catalog(store: PostgresPluginStore) -> None:
    """批量读目录 == 逐条读目录；缺失 id 不入字典、空入参直接返回空字典（P2-6）。"""
    listed = await store.list_catalog()
    assert listed, "目录基线不应为空"
    plugin_ids = [item.plugin_id for item in listed]

    batch = await store.catalogs_of([*plugin_ids, "plg_nosuchplugin"])

    assert set(batch) == set(plugin_ids)
    assert "plg_nosuchplugin" not in batch
    for plugin_id in plugin_ids:
        assert batch[plugin_id] == await store.get_catalog(plugin_id)
    assert await store.catalogs_of([]) == {}


async def test_catalogs_of_includes_hidden_rows(
    store: PostgresPluginStore, pg_settings: Settings
) -> None:
    """下架行必须仍能被批量读到（服务层靠 `status` 判 `16002`，不是靠"查不到"）。"""
    listed = await store.list_catalog()
    assert listed
    plugin_id = listed[0].plugin_id
    previous = listed[0].status
    await _set_catalog_status(pg_settings, plugin_id, "hidden")
    try:
        batch = await store.catalogs_of([plugin_id])
        assert plugin_id in batch, "下架行不得从批量结果里消失"
        assert batch[plugin_id].status == "hidden"
        assert batch[plugin_id].installable is False
        # 逐条读取同口径（两条路径不得漂移）
        single = await store.get_catalog(plugin_id)
        assert single is not None and single == batch[plugin_id]
    finally:
        await _set_catalog_status(pg_settings, plugin_id, previous)


async def test_count_catalog_by_category_matches_list_catalog(
    store: PostgresPluginStore,
) -> None:
    """分类计数 == `list_catalog()` 的分组结果（P2-6：只回非零分类）。"""
    listed = await store.list_catalog()
    assert listed
    expected: dict[str, int] = {}
    for item in listed:
        expected[item.category] = expected.get(item.category, 0) + 1

    counts = await store.count_catalog_by_category()

    assert counts == expected
    assert sum(counts.values()) == len(listed)


async def _set_catalog_status(settings: Settings, plugin_id: str, status: str) -> None:
    """直接改目录行的上架状态（造「下架行」这一场景；仓储层没有状态写入口）。

    Args:
        settings: 配置实例。
        plugin_id: 插件标识。
        status: 目标状态。
    """
    async with platform_scope(settings) as uow:
        await uow.execute(
            "UPDATE plugin_catalog SET status = :status"
            " WHERE tenant_id = '__platform__' AND plugin_id = :plugin_id",
            {"status": status, "plugin_id": plugin_id},
        )


# ===========================================================================
# 5. 后端选择 / 记录对象语义
# ===========================================================================
def test_store_factory_and_record_semantics(
    pg_settings: Settings, memory_settings: Settings
) -> None:
    """后端按 `business_store` 选择；`is_live` / `installable` 的边界值正确。"""
    reset_plugin_store()
    assert isinstance(get_plugin_store(pg_settings), PostgresPluginStore)
    assert get_memory_plugin_store() is None

    reset_plugin_store()
    memory_backend = get_plugin_store(memory_settings)
    assert isinstance(memory_backend, InMemoryPluginStore)
    assert get_memory_plugin_store() is memory_backend
    reset_plugin_store()

    assert InstallationRecord(status="enabled").is_live is True
    assert InstallationRecord(status="disabled").is_live is True, "停用仍是「已添加」"
    assert InstallationRecord(status="uninstalled").is_live is False
    assert CatalogRecord(plugin_id="plg_x", status="available").installable is True
    assert CatalogRecord(plugin_id="plg_x", status="hidden").installable is False
    assert now_iso().endswith("Z")


async def test_personal_capability_is_ignored_by_repo(store: PostgresPluginStore) -> None:
    """仓储层只落 `org.*` 三项：传个人能力**被忽略**，不进库也不报错。

    这是"个人能力恒开"在**持久化层**的边界：`plugin_capability_policy` 的语义就是
    "企业能否关掉某项 org 能力"，把 `personal.*` 写进去会让"管理员关掉了我的个人知识库"
    成为可能——所以宁可**静默丢弃**（服务层再抛 `20001` 给用户），也不存在库。

    **这不是数据库保证，是调用约定**：`plugin_capability_policy` 表上**没有** `personal.*`
    的 CHECK 约束，过滤发生在 `save_policies` 里的 `if capability in ORG_CAPABILITIES`。
    也就是说：任何绕过仓储层直接写库的路径（运维脚本 / 未来的批量导入）**可以把
    `personal.*` 写进去**，而 DB 不会拦。真正守"个人能力恒开"的是服务层 20001 +
    这一处仓储过滤，两道都在应用侧。下一个人若要在别处"依赖这个假设"，
    请先确认那条路径一定经过 `save_policies`。

    **若要让它变成数据库保证**，需要一条 `CHECK (capability NOT LIKE 'personal.%')`
    的迁移——但按"应用角色不做 DDL"的既定口径（阶段二 `0002` 与阶段三 `0004` 同一结论），
    这属于阶段四的 DBA 动作，不在本批。
    """
    saved = await store.save_policies(
        TENANT_ID,
        {"personal.knowledge.read": False, "org.repo.write": True},
        "usr_plug",
    )
    assert "personal.knowledge.read" not in saved, "个人能力不得出现在企业开关里"
    assert saved["org.repo.write"] is True
    stored = await store.list_policies(TENANT_ID)
    assert "personal.knowledge.read" not in stored
    assert stored["org.repo.write"] is True


# ===========================================================================
# 6. 内存实现：与 PG 同语义（含 `counts` / `clear` 这类测试隔离入口）
# ===========================================================================
async def test_memory_store_full_lifecycle() -> None:
    """内存实现：目录筛选、安装态、授权、开关、计数、清空全部走通。"""
    registry = PluginRegistry()
    registry.load_all(default_manifests_dir())
    store = InMemoryPluginStore(registry)

    catalog = await store.get_catalog("plg_dev_workbench")
    assert catalog is not None
    assert any(item.plugin_id == "plg_dev_workbench" for item in await store.list_catalog())
    assert all(item.category == "dev" for item in await store.list_catalog(category="dev"))
    assert any(
        item.plugin_id == "plg_dev_workbench" for item in await store.list_catalog(keyword="研发")
    )
    assert await store.get_catalog("plg_nosuchplugin") is None

    plugin_id = _plugin_id()
    assert await store.ensure_versions([_version(plugin_id)]) == 1
    assert await store.ensure_versions([_version(plugin_id)]) == 0
    assert (await store.get_version(plugin_id, "1.0.0")) is not None
    assert await store.get_version(plugin_id, "2.0.0") is None
    assert len(await store.list_versions(plugin_id)) == 1

    await store.create_installation(_installation(plugin_id))
    with pytest.raises(InstallationConflictError):
        await store.create_installation(_installation(plugin_id))
    assert await store.get_installation(TENANT_ID, plugin_id) is not None
    assert await store.get_installation(OTHER_TENANT, plugin_id) is None
    assert len(await store.list_installations(TENANT_ID)) == 1
    assert len(await store.list_installations(TENANT_ID, status="disabled")) == 0

    await store.ensure_versions([_version(plugin_id, "1.1.0")])
    swapped = await store.swap_version(
        TENANT_ID, plugin_id, expected_version="1.0.0", target_version="1.1.0"
    )
    assert swapped is not None and swapped.version == "1.1.0"
    assert (
        await store.swap_version(
            TENANT_ID, plugin_id, expected_version="1.0.0", target_version="1.0.0"
        )
        is None
    )

    await store.set_grants(TENANT_ID, plugin_id, "usr_mem", {"personal.tool.use"})
    assert await store.get_grants(TENANT_ID, plugin_id, "usr_mem") == {"personal.tool.use"}
    await store.save_policies(TENANT_ID, {"org.repo.write": True}, "usr_mem")
    assert (await store.list_policies(TENANT_ID))["org.repo.write"] is True

    counts = store.counts()
    assert counts["installations"] >= 1
    store.clear()
    assert await store.get_installation(TENANT_ID, plugin_id) is None
