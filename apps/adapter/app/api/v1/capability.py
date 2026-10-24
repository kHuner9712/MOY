"""目标式能力推荐端点（W1 切片 B，`architecture-w1-metrics-entry.md` §1.5.5）。

| # | 端点 | 方法 | 用途 |
| --- | --- | --- | --- |
| W1-B1 | `/api/v1/capabilities/recommend` | GET | 目标式能力推荐（Top N + 命中理由） |

**规则匹配，不引入模型**（PRD §2.3 边界 1）：目录实时组装
（`build_catalog()`，只读两个进程内注册表单例）+ 纯函数规则打分
（`recommend()`）。可解释、可测试、可离线；语义检索留 V1.1，
接口签名不变。

**参数边界**（AC-B5）：`goal` 上限 4000 字（与 A1 创建任务的 `goal`
上限一致）；`limit` 1..10（默认 3）；越界走既有
`RequestValidationError → 20001`（HTTP 400），不自定义错误码。

**空目标**（AC-B4）：`goal` 为空或纯空白 → 返回目录前 `limit` 条，
不报错。**无命中兜底**（AC-B3）：目录前 `limit` 条 + 引导文案，
不得空列表、禁占位词。

**鉴权**：`Depends(get_request_context)`（登录 + 租户解析）。目录是全局
只读组装，不按租户过滤；日志带 `tenant_id` 便于审计。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query

from app.core.logging import get_logger
from app.core.security import RequestContext, get_request_context
from app.schemas.capability import (
    CapabilityCard,
    CapabilityInputBrief,
    CapabilityRecommendData,
)
from app.schemas.common import ApiResponse
from app.services.capability_catalog import CapabilityEntry, build_catalog
from app.services.goal_router import ScoredCapability, recommend

logger = get_logger(__name__)

#: `goal` 长度上限（与 A1 创建任务的 `goal` 字段一致，AC-B5）
GOAL_MAX_LENGTH: int = 4000

#: `limit` 取值范围（PRD 只定默认 3，上限取 10，设计 §5 假设 2）
LIMIT_MIN: int = 1
LIMIT_MAX: int = 10

router = APIRouter(prefix="/capabilities", tags=["能力推荐"])


def _to_card(scored: ScoredCapability) -> CapabilityCard:
    """把打分结果映射成响应卡（契约字段名原样透出命中理由）。

    Args:
        scored: 打分结果（含目录条目与命中理由）。

    Returns:
        CapabilityCard: 推荐卡。
    """
    entry: CapabilityEntry = scored.entry
    kind: Literal["plugin", "tool"] = "plugin" if entry.kind == "plugin" else "tool"
    return CapabilityCard(
        id=entry.id,
        kind=kind,
        name=entry.name,
        summary=entry.summary,
        category=entry.category,
        score=scored.score,
        matched_terms=list(scored.matched_terms),
        matched_fields=list(scored.matched_fields),
        inputs=[
            CapabilityInputBrief(
                key=item.key,
                label=item.label,
                type=item.type,
                required=item.required,
            )
            for item in entry.inputs
        ],
    )


@router.get(
    "/recommend",
    response_model=ApiResponse[CapabilityRecommendData],
    summary="目标式能力推荐",
    description=(
        "输入一句想完成的事，按规则推荐最相关的能力（插件 / 工具）并给出命中理由"
        "与所需输入。空目标返回能力目录；没有命中时给目录兜底与引导。"
    ),
)
async def recommend_capabilities(
    context: RequestContext = Depends(get_request_context),
    goal: str = Query("", max_length=GOAL_MAX_LENGTH, description="用户目标（业务语言）"),
    limit: int = Query(3, ge=LIMIT_MIN, le=LIMIT_MAX, description="返回条数"),
) -> ApiResponse[CapabilityRecommendData]:
    """W1-B1 目标式能力推荐。

    纯内存计算（目录 + 打分），无 DB 往返；每次请求实时组装目录。

    Args:
        context: 请求上下文（登录 + 租户解析；目录全局只读，不按租户过滤）。
        goal: 用户目标（业务语言；空值返回目录，不报错）。
        limit: 返回条数（1..10，默认 3；越界 20001）。

    Returns:
        ApiResponse[CapabilityRecommendData]: 推荐结果。

    Raises:
        ApiError: 未登录 / 令牌无效（401）、`goal` 超长或 `limit` 越界（20001）、
            限流（429 / 20006）——均为既有机制。
    """
    catalog = build_catalog()
    result = recommend(goal, limit=limit, catalog=catalog)
    data = CapabilityRecommendData(
        goal=goal,
        matched=result.matched,
        fallback=result.fallback,
        fallback_hint=result.fallback_hint,
        total_catalog=len(catalog),
        items=[_to_card(item) for item in result.items],
    )
    logger.info(
        "capability_recommend_served",
        tenant_id=context.tenant_id,
        matched=data.matched,
        returned=len(data.items),
    )
    return ApiResponse[CapabilityRecommendData].ok(data)


__all__: list[str] = [
    "GOAL_MAX_LENGTH",
    "LIMIT_MAX",
    "LIMIT_MIN",
    "recommend_capabilities",
    "router",
]
