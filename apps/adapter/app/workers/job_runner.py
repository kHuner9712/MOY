"""后台任务引擎（阶段四 T4-03.2，架构 §2.3 / §4.5 / §5.3）。

进程内 Runner + PG 队列（`background_job`）。三条设计要点：

1. **注册表而非 if/elif**：新增任务类型只调 `register(kind, handler)`，
   不碰主循环。主循环里出现第二种 `if kind == ...` 的那天，就是它开始长出
   业务分支、再也测不全的那天。
2. **可同步驱动**：`tick()` 处理一轮并返回条数。测试因此不需要 sleep、不需要
   真起后台协程——"没有残留协程"是可断言的事实，而不是靠肉眼观察。
3. **可关**：`start()` 只在 `lifespan` 里按配置调用；`env=test` 默认不启动
   （`should_start_runner()`），避免后台协程在 pytest 的事件循环里留尾巴。

任务超时用 `asyncio.wait_for`：抽取卡住不能永久占着租约，否则那一行
`running` 直到租约过期才会被回收，中间这个进程还在等一个可能永不返回的请求。
"""

from __future__ import annotations

import asyncio
import os
import socket
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Final

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.metrics import BACKGROUND_JOB_DURATION_SECONDS, BACKGROUND_JOBS_TOTAL
from app.db.repositories.job_repo import (
    JobRecord,
    JobStore,
    get_job_store,
    sanitize_error,
)

logger = get_logger(__name__)

#: 任务处理器签名：拿到任务记录，自己按 `job.tenant_id` 开事务
JobHandler = Callable[[JobRecord], Awaitable[None]]

#: 默认轮询间隔（秒）
DEFAULT_POLL_INTERVAL_SECONDS: Final[float] = 1.0

#: 默认租约时长（秒）
DEFAULT_LEASE_SECONDS: Final[int] = 120

#: 默认单任务超时（秒）
DEFAULT_TASK_TIMEOUT_SECONDS: Final[float] = 60.0

#: 默认单轮认领条数
DEFAULT_BATCH_SIZE: Final[int] = 5


@dataclass(slots=True)
class _Registration:
    """一个 `kind` 的处理器与重试策略。

    Attributes:
        handler: 处理器。
        max_attempts: 该类任务的最大尝试次数（1 = 不重试）。
    """

    handler: JobHandler
    max_attempts: int


def build_worker_id() -> str:
    """构造持有者标识 `<host>:<pid>:<uuid>`（排障时能直接定位到进程）。

    Returns:
        str: 持有者标识。
    """
    host = socket.gethostname() or "unknown"
    return f"{host}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def should_start_runner(settings: Settings, *, has_engine: bool) -> bool:
    """判定是否应在 `lifespan` 里启动后台轮询。

    三个条件缺一不可：

    1. `background_jobs_enabled`（运维开关）；
    2. `env != "test"`（测试环境**永不**起后台协程：pytest 事件循环里留一个
       永不结束的 task，既污染断言也会在结束时刷 "Task was destroyed" 告警）；
    3. 存在数据库引擎（`business_store=memory` 时没有队列可消费，起来只是空转）。

    Args:
        settings: 配置实例。
        has_engine: 是否已创建业务库引擎。

    Returns:
        bool: 应启动时返回 True。
    """
    return bool(settings.background_jobs_enabled) and settings.env != "test" and has_engine


