import { z } from "zod";

export type AiRunStatus = "queued" | "running" | "completed" | "failed";
export type AiTriggerSource = "manual" | "followup_submit" | "nightly_scan" | "alert_regen" | "manager_review" | "import_complete";
export type AiScenario =
  | "followup_analysis"
  | "customer_health"
  | "leak_risk_inference"
  | "manager_summary"
  | "leak_risk"
  | "communication_extraction"
  | "sales_daily_report"
  | "sales_weekly_report"
  | "manager_daily_report"
  | "manager_weekly_report"
  | "sales_memory_compile"
  | "manager_quality_insight"
  | "user_coaching_report"
  | "daily_work_plan_generation"
  | "task_action_suggestion"
  | "manager_team_rhythm_insight"
  | "weekly_task_review"
  | "followup_prep_card"
  | "quote_prep_card"
  | "meeting_prep_card"
  | "task_brief_card"
  | "manager_attention_card"
  | "sales_morning_brief"
  | "manager_morning_brief"
  | "action_draft_generation"
  | "action_outcome_capture_assist"
  | "playbook_compile"
  | "outcome_effectiveness_review"
  | "personal_effectiveness_update"
  | "deal_room_command_summary"
  | "thread_summary"
  | "decision_support"
  | "intervention_recommendation"
  | "deal_playbook_mapping"
  | "email_draft_generation"
  | "meeting_agenda_generation"
  | "meeting_followup_summary"
  | "document_asset_summary"
  | "external_touchpoint_review"
  | "onboarding_recommendation"
  | "usage_health_summary"
  | "import_column_mapping_assist"
  | "import_review_summary"
  | "mobile_quick_capture_refine"
  | "mobile_brief_compact_summary"
  | "template_fit_recommendation"
  | "template_application_summary"
  | "industry_seed_customization"
  | "lead_qualification_assist"
  | "trial_conversion_review"
  | "growth_pipeline_summary"
  | "executive_brief_summary"
  | "customer_health_summary"
  | "automation_action_recommendation"
  | "retention_watch_review"
  | "import_business_summary"
  | "value_metrics_summary";
export type AiProviderId = "deepseek" | "openai" | "qwen" | "zhipu";
export type AiPromptProviderScope = "deepseek" | "universal";
export type AiResultSource = "provider" | "fallback";
export type LeakRiskLevel = "low" | "medium" | "high";
export type AnalysisRiskLevel = "low" | "medium" | "high";
export type CommunicationType = "phone" | "wechat" | "email" | "meeting" | "other";

export interface AiTokenUsage {
  promptTokens: number | null;
  completionTokens: number | null;
  totalTokens: number | null;
}

export interface AiToolDefinition {
  type: "function";
  function: {
    name: string;
    description?: string;
    // JSON schema-like object for function args
    parameters: Record<string, unknown>;
    strict?: boolean;
  };
}

export interface AiProviderRequest {
  scenario: AiScenario;
  model: string;
  systemPrompt: string;
  developerPrompt: string;
  userPrompt: string;
  jsonMode?: boolean;
  strictMode?: boolean;
  useReasonerModel?: boolean;
  tools?: AiToolDefinition[];
  temperature?: number;
  maxTokens?: number;
  timeoutMs?: number;
}

export interface AiProviderResponse {
  provider: AiProviderId;
  model: string;
  latencyMs: number;
  finishReason: string | null;
  usage: AiTokenUsage | null;
  rawResponse: Record<string, unknown>;
  rawText: string | null;
  parsedJson: Record<string, unknown> | null;
  strictFallbackUsed?: boolean;
  error: string | null;
}

export const followupAnalysisResultSchema = z.object({
  customer_status_summary: z.string().min(1),
  key_needs: z.array(z.string()).default([]),
  key_objections: z.array(z.string()).default([]),
  buying_signals: z.array(z.string()).default([]),
  risk_level: z.enum(["low", "medium", "high"]),
  leak_risk: z.enum(["low", "medium", "high"]),
  leak_reasons: z.array(z.string()).default([]),
  next_best_actions: z.array(z.string()).default([]),
  recommended_next_followup_at: z.string().nullable(),
  manager_attention_needed: z.boolean(),
  confidence_score: z.number().min(0).max(1),
  reasoning_brief: z.string().min(1)
});

