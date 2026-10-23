"""任务推进锁（阶段四 T4-04.4，架构 §2.6 防重复推进）。

**为什么需要它**：任务推进是"读步骤 → 跑一步 → 写结果"的多段流程，两个进程（或两个
请求触发的后台推进）同时进来，会各自读到 `current_step_seq=2`，各自把第 3 步跑一遍
——产物重复、额度重复扣、步骤序号跳变。加锁的必要性不是"怕慢"，是"怕重"。

**两种锁，按有没有事务选**（架构 §2.6 原话）：

- 有事务时 `pg_advisory_xact_lock`：事务一结束锁自动释放，最省心；
- 本模块的场景是**多次独立事务**（每步的结果单独提交），所以用
  `pg_try_advisory_lock` + `finally` 显式 `pg_advisory_unlock`，并**独占一条连接**
  ——会话级 advisory lock 绑定在连接上，连接归还池就会跟着丢。

`pg_try_*` 而不是 `pg_*`：拿不到锁时应当**立刻返回**（另一个推进者正在干活），
而不是把自己的协程挂在 DB 里等——任务推进是被用户请求触发的，不能把请求线程也等死。

内存模式用进程内 `asyncio.Lock`（非阻塞 `locked()` 判断），与 PG 语义保持一致：
拿不到就返回，不排队。
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

from sqlalchemy import text as sql_text

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.engine import get_engine

logger = get_logger(__name__)

#: advisory lock 的命名空间（不同业务用不同前缀，避免键空间撞车）
TASK_ADVANCE_LOCK_PREFIX: Final[str] = "task_advance:"

#: 进程内锁表（内存模式）
_local_locks: Final[dict[str, asyncio.Lock]] = {}


def advisory_key(name: str) -> int:
    """把任意字符串映射成 63 位有符号整数（`pg_advisory_lock(bigint)` 的取值范围）。

    Args:
        name: 锁名（如 `task_advance:task_01H...`）。

    Returns:
        int: 稳定的锁键（同一名字恒得同一数值）。
    """
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    value = int.from_bytes(digest[:8], "big")
    return value & 0x7FFF_FFFF_FFFF_FFFF


@asynccontextmanager
async def task_advance_lock(task_id: str, settings: Settings | None = None) -> AsyncIterator[bool]:
    """尝试获取"某任务的推进权"。

    Yields:
        bool: `True` 表示拿到（调用方继续推进）；`False` 表示别人正在推进（本次跳过）。

    Args:
        task_id: 任务 ID。
        settings: 可选配置实例。
    """
    conf = settings or get_settings()
    name = f"{TASK_ADVANCE_LOCK_PREFIX}{task_id}"

    if not conf.uses_postgres:
        lock = _local_locks.setdefault(name, asyncio.Lock())
        if lock.locked():
            yield False
            return
        async with lock:
            yield True
        return

    key = advisory_key(name)
    engine = get_engine(conf)
    if engine is None:  # pragma: no cover - uses_postgres 时引擎必存在
        yield False
        return
    # 独占连接：会话级 advisory lock 绑定在连接上，归还池即丢锁
    async with engine.connect() as connection:
        acquired = bool(
            (
                await connection.execute(
                    sql_text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
                )
            ).scalar()
        )
        if not acquired:
            logger.info("task_advance_lock_busy", task_id=task_id)
            yield False
            return
        try:
            yield True
        finally:
            await connection.execute(sql_text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            await connection.commit()


def reset_advance_locks() -> None:
    """清空进程内锁表（测试隔离用）。"""
    _local_locks.clear()


__all__: list[str] = [
    "TASK_ADVANCE_LOCK_PREFIX",
    "advisory_key",
    "reset_advance_locks",
    "task_advance_lock",
]
