"""工作流任务统计端点集成测试（W1 切片 A，`architecture-w1-metrics-entry.md` §3.1）。

覆盖 AC-A1~A3 / A7：

1. **AC-A2 分子分母可复算**：造数含「用户主动取消、`waiting_confirm` 跨期、
   `retry_of` 各 1 条」，接口值与 §3.1 公式**手工复算**一致（7d 与 30d 两个窗口）；
2. **AC-A3 空数据**：空租户全指标返回 0，无 `NaN`/`null`
   （顺带证明租户隔离：数据在另一个租户里）；
3. **AC-A1 参数边界**：`range=90d` → `code=20001` 且 HTTP 400（既有机制，不得 500）；
4. **AC-A7 双后端一致**：`pytest.mark.pg` 用例（`--run-pg` 实跑）用**同一批造数**
   跑 `PostgresTaskStore.task_stats` 的聚合 SQL，与内存实现逐字段一致
   （内存绿 ≠ PG 绿，B21c 五个 P0 的前车之鉴）。

窗口口径（§1.4.1）：`until = now(UTC)`，`since = until − 7d/30d`，任务按
`started_at ∈ [since, until)` 归属；期末未终态任务计入 `total` 与完成率分母
（按未完成）。私有化模式下生效租户恒为配置默认值（`default`）。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings, reset_settings_cache
from app.core.errors import ErrorCode
from app.db.engine import reset_engine_for_test
from app.db.repositories.task import (
    InMemoryTaskStore,
    PostgresTaskStore,
    TaskRecord,
    TaskStats,
    TaskStore,
    get_task_store,
)
from app.main import create_app
from tests.conftest import make_token

pytestmark = pytest.mark.integration

#: 私有化模式的生效租户（`resolve_tenant_id` 在 private 模式恒返回配置默认值）
TENANT = "default"

#: 另一个租户（证明空租户全 0 与租户隔离）
OTHER_TENANT = "tnt_stats_other"

#: 被测端点
STATS_PATH = "/api/v1/stats/tasks"


# ===========================================================================
# 造数（窗口内 7 条 + 窗口外 1 条；三类边界各 1 条）
# ===========================================================================
def _iso(base: datetime, **offset: float) -> str:
    """相对基准时间生成 ISO 8601（UTC，`Z` 后缀，与 `now_iso()` 同格式）。

    Args:
        base: 基准时间。
        **offset: `timedelta` 偏移参数（如 `days=-5, seconds=200`）。

    Returns:
        str: ISO 8601 时间戳。
    """
    return (base + timedelta(**offset)).isoformat().replace("+00:00", "Z")


def _record(**overrides: object) -> TaskRecord:
    """构造任务行（默认值 + 覆盖）。

    Args:
        **overrides: 字段覆盖。

    Returns:
        TaskRecord: 任务行。
    """
    payload: dict[str, object] = {
        "tenant_id": TENANT,
        "task_id": f"task_{uuid.uuid4().hex[:16]}",
        "plugin_id": "plg_weekly_report",
        "plugin_version": "1.0.0",
        "initiator_id": "usr_stats",
        "trigger_type": "manual",
        "status": "completed",
        "usage_units": 0,
        # 唯一幂等键：`uq_task_idem (tenant_id, plugin_id, idem_key)` 不含部分谓词，
        # 空串会被当成同一个键（见 task_repo 注释）
        "idem_key": f"idem-{uuid.uuid4().hex[:12]}",
    }
    payload.update(overrides)
    return TaskRecord(**payload)  # type: ignore[arg-type]


def _seed_records(now: datetime, *, tenant_id: str = TENANT) -> list[TaskRecord]:
    """构造统计造数（含「用户主动取消 / `waiting_confirm` 跨期 / `retry_of`」各 1 条）。

    窗口 `[now−7d, now)` 内 7 条：

    - `c1`/`c2`：usr_u1 在**同一插件**手动发起 2 次 completed
      （时长 200s/300s，用量 10/20）→ 唯一的重复用户；
    - `c3`/`f1`：usr_u2 一成一败，但分属**两个插件**（100s/50s，用量 5/2）→ 不重复；
    - `x1`：usr_u3 **用户主动取消**（`cancel_requested=true`）→ 进 `cancelled`、
      出完成率分母；
    - `w1`：usr_u3 `waiting_confirm`（now−12h，期末仍未终态，**另一个插件**）→
      只进 `total`；
    - `r1`：usr_u4 `trigger_type='retry'` 的 completed（150s，用量 8）→
      进 total/completed，但**剔出** active_users 与 repeat_users。

    窗口外 1 条：`o1` now−8d completed（100s，用量 10）→ 7d 排除、30d 含入。

    Args:
        now: 窗口终点基准（一般取 `datetime.now(UTC)`）。
        tenant_id: 租户 ID（PG 对比例子用独立租户）。

    Returns:
        list[TaskRecord]: 造数列表（插入顺序即列表顺序，`r1` 的 `retry_of` 指向 `c1`）。
    """
    common = {"tenant_id": tenant_id}
    return [
        _record(
            task_id="task_stats_c1_u1a",
            initiator_id="usr_u1",
            status="completed",
            started_at=_iso(now, days=-5),
            finished_at=_iso(now, days=-5, seconds=200),
            usage_units=10,
            **common,  # type: ignore[arg-type]
        ),
        _record(
            task_id="task_stats_c2_u1b",
            initiator_id="usr_u1",
            status="completed",
            started_at=_iso(now, days=-4),
            finished_at=_iso(now, days=-4, seconds=300),
            usage_units=20,
            **common,  # type: ignore[arg-type]
        ),
        _record(
            task_id="task_stats_c3_u2",
            initiator_id="usr_u2",
            plugin_id="plg_mail_draft",
            status="completed",
            started_at=_iso(now, days=-3),
            finished_at=_iso(now, days=-3, seconds=100),
            usage_units=5,
            **common,  # type: ignore[arg-type]
        ),
        _record(
            task_id="task_stats_f1_u2",
            initiator_id="usr_u2",
            plugin_id="plg_meeting_minutes",
            status="failed",
            started_at=_iso(now, days=-2),
            finished_at=_iso(now, days=-2, seconds=50),
            usage_units=2,
            **common,  # type: ignore[arg-type]
        ),
        _record(
            task_id="task_stats_x1_u3",
            initiator_id="usr_u3",
            plugin_id="plg_meeting_minutes",
            status="cancelled",
            cancel_requested=True,
            started_at=_iso(now, days=-1),
            **common,  # type: ignore[arg-type]
        ),
        _record(
            task_id="task_stats_w1_u3",
            initiator_id="usr_u3",
            plugin_id="plg_dev_workbench",
            status="waiting_confirm",
            started_at=_iso(now, hours=-12),
            **common,  # type: ignore[arg-type]
        ),
        _record(
            task_id="task_stats_r1_u4",
            initiator_id="usr_u4",
            trigger_type="retry",
            retry_of="task_stats_c1_u1a",
            status="completed",
            started_at=_iso(now, hours=-6),
            finished_at=_iso(now, hours=-6, seconds=150),
            usage_units=8,
            **common,  # type: ignore[arg-type]
        ),
        _record(
            task_id="task_stats_o1_u5",
            initiator_id="usr_u5",
            status="completed",
            started_at=_iso(now, days=-8),
            finished_at=_iso(now, days=-8, seconds=100),
            usage_units=10,
            **common,  # type: ignore[arg-type]
        ),
    ]


async def _seed_store(store: TaskStore, records: list[TaskRecord]) -> None:
    """把造数写进存储。

    PG 的 INSERT **不含 `finished_at` 列**（只有 `update_task` 会写），
    因此两端统一走「create + 按需 update(finished_at)」同一套动作，
    保证内存/PG 拿到逐字相同的数据。

    Args:
        store: 任务存储（内存或 PG）。
        records: 造数列表（顺序插入，满足 `retry_of` 外键）。
    """
    for record in records:
        await store.create_task(record)
        if record.finished_at:
            await store.update_task(
                record.tenant_id, record.task_id, finished_at=record.finished_at
            )


# ===========================================================================
# 夹具
# ===========================================================================
@pytest.fixture(autouse=True)
async def _dispose_engine_after_test() -> AsyncIterator[None]:
    """每个用例结束后释放进程级引擎（PG 用例专用；内存模式为空操作）。

    Yields:
        AsyncIterator[None]: 无值。
    """
    yield
    await reset_engine_for_test()


@pytest.fixture
async def seeded_stats(settings: Settings) -> AsyncIterator[list[TaskRecord]]:
    """造数夹具：把 §3.1 复算数据写进内存存储（每个用例一份全新数据）。

    **为什么不重置存储单例**：`TaskService` 单例在首次构造时捕获存储实例，
    而端点读的是存储单例。一旦在用例里 `reset_task_store()`，造数会进**新**
    实例、端点仍读**旧**实例——端点看不到数据，本文件曾因此把
    `tests/security/test_attack_object_scope.py`（顺序敏感）撞红。
    隔离因此靠 `clear()`：用例前清掉别人的数据，用例后清掉自己的。

    Args:
        settings: 桩模式配置（内存后端）。

    Yields:
        AsyncIterator[list[TaskRecord]]: 落库后的造数列表。
    """
    store = get_task_store(settings)
    assert isinstance(store, InMemoryTaskStore), "统计端点用例默认跑内存后端"
    store.clear()
    records = _seed_records(datetime.now(UTC))
    await _seed_store(store, records)
    try:
        yield records
    finally:
        store.clear()


def _headers(
    settings: Settings, *, tenant_id: str = TENANT, user_id: str = "usr_stats"
) -> dict[str, str]:
    """构造鉴权请求头。

    Args:
        settings: 配置实例（取同一把签发密钥）。
        tenant_id: 令牌租户与租户头。
        user_id: 用户 ID。

    Returns:
        dict[str, str]: 请求头。
    """
    token = make_token(settings=settings, user_id=user_id, tenant_id=tenant_id, roles=["member"])
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant_id}


@pytest.fixture
def saas_settings() -> Settings:
    """SaaS 模式配置（多租户：租户头生效，用于空租户/隔离用例）。

    Returns:
        Settings: 测试配置。
    """
    reset_settings_cache()
    return Settings(
        env="test",
        deploy_mode="saas",
        downstream_enabled=False,
        auth_mode="stub",
        jwt_secret="stats-test-secret",
        ratelimit_enabled=False,
        background_jobs_enabled=False,
        default_tenant_id="default",
    )


@pytest.fixture
def other_tenant_client(saas_settings: Settings) -> Iterator[TestClient]:
    """另一个租户的测试客户端（与 `client` 共用同一内存存储单例）。

    Args:
        saas_settings: 配置实例。

    Yields:
        Iterator[TestClient]: 测试客户端。
    """
    app: FastAPI = create_app(saas_settings)
    with TestClient(app) as test_client:
        yield test_client


# ===========================================================================
# 1. AC-A2：分子分母可复算
# ===========================================================================
def test_stats_tasks_matches_manual_recomputation(
    client: TestClient, settings: Settings, seeded_stats: list[TaskRecord]
) -> None:
    """AC-A2：7d 窗口接口值与 §3.1 公式手工复算**逐字段一致**。

    手工复算（造数见 `_seed_records`）：

    - `total=7`（窗口内 7 条，`o1` 在窗口外）；
    - `completed=4`（c1/c2/c3/r1）；`failed=1`（f1）；`cancelled=1`（x1，用户主动取消）；
    - `active_users=3`（usr_u1/u2/u3；usr_u4 只有 retry，usr_u5 在窗口外）；
    - `completion_rate=4/(7−1)×100=66.67`（用户主动取消出分母，系统取消不出——
      本造数无系统取消，分母 = total − cancelled）；
    - `avg_duration_seconds=(200+300+100+150)/4=187.5`；
    - `p50_duration_seconds`：`[100,150,200,300]` 线性插值 → 175.0；
    - `repeat_user_rate=1/3×100=33.33`（usr_u1 同插件 2 次，剔 retry）；
    - `units_per_success=(10+20+5+8)/4=10.75`。
    """
    response = client.get(STATS_PATH, headers=_headers(settings), params={"range": "7d"})
    assert response.status_code == 200, response.text
    assert response.json()["code"] == 0
    assert response.json()["data"] == {
        "range": "7d",
        "total": 7,
        "completed": 4,
        "failed": 1,
        "cancelled": 1,
        "active_users": 3,
        "completion_rate": 66.67,
        "avg_duration_seconds": 187.5,
        "p50_duration_seconds": 175.0,
        "repeat_user_rate": 33.33,
        "units_per_success": 10.75,
    }


def test_stats_tasks_range_30d_includes_older_window(
    client: TestClient, settings: Settings, seeded_stats: list[TaskRecord]
) -> None:
    """AC-A6/AC-A2：30d 窗口含入 now−8d 的任务（7d 排除它），公式同口径复算。

    手工复算（30d）：`total=8`、`completed=5`、`failed=1`、`cancelled=1`、
    `active_users=4`（+usr_u5）、`completion_rate=5/7×100=71.43`、
    `avg=(750+100)/5=170.0`、`p50([100,100,150,200,300])=150.0`、
    `repeat_user_rate=1/4×100=25.0`、`units_per_success=53/5=10.6`。
    """
    response = client.get(STATS_PATH, headers=_headers(settings), params={"range": "30d"})
    assert response.status_code == 200, response.text
    assert response.json()["data"] == {
        "range": "30d",
        "total": 8,
        "completed": 5,
        "failed": 1,
        "cancelled": 1,
        "active_users": 4,
        "completion_rate": 71.43,
        "avg_duration_seconds": 170.0,
        "p50_duration_seconds": 150.0,
        "repeat_user_rate": 25.0,
        "units_per_success": 10.6,
    }


def test_stats_tasks_defaults_to_7d(
    client: TestClient, settings: Settings, seeded_stats: list[TaskRecord]
) -> None:
    """`range` 缺省为 `7d`（设计假设：取信息量更高的小窗口）。"""
    response = client.get(STATS_PATH, headers=_headers(settings))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["range"] == "7d"
    assert data["total"] == 7


# ===========================================================================
# 2. AC-A3：空数据全 0（顺带租户隔离）
# ===========================================================================
def test_stats_tasks_empty_tenant_returns_all_zero(
    other_tenant_client: TestClient,
    saas_settings: Settings,
    seeded_stats: list[TaskRecord],
) -> None:
    """AC-A3：空租户全指标 0，无 `NaN`/`null`（数据在另一个租户里，读不到）。"""
    response = other_tenant_client.get(
        STATS_PATH, headers=_headers(saas_settings, tenant_id=OTHER_TENANT)
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data == {
        "range": "7d",
        "total": 0,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
        "active_users": 0,
        "completion_rate": 0.0,
        "avg_duration_seconds": 0.0,
        "p50_duration_seconds": 0.0,
        "repeat_user_rate": 0.0,
        "units_per_success": 0.0,
    }
    assert "NaN" not in response.text
    assert "null" not in response.text


# ===========================================================================
# 3. AC-A1：参数边界与鉴权
# ===========================================================================
def test_stats_tasks_invalid_range_returns_20001(
    client: TestClient, settings: Settings, seeded_stats: list[TaskRecord]
) -> None:
    """AC-A1：`range=90d` → `code=20001` 且 HTTP 400（既有机制，不得 500）。"""
    response = client.get(STATS_PATH, headers=_headers(settings), params={"range": "90d"})
    assert response.status_code == 400
    assert response.json()["code"] == int(ErrorCode.PARAM_INVALID)
    assert response.json()["data"] is None


def test_stats_tasks_requires_login(client: TestClient) -> None:
    """未登录 → 401（既有机制）。"""
    response = client.get(STATS_PATH)
    assert response.status_code == 401
    assert response.json()["code"] == int(ErrorCode.UNAUTHORIZED)


# ===========================================================================
# 4. AC-A7：双后端一致（--run-pg 实跑）
# ===========================================================================
@pytest.mark.pg
async def test_task_stats_matches_between_backends(pg_dsn: str) -> None:
    """AC-A7：同一批造数，PG 聚合 SQL 与内存实现**逐字段一致**。

    直跑两个仓储的 `task_stats()`（端点层比率/取整不参与——它只做一次
    机械换算，双后端差异只可能出现在仓储）。除相等断言外，再钉住内存侧的
    期望值本身，防止「两边一起错还能互相绿」。

    Args:
        pg_dsn: 测试库连接串（缺 DSN 时 skip，绝不假绿）。
    """
    reset_settings_cache()
    pg_settings = Settings(
        env="test",
        deploy_mode="saas",
        downstream_enabled=False,
        auth_mode="stub",
        jwt_secret="pg-stats-secret",
        ratelimit_enabled=False,
        background_jobs_enabled=False,
        business_store="postgres",
        database_url=pg_dsn,
    )
    tenant = f"tnt_pg_stats_{uuid.uuid4().hex[:8]}"
    records = _seed_records(datetime.now(UTC), tenant_id=tenant)
    memory = InMemoryTaskStore()
    pg_store = PostgresTaskStore(pg_settings)
    try:
        await _seed_store(memory, records)
        await _seed_store(pg_store, records)
        until = datetime.now(UTC)
        for days in (7, 30):
            since = until - timedelta(days=days)
            expected = await memory.task_stats(tenant, since.isoformat(), until.isoformat())
            actual = await pg_store.task_stats(tenant, since.isoformat(), until.isoformat())
            assert actual == expected, f"{days}d 窗口双后端不一致：{actual} != {expected}"
        # 钉住内存侧期望值（防止"两边一起错"）
        since_7d = until - timedelta(days=7)
        expected_7d = await memory.task_stats(tenant, since_7d.isoformat(), until.isoformat())
        assert expected_7d == TaskStats(
            total=7,
            completed=4,
            failed=1,
            cancelled=1,
            active_users=3,
            repeat_users=1,
            duration_sum_seconds=750.0,
            duration_p50_seconds=175.0,
            units_sum=43,
        )
    finally:
        from app.db.session import platform_scope

        async with platform_scope(pg_settings) as uow:
            for table in ("plugin_task_step", "plugin_task"):
                await uow.execute(
                    f"DELETE FROM {table} WHERE tenant_id = :tenant_id",  # noqa: S608
                    {"tenant_id": tenant},
                )
