import type { DocumentAssetType } from "@/types/touchpoint";

export interface EmailDraftFallbackResult {
  subject: string;
  opening: string;
  body: string;
  cta: string;
  caution_notes: string[];
}

export interface MeetingAgendaFallbackResult {
  meeting_goal: string;
  agenda_points: string[];
  must_cover: string[];
  risk_notes: string[];
  expected_next_step: string[];
}

export interface MeetingFollowupFallbackResult {
  meeting_summary: string;
  decisions_made: string[];
  next_actions: string[];
  followup_message_draft_hint: string;
  checkpoint_update_hint: string[];
}

export interface DocumentSummaryFallbackResult {
  document_type_guess: DocumentAssetType;
  summary: string;
  risk_flags: string[];
  recommended_actions: string[];
  related_checkpoint_hint: string[];
}

export interface ExternalTouchpointReviewFallbackResult {
  external_progress_assessment: string;
  stalled_touchpoints: string[];
  missing_touchpoints: string[];
  recommended_next_moves: string[];
}

export function buildFallbackEmailDraft(params: {
  customerName: string;
  context: "followup" | "quote" | "meeting_confirm" | "meeting_followup" | "manager_support";
}): EmailDraftFallbackResult {
  const subjectPrefix =
    params.context === "quote"
      ? "报价说明"
      : params.context === "meeting_confirm"
        ? "会议确认"
        : params.context === "meeting_followup"
          ? "会后跟进"
          : params.context === "manager_support"
            ? "支持协同"
            : "跟进";
  return {
    subject: `${subjectPrefix} | ${params.customerName}`,
    opening: `您好，感谢您近期与我们沟通。`,
    body: `基于当前进展，我们整理了下一步建议与可执行安排，方便您快速评估并推进。`,
    cta: `若您方便，我们建议在本周确认下一步时间点与负责人，以便持续推进。`,
    caution_notes: ["该草稿为规则模板，请在发送前确认价格、条款与时间承诺。"]
  };
}

export function buildFallbackMeetingAgenda(params: {
  customerName: string;
  meetingType: "customer_meeting" | "demo" | "proposal_review" | "internal_strategy" | "manager_intervention";
}): MeetingAgendaFallbackResult {
  return {
    meeting_goal: `围绕 ${params.customerName} 当前推进阶段达成明确下一步`,
    agenda_points: ["回顾上次结论", "确认当前关键问题", "对齐推进路径与时间点"],
    must_cover: ["决策链条", "预算/资源边界", "下一步 owner 与截止时间"],
    risk_notes: ["避免仅讨论信息而无行动承诺", "避免会后无人承接关键动作"],
    expected_next_step: ["形成会后行动清单", "生成 followup 任务并设置 due time"]
  };
}

export function buildFallbackMeetingFollowupSummary(params: {
  meetingTitle: string;
  notesSummary: string;
}): MeetingFollowupFallbackResult {
  return {
    meeting_summary: params.notesSummary.trim().length > 0 ? params.notesSummary : `会议 ${params.meetingTitle} 已完成，需要形成后续动作闭环。`,
    decisions_made: ["已明确需持续推进当前商机"],
    next_actions: ["整理会后跟进邮件", "更新任务与客户下一次跟进时间"],
    followup_message_draft_hint: "感谢参会并复述会议结论，明确下一步与时间节点。",
    checkpoint_update_hint: ["若已明确报价/合同动作，可更新相应 checkpoint 状态。"]
  };
}

export function inferDocumentTypeFromName(fileName: string): DocumentAssetType {
  const value = fileName.toLowerCase();
  if (value.includes("quote") || value.includes("报价")) return "quote";
  if (value.includes("proposal") || value.includes("方案")) return "proposal";
  if (value.includes("contract") || value.includes("合同")) return "contract_draft";
  if (value.includes("meeting") || value.includes("纪要")) return "meeting_note";
  if (value.includes("case")) return "case_study";
  if (value.includes("product")) return "product_material";
  return "other";
}

export function buildFallbackDocumentSummary(params: {
  fileName: string;
  extractedText: string;
}): DocumentSummaryFallbackResult {
  const typeGuess = inferDocumentTypeFromName(params.fileName);
  const snippet = params.extractedText.trim().slice(0, 200);
  const summary = snippet.length > 0 ? snippet : `文档 ${params.fileName} 已上传，建议尽快补充摘要。`;
  const recommendedActions = ["将文档关联到当前 customer/deal", "补齐下一步动作 owner 与时间"];
  if (typeGuess === "quote") recommendedActions.push("建议创建 send_quote 任务并跟踪客户回复");
  if (typeGuess === "contract_draft") recommendedActions.push("建议创建 contract_review checkpoint 并通知 manager");
  return {
    document_type_guess: typeGuess,
    summary,
    risk_flags: ["文档结论需人工确认后再对外承诺"],
    recommended_actions: recommendedActions,
    related_checkpoint_hint: typeGuess === "quote" ? ["quote_sent"] : typeGuess === "contract_draft" ? ["contract_review"] : ["proposal_sent"]
  };
}

export function buildFallbackExternalTouchpointReview(params: {
  totalEvents: number;
  waitingReplyThreads: number;
  scheduledMeetings: number;
  highPriorityDealWithoutTouchpoint: number;
}): ExternalTouchpointReviewFallbackResult {
  return {
    external_progress_assessment: `近周期外部触点事件 ${params.totalEvents} 条，等待回复 ${params.waitingReplyThreads} 条，待执行会议 ${params.scheduledMeetings} 场。`,
    stalled_touchpoints: params.waitingReplyThreads > 0 ? [`存在 ${params.waitingReplyThreads} 条 waiting_reply 线程需跟进`] : [],
    missing_touchpoints:
      params.highPriorityDealWithoutTouchpoint > 0 ? [`有 ${params.highPriorityDealWithoutTouchpoint} 个高优先级 deal 缺少近期外部推进`] : [],
    recommended_next_moves: [
      "优先处理等待客户回复的关键邮件线程",
      "在会后 24 小时内补齐 outcome 与下一步任务",
      "对高优先级 deal 设置本周外部触点最小动作"
    ]
  };
}

export function evaluateWaitingReplyNeed(params: {
  threadStatus: "open" | "waiting_reply" | "replied" | "archived";
  latestMessageAt: string | null;
  thresholdHours: number;
}): boolean {
  if (params.threadStatus !== "waiting_reply") return false;
  if (!params.latestMessageAt) return true;
  const diffHours = (Date.now() - new Date(params.latestMessageAt).getTime()) / (60 * 60 * 1000);
  return diffHours >= params.thresholdHours;
}

export function evaluateNoRecentTouchpoint(params: {
  latestTouchpointAt: string | null;
  dealPriorityBand: "normal" | "important" | "strategic" | "critical";
  thresholdDays: number;
}): boolean {
  if (!params.latestTouchpointAt) return params.dealPriorityBand === "strategic" || params.dealPriorityBand === "critical";
  const diffDays = (Date.now() - new Date(params.latestTouchpointAt).getTime()) / (24 * 60 * 60 * 1000);
  const multiplier = params.dealPriorityBand === "critical" ? 0.6 : params.dealPriorityBand === "strategic" ? 0.8 : 1;
  return diffDays >= params.thresholdDays * multiplier;
}

