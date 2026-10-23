"""`task_queue_depth` 定时刷新（阶段四 T4-05.1，架构 §8.1）。

**为什么必须定时刷新而不是在 `/metrics` 里现查**：Prometheus 默认 15s 抓一次，
现查意味着"抓取频率 = 查库频率"，多副本时把数据库持续打醒；而队列深度本身就是
分钟级才需要关注的容量信号，10s 一次的缓存值完全够用。

刷新失败（库抖动）**不重置 gauge**：保留上一次的值比"跳回 0"更接近真相——
0 会让人以为队列空了，从而错过积压。
"""

from __future__ import annotations

import asyncio
from typing import Final

from app.core.logging import get_logger
from app.core.metrics import TASK_QUEUE_DEPTH
from app.db.repositories.job_repo import JobStore

logger = get_logger(__name__)

#: 刷新间隔（秒）：队列深度是容量信号，10s 足够，且远低于抓取周期带来的压力
REFRESH_INTERVAL_SECONDS: Final[float] = 10.0


async def refresh_once(store: JobStore) -> int:
    """刷新一次 gauge，返回当前未终结任务数。

    Args:
        store: 任务存储。

    Returns:
        int: 未终结任务数（`queued` + `running`）；失败时返回 -1。
    """
    try:
        depth = await store.count_open()
    except Exception as exc:  # 刷新失败保留旧值（0 会误导成"队列空了"）
        logger.warning("task_queue_depth_refresh_failed", detail=type(exc).__name__)
        return -1
    TASK_QUEUE_DEPTH.set(depth)
    return depth


async def refresh_loop(
    store: JobStore, *, interval_seconds: float = REFRESH_INTERVAL_SECONDS
) -> None:
    """周期性刷新（由 `lifespan` 启停，与 `JobRunner` 同生命周期）。

    Args:
        store: 任务存储。
        interval_seconds: 刷新间隔（秒）。
    """
    while True:
        await refresh_once(store)
        await asyncio.sleep(interval_seconds)


__all__: list[str] = [
    "REFRESH_INTERVAL_SECONDS",
    "refresh_loop",
    "refresh_once",
]
