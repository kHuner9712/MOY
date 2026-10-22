"""后台任务引擎包（阶段四 T4-03.2，架构 §2.3）。

`JobRunner`：进程内消费者，PG 队列（`background_job`）+ `FOR UPDATE SKIP LOCKED`
认领 + `locked_until` 租约 + 按 `kind` 注册处理器。

**为什么不做独立 worker 进程**：轻量版的容器与内存预算卡得很死
（`docs/lite-container-baseline.md`），而 `SKIP LOCKED` + 租约在**多副本下依旧正确**，
所以标准版不需要换实现——只是每个副本都跑一个 Runner。
"""

from __future__ import annotations

from app.workers.job_runner import (
    JobHandler,
    JobRunner,
    build_worker_id,
    get_job_runner,
    reset_job_runner,
    set_job_runner,
    should_start_runner,
)

__all__: list[str] = [
    "JobHandler",
    "JobRunner",
    "build_worker_id",
    "get_job_runner",
    "reset_job_runner",
    "set_job_runner",
    "should_start_runner",
]
