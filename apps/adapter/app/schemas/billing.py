"""计费域接口契约（阶段二 BL1-BL10；本批只落 BL5）。

字段口径（架构文档 §4.5）：额度一律以「字数额度」为单位，**不出现 token**；
金额单位为**分**（`amount_cents`），展示层除以 100；百分比两位小数。

B07 只定义 BL5，B09 在本文件追加 BL1-BL4、BL6-BL10 模型（加性扩展）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

#: BL5 分组维度
UsageGroupBy = Literal["member", "department", "capability"]

#: 周期格式（`YYYY-MM`）
PERIOD_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"


class BillingUsageQuery(BaseModel):
    """BL5 用量查询参数。

    Attributes:
        period: 账期 `YYYY-MM`；缺省取当前 UTC 月。
        group_by: 分组维度；**管理视图**（需管理员）。
    """

    period: str = Field(
        default="",
        max_length=7,
        pattern=PERIOD_PATTERN,
        description="账期，形如 2026-11；缺省为当前月",
    )
    group_by: UsageGroupBy | None = Field(
        default=None, description="分组维度：member / department / capability（管理视图）"
    )


class UsageBreakdownItem(BaseModel):
    """用量分项。

    Attributes:
        key: 分项键（成员 ID / 部门 ID / 能力名）。
        display_name: 用户可见名称（未登记账号可为空）。
        units: 用量（字数额度）。
        percent: 占总额度百分比（两位小数）。
    """

    key: str = Field(default="", description="分项键")
    display_name: str = Field(default="", description="用户可见名称")
    units: int = Field(default=0, ge=0, description="用量（字数额度）")
    percent: float = Field(default=0.0, ge=0.0, description="占总额度百分比")


class UsageDailyTrendItem(BaseModel):
    """按自然日的用量点。

    Attributes:
        date: 日期 `YYYY-MM-DD`。
        units: 当日用量。
    """

    date: str = Field(default="", description="日期 YYYY-MM-DD")
    units: int = Field(default=0, ge=0, description="当日用量")


class BillingUsageData(BaseModel):
    """BL5 用量响应数据（个人视图与企业管理视图共用同一结构）。

    Attributes:
        period: 账期 `YYYY-MM`。
        quota_units_total: 额度总量。
        quota_units_used: 已用额度（与 M4 `used_units` **同刻恒等**）。
        quota_units_remaining: 剩余额度。
        usage_percent: 使用百分比。
        over_quota_warning: 是否已达/超额（软提示）。
        quota_unit: 额度单位文案。
        breakdown: 分项明细。
        daily_trend: 按日趋势。
    """

    period: str = Field(default="", description="账期，形如 2026-11")
    quota_units_total: int = Field(default=0, ge=0, description="额度总量")
    quota_units_used: int = Field(default=0, ge=0, description="已用额度")
    quota_units_remaining: int = Field(default=0, ge=0, description="剩余额度")
    usage_percent: float = Field(default=0.0, ge=0.0, description="使用百分比")
    over_quota_warning: bool = Field(default=False, description="是否已达额度上限")
    quota_unit: str = Field(default="", description="额度单位文案")
    breakdown: list[UsageBreakdownItem] = Field(default_factory=list, description="分项明细")
    daily_trend: list[UsageDailyTrendItem] = Field(default_factory=list, description="按日趋势")


__all__: list[str] = [
    "PERIOD_PATTERN",
    "BillingUsageData",
    "BillingUsageQuery",
    "UsageBreakdownItem",
    "UsageDailyTrendItem",
    "UsageGroupBy",
]
