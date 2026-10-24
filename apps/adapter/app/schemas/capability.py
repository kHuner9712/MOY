"""能力推荐响应契约（W1 切片 B，`architecture-w1-metrics-entry.md` §3.2）。

`GET /api/v1/capabilities/recommend` 的 `data` 模型：目标式推荐 Top N、
命中理由回填（AC-B2）、所需输入提示（AC-B7/D-17）、无命中兜底引导（AC-B3）。

口径红线（PRD §2.2 / §2.4）：

1. **规则匹配，不引入模型**：可解释、可测试、可离线；语义检索留 V1.1，
   接口签名不变。
2. **每张入选卡必有命中理由**：`matched_terms` 与 `matched_fields` 不得为空
   （禁空理由，否则等于换了个搜索框）。
3. **无命中不得返回空列表**：`fallback=true` 时给目录兜底 + 引导文案，
   且文案不得出现占位词（暂不支持/开发中/即将上线/敬请期待）。
4. **空 `goal` 不报错**：返回目录前 `limit` 条（`matched=false, fallback=false`）。
5. 工具卡 `kind="tool"`、`id="tool:{tool_type}"`、`inputs=[]`
   （工具没有「所需输入」概念）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CapabilityInputBrief(BaseModel):
    """所需输入提示（D-17「所需输入提示」的契约形态）。

    Attributes:
        key: 输入键（插件声明 `inputs[].key`）。
        label: 用户可见名称（插件声明 `inputs[].label`）。
        type: 控件类型（`text` / `textarea` / `select` / `email` / `file` 等）。
        required: 是否必填。
    """

    key: str = Field(default="", description="输入键")
    label: str = Field(default="", description="输入项名称")
    type: str = Field(default="", description="控件类型")
    required: bool = Field(default=False, description="是否必填")


class CapabilityCard(BaseModel):
    """一张能力推荐卡（插件或工具；实时组装，不落库）。

    Attributes:
        id: 能力标识（插件为 `plugin_id`；工具为 `tool:{tool_type}`）。
        kind: 能力类型（`plugin` 可发起任务；`tool` 落地到插件市场）。
        name: 能力名称（插件声明 / 工具注册表原值）。
        summary: 一句话说明（插件摘要；工具为操作展示名拼接）。
        category: 业务分类（声明原值）。
        score: 命中得分（兜底与空目标场景为 0）。
        matched_terms: 命中词（去重；兜底/空目标为空列表）。
        matched_fields: 命中字段（契约字段名；兜底/空目标为空列表）。
        inputs: 所需输入提示（工具恒为空列表）。
    """

    id: str = Field(default="", description="能力标识")
    kind: Literal["plugin", "tool"] = Field(default="plugin", description="能力类型")
    name: str = Field(default="", description="能力名称")
    summary: str = Field(default="", description="一句话说明")
    category: str = Field(default="", description="业务分类")
    score: int = Field(default=0, description="命中得分")
    matched_terms: list[str] = Field(default_factory=list, description="命中词")
    matched_fields: list[str] = Field(default_factory=list, description="命中字段")
    inputs: list[CapabilityInputBrief] = Field(default_factory=list, description="所需输入提示")


class CapabilityRecommendData(BaseModel):
    """目标式能力推荐结果。

    Attributes:
        goal: 用户目标回显（请求原值）。
        matched: 是否有 ≥1 条能力得分 > 0。
        fallback: 是否走了「无命中兜底」（目录兜底 + 引导文案）。
        fallback_hint: 兜底引导文案（无兜底时为空串；禁占位词）。
        total_catalog: 目录总条数（6 插件 + 5 工具，实时组装）。
        items: 推荐卡列表（无命中兜底/空目标时为目录前 `limit` 条，不得为空）。
    """

    goal: str = Field(default="", description="用户目标回显")
    matched: bool = Field(default=False, description="是否有命中")
    fallback: bool = Field(default=False, description="是否无命中兜底")
    fallback_hint: str = Field(default="", description="兜底引导文案")
    total_catalog: int = Field(default=0, description="目录总条数")
    items: list[CapabilityCard] = Field(default_factory=list, description="推荐卡列表")


__all__: list[str] = [
    "CapabilityCard",
    "CapabilityInputBrief",
    "CapabilityRecommendData",
]
