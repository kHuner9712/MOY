"""插件任务编排 · 声明解析（`TaskManifestResolver`，切片 2C-3b）。

**两条读取口径必须并存，不能合并**（合并会静默改变错误码）：

1. `find_manifest()`：**取不到返回 None，不抛**。列表视图用它——单条任务的声明缺失
   不该拖垮整个列表（LED-045：一条 `plugin_id` 指向不存在插件的脏任务曾让
   `GET /api/v1/agent/tasks` 整体 404）；
2. `manifest_of()`：**缺失是显性失败（16002）**。详情/执行路径用它——没有声明就无法
   正确渲染步骤、确认语义与核查清单，静默兜底会给出错误结果。

历史任务按创建时版本渲染（`plugin_version` 是快照，PRD R2 对策）；快照版本不在册时
回落最新在册版本。这条回落只发生在 `find_manifest()` 内部，`manifest_of()` 因此也
享受同一份回落，但缺失时仍然抛。
"""

from __future__ import annotations

from typing import Any

from app.business.task.deps import TaskDeps
from app.core.errors import ApiError, ErrorCode
from app.db.repositories.task_repo import TaskRecord
from app.plugins.schema import PluginManifest


class TaskManifestResolver:
    """按任务快照版本解析插件声明（Query / Runner 共用）。"""

    def __init__(self, deps: TaskDeps) -> None:
        """初始化。

        Args:
            deps: 共享依赖容器。
        """
        self._deps = deps

    def find_manifest(self, record: TaskRecord) -> PluginManifest | None:
        """按任务快照版本取声明（取不到返回 None，**不抛**）。

        历史任务按创建时版本渲染；快照版本不在册时回落最新在册版本。

        Args:
            record: 任务行。

        Returns:
            PluginManifest | None: 插件声明；缺失（含回落）返回 None。
        """
        manifest = self._deps.registry.get(record.plugin_id, record.plugin_version)
        if manifest is None:
            latest = self._deps.registry.latest(record.plugin_id)
            manifest = self._deps.registry.get(record.plugin_id, latest) if latest else None
        return manifest

    def manifest_of(self, record: TaskRecord) -> PluginManifest:
        """按任务快照版本取声明（历史任务按创建时版本渲染）。

        详情/执行路径专用：声明缺失是**显性失败**（16002）——这些路径
        没有声明就无法正确渲染步骤、确认语义与核查清单，静默兜底会给出
        错误结果，因此这里必须抛而不是回落。

        Args:
            record: 任务行。

        Returns:
            PluginManifest: 插件声明。

        Raises:
            ApiError: 声明缺失（16002）。
        """
        manifest = self.find_manifest(record)
        if manifest is None:
            raise ApiError(
                ErrorCode.PLUGIN_NOT_FOUND, internal_detail=f"{record.plugin_id} manifest missing"
            )
        return manifest

    def spec_of(self, manifest: PluginManifest | None, seq: int) -> Any:
        """按序号取步骤声明（跨组件调用用公开名，避免 ruff `SLF001`）。

        Args:
            manifest: 插件声明。
            seq: 步骤序号。

        Returns:
            Any: 步骤声明；不存在返回 None。
        """
        return _step_spec(manifest, seq)


def _step_spec(manifest: PluginManifest | None, seq: int) -> Any:
    """按序号取步骤声明。

    Args:
        manifest: 插件声明。
        seq: 步骤序号。

    Returns:
        Any: 步骤声明；不存在返回 None。
    """
    if manifest is None:
        return None
    for spec in manifest.steps:
        if spec.seq == seq:
            return spec
    return None


__all__: list[str] = ["TaskManifestResolver", "_step_spec"]
