"""工作流任务统计端点（W1 切片 A，`architecture-w1-metrics-entry.md` §1.4.4）。

| # | 端点 | 方法 | 用途 |
| --- | --- | --- | --- |
| W1-A1 | `/api/v1/stats/tasks` | GET | 工作流任务统计（M1/M3/M4/M5 + 辅助字段） |

**口径**（PRD §1.2/§1.3，分子分母可复算，AC-A2）：

- 统计域**只有 `plugin_task`（工作流任务域）**，不含对话/文档；与 `/model/usage`
  不是同一套数字，两域不得进同一百分比（AC-A5/R1）；
- 窗口：`until = now(UTC)`，`since = until − 7d/30d`，任务按
  `started_at ∈ [since, until)` 归属（与 A2 `created_from/to` 同口径）；
  期末未终态任务计入 `total` 与完成率分母（按未完成）；
- `cancelled` 只数**用户主动取消**（`cancel_requested=true`）；系统取消留在分母；
- `active_users` / `repeat_user_rate` 剔 `trigger_type='retry'`；
- M3 为「任务总时长」（`finished_at − started_at`，**含人工确认等待**）：
  零迁移条件下 `waiting_confirm` 停留无任何落点（设计 §9-Q1），
  对外不宣称「已扣除等待」。

**鉴权**：`Depends(get_request_context)`（登录 + 租户解析 + 跨租户成员校验）。
任务域端点实际只依赖登录上下文 + 租户隔离——代码中不存在 `task:read`
权限点（16 个业务权限点无此项，设计 §9-Q2），故**不扩权限矩阵**。

**双后端一致（AC-A7）**：比率与取整**只在本端点层做一次**
（`_build_stats_data`，全系统唯一取整点），仓储只回原始聚合值
（计数/合计/分位）——内存与 PG 结果逐字一致的机械保证。

**空数据（R4/AC-A3）**：空租户/空窗口由仓储返回全 0 `TaskStats`，
响应全 0，无 `NaN` / `null`。

**参数非法（AC-A1）**：`range` 非法值走既有 `RequestValidationError → 20001`
（HTTP 400）机制，不自定义错误码。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final, Literal

from fastapi import APIRouter, Depends, Query, Request

from app.core.config import get_request_settings
from app.core.logging import get_logger
from app.core.security import RequestContext, get_request_context
from app.db.repositories.task import TaskStats, get_task_store
from app.schemas.common import ApiResponse
from app.schemas.stats import TaskStatsData

logger = get_logger(__name__)

#: 统计窗口天数映射（`range` 字面量 → 天数；与 `Query(Literal[...])` 同步维护）
_WINDOW_DAYS: Final[dict[str, int]] = {"7d": 7, "30d": 30}

router = APIRouter(prefix="/stats", tags=["统计"])


def _build_stats_data(range_label: str, stats: TaskStats) -> TaskStatsData:
    """把原始聚合值变成响应数据（**全系统唯一取整点**）。

    比率与取整的全部规则（双后端逐字一致就靠这里只做一次）：

    - `completion_rate = completed/(total − cancelled)×100`，两位小数；分母 0 → 0.0；
    - `avg_duration_seconds = duration_sum/completed`，一位小数；completed=0 → 0.0；
    - `p50_duration_seconds` 直接取仓储的线性插值结果，一位小数；
    - `repeat_user_rate = repeat_users/active_users×100`，两位小数；分母 0 → 0.0；
    - `units_per_success = units_sum/completed`，两位小数；completed=0 → 0.0。

    Args:
        range_label: 统计期回显（`7d` / `30d`）。
        stats: 仓储返回的原始聚合值。

    Returns:
        TaskStatsData: 响应数据（空窗口全 0）。
    """
    rate_denominator = stats.total - stats.cancelled
    completion_rate = (
        round(stats.completed / rate_denominator * 100, 2) if rate_denominator > 0 else 0.0
    )
    avg_duration_seconds = (
        round(stats.duration_sum_seconds / stats.completed, 1) if stats.completed > 0 else 0.0
    )
    repeat_user_rate = (
        round(stats.repeat_users / stats.active_users * 100, 2) if stats.active_users > 0 else 0.0
    )
    units_per_success = round(stats.units_sum / stats.completed, 2) if stats.completed > 0 else 0.0
    return TaskStatsData(
        range=range_label,
        total=stats.total,
        completed=stats.completed,
        failed=stats.failed,
        cancelled=stats.cancelled,
        active_users=stats.active_users,
        completion_rate=completion_rate,
        avg_duration_seconds=avg_duration_seconds,
        p50_duration_seconds=round(stats.duration_p50_seconds, 1),
        repeat_user_rate=repeat_user_rate,
        units_per_success=units_per_success,
    )


@router.get(
    "/tasks",
    response_model=ApiResponse[TaskStatsData],
    summary="工作流任务统计",
    description=(
        "工作流任务域统计（近 7 天 / 近 30 天）：完成率、平均完成时长与中位数、"
        "重复使用率、单位成功任务用量。只读聚合，无分页；样本少时数值仅供参考。"
    ),
)
async def get_task_stats(
    request: Request,
    context: RequestContext = Depends(get_request_context),
    range: Literal["7d", "30d"] = Query("7d", description="统计窗口：7d 近 7 天 / 30d 近 30 天"),
) -> ApiResponse[TaskStatsData]:
    """W1-A1 工作流任务统计。

    Args:
        request: FastAPI 请求对象（取生效配置与存储后端）。
        context: 请求上下文（登录 + 租户解析 + 跨租户成员校验）。
        range: 统计窗口（`7d` / `30d`；非法值走既有 `RequestValidationError → 20001`）。

    Returns:
        ApiResponse[TaskStatsData]: 统计数据（空租户全 0，无 `NaN`/`null`）。

    Raises:
        ApiError: 未登录 / 令牌无效（401，既有机制）；限流（429 / 20006，既有机制）。
    """
    until = datetime.now(UTC)
    since = until - timedelta(days=_WINDOW_DAYS[range])
    store = get_task_store(get_request_settings(request))
    stats = await store.task_stats(context.tenant_id, since.isoformat(), until.isoformat())
    data = _build_stats_data(range, stats)
    logger.info(
        "task_stats_served",
        tenant_id=context.tenant_id,
        range=range,
        total=data.total,
    )
    return ApiResponse[TaskStatsData].ok(data)


__all__: list[str] = ["get_task_stats", "router"]