class JobRunner:
    """PG 队列的进程内消费者。"""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        store: JobStore | None = None,
        poll_interval_seconds: float | None = None,
        lease_seconds: int | None = None,
        task_timeout_seconds: float | None = None,
        batch_size: int | None = None,
        worker_id: str = "",
    ) -> None:
        """初始化。

        Args:
            settings: 可选配置实例。
            store: 任务存储（缺省按配置取）。
            poll_interval_seconds: 轮询间隔（缺省取配置）。
            lease_seconds: 租约时长（缺省取配置）。
            task_timeout_seconds: 单任务超时（缺省取配置）。
            batch_size: 单轮认领条数（缺省取配置）。
            worker_id: 持有者标识（缺省自动生成）。
        """
        self._settings = settings or get_settings()
        self._store: JobStore = store or get_job_store(self._settings)
        self._poll_interval = float(
            poll_interval_seconds
            if poll_interval_seconds is not None
            else self._settings.background_job_poll_interval_seconds
        )
        self._lease_seconds = int(
            lease_seconds
            if lease_seconds is not None
            else self._settings.background_job_lease_seconds
        )
        self._timeout = float(
            task_timeout_seconds
            if task_timeout_seconds is not None
            else self._settings.background_job_timeout_seconds
        )
        self._batch_size = int(
            batch_size if batch_size is not None else self._settings.background_job_batch_size
        )
        self._worker_id = worker_id or build_worker_id()
        self._handlers: dict[str, _Registration] = {}
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._handled = 0

    # ------------------------------------------------------------------
    # 注册表
    # ------------------------------------------------------------------
    def register(self, kind: str, handler: JobHandler, *, max_attempts: int = 3) -> None:
        """注册某类任务的处理器（新增 kind 不改主循环）。

        Args:
            kind: 任务类型（须是 `background_job.kind` 的合法取值）。
            handler: 处理器。
            max_attempts: 该类任务的最大尝试次数；`1` 表示失败即终结（不重试）。

        Raises:
            ValueError: `max_attempts` 非法。
        """
        if max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")
        self._handlers[kind] = _Registration(handler=handler, max_attempts=max_attempts)

    def registered_kinds(self) -> list[str]:
        """已注册的任务类型（按字典序，便于日志与断言）。

        Returns:
            list[str]: 类型列表。
        """
        return sorted(self._handlers)

    @property
    def worker_id(self) -> str:
        """持有者标识。

        Returns:
            str: 标识。
        """
        return self._worker_id

    @property
    def handled_total(self) -> int:
        """已处理（认领过）的任务总数。

        Returns:
            int: 条数。
        """
        return self._handled

    @property
    def running(self) -> bool:
        """轮询是否在跑。

        Returns:
            bool: 后台 task 未结束时返回 True。
        """
        return self._task is not None and not self._task.done()

    # ------------------------------------------------------------------
    # 驱动
    # ------------------------------------------------------------------
    async def tick(self) -> int:
        """认领并执行**一轮**任务，返回处理的条数（测试可同步驱动）。

        未注册的 `kind` 不静默丢弃：记 WARN 并置失败（`last_error` 写明缺处理器），
        否则任务会在队列里排到租约过期、反复被认领，看起来像"卡住了"。

        Returns:
            int: 本轮实际处理的任务条数。
        """
        claimed = await self._store.claim(
            worker_id=self._worker_id,
            limit=self._batch_size,
            lease_seconds=self._lease_seconds,
        )
        if not claimed:
            return 0
        for job in claimed:
            await self._run_job(job)
        self._handled += len(claimed)
        return len(claimed)

    async def _run_job(self, job: JobRecord) -> None:
        """执行单个任务（超时、失败、缺处理器都在此收口）。

        Args:
            job: 已认领的任务记录。
        """
        registration = self._handlers.get(job.kind)
        if registration is None:
            logger.warning(
                "job_handler_missing",
                job_id=job.job_id,
                tenant_id=job.tenant_id,
                kind=job.kind,
            )
            await self._store.mark_failed(
                job, error=sanitize_error(f"no handler registered for kind {job.kind}")
            )
            return
        # 注册表的策略是**上限**：库里写更大的值也不能突破（防"随手写 5 次重试"）
        effective = replace(
            job, max_attempts=min(max(1, job.max_attempts), registration.max_attempts)
        )
        started = time.perf_counter()
        try:
            await asyncio.wait_for(registration.handler(job), timeout=self._timeout)
        except asyncio.CancelledError:
            # 停机中断：不标记失败（任务仍是 running，租约到期后由下一次认领回收）
            raise
        except Exception as exc:
            BACKGROUND_JOB_DURATION_SECONDS.labels(kind=job.kind).observe(
                max(time.perf_counter() - started, 0.0)
            )
            error = f"{type(exc).__name__}"
            status = await self._store.mark_failed(effective, error=error)
            BACKGROUND_JOBS_TOTAL.labels(kind=job.kind, status=status).inc()
            logger.warning(
                "job_failed",
                job_id=job.job_id,
                tenant_id=job.tenant_id,
                kind=job.kind,
                attempts=job.attempts,
                max_attempts=effective.max_attempts,
                status=status,
                detail=type(exc).__name__,
            )
            return
        await self._store.mark_succeeded(job)
        BACKGROUND_JOBS_TOTAL.labels(kind=job.kind, status="succeeded").inc()
        BACKGROUND_JOB_DURATION_SECONDS.labels(kind=job.kind).observe(
            max(time.perf_counter() - started, 0.0)
        )
        logger.info(
            "job_succeeded",
            job_id=job.job_id,
            tenant_id=job.tenant_id,
            kind=job.kind,
            attempts=job.attempts,
        )

    def start(self) -> None:
        """启动后台轮询（幂等：已在跑时不做任何事）。

        只在 `lifespan` 里调用，且必须先过 `should_start_runner()`。
        """
        if self.running:
            logger.info("job_runner_already_running", worker_id=self._worker_id)
            return
        self._stopping = False
        self._task = asyncio.create_task(self._loop(), name="oa-background-job-runner")
        logger.info(
            "job_runner_started",
            worker_id=self._worker_id,
            kinds=self.registered_kinds(),
            poll_interval_seconds=self._poll_interval,
            lease_seconds=self._lease_seconds,
        )

    async def stop(self) -> None:
        """停止后台轮询并**等待协程真正结束**（不留残余 task）。

        只 `cancel()` 不 `await` 是常见错误：pytest 结束时会打印
        "Task was destroyed but it is pending"，且下一轮用例会看到幽灵协程。
        """
        self._stopping = True
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # 关停期的异常不外抛（不能因此让停机失败）
            logger.warning("job_runner_stop_failed", detail=type(exc).__name__)
        logger.info("job_runner_stopped", worker_id=self._worker_id)

    async def _loop(self) -> None:
        """轮询主循环（`start()` 创建的唯一协程）。"""
        while not self._stopping:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # 单轮失败不终止循环：下一轮还会再来
                logger.warning("job_runner_tick_failed", detail=type(exc).__name__)
            await asyncio.sleep(self._poll_interval)