export type FollowupAnalysisResult = z.infer<typeof followupAnalysisResultSchema>;

export const customerHealthResultSchema = z.object({
  stage_fit_assessment: z.string().min(1),
  momentum_score: z.number().min(0).max(100),
  relationship_score: z.number().min(0).max(100),
  decision_clarity_score: z.number().min(0).max(100),
  budget_clarity_score: z.number().min(0).max(100),
  timeline_clarity_score: z.number().min(0).max(100),
  overall_risk_level: z.enum(["low", "medium", "high"]),
  stall_signals: z.array(z.string()).default([]),
  suggested_strategy: z.array(z.string()).default([]),
  summary: z.string().min(1)
});

export type CustomerHealthResult = z.infer<typeof customerHealthResultSchema>;

export const leakAlertInferenceResultSchema = z.object({
  should_create_alert: z.boolean(),
  severity: z.enum(["info", "warning", "critical"]),
  primary_rule_type: z.enum([
    "no_followup_timeout",
    "quoted_but_stalled",
    "positive_reply_but_no_progress",
    "no_decision_maker",
    "high_probability_stalled",
    "ai_detected"
  ]),
  title: z.string().min(1),
  description: z.string().min(1),
  evidence: z.array(z.string()).default([]),
  suggested_owner_action: z.array(z.string()).default([]),
  due_at: z.string().nullable()
});

export type LeakAlertInferenceResult = z.infer<typeof leakAlertInferenceResultSchema>;

export const communicationExtractionResultSchema = z.object({
  matched_customer_name: z.string().nullable(),
  confidence_of_match: z.number().min(0).max(1),
  communication_type: z.enum(["phone", "wechat", "email", "meeting", "other"]),
  summary: z.string().min(1),
  key_needs: z.array(z.string()).default([]),
  key_objections: z.array(z.string()).default([]),
  buying_signals: z.array(z.string()).default([]),
  mentioned_budget: z.string().nullable(),
  mentioned_timeline: z.string().nullable(),
  decision_makers: z.array(z.string()).default([]),
  next_step: z.string().nullable(),
  recommended_next_followup_at: z.string().nullable(),
  should_create_followup: z.boolean(),
  should_update_opportunity: z.boolean(),
  should_trigger_alert_review: z.boolean(),
  structured_tags: z.array(z.string()).default([]),
  uncertainty_notes: z.array(z.string()).default([])
});

export type CommunicationExtractionResult = z.infer<typeof communicationExtractionResultSchema>;

export const reportGenerationResultSchema = z.object({
  title: z.string().min(1),
  summary: z.string().min(1),
  key_metrics: z.array(
    z.object({
      label: z.string().min(1),
      value: z.string().min(1),
      note: z.string().optional()
    })
  ),
  risk_list: z.array(z.string()).default([]),
  recommended_actions: z.array(z.string()).default([]),
  content_markdown: z.string().min(1)
});

export type ReportGenerationResult = z.infer<typeof reportGenerationResultSchema>;

export const salesMemoryCompileResultSchema = z.object({
  summary: z.string().min(1),
  preferred_customer_types: z.array(z.string()).default([]),
  preferred_communication_styles: z.array(z.string()).default([]),
  common_objections: z.array(z.string()).default([]),
  effective_tactics: z.array(z.string()).default([]),
  common_followup_rhythm: z.array(z.string()).default([]),
  quoting_style_notes: z.array(z.string()).default([]),
  risk_blind_spots: z.array(z.string()).default([]),
  manager_coaching_focus: z.array(z.string()).default([]),
  memory_items: z.array(
    z.object({
      memory_type: z.enum([
        "customer_preference",
        "communication_pattern",
        "objection_pattern",
        "tactic_pattern",
        "followup_rhythm",
        "risk_pattern",
        "coaching_hint"
      ]),
      title: z.string().min(1),
      description: z.string().min(1),
      evidence: z.array(z.string()).default([]),
      confidence_score: z.number().min(0).max(1),
      source_count: z.number().int().min(0).default(0)
    })
  ),
  confidence_score: z.number().min(0).max(1)
});

