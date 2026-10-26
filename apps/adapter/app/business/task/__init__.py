"""插件任务编排包（阶段三 B19，切片 2C-3b 三拆）。

**三拆的职责边界**（依赖方向单向，无环）：

```
deps.py        TaskDeps：11 个协作者（共享单实例，非 frozen；组件每次现取易变项）
   ↑
access.py      纯函数 assert_task_visible(record, auth)（Runner / Query 共用判据）
manifest.py    TaskManifestResolver：find_manifest（容错）/ manifest_of（显性 16002）
   ↑
query.py       TaskQuery：A2/A3/A4 只读视图 + 核查清单 + 下载地址 + 版本历史
runner.py      TaskRunner：唯一写路径（建任务/推进/重试/取消/产物编辑）
   ↑
facade.py      TaskService：装配 + 11 个属性代理 + 10 个转发（7 公共 + 3 旧名）
```

**谁依赖谁**：`facade → runner → {resolver, query} → deps`；`query → resolver`。
`query` 不依赖 `runner`（只读不写），`resolver` 不依赖任何组件（只依赖 `deps.registry`），
因此不存在 `runner ↔ query` 环。

公开入口仍是 `app/business/task_service`（`_service` 单例必须物理留在那里——
`tests/integration/test_a2_a4_plugin_payload.py:120` 会 monkeypatch 该模块全局）。
"""

from __future__ import annotations

from app.business.task.access import assert_task_visible
from app.business.task.deps import TaskDeps
from app.business.task.facade import TaskService
from app.business.task.manifest import TaskManifestResolver
from app.business.task.query import TaskQuery
from app.business.task.runner import TaskRunner

__all__: list[str] = [
    "TaskDeps",
    "TaskManifestResolver",
    "TaskQuery",
    "TaskRunner",
    "TaskService",
    "assert_task_visible",
]
