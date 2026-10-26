"""插件任务编排 · 依赖容器（`TaskDeps`，切片 2C-3b 三拆的装配面）。

**为什么要把 11 个协作者收成一个 dataclass**：三拆前 `TaskService.__init__` 同时负责
"选后端 + 造引擎 + 存 11 个引用"，拆成 Facade / Runner / Query / Resolver 之后，
若每个组件各自持有一份构造参数，就会出现"门面改了 `_usage`、Runner 还拿着旧的"这类
**两处配置漂移**。收成**一个共享实例**（不是拷贝）后，"谁能改"由门面决定，
"谁在用"由 `deps` 的字段决定。

**故意不 `frozen`，也故意不缓存**：`tests/unit/test_task_step_lock.py:96` 会写
`service._step_timeout_seconds = 0.2`、`tests/integration/test_quota_notice.py:367` 会写
`service._usage = <真 UsageService>`，都是**构造之后**注入。门面的这两个属性是
读写代理（写回本 dataclass），因此 Runner/Query **必须每次调用现取**
`deps.step_timeout_seconds` / `deps.usage`，不得在 `__init__` 里拷一份——拷了就看不见
写入，那 5 个用例会直接红。

依赖方向（无环）：`deps ← {access, manifest, query, runner} ← facade ← __init__`。
本文件只依赖 `core` / `db` / `plugins`，不反向依赖业务层的任何组件。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.business.audit_service import AuditService
from app.business.plugin_grant_service import PluginGrantService
from app.business.plugin_service import PluginService
from app.business.usage_service import UsageService
from app.core.config import Settings
from app.db.repositories.artifact_repo import ArtifactStore
from app.db.repositories.task import TaskStore
from app.plugins.engine import PluginEngine
from app.plugins.loader import PluginRegistry
from app.plugins.sanitizer import ArtifactSanitizer


@dataclass(slots=True)
class TaskDeps:
    """任务编排的 11 个协作者（**门面与组件共享同一个实例**）。

    Attributes:
        settings: 配置实例。
        tasks: 任务仓储。
        artifacts: 产物仓储。
        registry: 声明注册表。
        grants: 授权服务。
        plugins: 插件服务（安装态 / 套餐 / 声明）。
        engine: 步骤引擎（**全进程只建一次**，由门面注入）。
        audit: 审计服务。
        usage: 计量服务（门面 `_usage` 写入即改本字段，组件每次现取）。
        sanitizer: 产物脱敏器（WF2 编辑后重跑）。
        step_timeout_seconds: 单步超时（测试用可写；组件每次现取）。
    """

    settings: Settings
    tasks: TaskStore
    artifacts: ArtifactStore
    registry: PluginRegistry
    grants: PluginGrantService
    plugins: PluginService
    engine: PluginEngine
    audit: AuditService
    usage: UsageService
    sanitizer: ArtifactSanitizer
    step_timeout_seconds: float


__all__: list[str] = ["TaskDeps"]