export type SalesMemoryCompileResult = z.infer<typeof salesMemoryCompileResultSchema>;

export const managerQualityInsightResultSchema = z.object({
  executive_summary: z.string().min(1),
  replicable_patterns: z.array(z.string()).default([]),
  needs_coaching: z.array(
    z.object({
      user_id: z.string().min(1),
      user_name: z.string().min(1),
      reason: z.string().min(1),
      priority: z.enum(["low", "medium", "high"])
    })
  ),
  management_actions: z.array(z.string()).default([]),
  risk_warnings: z.array(z.string()).default([])
});

export type ManagerQualityInsightResult = z.infer<typeof managerQualityInsightResultSchema>;

export const userCoachingReportResultSchema = z.object({
  title: z.string().min(1),
  executive_summary: z.string().min(1),
  strengths: z.array(z.string()).default([]),
  weaknesses: z.array(z.string()).default([]),
  coaching_actions: z.array(z.string()).default([]),
  replicable_patterns: z.array(z.string()).default([]),
  risk_warnings: z.array(z.string()).default([]),
  content_markdown: z.string().min(1)
});

export type UserCoachingReportResult = z.infer<typeof userCoachingReportResultSchema>;

export const dailyWorkPlanGenerationResultSchema = z.object({
  focus_theme: z.string().min(1),
  must_do_item_ids: z.array(z.string()).default([]),
  prioritized_items: z.array(
    z.object({
      work_item_id: z.string().min(1),
      sequence_no: z.number().int().min(1),
      recommendation_reason: z.string().min(1),
      planned_time_block: z.enum(["early_morning", "morning", "noon", "afternoon", "evening"]),
      suggested_action: z.string().min(1)
    })
  ),
  recommended_time_blocks: z.array(
    z.object({
      block: z.enum(["early_morning", "morning", "noon", "afternoon", "evening"]),
      guidance: z.string().min(1)
    })
  ),
  plan_summary: z.string().min(1),
  caution_notes: z.array(z.string()).default([])
});

export type DailyWorkPlanGenerationResult = z.infer<typeof dailyWorkPlanGenerationResultSchema>;

export const taskActionSuggestionResultSchema = z.object({
  why_now: z.string().min(1),
  suggested_action: z.string().min(1),
  talk_track: z.array(z.string()).default([]),
  risk_if_delayed: z.string().min(1),
  success_signal: z.string().min(1),
  estimated_effort: z.enum(["low", "medium", "high"])
});

export type TaskActionSuggestionResult = z.infer<typeof taskActionSuggestionResultSchema>;

export const managerTeamRhythmInsightResultSchema = z.object({
  team_execution_summary: z.string().min(1),
  overdue_patterns: z.array(z.string()).default([]),
  under_attended_critical_customers: z.array(z.string()).default([]),
  who_needs_support: z.array(
    z.object({
      user_id: z.string().min(1),
      user_name: z.string().min(1),
      reason: z.string().min(1),
      priority: z.enum(["low", "medium", "high"])
    })
  ),
  which_actions_should_be_prioritized: z.array(z.string()).default([]),
  managerial_actions: z.array(z.string()).default([])
});

export type ManagerTeamRhythmInsightResult = z.infer<typeof managerTeamRhythmInsightResultSchema>;

export const weeklyTaskReviewResultSchema = z.object({
  completion_summary: z.string().min(1),
  carry_over_reasons: z.array(z.string()).default([]),
  execution_strengths: z.array(z.string()).default([]),
  execution_gaps: z.array(z.string()).default([]),
  next_week_focus: z.array(z.string()).default([])
});

