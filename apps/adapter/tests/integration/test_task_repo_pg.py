"""阶段三 PG 持久化实现测试（B21c）：`PostgresTaskStore` / `PostgresArtifactStore`。

**为什么必须有这个文件**（B21c 的由来）：B20 判 FAIL 的依据不是"业务逻辑没测"，
而是**持久化层一半方法从没被执行过**——`task_repo` 47.81%、`artifact_repo` 60.67%。
而一旦真跑起来，暴露出 **五个 P0**（全部已在 `ad22` 那一批修复，见本文件末尾的"曾红"清单）：

1. `_TaskRepository` / `_StepRepository` / `_ArtifactRepository` **没声明 `table`** →
   `BaseRepository.__init__` 的 `assert_identifier("")` 直接抛 `30003`，
   **在任何 SQL 执行之前**，所以这三个 store 的每个方法都失败；
2. `create_task` 的 INSERT **25 列 / 24 值**，且 `confirm_step_seq` 写了两次；
3. 同上，`queue_position` 被当成存储列，而 `plugin_task` **没有这一列**
   （排队是 `status='queued'` + `started_at` 先来后到表达的）；
4. `timestamptz` 参数传的是 **ISO 字符串** → asyncpg 只收 `datetime`，
   报 `TypeError: expected a datetime...`；
5. `reason_text` / `operator_action` 在 DDL 里是 `NOT NULL DEFAULT ''`，
   代码却写 `NULL` → `NotNullViolationError`。

这五条的共同点：**内存后端全绿，PG 后端 100% 不可用**，且异常被包装成
「系统繁忙，请稍后再试」，现场完全看不出是持久化层的问题。
本文件的作用就是让这条路径**每次 CI 都真的被执行一次**。

用 `--run-pg` 启用（缺 `OA_TEST_DATABASE_URL` 时 skip，绝不假绿）。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import Settings, reset_settings_cache
from app.db.repositories.artifact_repo import (
    ArtifactRecord,
    PostgresArtifactStore,
)
from app.db.repositories.task_repo import (
    STATUS_QUEUED,
    PostgresTaskStore,
    StepRecord,
    TaskRecord,
    now_iso,
)
from app.db.session import platform_scope

pytestmark = [pytest.mark.integration, pytest.mark.pg]

pytest_plugins = ["tests.integration.pg_fixture"]

#: 本次运行专用租户（用例结束清理；不与其他文件的租户重叠）
TENANT_ID = f"tnt_pg_b21c_{uuid.uuid4().hex[:8]}"

#: 另一个租户（证明租户隔离：跨租户读不到）
OTHER_TENANT = f"tnt_pg_b21c_other_{uuid.uuid4().hex[:8]}"

#: 测试用插件
PLUGIN_ID = "plg_dev_workbench"


@pytest.fixture(autouse=True)
async def _dispose_engine_after_test() -> AsyncIterator[None]:
    """每个用例结束后释放进程级引擎（pytest-asyncio 每个用例一个新事件循环）。

    Yields:
        AsyncIterator[None]: 无值。
    """
    from app.db.engine import dispose_engine

    yield
    await dispose_engine()


@pytest.fixture
def pg_settings(pg_dsn: str) -> Settings:
    """PostgreSQL 后端的测试配置。

    Args:
        pg_dsn: 测试库连接串（`pg_fixture` 提供，缺 DSN 时用例 skip）。

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
async def task_store(pg_settings: Settings) -> AsyncIterator[PostgresTaskStore]:
    """任务存储（用例结束清理本次租户的任务与步骤）。

    Args:
        pg_settings: 配置实例。

    Yields:
        AsyncIterator[PostgresTaskStore]: 任务存储。
    """
    store = PostgresTaskStore(pg_settings)
    try:
        yield store
    finally:
        await _cleanup(pg_settings)


@pytest.fixture
async def artifact_store(pg_settings: Settings) -> AsyncIterator[PostgresArtifactStore]:
    """产物存储（用例结束清理）。

    Args:
        pg_settings: 配置实例。

    Yields:
        AsyncIterator[PostgresArtifactStore]: 产物存储。
    """
    store = PostgresArtifactStore(pg_settings)
    try:
        yield store
    finally:
        await _cleanup(pg_settings)


