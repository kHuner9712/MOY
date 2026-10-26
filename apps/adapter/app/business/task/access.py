"""插件任务编排 · 对象级判权（纯函数，切片 2C-3b）。

**为什么抽成纯函数而不是留在类里**：判权只依赖「任务行 + 鉴权上下文」两个入参，
不碰任何协作者。留在门面里会让"谁能看这个任务"这条规则和"如何推进任务"耦合在同一个
类上；抽成 `assert_task_visible(record, auth)` 后，Runner（`retry` / `cancel`）与
Query（`get_task` / `steps`）共用**同一份**判据，不会出现"列表能看、详情不能看"
这类口径分叉。
"""

from __future__ import annotations

from app.core.errors import ApiError, ErrorCode
from app.core.security import AuthContext
from app.db.repositories.task import TaskRecord


def assert_task_visible(record: TaskRecord, auth: AuthContext) -> None:
    """对象级判权：发起人本人，或持有 `audit:log:read` 的管理者。

    Args:
        record: 任务行。
        auth: 鉴权上下文。

    Raises:
        ApiError: 无权查看（20003）。
    """
    if record.initiator_id == auth.user_id:
        return
    if "audit:log:read" in auth.roles or "team:member:update" in auth.roles:
        return
    raise ApiError(
        ErrorCode.FORBIDDEN,
        message="你没有权限查看该任务",
        internal_detail=f"actor={auth.user_id} initiator={record.initiator_id}",
    )


__all__: list[str] = ["assert_task_visible"]
