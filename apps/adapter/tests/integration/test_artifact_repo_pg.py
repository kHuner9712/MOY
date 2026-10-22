"""阶段三持久化补测（B21c）：`artifact_repo` 的 PG 与内存双实现。

**为什么单独一个文件**：`test_task_repo_pg.py` 只覆盖了产物存储的"添加 / 读回 /
一次改版"三条主路径（`artifact_repo` 66.48%）。未覆盖的部分恰恰是**最容易静默失效的**：

- `list_by_step`（A4 时间线按步取产物）与 `save_version` 的第二次之后（`version_history`
  累积、`MAX_VERSION_HISTORY` 截断）；
- `assert_editable`（WF2 只读类型的守门人）——它不写库，但一旦失效，`link`/`file`
  就能被编辑，AC-02 的结果物核查直接失去客观性；
- `get_artifact_store` 的后端选择与 `reset_*`（测试隔离的入口，选错后端等于测了假对象）。

用 `--run-pg` 启用；缺 `OA_TEST_DATABASE_URL` 时 skip，**绝不假绿**。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime

import pytest

from app.core.config import Settings, reset_settings_cache
from app.core.errors import ApiError, ErrorCode
from app.db.repositories.artifact_repo import (
    MAX_VERSION_HISTORY,
    ArtifactRecord,
    InMemoryArtifactStore,
    PostgresArtifactStore,
    assert_editable,
    get_artifact_store,
    get_memory_artifact_store,
    now_iso,
    reset_artifact_store,
)
from app.db.repositories.task_repo import PostgresTaskStore, TaskRecord
from app.db.session import platform_scope

pytestmark = [pytest.mark.integration, pytest.mark.pg]

pytest_plugins = ["tests.integration.pg_fixture"]

#: 本次运行专用租户（用例结束清理）
TENANT_ID = f"tnt_pg_art_{uuid.uuid4().hex[:8]}"

#: 另一个租户（证明租户隔离）
OTHER_TENANT = f"tnt_pg_art_other_{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
async def _dispose_engine_after_test() -> AsyncIterator[None]:
    """每个用例结束后释放进程级引擎（pytest-asyncio 每个用例一个新事件循环）。

    Yields:
        AsyncIterator[None]: 无值。
    """
    from app.db.engine import dispose_engine

    yield
    await dispose_engine()
    reset_artifact_store()


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
    """内存后端的测试配置（用于后端选择的对照）。

    Returns:
        Settings: 配置实例。
    """
    reset_settings_cache()
    return Settings(env="test", business_store="memory")


@pytest.fixture
async def store(pg_settings: Settings) -> AsyncIterator[PostgresArtifactStore]:
    """PG 产物存储（用例结束清理本次租户的行）。

    Args:
        pg_settings: 配置实例。

    Yields:
        AsyncIterator[PostgresArtifactStore]: 产物存储。
    """
    instance = PostgresArtifactStore(pg_settings)
    try:
        yield instance
    finally:
        await _cleanup(pg_settings)


async def _cleanup(settings: Settings) -> None:
    """清理本次运行写入的行（平台作用域绕 RLS，只删自己的租户）。

    Args:
        settings: 配置实例。
    """
    async with platform_scope(settings) as uow:
        for table in ("plugin_artifact", "plugin_task_step", "plugin_task"):
            for tenant in (TENANT_ID, OTHER_TENANT):
                await uow.execute(
                    f"DELETE FROM {table} WHERE tenant_id = :tenant_id",  # noqa: S608
                    {"tenant_id": tenant},
                )


async def _seed_task(settings: Settings, *, tenant_id: str = TENANT_ID) -> str:
    """先落一个真实任务（`plugin_artifact.task_id` 有外键）。

    Args:
        settings: 配置实例。
        tenant_id: 企业 ID。

    Returns:
        str: 任务 ID。
    """
    task_store = PostgresTaskStore(settings)
    record = TaskRecord(
        tenant_id=tenant_id,
        task_id=f"task_{uuid.uuid4().hex[:16]}",
        plugin_id="plg_dev_workbench",
        plugin_version="1.0.0",
        initiator_id="usr_art",
        status="running",
        started_at=now_iso(),
        idem_key=f"idem-{uuid.uuid4().hex[:12]}",
    )
    await task_store.create_task(record)
    return record.task_id


def _record(
    task_id: str, *, output_key: str = "plan", artifact_type: str = "text", **overrides: object
) -> ArtifactRecord:
    """构造产物行。

    Args:
        task_id: 所属任务。
        output_key: 产物键。
        artifact_type: 产物类型。
        **overrides: 其它字段覆盖。

    Returns:
        ArtifactRecord: 产物行。
    """
    payload: dict[str, object] = {
        "tenant_id": TENANT_ID,
        "artifact_id": f"art_{uuid.uuid4().hex[:12]}",
        "task_id": task_id,
        "step_id": f"step_{uuid.uuid4().hex[:8]}",
        "output_key": output_key,
        "type": artifact_type,
        "name": "技术方案",
        "inline_text": "方案正文",
        "preview_text": "方案正文",
        "content_sha256": "0" * 64,
    }
    payload.update(overrides)
    return ArtifactRecord(**payload)  # type: ignore[arg-type]


# ===========================================================================
# 1. PG：按步取产物（A4 时间线）与版本链累积
# ===========================================================================
async def test_list_by_step_returns_only_that_step(
    store: PostgresArtifactStore, pg_settings: Settings
) -> None:
    """`list_by_step` 只返回指定步骤的产物（A4 每步挂在步骤下）。"""
    task_id = await _seed_task(pg_settings)
    step_a = f"step_{uuid.uuid4().hex[:8]}"
    step_b = f"step_{uuid.uuid4().hex[:8]}"
    await store.add_artifacts(
        [
            _record(task_id, output_key="key_points", step_id=step_a),
            _record(task_id, output_key="plan", step_id=step_b),
        ]
    )
    items = await store.list_by_step(TENANT_ID, task_id, step_a)
    assert [item.output_key for item in items] == ["key_points"]
    assert await store.list_by_step(OTHER_TENANT, task_id, step_a) == []


async def test_version_history_accumulates_across_edits(
    store: PostgresArtifactStore, pg_settings: Settings
) -> None:
    """连续两次编辑：版本 +2、历史长度 2、摘要每次变化（WF2 版本只增不删）。"""
    task_id = await _seed_task(pg_settings)
    record = _record(task_id)
    await store.add_artifacts([record])

    first = await store.save_version(
        TENANT_ID,
        record.artifact_id,
        inline_text="第一版",
        preview_text="第一版",
        content_sha256="1" * 64,
    )
    second = await store.save_version(
        TENANT_ID,
        record.artifact_id,
        inline_text="第二版",
        preview_text="第二版",
        content_sha256="2" * 64,
    )
    assert first is not None and second is not None
    assert second.version == first.version + 1
    assert len(second.version_history) == 2, "两次编辑后历史里应有两个旧版本"
    assert second.content_sha256 == "2" * 64
    assert {item["content_sha256"] for item in second.version_history} >= {"0" * 64, "1" * 64}


async def test_version_history_is_capped_at_max(
    store: PostgresArtifactStore, pg_settings: Settings
) -> None:
    """历史超过 `MAX_VERSION_HISTORY` 时丢最旧的（否则单产物会无限膨胀）。"""
    task_id = await _seed_task(pg_settings)
    record = _record(task_id)
    await store.add_artifacts([record])
    for index in range(MAX_VERSION_HISTORY + 2):
        await store.save_version(
            TENANT_ID,
            record.artifact_id,
            inline_text=f"第 {index} 版",
            preview_text=f"第 {index} 版",
            content_sha256=f"{index:064d}",
        )
    fetched = await store.get(TENANT_ID, record.artifact_id)
    assert fetched is not None
    assert len(fetched.version_history) == MAX_VERSION_HISTORY


async def test_save_version_on_missing_artifact_returns_none(
    store: PostgresArtifactStore,
) -> None:
    """产物不存在时返回 None（不是抛异常，也不是凭空建行）。"""
    assert (
        await store.save_version(
            TENANT_ID,
            "art_missing",
            inline_text="x",
            preview_text="x",
            content_sha256="0" * 64,
        )
        is None
    )


# ===========================================================================
# 2. WF2 只读守门人：`assert_editable`
# ===========================================================================
@pytest.mark.parametrize("artifact_type", ["draft", "doc", "table", "text"])
def test_assert_editable_accepts_editable_types(artifact_type: str) -> None:
    """可编辑类型（稿件 / 文档 / 表格 / 纯文本）放行。"""
    assert_editable(_record("task_x", artifact_type=artifact_type))


@pytest.mark.parametrize("artifact_type", ["link", "file"])
def test_assert_editable_rejects_readonly_types(artifact_type: str) -> None:
    """`link`（提交记录）/ `file`（代码文件）→ 20001。

    这条不写库，但它是 AC-02 客观性的守门人：允许改提交记录，核查清单就失去意义。
    """
    with pytest.raises(ApiError) as excinfo:
        assert_editable(_record("task_x", artifact_type=artifact_type))
    assert int(excinfo.value.code) == int(ErrorCode.PARAM_INVALID)


# ===========================================================================
# 3. 后端选择与测试隔离入口
# ===========================================================================
def test_store_factory_selects_backend_by_settings(
    pg_settings: Settings, memory_settings: Settings
) -> None:
    """`business_store=postgres` 走 PG，`memory` 走内存；`reset_*` 可切换。"""
    reset_artifact_store()
    pg_backend = get_artifact_store(pg_settings)
    assert isinstance(pg_backend, PostgresArtifactStore)
    assert get_memory_artifact_store() is None, "PG 后端下不应返回内存实现"

    reset_artifact_store()
    memory_backend = get_artifact_store(memory_settings)
    assert isinstance(memory_backend, InMemoryArtifactStore)
    assert get_memory_artifact_store() is memory_backend
    reset_artifact_store()


def test_now_iso_is_utc_marker() -> None:
    """`now_iso` 产出带 `Z` 的 UTC 时间（与库里 timestamptz 的写法一致）。"""
    value = now_iso()
    assert value.endswith("Z")
    assert datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None


# ===========================================================================
# 4. 内存实现：与 PG 同语义（两套实现语义漂移是最难查的一类）
# ===========================================================================
async def test_memory_store_roundtrip_and_versioning() -> None:
    """内存实现：写入 → 按任务/按步取 → 改版累积历史 → `clear` 清空。"""
    store = InMemoryArtifactStore()
    record = _record("task_mem")
    created = await store.add_artifacts([record])
    assert created[0].expires_at, "缺省应回填过期时间（180 天）"

    assert await store.get(TENANT_ID, record.artifact_id) is not None
    assert len(await store.list_by_task(TENANT_ID, "task_mem")) == 1
    assert len(await store.list_by_step(TENANT_ID, "task_mem", record.step_id)) == 1
    assert await store.list_by_task(OTHER_TENANT, "task_mem") == []

    updated = await store.save_version(
        TENANT_ID,
        record.artifact_id,
        inline_text="改过",
        preview_text="改过",
        content_sha256="f" * 64,
    )
    assert updated is not None and updated.version == 2
    assert updated.version_history and updated.version_history[0]["inline_text"] == "方案正文"
    assert (
        await store.save_version(
            TENANT_ID, "art_missing", inline_text="x", preview_text="x", content_sha256="0" * 64
        )
        is None
    )

    store.clear()
    assert await store.get(TENANT_ID, record.artifact_id) is None


async def test_memory_store_saves_binary_content_and_size() -> None:
    """`file` 类型保存字节内容时，大小按字节算（不是字符数）。"""
    store = InMemoryArtifactStore()
    record = _record("task_bin", artifact_type="file", inline_text="")
    await store.add_artifacts([record])
    payload = "中文正文".encode()
    updated = await store.save_version(
        TENANT_ID,
        record.artifact_id,
        inline_text="",
        preview_text="",
        content_sha256="a" * 64,
        content=payload,
        content_type="application/octet-stream",
    )
    assert updated is not None
    assert updated.size_bytes == len(payload)
    assert updated.content == payload