async def _seed_task(pg_settings: Settings) -> str:
    """先落一个真实任务（`plugin_artifact.task_id` 对 `plugin_task` 有外键）。

    Args:
        pg_settings: 配置实例。

    Returns:
        str: 任务 ID。
    """
    store = PostgresTaskStore(pg_settings)
    record = _task_record()
    await store.create_task(record)
    return record.task_id


async def _cleanup(settings: Settings) -> None:
    """清理本次运行写入的行（用平台作用域绕过 RLS，只删自己的租户）。

    Args:
        settings: 配置实例。
    """
    async with platform_scope(settings) as uow:
        for table in ("plugin_artifact", "plugin_task_step", "plugin_task"):
            await uow.execute(
                f"DELETE FROM {table} WHERE tenant_id = :tenant_id",  # noqa: S608
                {"tenant_id": TENANT_ID},
            )
            await uow.execute(
                f"DELETE FROM {table} WHERE tenant_id = :tenant_id",  # noqa: S608
                {"tenant_id": OTHER_TENANT},
            )


def _task_record(
    *,
    tenant_id: str = TENANT_ID,
    task_id: str = "",
    plugin_id: str = PLUGIN_ID,
    status: str = "running",
    initiator_id: str = "usr_b21c",
    **overrides: object,
) -> TaskRecord:
    """构造任务行。

    Args:
        tenant_id: 企业 ID。
        task_id: 任务 ID（空则随机）。
        plugin_id: 插件标识。
        status: 状态。
        initiator_id: 发起人。
        **overrides: 其它字段覆盖。

    Returns:
        TaskRecord: 任务行。
    """
    payload: dict[str, object] = {
        "tenant_id": tenant_id,
        "task_id": task_id or f"task_{uuid.uuid4().hex[:16]}",
        "plugin_id": plugin_id,
        "plugin_version": "1.0.0",
        "initiator_id": initiator_id,
        "status": status,
        "started_at": now_iso(),
        # 唯一幂等键：`uq_task_idem (tenant_id, plugin_id, idem_key)` 不含部分谓词，
        # 空串会被当成同一个键（见 task_service 里那段注释）。
        "idem_key": f"idem-{uuid.uuid4().hex[:12]}",
    }
    payload.update(overrides)
    return TaskRecord(**payload)  # type: ignore[arg-type]


# ===========================================================================
# 1. 建任务与读回：曾因「表名未声明 + INSERT 列值不匹配」100% 失败
# ===========================================================================
async def test_create_task_roundtrips_all_fields(task_store: PostgresTaskStore) -> None:
    """建任务后所有字段都能原样读回（JSONB / 时间 / 空串都不能变形）。"""
    record = _task_record(
        inputs={"title": "拆解需求", "repo": "acme/web"},
        checklist=[{"key": "plan", "status": "checked"}],
        warnings=["第 4 步没有完成"],
        result_summary="已生成方案",
        usage_units=42,
        idem_key=f"idem-{uuid.uuid4().hex[:8]}",
        confirm_step_seq=2,
        confirmed_step_seq=2,
    )
    created = await task_store.create_task(record)
    assert created.task_id == record.task_id
    assert created.inputs == {"title": "拆解需求", "repo": "acme/web"}
    assert created.checklist == [{"key": "plan", "status": "checked"}]
    assert created.warnings == ["第 4 步没有完成"]
    assert created.usage_units == 42
    assert created.confirm_step_seq == 2
    assert created.confirmed_step_seq == 2

    fetched = await task_store.get_task(TENANT_ID, record.task_id)
    assert fetched is not None
    assert fetched.plugin_id == PLUGIN_ID
    assert fetched.inputs == created.inputs


async def test_get_task_is_tenant_scoped(task_store: PostgresTaskStore) -> None:
    """跨租户读不到（RLS + 显式 `tenant_id` 双保险）。"""
    record = _task_record()
    await task_store.create_task(record)
    assert await task_store.get_task(OTHER_TENANT, record.task_id) is None


