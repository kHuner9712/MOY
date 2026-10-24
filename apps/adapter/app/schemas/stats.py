"""任务统计响应契约（W1 切片 A，`architecture-w1-metrics-entry.md` §3.1）。

`GET /api/v1/stats/tasks` 的 `data` 模型：M1 完成率 / M3 时长（均值 + P50）/
M4 重复使用率 / M5 单位成功用量，外加小样本判定与样本量标注所需的辅助字段。

口径红线（PRD §1.2 / §1.3，验收一票否决）：

1. **统计域只有 `plugin_task`（工作流任务域）**，不含对话/文档；
   与 `/model/usage` 不是同一套数字，两域不得进同一百分比（AC-A5/R1）。
2. **数值字段全部带 0 默认值**：空租户/空窗口返回全 0，
   不得出现 `NaN` / `null`（R4/AC-A3）。
3. **百分比为 0~100 浮点**：完成率与重复使用率两位小数、时长一位小数、
   单位成功用量两位小数；取整只在端点层做一次（双后端逐字一致的机械保证）。
4. 字段名 snake_case；`range` 回显请求的统计窗口（`7d` / `30d`）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class TaskStatsData(BaseModel):
    """工作流任务统计数据（M1/M3/M4/M5 + 辅助字段 + 统计期回显）。

    Attributes:
        range: 统计期回显（与请求一致，`7d` / `30d`）。
        total: 窗口内发起任务数（含期末未终态）。
        completed: `status='completed'` 的任务数。
        failed: `status='failed'` 的任务数。
        cancelled: 用户主动取消数（`status='cancelled'` 且 `cancel_requested=true`；
            系统取消留在完成率分母）。
        active_users: 发起 ≥1 次的去重用户数（剔 `trigger_type='retry'`）。
        completion_rate: M1 完成率，`completed/(total−cancelled)×100`，两位小数；
            分母 0 时为 0.0。
        avg_duration_seconds: M3 已完成任务平均总时长（秒，一位小数）；
            **口径注记：含人工确认等待**（零迁移条件下该停留无落点，见设计 §9-Q1）。
        p50_duration_seconds: M3 同口径 P50（线性插值，一位小数）。
        repeat_user_rate: M4 重复使用率，同插件发起 ≥2 次用户/发起 ≥1 次用户×100，
            两位小数；分母 0 时为 0.0。
        units_per_success: M5 单位成功用量，已完成任务用量合计/已完成数，
            两位小数；分母 0 时为 0.0。
    """

    range: str = Field(default="7d", description="统计期回显（7d / 30d）")
    total: int = Field(default=0, description="窗口内发起任务数（含期末未终态）")
    completed: int = Field(default=0, description="已完成任务数")
    failed: int = Field(default=0, description="失败任务数")
    cancelled: int = Field(default=0, description="用户主动取消任务数")
    active_users: int = Field(default=0, description="发起过的去重用户数（剔重试）")
    completion_rate: float = Field(default=0.0, description="工作流任务完成率（百分比，两位小数）")
    avg_duration_seconds: float = Field(
        default=0.0, description="已完成任务平均总时长（秒，一位小数）"
    )
    p50_duration_seconds: float = Field(
        default=0.0, description="已完成任务时长中位数（秒，一位小数）"
    )
    repeat_user_rate: float = Field(
        default=0.0, description="工作流任务重复使用率（百分比，两位小数）"
    )
    units_per_success: float = Field(default=0.0, description="单位成功任务用量（两位小数）")


__all__: list[str] = ["TaskStatsData"]