export type WeeklyTaskReviewResult = z.infer<typeof weeklyTaskReviewResultSchema>;

export const followupPrepCardResultSchema = z.object({
  current_state_summary: z.string().min(1),
  why_contact_now: z.string().min(1),
  contact_goal: z.string().min(1),
  recommended_angle: z.string().min(1),
  key_points_to_mention: z.array(z.string()).default([]),
  likely_objections: z.array(z.string()).default([]),
  suggested_talk_track: z.array(z.string()).default([]),
  risk_notes: z.array(z.string()).default([]),
  success_signal: z.string().min(1),
  missing_information: z.array(z.string()).default([])
});

export type FollowupPrepCardResult = z.infer<typeof followupPrepCardResultSchema>;

export const quotePrepCardResultSchema = z.object({
  quote_context_summary: z.string().min(1),
  suggested_pricing_strategy: z.string().min(1),
  value_points_to_emphasize: z.array(z.string()).default([]),
  objection_handling_notes: z.array(z.string()).default([]),
  required_information_before_quote: z.array(z.string()).default([]),
  next_step_after_quote: z.array(z.string()).default([]),
  quote_risks: z.array(z.string()).default([])
});

export type QuotePrepCardResult = z.infer<typeof quotePrepCardResultSchema>;

export const meetingPrepCardResultSchema = z.object({
  meeting_goal: z.string().min(1),
  participant_focus_hypothesis: z.array(z.string()).default([]),
  must_ask_questions: z.array(z.string()).default([]),
  must_cover_points: z.array(z.string()).default([]),
  meeting_flow_suggestion: z.array(z.string()).default([]),
  red_flags: z.array(z.string()).default([]),
  post_meeting_actions: z.array(z.string()).default([])
});

export type MeetingPrepCardResult = z.infer<typeof meetingPrepCardResultSchema>;

export const taskBriefCardResultSchema = z.object({
  task_summary: z.string().min(1),
  why_this_matters: z.string().min(1),
  best_next_action: z.string().min(1),
  preparation_checklist: z.array(z.string()).default([]),
  talk_track: z.array(z.string()).default([]),
  done_definition: z.string().min(1)
});

export type TaskBriefCardResult = z.infer<typeof taskBriefCardResultSchema>;

export const managerAttentionCardResultSchema = z.object({
  why_manager_should_intervene: z.string().min(1),
  intervention_goal: z.string().min(1),
  suggested_manager_action: z.array(z.string()).default([]),
  expected_outcome: z.array(z.string()).default([]),
  caution_notes: z.array(z.string()).default([])
});

export type ManagerAttentionCardResult = z.infer<typeof managerAttentionCardResultSchema>;

export const morningBriefResultSchema = z.object({
  headline: z.string().min(1),
  focus_theme: z.string().min(1),
  top_tasks: z.array(z.string()).default([]),
  customers_to_prepare: z.array(z.string()).default([]),
  top_risks: z.array(z.string()).default([]),
  pending_drafts: z.array(z.string()).default([]),
  memory_reminders: z.array(z.string()).default([]),
  action_note: z.string().min(1),
  manager_actions: z.array(z.string()).default([])
});

export type MorningBriefResult = z.infer<typeof morningBriefResultSchema>;

export const actionDraftGenerationResultSchema = z.object({
  draft_title: z.string().min(1),
  draft_type: z.enum([
    "followup_message",
    "quote_explanation",
    "meeting_opening",
    "meeting_summary",
    "manager_checkin_note",
    "internal_update"
  ]),
  audience: z.string().min(1),
  purpose: z.string().min(1),
  content_text: z.string().min(1),
  content_markdown: z.string().min(1),
  rationale: z.string().min(1),
  caution_notes: z.array(z.string()).default([])
});

export type ActionDraftGenerationResult = z.infer<typeof actionDraftGenerationResultSchema>;