async def test_create_task_does_not_duplicate_confirm_column(
    task_store: PostgresTaskStore,
) -> None:
    """INSERT 的列清单不得重复、不得引用不存在的列（P0 回归钉子）。

    曾写：`confirm_step_seq` 两次 + `queue_position`（无此列）→ 25 列 / 24 值。
    这条用例的价值不在断言本身，而在**它会让 INSERT 真的执行一次**——
    SQL 有错就必然在这里炸。
    """
    created = await task_store.create_task(_task_record(confirm_step_seq=0))
    assert created.task_id


# ===========================================================================
# 2. 排队：`queue_position` 是派生值，不是存储列
# ===========================================================================
async def test_queued_tasks_get_derived_positions(task_store: PostgresTaskStore) -> None:
    """排队位次按 `started_at` 先来后到派生为 1..n（表里没有这一列）。"""
    base = datetime.now(UTC)
    first = _task_record(status=STATUS_QUEUED, started_at=base.isoformat())
    second = _task_record(
        status=STATUS_QUEUED, started_at=(base + timedelta(seconds=1)).isoformat()
    )
    await task_store.create_task(first)
    await task_store.create_task(second)

    queued = await task_store.list_queued(TENANT_ID, PLUGIN_ID)
    assert [item.task_id for item in queued] == [first.task_id, second.task_id]
    assert [item.queue_position for item in queued] == [1, 2]

    created_position = await task_store.create_task(
        _task_record(status=STATUS_QUEUED, started_at=(base + timedelta(seconds=2)).isoformat())
    )
    assert created_position.queue_position == 3, "建任务时就要回填位次（A1 要返回给前端）"


async def test_next_queued_returns_fifo_head(task_store: PostgresTaskStore) -> None:
    """`next_queued` 取最早排队的那个（额度释放后拉起的就是它）。"""
    base = datetime.now(UTC)
    head = _task_record(status=STATUS_QUEUED, started_at=base.isoformat())
    await task_store.create_task(head)
    await task_store.create_task(
        _task_record(status=STATUS_QUEUED, started_at=(base + timedelta(seconds=5)).isoformat())
    )
    nxt = await task_store.next_queued(TENANT_ID, PLUGIN_ID)
    assert nxt is not None and nxt.task_id == head.task_id


async def test_count_occupying_includes_waiting_confirm(task_store: PostgresTaskStore) -> None:
    """占用额度 = `running` + `waiting_confirm`（等确认的单仍然占着这次执行）。"""
    await task_store.create_task(_task_record(status="running"))
    await task_store.create_task(_task_record(status="waiting_confirm"))
    await task_store.create_task(_task_record(status=STATUS_QUEUED))
    await task_store.create_task(_task_record(status="completed"))
    assert await task_store.count_occupying(TENANT_ID, PLUGIN_ID) == 2
    assert await task_store.count_queued(TENANT_ID, PLUGIN_ID) == 1


async def test_next_queued_is_none_when_queue_empty(task_store: PostgresTaskStore) -> None:
    """队列空时返回 None（不能抛异常，否则"额度释放"路径会在空队列上炸）。"""
    assert await task_store.next_queued(TENANT_ID, PLUGIN_ID) is None
    assert await task_store.list_queued(TENANT_ID, PLUGIN_ID) == []


# ===========================================================================
# 3. 列表筛选（五个筛选 + 分页）
# ===========================================================================
async def test_list_tasks_filters_and_pagination(task_store: PostgresTaskStore) -> None:
    """五个筛选各自生效，且分页的 total 是**筛选后**的总数。"""
    await task_store.create_task(_task_record(status="completed", initiator_id="usr_a"))
    await task_store.create_task(_task_record(status="failed", initiator_id="usr_a"))
    await task_store.create_task(
        _task_record(status="completed", plugin_id="plg_content_ops", initiator_id="usr_b")
    )

    items, total = await task_store.list_tasks(TENANT_ID)
    assert total == 3 and len(items) == 3

    items, total = await task_store.list_tasks(TENANT_ID, status="completed")
    assert total == 2 and all(item.status == "completed" for item in items)

    items, total = await task_store.list_tasks(TENANT_ID, plugin_id="plg_content_ops")
    assert total == 1 and items[0].plugin_id == "plg_content_ops"

    items, total = await task_store.list_tasks(TENANT_ID, initiator_id="usr_a")
    assert total == 2

    items, total = await task_store.list_tasks(TENANT_ID, page=1, page_size=2)
    assert total == 3 and len(items) == 2
    items, total = await task_store.list_tasks(TENANT_ID, page=2, page_size=2)
    assert total == 3 and len(items) == 1


