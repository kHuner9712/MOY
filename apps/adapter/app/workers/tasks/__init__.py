"""后台任务处理器包（阶段四 T4-03.2 / T4-05.3，架构 §2.3）。

每个 `kind` 一个模块，暴露 `handle_xxx(job, service)` 与 `register_xxx(runner, service)`。
本包是**装配点**：把仓储/服务接到 Runner 的注册表上，因此 `build_default_runner()`
是唯一知道"有哪些任务类型"的地方，Runner 本身不认识任何业务。

已注册：

- `memory_extract`：对话每 10 轮触发一次（`max_attempts=1`，失败即丢弃）；
- `retention_cleanup`：每租户每日一次保留期清理（`max_attempts=3`）。

**`tenant_export` 刻意没有处理器**：阶段四端点预算里没有"查导出进度"的位置，
异步导出会变成"排了队但取不到"，因此 TD1 改为**同步生成**（决策与理由见
`app/business/tenant_data_service.py` 模块说明与 `docs/phase4-batch-log.md`）。
`kind` 仍留在迁移 `0010` 的 CHECK 里——枚举是冻结的，删一个取值等于改契约；
队列里不会出现该类任务，而将来若转回异步，处理器位置现成。

注册了却没有处理器，比没注册更糟：任务会被反复认领后判失败。
"""

from __future__ import annotations

from functools import partial

from app.business.memory_service import MEMORY_EXTRACT_MAX_ATTEMPTS, MemoryService
from app.core.config import Settings, get_settings
from app.workers.job_runner import JobRunner
from app.workers.tasks.memory_extract import handle_memory_extract
from app.workers.tasks.retention_cleanup import register_retention_cleanup

__all__: list[str] = ["build_default_runner"]


def build_default_runner(
    settings: Settings | None = None, *, memory: MemoryService | None = None
) -> JobRunner:
    """按默认处理器装配 Runner。

    Args:
        settings: 可选配置实例。
        memory: 可选记忆服务（测试注入）。

    Returns:
        JobRunner: 已注册 `memory_extract` 与 `retention_cleanup` 的 Runner。
    """
    conf = settings or get_settings()
    service = memory or MemoryService(conf)
    runner = JobRunner(conf)
    runner.register(
        "memory_extract",
        partial(handle_memory_extract, service=service),
        max_attempts=MEMORY_EXTRACT_MAX_ATTEMPTS,
    )
    register_retention_cleanup(runner, conf)
    return runner