#: Runner 单例（`lifespan` 持有）
_job_runner: JobRunner | None = None


def get_job_runner(settings: Settings | None = None) -> JobRunner:
    """取 Runner 单例（未创建时按默认处理器装配）。

    Args:
        settings: 可选配置实例。

    Returns:
        JobRunner: Runner 实例。
    """
    global _job_runner
    conf = settings or get_settings()
    if _job_runner is None:
        from app.workers.tasks import build_default_runner

        _job_runner = build_default_runner(conf)
    return _job_runner


def set_job_runner(runner: JobRunner | None) -> None:
    """替换 Runner 单例（`lifespan` 接线与测试注入用）。

    Args:
        runner: Runner 实例；`None` 表示清空。
    """
    global _job_runner
    _job_runner = runner


def reset_job_runner() -> None:
    """重置 Runner 单例（测试隔离用；不会停止已启动的协程）。"""
    global _job_runner
    _job_runner = None


__all__: list[str] = [
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_LEASE_SECONDS",
    "DEFAULT_POLL_INTERVAL_SECONDS",
    "DEFAULT_TASK_TIMEOUT_SECONDS",
    "JobHandler",
    "JobRunner",
    "build_worker_id",
    "get_job_runner",
    "reset_job_runner",
    "set_job_runner",
    "should_start_runner",
]