async def test_list_tasks_time_range_filters(task_store: PostgresTaskStore) -> None:
    """时间范围筛选：`created_from` / `created_to` 都是 `timestamptz` 参数。

    这条曾报 `TypeError: expected a datetime...` —— ISO 字符串直接传给 asyncpg。
    """
    await task_store.create_task(_task_record(started_at="2020-01-01T00:00:00Z"))
    await task_store.create_task(_task_record(started_at="2030-01-01T00:00:00Z"))

    _, recent = await task_store.list_tasks(TENANT_ID, created_from="2025-01-01T00:00:00Z")
    assert recent == 1, "只应命中 2030 那条"
    _, old = await task_store.list_tasks(TENANT_ID, created_to="2025-01-01T00:00:00Z")
    assert old == 1, "只应命中 2020 那条"
    _, all_rows = await task_store.list_tasks(
        TENANT_ID, created_from="2019-01-01T00:00:00Z", created_to="2031-01-01T00:00:00Z"
    )
    assert all_rows == 2


async def test_find_by_idem_hits_and_misses(task_store: PostgresTaskStore) -> None:
    """幂等键命中返回同一任务；空键直接返回 None（不查库）。"""
    key = f"idem-{uuid.uuid4().hex[:8]}"
    record = _task_record(idem_key=key)
    await task_store.create_task(record)

    found = await task_store.find_by_idem(TENANT_ID, PLUGIN_ID, key)
    assert found is not None and found.task_id == record.task_id
    assert await task_store.find_by_idem(TENANT_ID, PLUGIN_ID, "no-such-key") is None
    assert await task_store.find_by_idem(TENANT_ID, PLUGIN_ID, "") is None
    assert await task_store.find_by_idem(OTHER_TENANT, PLUGIN_ID, key) is None


# ===========================================================================
# 4. 更新：白名单 / 时间字段 / 终态
# ===========================================================================
async def test_update_task_writes_fields_and_timestamps(task_store: PostgresTaskStore) -> None:
    """更新含 JSONB 与 `finished_at`（后者曾因字符串参数报 TypeError）。"""
    record = _task_record()
    await task_store.create_task(record)

    updated = await task_store.update_task(
        TENANT_ID,
        record.task_id,
        status="completed",
        progress=100,
        usage_units=7,
        checklist=[{"key": "commit", "status": "checked"}],
        finished_at=now_iso(),
    )
    assert updated is not None
    assert updated.status == "completed"
    assert updated.progress == 100
    assert updated.usage_units == 7
    assert updated.checklist == [{"key": "commit", "status": "checked"}]
    assert updated.finished_at.startswith("20"), "finished_at 应已落库"


async def test_update_task_rejects_unknown_field(task_store: PostgresTaskStore) -> None:
    """白名单外的字段拒绝（防动态列名注入），且不动库里的行。"""
    from app.core.errors import ApiError

    record = _task_record()
    await task_store.create_task(record)
    with pytest.raises(ApiError):
        await task_store.update_task(TENANT_ID, record.task_id, plugin_id="plg_hijack")
    still = await task_store.get_task(TENANT_ID, record.task_id)
    assert still is not None and still.tenant_id == TENANT_ID


async def test_update_task_missing_row_returns_none(task_store: PostgresTaskStore) -> None:
    """不存在的任务返回 None（不抛异常）。"""
    assert await task_store.update_task(TENANT_ID, "task_missing", status="failed") is None


async def test_update_task_with_no_fields_is_a_read(task_store: PostgresTaskStore) -> None:
    """空更新等价于读（不生成 `SET` 空子句，避免 SQL 语法错）。"""
    record = _task_record()
    await task_store.create_task(record)
    same = await task_store.update_task(TENANT_ID, record.task_id)
    assert same is not None and same.task_id == record.task_id