export const actionOutcomeCaptureAssistResultSchema = z.object({
  outcome_type: z.enum(["followup_result", "quote_result", "meeting_result", "task_result", "manager_intervention_result"]),
  result_status: z.enum(["positive_progress", "neutral", "stalled", "risk_increased", "closed_won", "closed_lost"]),
  stage_changed: z.boolean(),
  old_stage: z.string().nullable(),
  new_stage: z.string().nullable(),
  customer_sentiment_shift: z.enum(["improved", "unchanged", "worsened", "unknown"]),
  key_outcome_summary: z.string().min(1),
  new_objections: z.array(z.string()).default([]),
  new_risks: z.array(z.string()).default([]),
  next_step_defined: z.boolean(),
  next_step_text: z.string().nullable(),
  followup_due_at: z.string().nullable(),
  used_prep_card: z.boolean(),
  used_draft: z.boolean(),
  usefulness_rating: z.enum(["helpful", "somewhat_helpful", "not_helpful", "unknown"]),
  notes: z.string().nullable()
});

export type ActionOutcomeCaptureAssistResult = z.infer<typeof actionOutcomeCaptureAssistResultSchema>;

export const playbookCompileResultSchema = z.object({
  playbook_type: z.enum(["objection_handling", "customer_segment", "quote_strategy", "meeting_strategy", "followup_rhythm", "risk_recovery"]),
  title: z.string().min(1),
  summary: z.string().min(1),
  confidence_score: z.number().min(0).max(1),
  applicability_notes: z.string().min(1),
  entries: z.array(
    z.object({
      entry_title: z.string().min(1),
      entry_summary: z.string().min(1),
      conditions: z.record(z.unknown()).default({}),
      recommended_actions: z.array(z.string()).default([]),
      caution_notes: z.array(z.string()).default([]),
      evidence_snapshot: z.record(z.unknown()).default({}),
      success_signal: z.record(z.unknown()).default({}),
      failure_modes: z.array(z.string()).default([]),
      confidence_score: z.number().min(0).max(1),
      sort_order: z.number().int().min(1).default(100)
    })
  )
});

export type PlaybookCompileResult = z.infer<typeof playbookCompileResultSchema>;

export const outcomeEffectivenessReviewResultSchema = z.object({
  title: z.string().min(1),
  executive_summary: z.string().min(1),
  effective_patterns: z.array(z.string()).default([]),
  ineffective_patterns: z.array(z.string()).default([]),
  repeated_failures: z.array(z.string()).default([]),
  coaching_actions: z.array(z.string()).default([]),
  playbook_candidates: z.array(z.string()).default([])
});

export type OutcomeEffectivenessReviewResult = z.infer<typeof outcomeEffectivenessReviewResultSchema>;

export const personalEffectivenessUpdateResultSchema = z.object({
  summary: z.string().min(1),
  helpful_suggestion_patterns: z.array(z.string()).default([]),
  ineffective_suggestion_patterns: z.array(z.string()).default([]),
  rhythm_adjustments: z.array(z.string()).default([]),
  coaching_focus_updates: z.array(z.string()).default([]),
  confidence_score: z.number().min(0).max(1),
  uncertainty_notes: z.array(z.string()).default([])
});

export type PersonalEffectivenessUpdateResult = z.infer<typeof personalEffectivenessUpdateResultSchema>;

export const dealRoomCommandSummaryResultSchema = z.object({
  command_summary: z.string().min(1),
  current_goal_refinement: z.string().min(1),
  key_blockers: z.array(z.string()).default([]),
  recommended_next_moves: z.array(z.string()).default([]),
  manager_attention_reason: z.string().min(1),
  missing_information: z.array(z.string()).default([])
});

export type DealRoomCommandSummaryResult = z.infer<typeof dealRoomCommandSummaryResultSchema>;

export const threadSummaryResultSchema = z.object({
  summary: z.string().min(1),
  open_questions: z.array(z.string()).default([]),
  recommended_next_action: z.string().min(1),
  decision_needed: z.boolean()
});

export type ThreadSummaryResult = z.infer<typeof threadSummaryResultSchema>;

