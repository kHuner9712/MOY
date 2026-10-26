"""插件任务编排服务（阶段三 B19，`architecture-phase3.md` §3.3 / §5.3 / D-22）。

**职责已按切片 2C-3b 三拆到 `app/business/task/`**（本文件是**公开入口 + 单例**）：

| 模块 | 职责 |
| --- | --- |
| `task/deps.py` | `TaskDeps`：11 个协作者（共享单实例） |
| `task/access.py` | 对象级判权纯函数 `assert_task_visible` |
| `task/manifest.py` | `TaskManifestResolver`（容错读取 / 显性 16002） |
| `task/query.py` | `TaskQuery`：A2/A3/A4 只读视图 + 核查清单 + 下载地址 |
| `task/runner.py` | `TaskRunner`：**唯一写路径**（建任务/推进/重试/取消/产物编辑） |
| `task/facade.py` | `TaskService`：装配 + 11 个属性代理 + 10 个转发 |

**`_service` 单例必须物理留在本文件**（不是"挪进包也无所谓"）：
`tests/integration/test_a2_a4_plugin_payload.py:120` 直接
`monkeypatch.setattr(task_service_module, "_service", service)`——单例搬进包 `__init__`
会让这条 monkeypatch 变成空操作，用例表面通过、实际换的是另一个实例。

`_step_spec` / `_artifact_type` / `_summary_text` 是**按旧路径引入的兼容转出**
（`tests/unit/test_b19_coverage_gaps.py:428/443/456` 直接 import），它们的真源分别在
`task/manifest.py`、`task/query.py`、`task/runner.py`。

**推进位置的选择（写清楚，别让下一个人以为是漏了后台任务）**：
V1.0 单副本、步骤全是平台内置调用（毫秒级），因此 `advance_record()` 在**请求内同步执行**，
不用 `asyncio.create_task`。原因有三：① `TestClient`/同步调用下后台任务不保证跑完，
"创建成功但永远 running"会变成一类无法复现的报错；② 断点续跑（`waiting_confirm`）
本来就要求"停住"，同步推进把这条语义变成**确定性**的；③ 多副本前要换 Valkey 队列
（登记 U-06），届时 `advance_record()` 仍是唯一入口，只换调度不改状态机。

**并发上限用状态表达，不用错误码**（PRD E-12）：同租户同插件占用额度（`running` +
`waiting_confirm`）达到 `settings.plugin_task_concurrency` 时，下一个落 `queued` 并返回
`queue_position`；额度释放时**自动**把最早排队的任务转 `running` 并继续推进。
"""

from __future__ import annotations

from collections.abc import Callable

from app.business.task.facade import TaskService

# 兼容转出（**不进 `__all__`**：公开面与三拆前一致，仍是 4 项）：这三个私有辅助
# 被既有测试按旧路径 import（`tests/unit/test_b19_coverage_gaps.py:428/443/456`），
# 故此处显式豁免未使用告警，真源分别在 task/manifest.py · task/query.py · task/runner.py。
from app.business.task.manifest import _step_spec  # noqa: F401
from app.business.task.query import _artifact_type  # noqa: F401
from app.business.task.runner import _summary_text  # noqa: F401
from app.core.config import Settings
from app.db.repositories.task import OCCUPYING_STATUSES
from app.plugins.actions import ActionGateway

_service: TaskService | None = None


def get_task_service(
    settings: Settings | None = None,
    *,
    gateway_factory: Callable[[Settings], ActionGateway] | None = None,
) -> TaskService:
    """获取任务服务单例。

    单例语义：`gateway_factory` 只在**首次构造**时生效（组合根每次请求都传同一个
    工厂函数，因此不会出现"先到先得"的配置漂移）；测试要换门面就
    `reset_task_service()` 后再取。

    Args:
        settings: 可选配置实例。
        gateway_factory: 动作门面工厂（组合根注入；审计 P1-6）。

    Returns:
        TaskService: 服务实例。
    """
    global _service
    if _service is None:
        _service = TaskService(settings, gateway_factory=gateway_factory)
    return _service


def reset_task_service() -> None:
    """重置单例（测试隔离）。"""
    global _service
    _service = None


__all__: list[str] = [
    "OCCUPYING_STATUSES",
    "TaskService",
    "get_task_service",
    "reset_task_service",
]