# ===========================================================================
# 5. 步骤：建、列、更新、重置
# ===========================================================================
async def test_create_steps_writes_empty_strings_not_null(
    task_store: PostgresTaskStore,
) -> None:
    """步骤的 `reason_text`/`operator_action` 是 `NOT NULL DEFAULT ''`。

    曾写成 `NULLIF(:reason_text,'')` → 空串变 NULL → `NotNullViolationError`，
    于是"建任务成功、建步骤失败"——任务永远停在第一步。
    """
    record = _task_record()
    await task_store.create_task(record)
    steps = await task_store.create_steps(
        [
            StepRecord(
                tenant_id=TENANT_ID,
                step_id=f"step_{uuid.uuid4().hex[:12]}",
                task_id=record.task_id,
                seq=1,
                title="理解需求",
                action="understand",
            )
        ]
    )
    assert len(steps) == 1
    assert steps[0].reason_text == "" and steps[0].operator_action == ""


async def test_update_step_writes_timestamps_and_blank_reason(
    task_store: PostgresTaskStore,
) -> None:
    """更新步骤含 `started_at`/`finished_at` 与空 `reason_text`（两类 P0 同时踩）。"""
    record = _task_record()
    await task_store.create_task(record)
    steps = await task_store.create_steps(
        [
            StepRecord(
                tenant_id=TENANT_ID,
                step_id=f"step_{uuid.uuid4().hex[:12]}",
                task_id=record.task_id,
                seq=1,
                title="理解需求",
                action="understand",
            )
        ]
    )
    updated = await task_store.update_step(
        TENANT_ID,
        steps[0].step_id,
        status="completed",
        started_at=now_iso(),
        finished_at=now_iso(),
        reason_text="",
        operator_action="",
        output_summary="已抽取 3 条要点",
    )
    assert updated is not None
    assert updated.status == "completed"
    assert updated.started_at.startswith("20") and updated.finished_at.startswith("20")
    assert updated.output_summary == "已抽取 3 条要点"


async def test_update_step_rejects_unknown_field(task_store: PostgresTaskStore) -> None:
    """步骤同样有白名单。"""
    from app.core.errors import ApiError

    record = _task_record()
    await task_store.create_task(record)
    steps = await task_store.create_steps(
        [
            StepRecord(
                tenant_id=TENANT_ID,
                step_id=f"step_{uuid.uuid4().hex[:12]}",
                task_id=record.task_id,
                seq=1,
                title="理解需求",
                action="understand",
            )
        ]
    )
    with pytest.raises(ApiError):
        await task_store.update_step(TENANT_ID, steps[0].step_id, task_id="other")


async def test_list_steps_orders_by_seq(task_store: PostgresTaskStore) -> None:
    """步骤按 `seq` 升序返回（前端时间线的顺序依赖它）。"""
    record = _task_record()
    await task_store.create_task(record)
    await task_store.create_steps(
        [
            StepRecord(
                tenant_id=TENANT_ID,
                step_id=f"step_{uuid.uuid4().hex[:12]}",
                task_id=record.task_id,
                seq=seq,
                title=f"第 {seq} 步",
                action="compose",
            )
            for seq in (3, 1, 2)
        ]
    )
    steps = await task_store.list_steps(TENANT_ID, record.task_id)
    assert [step.seq for step in steps] == [1, 2, 3]


async def test_reset_steps_from_only_touches_later_steps(
    task_store: PostgresTaskStore,
) -> None:
    """`redo` 重置：`seq` 及之后回 `pending`，之前的步骤**不动**。

    曾把 `reason_text` 写成 NULL → NOT NULL 违约，整个 `redo` 路径不可用。
    """
    record = _task_record()
    await task_store.create_task(record)
    steps = await task_store.create_steps(
        [
            StepRecord(
                tenant_id=TENANT_ID,
                step_id=f"step_{uuid.uuid4().hex[:12]}",
                task_id=record.task_id,
                seq=seq,
                title=f"第 {seq} 步",
                action="compose",
                status="completed",
                attempt=1,
                reason_text="上一次的原因",
            )
            for seq in (1, 2, 3)
        ]
    )
    reset = await task_store.reset_steps_from(TENANT_ID, record.task_id, 2)
    assert reset == 2

    after = {step.seq: step for step in await task_store.list_steps(TENANT_ID, record.task_id)}
    assert after[1].status == "completed", "第 1 步不该被重do"
    assert after[1].reason_text == "上一次的原因"
    assert after[2].status == "pending" and after[2].attempt == 0
    assert after[3].status == "pending"
    del steps


