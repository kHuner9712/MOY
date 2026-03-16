import type { MobileBriefCompactSummaryResult, MobileQuickCaptureRefineResult } from "@/types/ai";

export function buildFallbackMobileQuickCaptureRefine(params: {
  rawInput: string;
  hasCustomerContext: boolean;
}): MobileQuickCaptureRefineResult {
  const trimmed = params.rawInput.trim();
  const short = trimmed.length > 120 ? `${trimmed.slice(0, 120)}...` : trimmed;

  return {
    refined_summary: short || "已保存移动端纪要草稿，待补充客户与下一步。",
    likely_source_type: "manual_note",
    next_best_fields_to_fill: ["请选择 source type", "补充客户信息", "补充下一步计划", "补充下次跟进时间"],
    should_save_as_draft_only: !params.hasCustomerContext,
    followup_hint: params.hasCustomerContext ? "建议 24 小时内补充完整跟进并提交。" : "建议先匹配客户后再提交正式跟进。"
  };
}

export function buildFallbackMobileBriefCompactSummary(params: {
  focusTheme: string | null;
  topPriorities: string[];
  urgentRisks: string[];
}): MobileBriefCompactSummaryResult {
  const compactHeadline = params.focusTheme?.trim() || "今日执行重点：先处理高风险和到期任务";
  const topPriorities = params.topPriorities.length > 0 ? params.topPriorities.slice(0, 5) : ["优先完成今日 Must-Do 任务"];
  const urgentRisks = params.urgentRisks.length > 0 ? params.urgentRisks.slice(0, 4) : ["暂无新增高风险，可按计划推进"];

  return {
    compact_headline: compactHeadline,
    top_priorities: topPriorities,
    urgent_risks: urgentRisks,
    one_line_guidance: "先完成关键任务，再处理等待回复与会前准备。"
  };
}
