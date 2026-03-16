import type {
  ActionDraftGenerationResult,
  FollowupPrepCardResult,
  ManagerAttentionCardResult,
  MeetingPrepCardResult,
  MorningBriefResult,
  QuotePrepCardResult,
  TaskBriefCardResult
} from "@/types/ai";
import type { ContentDraftType, MorningBriefType, PrepCardType } from "@/types/preparation";

export function buildFallbackFollowupPrepCard(params: {
  customerName: string;
  stage: string;
  riskLevel: string;
  nextFollowupAt: string | null;
}): FollowupPrepCardResult {
  return {
    current_state_summary: `${params.customerName} currently stays in stage ${params.stage} with risk level ${params.riskLevel}.`,
    why_contact_now: params.nextFollowupAt ? "Next follow-up time is approaching." : "Customer progression needs a new confirmed action.",
    contact_goal: "Confirm decision path and secure the next concrete milestone.",
    recommended_angle: "Focus on customer value and timeline certainty.",
    key_points_to_mention: ["Recap last agreed actions", "Confirm current blocker", "Propose one concrete next step"],
    likely_objections: ["Budget sensitivity", "Internal approval delay"],
    suggested_talk_track: ["Ask for latest internal status", "Reframe expected business outcome", "Close with a specific date"],
    risk_notes: ["If delayed, momentum may drop and decision window may close."],
    success_signal: "Customer confirms next action owner and time commitment.",
    missing_information: ["Latest budget status", "Current decision participants"]
  };
}

export function buildFallbackQuotePrepCard(params: {
  customerName: string;
  opportunityTitle: string | null;
}): QuotePrepCardResult {
  return {
    quote_context_summary: `${params.customerName} is entering quote discussion${params.opportunityTitle ? ` for ${params.opportunityTitle}` : ""}.`,
    suggested_pricing_strategy: "Use phased pricing with outcome-based value framing.",
    value_points_to_emphasize: ["Risk reduction", "Faster implementation", "Measurable ROI"],
    objection_handling_notes: ["Prepare scope-options for budget concern", "Anchor by business impact rather than unit price"],
    required_information_before_quote: ["Final usage scope", "Decision timeline", "Approval process owner"],
    next_step_after_quote: ["Schedule quote walkthrough", "Confirm comparison criteria", "Set decision checkpoint"],
    quote_risks: ["Quote sent without decision path may stall", "Overly generic quote may trigger pure price comparison"]
  };
}

export function buildFallbackMeetingPrepCard(params: {
  customerName: string;
  meetingPurpose: string;
}): MeetingPrepCardResult {
  return {
    meeting_goal: `Drive clear progress for ${params.customerName} on ${params.meetingPurpose}.`,
    participant_focus_hypothesis: ["Business owner focuses on outcome", "Execution owner focuses on timeline and workload"],
    must_ask_questions: ["What decision criteria remain unresolved?", "Who signs off the final scope?"],
    must_cover_points: ["Current progress recap", "Unblocked path", "Next action assignment"],
    meeting_flow_suggestion: ["5-min context recap", "15-min issue alignment", "10-min decision and next-step close"],
    red_flags: ["No clear decision owner", "Timeline keeps shifting without reason"],
    post_meeting_actions: ["Send summary in 24h", "Confirm owner-action-deadline triplet"]
  };
}

export function buildFallbackTaskBriefCard(params: {
  taskTitle: string;
  rationale: string;
}): TaskBriefCardResult {
  return {
    task_summary: params.taskTitle,
    why_this_matters: params.rationale || "This task directly affects pipeline momentum.",
    best_next_action: "Complete one customer-facing action and confirm a dated next step.",
    preparation_checklist: ["Check latest followup context", "Confirm key risk and objective", "Prepare one clear ask"],
    talk_track: ["State purpose briefly", "Align on current blocker", "Close with owner + time"],
    done_definition: "Customer confirms next action with owner and timeline."
  };
}

export function buildFallbackManagerAttentionCard(params: {
  customerName: string;
  reason: string;
}): ManagerAttentionCardResult {
  return {
    why_manager_should_intervene: `${params.customerName} needs management support: ${params.reason}`,
    intervention_goal: "Unblock decision and reduce stall risk this week.",
    suggested_manager_action: ["Coach owner on next call objective", "Join one key alignment meeting if needed", "Set short review checkpoint"],
    expected_outcome: ["Clear action owner", "Faster decision feedback", "Reduced risk of silent stagnation"],
    caution_notes: ["Keep intervention supportive and execution-focused."]
  };
}

export function buildFallbackMorningBrief(params: {
  briefType: MorningBriefType;
  topTasks: string[];
  topRisks: string[];
  customersToPrepare: string[];
  pendingDraftCount: number;
}): MorningBriefResult {
  return {
    headline: params.briefType === "manager_morning" ? "Today management focus: unblock key execution risks." : "Today sales focus: execute high-impact actions first.",
    focus_theme: "Clear priorities, fast follow-through, and risk-first execution.",
    top_tasks: params.topTasks.slice(0, 5),
    customers_to_prepare: params.customersToPrepare.slice(0, 5),
    top_risks: params.topRisks.slice(0, 5),
    pending_drafts: params.pendingDraftCount > 0 ? [`${params.pendingDraftCount} pending drafts need confirmation`] : [],
    memory_reminders: ["Use concrete next-step closure", "Avoid vague followup notes"],
    action_note: "Finish the top 3 actions before noon and close each with owner/time commitment.",
    manager_actions: params.briefType === "manager_morning" ? ["Prioritize overloaded owners", "Review unattended critical customers"] : []
  };
}

export function buildFallbackActionDraft(params: {
  draftType: ContentDraftType;
  customerName: string | null;
  taskTitle: string | null;
}): ActionDraftGenerationResult {
  const subject = params.customerName ?? "customer";
  const purpose =
    params.draftType === "quote_explanation"
      ? "Explain quote logic and keep progression moving."
      : params.draftType === "manager_checkin_note"
        ? "Provide supportive manager guidance for execution."
        : "Advance the next customer action with clarity.";

  return {
    draft_title: params.taskTitle ?? `${params.draftType} draft`,
    draft_type: params.draftType,
    audience: subject,
    purpose,
    content_text: `Hi, regarding our recent discussion with ${subject}, I want to align on the next step and timeline. Please confirm the best checkpoint for follow-up.`,
    content_markdown: `Hi,\n\nRegarding our recent discussion with ${subject}, I would like to align on the next step and timing.\n\nPlease help confirm:\n1. The current key blocker\n2. The owner of the next action\n3. The checkpoint for our next follow-up`,
    rationale: "Template fallback generated from current task/customer context.",
    caution_notes: ["Please edit tone and details before external use.", "Do not include unverified pricing or contract commitments."]
  };
}

export function mapPrepCardTypeToScenario(cardType: PrepCardType):
  | "followup_prep_card"
  | "quote_prep_card"
  | "meeting_prep_card"
  | "task_brief_card"
  | "manager_attention_card" {
  if (cardType === "followup_prep") return "followup_prep_card";
  if (cardType === "quote_prep") return "quote_prep_card";
  if (cardType === "meeting_prep") return "meeting_prep_card";
  if (cardType === "task_brief") return "task_brief_card";
  return "manager_attention_card";
}
