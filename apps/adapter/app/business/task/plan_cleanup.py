"""计划超时清理（矩阵第 2 项）· 扫过期计划并条件取消。

**为什么要清理**：计划任务停在 `waiting_plan` 不计并发额度，如果用户既不确认
也不取消，它会永久留在任务列表里（"永远待办"）。清理器把它们置为
`cancelled(reason=计划已过期，未执行)`，既保留可解释的历史，又不占用任何执行资源。

**三条口径**：

1. **条件写**：取消必须同时满足"仍是 `waiting_plan`"与"计划版本未变"。
   用户刚确认 / 刚编辑的任务不会被旧快照误杀（见 `plan.expire_plan`）。
2. **不是用户取消**：`cancel_requested` 保持 False，任务统计的完成率口径不变。
3. **RLS 兜底**：枚举租户用只读 `platform_scope()`（只 SELECT tenant_id），
   实际取消逐租户走 `tenant_scope` 写路径——与 `retention_cleanup` 同一模式，
   不新开跨租户写通道。

**两处触发点**：启动后的后台轮询（本模块 `plan_cleanup_loop`）负责"没人管"的
任务；`GET /tasks/{id}` 与计划编辑/确认路径对单张任务做**懒过期**，保证用户
永远看不到"已经过期却还显示可确认"的计划。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime

from app.business.task.plan import expire_plan
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.repositories.task import TaskStore, get_task_store
from app.db.session import platform_scope

logger = get_logger(__name__)

#: 单次扫描每个租户的上限（与 `QUEUE_SCAN_LIMIT` 同一量级）。
SWEEP_BATCH_LIMIT: int = 200

#: 后台轮询间隔（秒）。计划默认 900s 过期，30s 粒度足够；不追求秒级。
SWEEP_INTERVAL_SECONDS: int = 30


async def sweep_expired_plans(
    tasks: TaskStore,
    settings: Settings | None = None,
    *,
    tenant_ids: Sequence[str] | None = None,
    now: datetime | None = None,
    limit: int = SWEEP_BATCH_LIMIT,
) -> int:
    """扫描并取消已过期的计划任务。

    Args:
        tasks: 任务仓储。
        settings: 配置（功能关闭时直接返回 0）。
        tenant_ids: 租户列表（测试注入；缺省用只读 `platform_scope` 枚举）。
        now: 基准时间（测试注入）。
        limit: 每租户单批上限。

    Returns:
        int: 本次实际取消的任务数。
    """
    conf = settings or get_settings()
    if not conf.task_plan_enabled:
        return 0
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    before = moment.isoformat().replace("+00:00", "Z")
    ids = list(tenant_ids) if tenant_ids is not None else await _list_tenants(conf)
    swept = 0
    for tenant_id in ids:
        for record in await tasks.list_expired_plans(tenant_id, before, limit):
            if await expire_plan(tasks, record):
                swept += 1
    return swept


async def _list_tenants(settings: Settings) -> list[str]:
    """枚举租户（只读 `platform_scope`，只 SELECT tenant_id）。

    不复用 `workers.tasks.retention_cleanup.list_tenants`：那会让 business 反向
    依赖 workers（`validate_import_directions` 的 R-BIZ-WRK）。这里只做一次只读枚举，
    写路径仍逐租户回到 `tenant_scope`，与保留期清理同一模式。

    Args:
        settings: 配置。

    Returns:
        list[str]: 租户 ID 列表。
    """
    async with platform_scope(settings, read_only=True) as uow:
        rows = await uow.fetch_all("SELECT tenant_id FROM tenant ORDER BY tenant_id")
    return [str(row["tenant_id"]) for row in rows]


async def plan_cleanup_loop(
    settings: Settings | None = None,
    *,
    tasks: TaskStore | None = None,
    interval: int = SWEEP_INTERVAL_SECONDS,
) -> None:
    """后台轮询：每隔 `interval` 秒扫描一次过期计划。

    Args:
        settings: 配置。
        tasks: 任务仓储（缺省按配置选后端）。
        interval: 轮询间隔（秒）。
    """
    conf = settings or get_settings()
    store = tasks or get_task_store(conf)
    while True:
        try:
            swept = await sweep_expired_plans(store, conf)
            if swept:
                logger.info("task_plan_cleanup", swept=swept)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # 清理是"后台维护"，失败只告警不退出：下一轮会重试，
            # 且单任务路径的懒过期仍能保证用户视角正确。
            logger.warning("task_plan_cleanup_failed", error=type(exc).__name__)
        await asyncio.sleep(interval)


__all__: list[str] = [
    "SWEEP_BATCH_LIMIT",
    "SWEEP_INTERVAL_SECONDS",
    "plan_cleanup_loop",
    "sweep_expired_plans",
]
