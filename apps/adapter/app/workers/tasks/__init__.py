"""后台任务处理器包（阶段四 T4-03.2，架构 §2.3）。

每个 `kind` 一个模块，暴露 `handle_xxx(job, service)` 与 `register_xxx(runner, service)`。
本包是**装配点**：把仓储/服务接到 Runner 的注册表上，因此 `build_default_runner()`
是唯一知道"有哪些任务类型"的地方，Runner 本身不认识任何业务。

导出与保留期清理两种任务随各自提交（T4-05）接入，此处只注册 `memory_extract`——
注册了却没有处理器，比没注册更糟：任务会被反复认领后判失败。
"""

from __future__ import annotations

from functools import partial

from app.business.memory_service import MEMORY_EXTRACT_MAX_ATTEMPTS, MemoryService
from app.core.config import Settings, get_settings
from app.workers.job_runner import JobRunner
from app.workers.tasks.memory_extract import handle_memory_extract

__all__: list[str] = ["build_default_runner"]


def build_default_runner(
    settings: Settings | None = None, *, memory: MemoryService | None = None
) -> JobRunner:
    """按默认处理器装配 Runner。

    Args:
        settings: 可选配置实例。
        memory: 可选记忆服务（测试注入）。

    Returns:
        JobRunner: 已注册 `memory_extract` 的 Runner。
    """
    conf = settings or get_settings()
    service = memory or MemoryService(conf)
    runner = JobRunner(conf)
    runner.register(
        "memory_extract",
        partial(handle_memory_extract, service=service),
        max_attempts=MEMORY_EXTRACT_MAX_ATTEMPTS,
    )
    return runner
