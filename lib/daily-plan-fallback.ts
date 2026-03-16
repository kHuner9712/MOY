import type { DailyWorkPlanGenerationResult, TaskActionSuggestionResult } from "@/types/ai";
import type { PlanTimeBlock, WorkItem } from "@/types/work";

function getBlock(index: number): PlanTimeBlock {
  const blocks: PlanTimeBlock[] = ["early_morning", "morning", "noon", "afternoon", "evening"];
  return blocks[index % blocks.length];
}

export function buildFallbackDailyPlan(params: {
  sortedItems: WorkItem[];
  userName: string;
  planDate: string;
}): DailyWorkPlanGenerationResult {
  const prioritized = params.sortedItems.slice(0, 12).map((item, index) => ({
    work_item_id: item.id,
    sequence_no: index + 1,
    recommendation_reason: item.rationale || "规则排序建议优先执行",
    planned_time_block: getBlock(index),
    suggested_action: item.description || `优先处理任务：${item.title}`
  }));

  const mustDo = prioritized
    .filter((item, idx) => idx < 3)
    .map((item) => item.work_item_id);

  return {
    focus_theme: `${params.userName} 今日重点：高风险与高价值客户优先推进`,
    must_do_item_ids: mustDo,
    prioritized_items: prioritized,
    recommended_time_blocks: [
      { block: "early_morning", guidance: "先处理逾期和关键风险任务" },
      { block: "morning", guidance: "推进报价、决策人确认等关键动作" },
      { block: "afternoon", guidance: "处理常规跟进和草稿确认任务" }
    ],
    plan_summary: `${params.planDate} 使用规则优先级生成基础计划，建议先完成前 3 个必做任务。`,
    caution_notes: ["本计划为规则回退结果，建议稍后重新触发 AI 编排"]
  };
}

export function buildFallbackTaskActionSuggestion(params: {
  title: string;
  rationale: string;
  dueAt: string | null;
}): TaskActionSuggestionResult {
  return {
    why_now: params.rationale || "该任务与当前客户推进节奏高度相关，建议立即处理",
    suggested_action: `先完成：${params.title}`,
    talk_track: ["先确认客户当前进展", "明确下一步可执行动作", "约定具体时间点并落库"],
    risk_if_delayed: params.dueAt ? "延迟处理可能导致节点超时，影响成交推进" : "延迟处理可能导致客户热度下降",
    success_signal: "客户确认下一步动作并接受明确时间安排",
    estimated_effort: "medium"
  };
}
