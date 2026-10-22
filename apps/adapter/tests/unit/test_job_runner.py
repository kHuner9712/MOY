"""后台任务引擎的单元验收（阶段四 T4-03.2，架构 §2.3 / §3.4 / §4.5）。

四条判据都对应"错了会在生产上丢任务或重复执行"的形态：

1. **同 kind 去重**：同一会话被反复排队，会把同一次抽取跑 N 遍（额度白烧）；
2. **租约回收**：进程被 kill 后行停在 `running`，不回收则任务永久卡死；
3. **`max_attempts` 生效**：`memory_extract` 是 1（不重试），写错成 3 会重复烧上游；
4. **`tick()` 可同步驱动且不留残余协程**：pytest 事件循环里留一个永不结束的 task
   会污染后续用例（"Task was destroyed but it is pending"）。

全部用内存实现 + 可注入时钟，不依赖真实 PostgreSQL 与 sleep。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.db.repositories.job_repo import (
    InMemoryJobStore,
    JobRecord,
    retry_delay_seconds,
    sanitize_error,
)
from app.workers.job_runner import JobRunner, build_worker_id, should_start_runner

pytestmark = pytest.mark.unit

TENANT = "tnt_test_tenant"
OTHER_TENANT = "tnt_other_tenant"


class _Clock:
    """可前进的测试时钟（避免用 sleep 等真实时间）。"""

    def __init__(self) -> None:
        self.now = datetime(2026, 11, 1, 9, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        """前进指定秒数。

        Args:
            seconds: 秒数。
        """
        self.now = self.now + timedelta(seconds=seconds)


def _runner(store: InMemoryJobStore, **kwargs: object) -> JobRunner:
    """构造只依赖内存存储的 Runner。

    Args:
        store: 任务存储。
        **kwargs: 其它 Runner 参数。

    Returns:
        JobRunner: Runner 实例。
    """
    from app.core.config import Settings

    settings = Settings(env="test", business_store="memory", background_jobs_enabled=True)
    return JobRunner(
        settings,
        store=store,
        worker_id="test-worker",
        task_timeout_seconds=1.0,
        lease_seconds=60,
        batch_size=10,
        **kwargs,  # type: ignore[arg-type]
    )


async def _enqueue(
    store: InMemoryJobStore,
    *,
    kind: str = "memory_extract",
    dedupe_key: str = "conv_01",
    max_attempts: int = 1,
    tenant_id: str = TENANT,
) -> JobRecord | None:
    """入队一个任务。

    Args:
        store: 任务存储。
        kind: 任务类型。
        dedupe_key: 去重键。
        max_attempts: 最大尝试次数。
        tenant_id: 企业 ID。

    Returns:
        JobRecord | None: 入队结果（去重命中时为 None）。
    """
    return await store.enqueue(
        JobRecord(
            tenant_id=tenant_id,
            kind=kind,
            dedupe_key=dedupe_key,
            payload={"conversation_id": dedupe_key},
            max_attempts=max_attempts,
        )
    )


class TestEnqueueDedupe:
    """同 kind 同去重键只允许一个未终结任务。"""

    async def test_same_kind_same_key_is_deduped(self) -> None:
        """重复入队返回 None，库里只有一条。"""
        clock = _Clock()
        store = InMemoryJobStore(now=clock)
        first = await _enqueue(store)
        second = await _enqueue(store)

        assert first is not None
        assert second is None
        queued = await store.list_by_status(TENANT, "queued")
        assert len(queued) == 1

    async def test_different_kind_or_tenant_is_not_deduped(self) -> None:
        """不同 kind / 不同租户不算重复。"""
        store = InMemoryJobStore(now=_Clock())
        assert await _enqueue(store, kind="memory_extract") is not None
        assert await _enqueue(store, kind="tenant_export") is not None
        assert await _enqueue(store, kind="memory_extract", tenant_id=OTHER_TENANT) is not None

    async def test_dedupe_released_after_task_finishes(self) -> None:
        """任务终结后同键可再次入队（否则一个会话这一辈子只能抽一次）。"""
        clock = _Clock()
        store = InMemoryJobStore(now=clock)
        runner = _runner(store)

        async def handler(job: JobRecord) -> None:
            _ = job

        runner.register("memory_extract", handler, max_attempts=1)
        assert await _enqueue(store) is not None
        assert await runner.tick() == 1
        assert await _enqueue(store) is not None


class TestClaimAndLease:
    """认领、租约与回收。"""

    async def test_tick_claims_and_marks_succeeded(self) -> None:
        """`tick()` 同步驱动一轮：状态 succeeded、attempts=1。"""
        clock = _Clock()
        store = InMemoryJobStore(now=clock)
        runner = _runner(store)
        seen: list[str] = []

        async def handler(job: JobRecord) -> None:
            seen.append(job.job_id)
            assert job.status == "running"
            assert job.locked_by == "test-worker"

        runner.register("memory_extract", handler, max_attempts=1)
        job = await _enqueue(store)
        assert job is not None

        assert await runner.tick() == 1
        assert seen == [job.job_id]
        stored = await store.get(TENANT, job.job_id)
        assert stored is not None
        assert stored.status == "succeeded"
        assert stored.attempts == 1
        assert stored.locked_by == ""
        assert stored.finished_at

    async def test_tick_returns_zero_when_idle(self) -> None:
        """队列为空时 `tick()` 返回 0（不抛、不阻塞）。"""
        runner = _runner(InMemoryJobStore(now=_Clock()))
        assert await runner.tick() == 0

    async def test_expired_lease_is_reclaimed(self) -> None:
        """进程崩溃留下的 `running` 行在租约过期后被重新认领。"""
        clock = _Clock()
        store = InMemoryJobStore(now=clock)
        job = await _enqueue(store)
        assert job is not None

        # 模拟"另一个进程认领后就崩了"：认领但从不标记终结
        crashed = await store.claim(worker_id="dead-worker", limit=1, lease_seconds=10)
        assert len(crashed) == 1
        assert crashed[0].attempts == 1

        # 租约未过期 → 不可被认领
        runner = _runner(store)
        assert await runner.tick() == 0

        # 租约过期 → 回收并成功执行
        clock.advance(30)
        ran: list[str] = []

        async def handler(item: JobRecord) -> None:
            ran.append(item.job_id)

        runner.register("memory_extract", handler, max_attempts=3)
        assert await runner.tick() == 1
        assert ran == [job.job_id]
        stored = await store.get(TENANT, job.job_id)
        assert stored is not None
        assert stored.status == "succeeded"
        assert stored.attempts == 2  # 崩溃那次 + 本次


class TestRetryPolicy:
    """`max_attempts` 与按 kind 的重试策略。"""

    async def test_max_attempts_one_means_no_retry(self) -> None:
        """`max_attempts=1`（memory_extract 的口径）：首次失败即终结。"""
        clock = _Clock()
        store = InMemoryJobStore(now=clock)
        runner = _runner(store)
        calls: list[int] = []

        async def failing(job: JobRecord) -> None:
            calls.append(job.attempts)
            raise RuntimeError("upstream boom")

        runner.register("memory_extract", failing, max_attempts=1)
        job = await _enqueue(store, max_attempts=1)
        assert job is not None

        assert await runner.tick() == 1
        stored = await store.get(TENANT, job.job_id)
        assert stored is not None
        assert stored.status == "failed"
        assert stored.attempts == 1
        assert "RuntimeError" in stored.last_error

        # 再驱动一轮也不会重试（已终结）
        clock.advance(120)
        assert await runner.tick() == 0
        assert calls == [1]

    async def test_max_attempts_retries_until_exhausted(self) -> None:
        """`max_attempts=2`：第一次失败退回队列，第二次失败终结。"""
        clock = _Clock()
        store = InMemoryJobStore(now=clock)
        runner = _runner(store)
        calls: list[int] = []

        async def failing(job: JobRecord) -> None:
            calls.append(job.attempts)
            raise RuntimeError("still boom")

        runner.register("tenant_export", failing, max_attempts=2)
        job = await _enqueue(store, kind="tenant_export", max_attempts=2)
        assert job is not None

        assert await runner.tick() == 1
        mid = await store.get(TENANT, job.job_id)
        assert mid is not None
        assert mid.status == "queued"
        assert mid.run_after  # 已排退避时间

        clock.advance(retry_delay_seconds(1) + 1)
        assert await runner.tick() == 1
        done = await store.get(TENANT, job.job_id)
        assert done is not None
        assert done.status == "failed"
        assert done.attempts == 2
        assert calls == [1, 2]

    async def test_registry_policy_caps_database_value(self) -> None:
        """注册表的重试策略是**上限**：库里写 3 也不能突破 `max_attempts=1`。"""
        clock = _Clock()
        store = InMemoryJobStore(now=clock)
        runner = _runner(store)

        async def failing(job: JobRecord) -> None:
            _ = job
            raise RuntimeError("boom")

        runner.register("memory_extract", failing, max_attempts=1)
        job = await _enqueue(store, max_attempts=3)
        assert job is not None

        assert await runner.tick() == 1
        stored = await store.get(TENANT, job.job_id)
        assert stored is not None
        assert stored.status == "failed"

    async def test_unregistered_kind_fails_loudly(self) -> None:
        """没有处理器的 kind 判失败并写明原因（不静默丢弃、不无限认领）。"""
        clock = _Clock()
        store = InMemoryJobStore(now=clock)
        runner = _runner(store)
        job = await _enqueue(store, kind="retention_cleanup", dedupe_key="")
        assert job is not None

        assert await runner.tick() == 1
        stored = await store.get(TENANT, job.job_id)
        assert stored is not None
        assert stored.status == "failed"
        assert "no handler" in stored.last_error


class TestStartStop:
    """`start()` / `stop()` 的生命周期（测试环境不留后台协程）。"""

    async def test_start_then_stop_leaves_no_residual_task(self) -> None:
        """停止后事件循环里不再有 runner 协程。"""
        store = InMemoryJobStore(now=_Clock())
        runner = _runner(store, poll_interval_seconds=0.01)

        async def handler(job: JobRecord) -> None:
            _ = job

        runner.register("memory_extract", handler, max_attempts=1)
        await _enqueue(store)

        runner.start()
        assert runner.running
        await asyncio.sleep(0.05)
        await _enqueue(store, dedupe_key="conv_02")
        await asyncio.sleep(0.05)
        await runner.stop()

        assert not runner.running
        assert runner.handled_total >= 1
        names = [task.get_name() for task in asyncio.all_tasks()]
        assert "oa-background-job-runner" not in names

    async def test_stop_is_idempotent(self) -> None:
        """重复 `stop()` 不抛（lifespan 与测试钩子可能都调一次）。"""
        runner = _runner(InMemoryJobStore(now=_Clock()))
        await runner.stop()
        await runner.stop()


class TestHelpers:
    """辅助函数与启动判定。"""

    def test_should_start_runner_matrix(self) -> None:
        """三个条件缺一不可。"""
        from app.core.config import Settings

        enabled = Settings(
            env="development", business_store="postgres", background_jobs_enabled=True
        )
        assert should_start_runner(enabled, has_engine=True) is True
        assert should_start_runner(enabled, has_engine=False) is False

        disabled = Settings(
            env="development", business_store="postgres", background_jobs_enabled=False
        )
        assert should_start_runner(disabled, has_engine=True) is False

        testing = Settings(env="test", business_store="postgres", background_jobs_enabled=True)
        assert should_start_runner(testing, has_engine=True) is False

    def test_worker_id_shape(self) -> None:
        """持有者标识形如 `<host>:<pid>:<uuid8>`（排障要能定位进程）。"""
        worker_id = build_worker_id()
        assert worker_id.count(":") == 2
        assert len(worker_id.rsplit(":", 1)[1]) == 8

    def test_sanitize_error_strips_newlines_and_truncates(self) -> None:
        """`last_error` 单行且截断（不含正文）。"""
        assert sanitize_error("a\nb\nc") == "a b c"
        assert len(sanitize_error("x" * 1000)) == 200