# ===========================================================================
# 6. 产物存储：曾因「表名未声明」整类不可用
# ===========================================================================
def _artifact_record(*, task_id: str, output_key: str = "plan") -> ArtifactRecord:
    """构造产物行。

    Args:
        task_id: 所属任务。
        output_key: 产物键。

    Returns:
        ArtifactRecord: 产物行。
    """
    return ArtifactRecord(
        tenant_id=TENANT_ID,
        artifact_id=f"art_{uuid.uuid4().hex[:12]}",
        task_id=task_id,
        output_key=output_key,
        type="text",
        name="技术方案",
        inline_text="方案正文",
        content_sha256="0" * 64,
    )


async def test_artifact_add_and_get(
    artifact_store: PostgresArtifactStore, pg_settings: Settings
) -> None:
    """产物批量写入并读回（`add_artifacts` 是 WF1 收尾的落库入口）。"""
    task_id = await _seed_task(pg_settings)
    record = _artifact_record(task_id=task_id)
    created = await artifact_store.add_artifacts([record])
    assert len(created) == 1

    fetched = await artifact_store.get(TENANT_ID, record.artifact_id)
    assert fetched is not None
    assert fetched.inline_text == "方案正文"
    assert fetched.version >= 1
    assert await artifact_store.get(OTHER_TENANT, record.artifact_id) is None


async def test_artifact_save_version_keeps_history(
    artifact_store: PostgresArtifactStore, pg_settings: Settings
) -> None:
    """WF2 每次保存 `version + 1`，旧版本压进 `version_history[]`（只增不删）。"""
    task_id = await _seed_task(pg_settings)
    record = _artifact_record(task_id=task_id)
    await artifact_store.add_artifacts([record])
    first = await artifact_store.get(TENANT_ID, record.artifact_id)
    assert first is not None
    base_version = first.version

    updated = await artifact_store.save_version(
        TENANT_ID,
        record.artifact_id,
        inline_text="改过的正文",
        preview_text="改过的正文",
        content_sha256="1" * 64,
    )
    assert updated is not None
    assert updated.version == base_version + 1
    assert updated.inline_text == "改过的正文"
    assert updated.version_history, "旧版本必须留在历史里，否则 WF2 无法回看"


async def test_artifact_list_by_task(
    artifact_store: PostgresArtifactStore, pg_settings: Settings
) -> None:
    """按任务列出产物（核查清单就靠这个把产物挂回任务）。"""
    task_id = await _seed_task(pg_settings)
    await artifact_store.add_artifacts(
        [
            _artifact_record(task_id=task_id, output_key="plan"),
            _artifact_record(task_id=task_id, output_key="code_files"),
        ]
    )
    items = await artifact_store.list_by_task(TENANT_ID, task_id)
    assert {item.output_key for item in items} == {"plan", "code_files"}
    assert await artifact_store.list_by_task(OTHER_TENANT, task_id) == []


# ===========================================================================
# 7. 插件存储：目录 / 版本 / 安装 / 授权 / 企业开关
# ===========================================================================
#: `ensure_versions` 会把目录行一并 upsert，所以用例用独立插件 ID 避免互相踩
def _plugin_id() -> str:
    """生成本用例专用的插件标识（`plg_` + 小写字母开头）。

    Returns:
        str: 插件标识。
    """
    return f"plg_pg{uuid.uuid4().hex[:8]}".replace(" ", "")