export const decisionSupportResultSchema = z.object({
  options_assessment: z.array(
    z.object({
      option: z.string().min(1),
      pros: z.array(z.string()).default([]),
      cons: z.array(z.string()).default([]),
      risk_level: z.enum(["low", "medium", "high"])
    })
  ),
  recommended_option: z.string().min(1),
  pros_cons: z.object({
    pros: z.array(z.string()).default([]),
    cons: z.array(z.string()).default([])
  }),
  caution_notes: z.array(z.string()).default([]),
  followup_actions: z.array(z.string()).default([])
});

export type DecisionSupportResult = z.infer<typeof decisionSupportResultSchema>;

export const interventionRecommendationResultSchema = z.object({
  whether_to_intervene: z.boolean(),
  why_now: z.string().min(1),
  intervention_goal: z.string().min(1),
  suggested_manager_action: z.array(z.string()).default([]),
  expected_shift: z.array(z.string()).default([])
});

export type InterventionRecommendationResult = z.infer<typeof interventionRecommendationResultSchema>;

export const dealPlaybookMappingResultSchema = z.object({
  relevant_playbooks: z.array(
    z.object({
      playbook_id: z.string().uuid().nullable(),
      title: z.string().min(1),
      applicability_score: z.number().min(0).max(1),
      applicability_reason: z.string().min(1),
      suggested_application: z.array(z.string()).default([])
    })
  ),
  applicability_reason: z.string().min(1),
  suggested_application: z.array(z.string()).default([])
});

export type DealPlaybookMappingResult = z.infer<typeof dealPlaybookMappingResultSchema>;

export const emailDraftGenerationResultSchema = z.object({
  subject: z.string().min(1),
  opening: z.string().min(1),
  body: z.string().min(1),
  cta: z.string().min(1),
  caution_notes: z.array(z.string()).default([])
});

export type EmailDraftGenerationResult = z.infer<typeof emailDraftGenerationResultSchema>;

export const meetingAgendaGenerationResultSchema = z.object({
  meeting_goal: z.string().min(1),
  agenda_points: z.array(z.string()).default([]),
  must_cover: z.array(z.string()).default([]),
  risk_notes: z.array(z.string()).default([]),
  expected_next_step: z.array(z.string()).default([])
});

export type MeetingAgendaGenerationResult = z.infer<typeof meetingAgendaGenerationResultSchema>;

export const meetingFollowupSummaryResultSchema = z.object({
  meeting_summary: z.string().min(1),
  decisions_made: z.array(z.string()).default([]),
  next_actions: z.array(z.string()).default([]),
  followup_message_draft_hint: z.string().min(1),
  checkpoint_update_hint: z.array(z.string()).default([])
});

export type MeetingFollowupSummaryResult = z.infer<typeof meetingFollowupSummaryResultSchema>;

export const documentAssetSummaryResultSchema = z.object({
  document_type_guess: z.enum(["proposal", "quote", "contract_draft", "meeting_note", "case_study", "product_material", "other"]),
  summary: z.string().min(1),
  risk_flags: z.array(z.string()).default([]),
  recommended_actions: z.array(z.string()).default([]),
  related_checkpoint_hint: z.array(z.string()).default([])
});

export type DocumentAssetSummaryResult = z.infer<typeof documentAssetSummaryResultSchema>;

export const externalTouchpointReviewResultSchema = z.object({
  external_progress_assessment: z.string().min(1),
  stalled_touchpoints: z.array(z.string()).default([]),
  missing_touchpoints: z.array(z.string()).default([]),
  recommended_next_moves: z.array(z.string()).default([])
});

export type ExternalTouchpointReviewResult = z.infer<typeof externalTouchpointReviewResultSchema>;

export const onboardingRecommendationResultSchema = z.object({
  next_best_setup_steps: z.array(z.string()).default([]),
  missing_foundations: z.array(z.string()).default([]),
  recommended_demo_flow: z.array(z.string()).default([]),
  recommended_team_actions: z.array(z.string()).default([]),
  risks_if_skipped: z.array(z.string()).default([])
});