def _version_record(plugin_id: str, version: str = "1.0.0") -> object:
    """构造声明版本行。

    Args:
        plugin_id: 插件标识。
        version: 版本号。

    Returns:
        object: `VersionRecord`。
    """
    from app.db.repositories.plugin_repo import VersionRecord

    return VersionRecord(
        plugin_id=plugin_id,
        version=version,
        manifest={"plugin_id": plugin_id, "version": version},
        manifest_sha256="0" * 64,
        min_platform_version="1.0.0",
        capabilities=["personal.tool.use"],
        actions=["compose"],
        step_titles=["理解需求"],
        status="active",
    )


@pytest.fixture
async def plugin_store(pg_settings: Settings) -> AsyncIterator[object]:
    """插件存储（用例结束清理本次写入的插件与安装）。

    Args:
        pg_settings: 配置实例。

    Yields:
        AsyncIterator[object]: `PostgresPluginStore`。
    """
    from app.db.repositories.plugin_repo import PostgresPluginStore

    store = PostgresPluginStore(pg_settings)
    try:
        yield store
    finally:
        async with platform_scope(pg_settings) as uow:
            for table in (
                "plugin_grant",
                "plugin_installation",
                "plugin_capability_policy",
                "plugin_version",
                "plugin_catalog",
            ):
                await uow.execute(
                    f"DELETE FROM {table} WHERE tenant_id = :tenant_id",  # noqa: S608
                    {"tenant_id": TENANT_ID},
                )
                if table in {"plugin_version", "plugin_catalog"}:
                    await uow.execute(
                        f"DELETE FROM {table} WHERE plugin_id LIKE 'plg_pg%'",  # noqa: S608
                    )


async def test_plugin_versions_are_immutable_by_insert(plugin_store: object) -> None:
    """`ensure_versions` 幂等：同一 `(plugin_id, version)` 第二次不新增行。"""
    plugin_id = _plugin_id()
    record = _version_record(plugin_id)
    first = await plugin_store.ensure_versions([record])  # type: ignore[attr-defined]
    second = await plugin_store.ensure_versions([record])  # type: ignore[attr-defined]
    assert first >= 1, "首次应登记版本"
    assert second == 0, "同版本重复登记必须是 0（版本不可变）"

    fetched = await plugin_store.get_version(plugin_id, "1.0.0")  # type: ignore[attr-defined]
    assert fetched is not None and fetched.plugin_id == plugin_id
    versions = await plugin_store.list_versions(plugin_id)  # type: ignore[attr-defined]
    assert [item.version for item in versions] == ["1.0.0"]


async def test_plugin_catalog_lists_and_filters(plugin_store: object) -> None:
    """目录可按分类与关键词筛选（市场页就靠这两个参数）。

    `plugin_catalog` 行由 init 脚本播种（6 个内置插件），`ensure_versions` 只写
    `plugin_version`——所以目录用例用内置插件，不去造新目录行。
    """
    plugin_id = "plg_dev_workbench"

    catalog = await plugin_store.get_catalog(plugin_id)  # type: ignore[attr-defined]
    assert catalog is not None and catalog.plugin_id == plugin_id

    listed = await plugin_store.list_catalog()  # type: ignore[attr-defined]
    assert any(item.plugin_id == plugin_id for item in listed)
    by_category = await plugin_store.list_catalog(category="dev")  # type: ignore[attr-defined]
    assert all(item.category == "dev" for item in by_category)
    by_keyword = await plugin_store.list_catalog(keyword=catalog.name[:2])  # type: ignore[attr-defined]
    assert any(item.plugin_id == plugin_id for item in by_keyword)


async def test_plugin_catalog_rating_and_install_count(plugin_store: object) -> None:
    """评分与安装数按增量累加（评价提交 / 安装成功时各调一次）。

    用例结束把三个计数恢复原值——目录行是**共享**的（`tenant_id='__platform__'`），
    改了不还会污染别的用例与手工验证。
    """
    plugin_id = "plg_content_ops"
    before = await plugin_store.get_catalog(plugin_id)  # type: ignore[attr-defined]
    assert before is not None
    try:
        await plugin_store.set_catalog_rating(plugin_id, rating_avg=4.5, rating_count=2)  # type: ignore[attr-defined]
        await plugin_store.adjust_install_count(plugin_id, 3)  # type: ignore[attr-defined]
        catalog = await plugin_store.get_catalog(plugin_id)  # type: ignore[attr-defined]
        assert catalog is not None
        assert float(catalog.rating_avg) == 4.5
        assert catalog.rating_count == 2
        assert catalog.install_count == before.install_count + 3
    finally:
        await plugin_store.set_catalog_rating(  # type: ignore[attr-defined]
            plugin_id, rating_avg=float(before.rating_avg), rating_count=before.rating_count
        )
        await plugin_store.adjust_install_count(plugin_id, -3)  # type: ignore[attr-defined]


async def test_plugin_installation_lifecycle(plugin_store: object) -> None:
    """安装 → 读回 → 按租户列出 → 再次创建被拒（版本不可覆盖）。"""
    from app.db.repositories.plugin_repo import InstallationRecord

    plugin_id = _plugin_id()
    await plugin_store.ensure_versions([_version_record(plugin_id)])  # type: ignore[attr-defined]
    record = InstallationRecord(
        tenant_id=TENANT_ID,
        plugin_id=plugin_id,
        version="1.0.0",
        status="enabled",
        installed_by="usr_b21c",
    )
    created = await plugin_store.create_installation(record)  # type: ignore[attr-defined]
    assert created.plugin_id == plugin_id

    fetched = await plugin_store.get_installation(TENANT_ID, plugin_id)  # type: ignore[attr-defined]
    assert fetched is not None and fetched.status == "enabled"
    assert await plugin_store.get_installation(OTHER_TENANT, plugin_id) is None  # type: ignore[attr-defined]

    listed = await plugin_store.list_installations(TENANT_ID)  # type: ignore[attr-defined]
    assert any(item.plugin_id == plugin_id for item in listed)

    updated = await plugin_store.save_installation(  # type: ignore[attr-defined]
        InstallationRecord(
            tenant_id=TENANT_ID,
            plugin_id=plugin_id,
            version="1.0.0",
            status="disabled",
            settings={"default_branch": "main"},
            installed_by="usr_b21c",
        )
    )
    assert updated.status == "disabled"
    assert updated.settings == {"default_branch": "main"}


async def test_plugin_grants_roundtrip(plugin_store: object) -> None:
    """授权集合按「租户 × 插件 × 成员」存取，且只增删本次传入的项。"""
    plugin_id = _plugin_id()
    await plugin_store.ensure_versions([_version_record(plugin_id)])  # type: ignore[attr-defined]
    await plugin_store.set_grants(  # type: ignore[attr-defined]
        TENANT_ID, plugin_id, "usr_b21c", {"personal.tool.use", "org.repo.write"}
    )
    assert await plugin_store.get_grants(TENANT_ID, plugin_id, "usr_b21c") == {  # type: ignore[attr-defined]
        "personal.tool.use",
        "org.repo.write",
    }

    await plugin_store.set_grants(  # type: ignore[attr-defined]
        TENANT_ID, plugin_id, "usr_b21c", {"personal.tool.use"}
    )
    assert await plugin_store.get_grants(TENANT_ID, plugin_id, "usr_b21c") == {  # type: ignore[attr-defined]
        "personal.tool.use"
    }
    assert await plugin_store.get_grants(OTHER_TENANT, plugin_id, "usr_b21c") == set()  # type: ignore[attr-defined]


async def test_plugin_capability_policies_roundtrip(plugin_store: object) -> None:
    """企业能力开关：存三项，读回字典；未设置的能力不出现在字典里。"""
    await plugin_store.save_policies(  # type: ignore[attr-defined]
        TENANT_ID, {"org.repo.write": True, "org.external.publish": False}, "usr_b21c"
    )
    policies = await plugin_store.list_policies(TENANT_ID)  # type: ignore[attr-defined]
    assert policies["org.repo.write"] is True
    assert policies["org.external.publish"] is False
    # 未设置的企业**默认关闭**（不是"没有键"）：企业没开过的能力一律 False
    other = await plugin_store.list_policies(OTHER_TENANT)  # type: ignore[attr-defined]
    assert other and not any(other.values()), "另一个租户不该继承本租户的开关"