export type OnboardingRecommendationResult = z.infer<typeof onboardingRecommendationResultSchema>;

export const usageHealthSummaryResultSchema = z.object({
  usage_summary: z.string().min(1),
  hot_features: z.array(z.string()).default([]),
  underused_features: z.array(z.string()).default([]),
  quota_risks: z.array(z.string()).default([]),
  recommended_adjustments: z.array(z.string()).default([])
});

export type UsageHealthSummaryResult = z.infer<typeof usageHealthSummaryResultSchema>;

export const importColumnMappingAssistResultSchema = z.object({
  mapping_suggestions: z.array(
    z.object({
      source_column_name: z.string().min(1),
      mapped_target_entity: z.enum(["customer", "opportunity", "followup", "mixed"]).nullable(),
      mapped_target_field: z.string().nullable(),
      confidence: z.number().min(0).max(1),
      warning: z.string().nullable()
    })
  ),
  warnings: z.array(z.string()).default([])
});

export type ImportColumnMappingAssistResult = z.infer<typeof importColumnMappingAssistResultSchema>;

export const importReviewSummaryResultSchema = z.object({
  summary: z.string().min(1),
  issues: z.array(z.string()).default([]),
  recommended_cleanup: z.array(z.string()).default([]),
  recommended_next_steps: z.array(z.string()).default([])
});

export type ImportReviewSummaryResult = z.infer<typeof importReviewSummaryResultSchema>;

export const mobileQuickCaptureRefineResultSchema = z.object({
  refined_summary: z.string().min(1),
  likely_source_type: z.enum(["manual_note", "pasted_chat", "call_summary", "meeting_note", "voice_transcript", "imported_text"]),
  next_best_fields_to_fill: z.array(z.string()).default([]),
  should_save_as_draft_only: z.boolean(),
  followup_hint: z.string().min(1)
});

export type MobileQuickCaptureRefineResult = z.infer<typeof mobileQuickCaptureRefineResultSchema>;

export const mobileBriefCompactSummaryResultSchema = z.object({
  compact_headline: z.string().min(1),
  top_priorities: z.array(z.string()).default([]),
  urgent_risks: z.array(z.string()).default([]),
  one_line_guidance: z.string().min(1)
});

export type MobileBriefCompactSummaryResult = z.infer<typeof mobileBriefCompactSummaryResultSchema>;

export const templateFitRecommendationResultSchema = z.object({
  recommended_template_key: z.string().min(1),
  fit_reasons: z.array(z.string()).default([]),
  risks_of_mismatch: z.array(z.string()).default([]),
  recommended_apply_mode: z.enum(["onboarding_default", "demo_seed", "manual_apply", "trial_bootstrap"]),
  recommended_overrides: z.array(z.string()).default([])
});

export type TemplateFitRecommendationResult = z.infer<typeof templateFitRecommendationResultSchema>;

export const templateApplicationSummaryResultSchema = z.object({
  what_will_change: z.array(z.string()).default([]),
  what_will_not_change: z.array(z.string()).default([]),
  caution_notes: z.array(z.string()).default([]),
  recommended_next_steps: z.array(z.string()).default([])
});

export type TemplateApplicationSummaryResult = z.infer<typeof templateApplicationSummaryResultSchema>;

export const industrySeedCustomizationResultSchema = z.object({
  demo_seed_customization_hints: z.array(z.string()).default([]),
  playbook_emphasis: z.array(z.string()).default([]),
  checkpoint_emphasis: z.array(z.string()).default([]),
  manager_focus_hints: z.array(z.string()).default([])
});

export type IndustrySeedCustomizationResult = z.infer<typeof industrySeedCustomizationResultSchema>;

export const leadQualificationAssistResultSchema = z.object({
  qualification_assessment: z.string().min(1),
  fit_score: z.number().min(0).max(100),
  likely_use_case: z.string().min(1),
  suggested_owner_type: z.enum(["sales", "manager"]),
  suggested_next_actions: z.array(z.string()).default([]),
  risk_flags: z.array(z.string()).default([])
});

export type LeadQualificationAssistResult = z.infer<typeof leadQualificationAssistResultSchema>;

export const trialConversionReviewResultSchema = z.object({
  activation_health: z.string().min(1),
  readiness_assessment: z.string().min(1),
  risk_factors: z.array(z.string()).default([]),
  recommended_conversion_actions: z.array(z.string()).default([]),
  recommended_owner_followup: z.array(z.string()).default([])
});

export type TrialConversionReviewResult = z.infer<typeof trialConversionReviewResultSchema>;

export const growthPipelineSummaryResultSchema = z.object({
  funnel_summary: z.string().min(1),
  best_channels: z.array(z.string()).default([]),
  weak_points: z.array(z.string()).default([]),
  high_potential_segments: z.array(z.string()).default([]),
  next_best_actions: z.array(z.string()).default([])
});

export type GrowthPipelineSummaryResult = z.infer<typeof growthPipelineSummaryResultSchema>;

export const executiveBriefSummaryResultSchema = z.object({
  headline: z.string().min(1),
  top_risks: z.array(z.string()).default([]),
  top_opportunities: z.array(z.string()).default([]),
  suggested_actions: z.array(z.string()).default([]),
  watch_items: z.array(z.string()).default([])
});

export type ExecutiveBriefSummaryResult = z.infer<typeof executiveBriefSummaryResultSchema>;

export const customerHealthSummaryResultSchema = z.object({
  health_summary: z.string().min(1),
  risk_flags: z.array(z.string()).default([]),
  positive_signals: z.array(z.string()).default([]),
  recommended_actions: z.array(z.string()).default([])
});

export type CustomerHealthSummaryResult = z.infer<typeof customerHealthSummaryResultSchema>;

export const automationActionRecommendationResultSchema = z.object({
  why_it_matters: z.string().min(1),
  suggested_action: z.string().min(1),
  urgency: z.enum(["low", "medium", "high"]),
  owner_hint: z.string().min(1)
});

export type AutomationActionRecommendationResult = z.infer<typeof automationActionRecommendationResultSchema>;

export const retentionWatchReviewResultSchema = z.object({
  at_risk_customers: z.array(z.string()).default([]),
  expansion_candidates: z.array(z.string()).default([]),
  recommended_retention_moves: z.array(z.string()).default([]),
  recommended_owner_actions: z.array(z.string()).default([])
});

export type RetentionWatchReviewResult = z.infer<typeof retentionWatchReviewResultSchema>;

export interface AiPromptVersion {
  id: string;
  org_id: string;
  name: string;
  version: string;
  scenario: AiScenario;
  provider_scope: AiPromptProviderScope;
  system_prompt: string;
  developer_prompt: string;
  output_schema: Record<string, unknown>;
  is_active: boolean;
  created_at: string;
}

export interface AiRun {
  id: string;
  org_id: string;
  customer_id: string | null;
  followup_id: string | null;
  triggered_by_user_id: string | null;
  trigger_source: AiTriggerSource;
  scenario: AiScenario;
  provider: AiProviderId;
  model: string;
  prompt_version: string;
  status: AiRunStatus;
  input_snapshot: Record<string, unknown> | null;
  output_snapshot: Record<string, unknown> | null;
  parsed_result: Record<string, unknown> | null;
  error_message: string | null;
  latency_ms: number | null;
  result_source: AiResultSource;
  fallback_reason: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export interface AlertRuleRun {
  id: string;
  org_id: string;
  rule_name: string;
  status: "running" | "completed" | "failed";
  scanned_count: number;
  created_alert_count: number;
  deduped_alert_count: number;
  resolved_alert_count: number;
  started_at: string;
  completed_at: string | null;
  error_message: string | null;
  created_at: string;
}

export const valueMetricsSummaryResultSchema = z.object({
  headline: z.string().min(1),
  highlights: z.array(z.string()).default([]),
  recommendation: z.string().min(1)
});

export type ValueMetricsSummaryResult = z.infer<typeof valueMetricsSummaryResultSchema>;
