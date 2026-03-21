import type { AlertItem } from "@/types/alert";
import type { User } from "@/types/auth";
import type { CommunicationInputItem } from "@/types/communication";
import type { Customer } from "@/types/customer";
import type { Database } from "@/types/database";
import type { CollaborationMessage, CollaborationThread, DealCheckpoint, DealParticipant, DealRoom, DecisionRecord, InterventionRequest } from "@/types/deal";
import type { FollowupRecord } from "@/types/followup";
import type { MemoryFeedback, UserMemoryItem, UserMemoryProfile } from "@/types/memory";
import type { ActionOutcome, OutcomeReview, SuggestionAdoption } from "@/types/outcome";
import type { Opportunity } from "@/types/opportunity";
import type { Playbook, PlaybookEntry, PlaybookFeedback } from "@/types/playbook";
import type { DedupeMatchGroup, ImportAuditEvent, ImportJob, ImportJobColumn, ImportJobRow, ImportTemplate } from "@/types/import";
import type { MobileDeviceSession, MobileDraftSyncJob, OfflineActionQueueItem } from "@/types/mobile";
import type {
  OrgConfigAuditLog,
  OnboardingRun,
  OrgAiSettings,
  OrgFeatureFlag,
  OrgInvite,
  OrgMemberRole,
  OrgMembership,
  OrgPlanProfile,
  OrgSeatStatus,
  OrgSettings,
  OrgUsageCounter,
  UserUsageCounter
} from "@/types/productization";
import type { GeneratedReport } from "@/types/report";
import type { BehaviorQualitySnapshot, CoachingReport } from "@/types/quality";
import type { ContentDraft, MorningBrief, PrepCard, PrepFeedback } from "@/types/preparation";
import type { CalendarEvent, DocumentAsset, EmailMessage, EmailThread, ExternalAccount, ExternalTouchpointEvent } from "@/types/touchpoint";
import type { DailyWorkPlan, DailyWorkPlanItem, TaskExecutionLog, WorkAgentRun, WorkItem } from "@/types/work";

type ProfileRow = Database["public"]["Tables"]["profiles"]["Row"];
type CustomerRow = Database["public"]["Tables"]["customers"]["Row"];
type FollowupRow = Database["public"]["Tables"]["followups"]["Row"];
type OpportunityRow = Database["public"]["Tables"]["opportunities"]["Row"];
type AlertRow = Database["public"]["Tables"]["alerts"]["Row"];
type CommunicationInputRow = Database["public"]["Tables"]["communication_inputs"]["Row"];
type GeneratedReportRow = Database["public"]["Tables"]["generated_reports"]["Row"];
type UserMemoryProfileRow = Database["public"]["Tables"]["user_memory_profiles"]["Row"];
type UserMemoryItemRow = Database["public"]["Tables"]["user_memory_items"]["Row"];
type BehaviorQualitySnapshotRow = Database["public"]["Tables"]["behavior_quality_snapshots"]["Row"];
type CoachingReportRow = Database["public"]["Tables"]["coaching_reports"]["Row"];
type MemoryFeedbackRow = Database["public"]["Tables"]["memory_feedback"]["Row"];
type WorkItemRow = Database["public"]["Tables"]["work_items"]["Row"];
type DailyWorkPlanRow = Database["public"]["Tables"]["daily_work_plans"]["Row"];
type DailyWorkPlanItemRow = Database["public"]["Tables"]["daily_work_plan_items"]["Row"];
type TaskExecutionLogRow = Database["public"]["Tables"]["task_execution_logs"]["Row"];
type WorkAgentRunRow = Database["public"]["Tables"]["work_agent_runs"]["Row"];
type PrepCardRow = Database["public"]["Tables"]["prep_cards"]["Row"];
type MorningBriefRow = Database["public"]["Tables"]["morning_briefs"]["Row"];
type ContentDraftRow = Database["public"]["Tables"]["content_drafts"]["Row"];
type PrepFeedbackRow = Database["public"]["Tables"]["prep_feedback"]["Row"];
type ActionOutcomeRow = Database["public"]["Tables"]["action_outcomes"]["Row"];
type SuggestionAdoptionRow = Database["public"]["Tables"]["suggestion_adoptions"]["Row"];
type PlaybookRow = Database["public"]["Tables"]["playbooks"]["Row"];
type PlaybookEntryRow = Database["public"]["Tables"]["playbook_entries"]["Row"];
type OutcomeReviewRow = Database["public"]["Tables"]["outcome_reviews"]["Row"];
type PlaybookFeedbackRow = Database["public"]["Tables"]["playbook_feedback"]["Row"];
type DealRoomRow = Database["public"]["Tables"]["deal_rooms"]["Row"];
type CollaborationThreadRow = Database["public"]["Tables"]["collaboration_threads"]["Row"];
type CollaborationMessageRow = Database["public"]["Tables"]["collaboration_messages"]["Row"];
type DecisionRecordRow = Database["public"]["Tables"]["decision_records"]["Row"];
type DealParticipantRow = Database["public"]["Tables"]["deal_participants"]["Row"];
type DealCheckpointRow = Database["public"]["Tables"]["deal_checkpoints"]["Row"];
type InterventionRequestRow = Database["public"]["Tables"]["intervention_requests"]["Row"];
type ExternalAccountRow = Database["public"]["Tables"]["external_accounts"]["Row"];
type EmailThreadRow = Database["public"]["Tables"]["email_threads"]["Row"];
type EmailMessageRow = Database["public"]["Tables"]["email_messages"]["Row"];
type CalendarEventRow = Database["public"]["Tables"]["calendar_events"]["Row"];
type DocumentAssetRow = Database["public"]["Tables"]["document_assets"]["Row"];
type ExternalTouchpointEventRow = Database["public"]["Tables"]["external_touchpoint_events"]["Row"];
type OrgSettingsRow = Database["public"]["Tables"]["org_settings"]["Row"];
type OrgFeatureFlagRow = Database["public"]["Tables"]["org_feature_flags"]["Row"];
type OrgAiSettingsRow = Database["public"]["Tables"]["org_ai_settings"]["Row"];
type OrgMembershipRow = Database["public"]["Tables"]["org_memberships"]["Row"];
type OrgInviteRow = Database["public"]["Tables"]["org_invites"]["Row"];
type OrgUsageCounterRow = Database["public"]["Tables"]["org_usage_counters"]["Row"];
type UserUsageCounterRow = Database["public"]["Tables"]["user_usage_counters"]["Row"];
type OrgPlanProfileRow = Database["public"]["Tables"]["org_plan_profiles"]["Row"];
type OnboardingRunRow = Database["public"]["Tables"]["onboarding_runs"]["Row"];
type OrgConfigAuditLogRow = Database["public"]["Tables"]["org_config_audit_logs"]["Row"];
type ImportJobRowDb = Database["public"]["Tables"]["import_jobs"]["Row"];
type ImportJobColumnRow = Database["public"]["Tables"]["import_job_columns"]["Row"];
type ImportJobRowRow = Database["public"]["Tables"]["import_job_rows"]["Row"];
type ImportTemplateRow = Database["public"]["Tables"]["import_templates"]["Row"];
type DedupeMatchGroupRow = Database["public"]["Tables"]["dedupe_match_groups"]["Row"];
type ImportAuditEventRow = Database["public"]["Tables"]["import_audit_events"]["Row"];
type MobileDraftSyncJobRow = Database["public"]["Tables"]["mobile_draft_sync_jobs"]["Row"];
type MobileDeviceSessionRow = Database["public"]["Tables"]["mobile_device_sessions"]["Row"];
type OfflineActionQueueRow = Database["public"]["Tables"]["offline_action_queue"]["Row"];

interface ProfileLite {
  id: string;
  display_name: string;
}

interface CustomerLite {
  id: string;
  company_name: string;
}

function computeStalledDays(lastFollowupAt: string | null, createdAt: string): number {
  const source = lastFollowupAt ?? createdAt;
  const diffMs = Date.now() - new Date(source).getTime();
  return Math.max(0, Math.floor(diffMs / (24 * 60 * 60 * 1000)));
}

export function mapProfileToUser(
  profile: ProfileRow,
  email: string | undefined,
  options?: {
    effectiveRole?: User["role"];
    orgRole?: OrgMemberRole | null;
    orgSeatStatus?: OrgSeatStatus | null;
  }
): User {
  return {
    id: profile.id,
    orgId: profile.org_id,
    name: profile.display_name,
    role: options?.effectiveRole ?? profile.role,
    title: profile.title ?? "Sales",
    email: email ?? "",
    team: profile.team_name ?? "Sales Team",
    orgRole: options?.orgRole ?? undefined,
    orgSeatStatus: options?.orgSeatStatus ?? undefined
  };
}

export function mapCustomerRow(
  row: CustomerRow & {
    owner?: ProfileLite | null;
  }
): Customer {
  return {
    id: row.id,
    customerName: row.name,
    companyName: row.company_name,
    contactName: row.contact_name,
    phone: row.phone ?? "-",
    email: row.email ?? "-",
    sourceChannel: row.source_channel ?? "-",
    stage: row.current_stage,
    ownerId: row.owner_id,
    ownerName: row.owner?.display_name ?? "Unassigned",
    lastFollowupAt: row.last_followup_at ?? row.created_at,
    nextFollowupAt: row.next_followup_at ?? row.created_at,
    winProbability: row.win_probability,
    riskLevel: row.risk_level,
    tags: row.tags ?? [],
    aiSummary: row.ai_summary ?? "No AI summary yet.",
    aiSuggestion: row.ai_suggestion ?? "No AI suggestion yet.",
    aiRiskJudgement: row.ai_risk_judgement ?? "No AI risk judgement yet.",
    stalledDays: computeStalledDays(row.last_followup_at, row.created_at),
    hasDecisionMaker: row.has_decision_maker,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapFollowupRow(
  row: FollowupRow & {
    owner?: ProfileLite | null;
  }
): FollowupRecord {
  return {
    id: row.id,
    customerId: row.customer_id,
    ownerId: row.owner_id,
    ownerName: row.owner?.display_name ?? "Unknown",
    method: row.communication_type,
    summary: row.summary,
    customerNeeds: row.customer_needs,
    objections: row.objections ?? "",
    nextPlan: row.next_step,
    nextFollowupAt: row.next_followup_at ?? row.created_at,
    needsAiAnalysis: row.needs_ai_analysis,
    sourceInputId: row.source_input_id,
    draftStatus: row.draft_status,
    createdAt: row.created_at
  };
}

export function mapOpportunityRow(
  row: OpportunityRow & {
    owner?: ProfileLite | null;
    customer?: CustomerLite | null;
  }
): Opportunity {
  return {
    id: row.id,
    customerId: row.customer_id,
    customerName: row.customer?.company_name ?? "Unknown customer",
    name: row.title,
    expectedAmount: row.amount,
    stage: row.stage,
    ownerId: row.owner_id,
    ownerName: row.owner?.display_name ?? "Unknown",
    lastProgressAt: row.last_activity_at ?? row.created_at,
    riskLevel: row.risk_level,
    closeDate: row.expected_close_date ?? row.created_at.slice(0, 10)
  };
}

export function mapAlertRow(
  row: AlertRow & {
    owner?: ProfileLite | null;
    customer?: CustomerLite | null;
  }
): AlertItem {
  const evidence = Array.isArray(row.evidence) ? row.evidence.filter((item): item is string => typeof item === "string") : [];

  return {
    id: row.id,
    customerId: row.customer_id ?? "",
    customerName: row.customer?.company_name ?? "System alert",
    opportunityId: row.opportunity_id,
    ownerId: row.owner_id ?? "",
    ownerName: row.owner?.display_name ?? "System",
    ruleType: row.rule_type,
    source: row.source ?? "rule",
    level: row.severity,
    status: row.status,
    title: row.title,
    message: row.description ?? row.title,
    evidence,
    suggestedOwnerAction: row.suggested_owner_action ?? [],
    dueAt: row.due_at,
    resolvedAt: row.resolved_at,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapCommunicationInputRow(
  row: CommunicationInputRow & {
    owner?: ProfileLite | null;
    customer?: CustomerLite | null;
  }
): CommunicationInputItem {
  return {
    id: row.id,
    orgId: row.org_id,
    customerId: row.customer_id,
    customerName: row.customer?.company_name ?? null,
    ownerId: row.owner_id,
    ownerName: row.owner?.display_name ?? "Unknown",
    sourceType: row.source_type,
    title: row.title ?? "Untitled input",
    rawContent: row.raw_content,
    inputLanguage: row.input_language,
    occurredAt: row.occurred_at,
    extractedFollowupId: row.extracted_followup_id,
    extractionStatus: row.extraction_status,
    extractionError: row.extraction_error,
    extractedData: (row.extracted_data as Record<string, unknown> | null) ?? {},
    createdBy: row.created_by,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapGeneratedReportRow(row: GeneratedReportRow): GeneratedReport {
  return {
    id: row.id,
    orgId: row.org_id,
    reportType: row.report_type,
    targetUserId: row.target_user_id,
    scopeType: row.scope_type,
    periodStart: row.period_start,
    periodEnd: row.period_end,
    status: row.status,
    title: row.title ?? "Untitled report",
    summary: row.summary ?? "",
    contentMarkdown: row.content_markdown ?? "",
    metricsSnapshot: (row.metrics_snapshot as Record<string, unknown> | null) ?? {},
    sourceSnapshot: (row.source_snapshot as Record<string, unknown> | null) ?? {},
    generatedBy: row.generated_by,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

function toStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

export function mapUserMemoryProfileRow(row: UserMemoryProfileRow): UserMemoryProfile {
  return {
    id: row.id,
    orgId: row.org_id,
    userId: row.user_id,
    memoryVersion: row.memory_version,
    summary: row.summary,
    preferredCustomerTypes: toStringArray(row.preferred_customer_types),
    preferredCommunicationStyles: toStringArray(row.preferred_communication_styles),
    commonObjections: toStringArray(row.common_objections),
    effectiveTactics: toStringArray(row.effective_tactics),
    commonFollowupRhythm: toStringArray(row.common_followup_rhythm),
    quotingStyleNotes: toStringArray(row.quoting_style_notes),
    riskBlindSpots: toStringArray(row.risk_blind_spots),
    managerCoachingFocus: toStringArray(row.manager_coaching_focus),
    confidenceScore: Number(row.confidence_score ?? 0),
    sourceWindowDays: row.source_window_days,
    lastCompiledAt: row.last_compiled_at,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapUserMemoryItemRow(row: UserMemoryItemRow): UserMemoryItem {
  return {
    id: row.id,
    orgId: row.org_id,
    userId: row.user_id,
    memoryType: row.memory_type,
    title: row.title,
    description: row.description,
    evidenceSnapshot: (row.evidence_snapshot as Record<string, unknown> | null) ?? {},
    confidenceScore: Number(row.confidence_score ?? 0),
    sourceCount: row.source_count,
    status: row.status,
    createdBySystem: row.created_by_system,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapBehaviorQualitySnapshotRow(row: BehaviorQualitySnapshotRow): BehaviorQualitySnapshot {
  return {
    id: row.id,
    orgId: row.org_id,
    userId: row.user_id,
    snapshotDate: row.snapshot_date,
    periodType: row.period_type,
    assignedCustomerCount: row.assigned_customer_count,
    activeCustomerCount: row.active_customer_count,
    followupCount: row.followup_count,
    onTimeFollowupRate: Number(row.on_time_followup_rate ?? 0),
    overdueFollowupRate: Number(row.overdue_followup_rate ?? 0),
    followupCompletenessScore: Number(row.followup_completeness_score ?? 0),
    stageProgressionScore: Number(row.stage_progression_score ?? 0),
    riskResponseScore: Number(row.risk_response_score ?? 0),
    highValueFocusScore: Number(row.high_value_focus_score ?? 0),
    activityQualityScore: Number(row.activity_quality_score ?? 0),
    shallowActivityRatio: Number(row.shallow_activity_ratio ?? 0),
    stalledCustomerCount: row.stalled_customer_count,
    highRiskUnhandledCount: row.high_risk_unhandled_count,
    summary: row.summary,
    metricsSnapshot: (row.metrics_snapshot as Record<string, unknown> | null) ?? {},
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapCoachingReportRow(row: CoachingReportRow): CoachingReport {
  return {
    id: row.id,
    orgId: row.org_id,
    reportScope: row.report_scope,
    targetUserId: row.target_user_id,
    periodStart: row.period_start,
    periodEnd: row.period_end,
    status: row.status,
    title: row.title ?? "Untitled coaching report",
    executiveSummary: row.executive_summary ?? "",
    strengths: toStringArray(row.strengths),
    weaknesses: toStringArray(row.weaknesses),
    coachingActions: toStringArray(row.coaching_actions),
    replicablePatterns: toStringArray(row.replicable_patterns),
    riskWarnings: toStringArray(row.risk_warnings),
    contentMarkdown: row.content_markdown ?? "",
    sourceSnapshot: (row.source_snapshot as Record<string, unknown> | null) ?? {},
    generatedBy: row.generated_by,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapMemoryFeedbackRow(row: MemoryFeedbackRow): MemoryFeedback {
  return {
    id: row.id,
    orgId: row.org_id,
    userId: row.user_id,
    memoryItemId: row.memory_item_id,
    feedbackType: row.feedback_type,
    feedbackText: row.feedback_text,
    createdAt: row.created_at
  };
}

export function mapWorkItemRow(
  row: WorkItemRow & {
    owner?: ProfileLite | null;
    customer?: CustomerLite | null;
  }
): WorkItem {
  return {
    id: row.id,
    orgId: row.org_id,
    ownerId: row.owner_id,
    ownerName: row.owner?.display_name ?? "Unknown",
    customerId: row.customer_id,
    customerName: row.customer?.company_name ?? null,
    opportunityId: row.opportunity_id,
    sourceType: row.source_type,
    workType: row.work_type,
    title: row.title,
    description: row.description,
    rationale: row.rationale,
    priorityScore: Number(row.priority_score ?? 0),
    priorityBand: row.priority_band,
    status: row.status,
    scheduledFor: row.scheduled_for,
    dueAt: row.due_at,
    completedAt: row.completed_at,
    snoozedUntil: row.snoozed_until,
    sourceRefType: row.source_ref_type,
    sourceRefId: row.source_ref_id,
    aiGenerated: row.ai_generated,
    aiRunId: row.ai_run_id,
    createdBy: row.created_by,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapDailyWorkPlanRow(row: DailyWorkPlanRow): DailyWorkPlan {
  return {
    id: row.id,
    orgId: row.org_id,
    userId: row.user_id,
    planDate: row.plan_date,
    status: row.status,
    summary: row.summary,
    totalItems: row.total_items,
    criticalItems: row.critical_items,
    focusTheme: row.focus_theme,
    sourceSnapshot: (row.source_snapshot as Record<string, unknown> | null) ?? {},
    generatedBy: row.generated_by,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapDailyWorkPlanItemRow(row: DailyWorkPlanItemRow): DailyWorkPlanItem {
  return {
    id: row.id,
    orgId: row.org_id,
    planId: row.plan_id,
    workItemId: row.work_item_id,
    sequenceNo: row.sequence_no,
    plannedTimeBlock: row.planned_time_block,
    recommendationReason: row.recommendation_reason,
    mustDo: row.must_do,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapTaskExecutionLogRow(row: TaskExecutionLogRow): TaskExecutionLog {
  return {
    id: row.id,
    orgId: row.org_id,
    workItemId: row.work_item_id,
    userId: row.user_id,
    actionType: row.action_type,
    actionNote: row.action_note,
    beforeSnapshot: (row.before_snapshot as Record<string, unknown> | null) ?? {},
    afterSnapshot: (row.after_snapshot as Record<string, unknown> | null) ?? {},
    createdAt: row.created_at
  };
}

export function mapWorkAgentRunRow(row: WorkAgentRunRow): WorkAgentRun {
  return {
    id: row.id,
    orgId: row.org_id,
    userId: row.user_id,
    runScope: row.run_scope,
    status: row.status,
    inputSnapshot: (row.input_snapshot as Record<string, unknown> | null) ?? {},
    outputSnapshot: (row.output_snapshot as Record<string, unknown> | null) ?? {},
    parsedResult: (row.parsed_result as Record<string, unknown> | null) ?? {},
    provider: row.provider,
    model: row.model,
    resultSource: row.result_source,
    fallbackReason: row.fallback_reason,
    errorMessage: row.error_message,
    startedAt: row.started_at,
    completedAt: row.completed_at,
    createdAt: row.created_at
  };
}

export function mapPrepCardRow(row: PrepCardRow): PrepCard {
  return {
    id: row.id,
    orgId: row.org_id,
    ownerId: row.owner_id,
    customerId: row.customer_id,
    opportunityId: row.opportunity_id,
    workItemId: row.work_item_id,
    cardType: row.card_type,
    status: row.status,
    title: row.title,
    summary: row.summary,
    cardPayload: (row.card_payload as Record<string, unknown> | null) ?? {},
    sourceSnapshot: (row.source_snapshot as Record<string, unknown> | null) ?? {},
    generatedBy: row.generated_by,
    aiRunId: row.ai_run_id,
    validUntil: row.valid_until,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapMorningBriefRow(row: MorningBriefRow): MorningBrief {
  return {
    id: row.id,
    orgId: row.org_id,
    targetUserId: row.target_user_id,
    briefType: row.brief_type,
    briefDate: row.brief_date,
    status: row.status,
    headline: row.headline ?? "",
    executiveSummary: row.executive_summary ?? "",
    briefPayload: (row.brief_payload as Record<string, unknown> | null) ?? {},
    sourceSnapshot: (row.source_snapshot as Record<string, unknown> | null) ?? {},
    generatedBy: row.generated_by,
    aiRunId: row.ai_run_id,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapContentDraftRow(row: ContentDraftRow): ContentDraft {
  return {
    id: row.id,
    orgId: row.org_id,
    ownerId: row.owner_id,
    customerId: row.customer_id,
    opportunityId: row.opportunity_id,
    prepCardId: row.prep_card_id,
    workItemId: row.work_item_id,
    draftType: row.draft_type,
    status: row.status,
    title: row.title,
    contentMarkdown: row.content_markdown,
    contentText: row.content_text,
    rationale: row.rationale,
    sourceSnapshot: (row.source_snapshot as Record<string, unknown> | null) ?? {},
    generatedBy: row.generated_by,
    aiRunId: row.ai_run_id,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapPrepFeedbackRow(row: PrepFeedbackRow): PrepFeedback {
  return {
    id: row.id,
    orgId: row.org_id,
    userId: row.user_id,
    targetType: row.target_type,
    targetId: row.target_id,
    feedbackType: row.feedback_type,
    feedbackText: row.feedback_text,
    createdAt: row.created_at
  };
}

export function mapActionOutcomeRow(row: ActionOutcomeRow): ActionOutcome {
  return {
    id: row.id,
    orgId: row.org_id,
    ownerId: row.owner_id,
    customerId: row.customer_id,
    opportunityId: row.opportunity_id,
    workItemId: row.work_item_id,
    followupId: row.followup_id,
    communicationInputId: row.communication_input_id,
    prepCardId: row.prep_card_id,
    contentDraftId: row.content_draft_id,
    outcomeType: row.outcome_type,
    resultStatus: row.result_status,
    stageChanged: row.stage_changed,
    oldStage: row.old_stage,
    newStage: row.new_stage,
    customerSentimentShift: row.customer_sentiment_shift,
    keyOutcomeSummary: row.key_outcome_summary,
    newObjections: toStringArray(row.new_objections),
    newRisks: toStringArray(row.new_risks),
    nextStepDefined: row.next_step_defined,
    nextStepText: row.next_step_text,
    followupDueAt: row.followup_due_at,
    usedPrepCard: row.used_prep_card,
    usedDraft: row.used_draft,
    usefulnessRating: row.usefulness_rating,
    notes: row.notes,
    createdBy: row.created_by,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapSuggestionAdoptionRow(row: SuggestionAdoptionRow): SuggestionAdoption {
  return {
    id: row.id,
    orgId: row.org_id,
    userId: row.user_id,
    targetType: row.target_type,
    targetId: row.target_id,
    adoptionType: row.adoption_type,
    editDistanceHint: row.edit_distance_hint,
    adoptionContext: row.adoption_context,
    linkedOutcomeId: row.linked_outcome_id,
    createdAt: row.created_at
  };
}

export function mapPlaybookRow(row: PlaybookRow): Playbook {
  return {
    id: row.id,
    orgId: row.org_id,
    scopeType: row.scope_type,
    ownerUserId: row.owner_user_id,
    playbookType: row.playbook_type,
    title: row.title,
    summary: row.summary,
    status: row.status,
    confidenceScore: Number(row.confidence_score ?? 0),
    applicabilityNotes: row.applicability_notes,
    sourceSnapshot: (row.source_snapshot as Record<string, unknown> | null) ?? {},
    generatedBy: row.generated_by,
    aiRunId: row.ai_run_id,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapPlaybookEntryRow(row: PlaybookEntryRow): PlaybookEntry {
  return {
    id: row.id,
    orgId: row.org_id,
    playbookId: row.playbook_id,
    entryTitle: row.entry_title,
    entrySummary: row.entry_summary,
    conditions: (row.conditions as Record<string, unknown> | null) ?? {},
    recommendedActions: toStringArray(row.recommended_actions),
    cautionNotes: toStringArray(row.caution_notes),
    evidenceSnapshot: (row.evidence_snapshot as Record<string, unknown> | null) ?? {},
    successSignal: (row.success_signal as Record<string, unknown> | null) ?? {},
    failureModes: toStringArray(row.failure_modes),
    confidenceScore: Number(row.confidence_score ?? 0),
    sortOrder: row.sort_order,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapOutcomeReviewRow(row: OutcomeReviewRow): OutcomeReview {
  return {
    id: row.id,
    orgId: row.org_id,
    reviewScope: row.review_scope,
    targetUserId: row.target_user_id,
    periodStart: row.period_start,
    periodEnd: row.period_end,
    status: row.status,
    title: row.title ?? "",
    executiveSummary: row.executive_summary ?? "",
    effectivePatterns: toStringArray(row.effective_patterns),
    ineffectivePatterns: toStringArray(row.ineffective_patterns),
    repeatedFailures: toStringArray(row.repeated_failures),
    coachingActions: toStringArray(row.coaching_actions),
    playbookCandidates: toStringArray(row.playbook_candidates),
    sourceSnapshot: (row.source_snapshot as Record<string, unknown> | null) ?? {},
    generatedBy: row.generated_by,
    aiRunId: row.ai_run_id,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapPlaybookFeedbackRow(row: PlaybookFeedbackRow): PlaybookFeedback {
  return {
    id: row.id,
    orgId: row.org_id,
    userId: row.user_id,
    playbookId: row.playbook_id,
    playbookEntryId: row.playbook_entry_id,
    feedbackType: row.feedback_type,
    feedbackText: row.feedback_text,
    createdAt: row.created_at
  };
}

export function mapDealRoomRow(
  row: DealRoomRow & {
    owner?: ProfileLite | null;
    customer?: CustomerLite | null;
    opportunity?: { id: string; title: string } | null;
  }
): DealRoom {
  return {
    id: row.id,
    orgId: row.org_id,
    customerId: row.customer_id,
    customerName: row.customer?.company_name ?? "Unknown customer",
    opportunityId: row.opportunity_id,
    opportunityTitle: row.opportunity?.title ?? null,
    ownerId: row.owner_id,
    ownerName: row.owner?.display_name ?? "Unknown owner",
    roomStatus: row.room_status,
    priorityBand: row.priority_band,
    title: row.title,
    commandSummary: row.command_summary,
    currentGoal: row.current_goal,
    currentBlockers: toStringArray(row.current_blockers),
    nextMilestone: row.next_milestone,
    nextMilestoneDueAt: row.next_milestone_due_at,
    managerAttentionNeeded: row.manager_attention_needed,
    sourceSnapshot: (row.source_snapshot as Record<string, unknown> | null) ?? {},
    createdBy: row.created_by,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapCollaborationThreadRow(row: CollaborationThreadRow): CollaborationThread {
  return {
    id: row.id,
    orgId: row.org_id,
    dealRoomId: row.deal_room_id,
    threadType: row.thread_type,
    title: row.title,
    status: row.status,
    summary: row.summary,
    createdBy: row.created_by,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapCollaborationMessageRow(
  row: CollaborationMessageRow & {
    author?: ProfileLite | null;
  }
): CollaborationMessage {
  return {
    id: row.id,
    orgId: row.org_id,
    threadId: row.thread_id,
    authorUserId: row.author_user_id,
    authorName: row.author?.display_name ?? "Unknown",
    messageType: row.message_type,
    bodyMarkdown: row.body_markdown,
    mentions: toStringArray(row.mentions),
    sourceRefType: row.source_ref_type,
    sourceRefId: row.source_ref_id,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapDecisionRecordRow(row: DecisionRecordRow): DecisionRecord {
  return {
    id: row.id,
    orgId: row.org_id,
    dealRoomId: row.deal_room_id,
    customerId: row.customer_id,
    opportunityId: row.opportunity_id,
    decisionType: row.decision_type,
    status: row.status,
    title: row.title,
    contextSummary: row.context_summary,
    optionsConsidered: toStringArray(row.options_considered),
    recommendedOption: row.recommended_option,
    decisionReason: row.decision_reason,
    decidedBy: row.decided_by,
    requestedBy: row.requested_by,
    dueAt: row.due_at,
    completedAt: row.completed_at,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapDealParticipantRow(
  row: DealParticipantRow & {
    profile?: ProfileLite | null;
  }
): DealParticipant {
  return {
    id: row.id,
    orgId: row.org_id,
    dealRoomId: row.deal_room_id,
    userId: row.user_id,
    userName: row.profile?.display_name ?? "Unknown",
    roleInRoom: row.role_in_room,
    isActive: row.is_active,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapDealCheckpointRow(
  row: DealCheckpointRow & {
    owner?: ProfileLite | null;
  }
): DealCheckpoint {
  return {
    id: row.id,
    orgId: row.org_id,
    dealRoomId: row.deal_room_id,
    checkpointType: row.checkpoint_type,
    status: row.status,
    title: row.title,
    description: row.description,
    dueAt: row.due_at,
    completedAt: row.completed_at,
    ownerId: row.owner_id,
    ownerName: row.owner?.display_name ?? null,
    evidenceSnapshot: (row.evidence_snapshot as Record<string, unknown> | null) ?? {},
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapInterventionRequestRow(
  row: InterventionRequestRow & {
    requester?: ProfileLite | null;
    target?: ProfileLite | null;
  }
): InterventionRequest {
  return {
    id: row.id,
    orgId: row.org_id,
    dealRoomId: row.deal_room_id,
    requestedBy: row.requested_by,
    requestedByName: row.requester?.display_name ?? "Unknown",
    targetUserId: row.target_user_id,
    targetUserName: row.target?.display_name ?? null,
    requestType: row.request_type,
    priorityBand: row.priority_band,
    status: row.status,
    requestSummary: row.request_summary,
    contextSnapshot: (row.context_snapshot as Record<string, unknown> | null) ?? {},
    dueAt: row.due_at,
    completedAt: row.completed_at,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapExternalAccountRow(row: ExternalAccountRow): ExternalAccount {
  return {
    id: row.id,
    orgId: row.org_id,
    userId: row.user_id,
    providerType: row.provider_type,
    providerName: row.provider_name,
    accountLabel: row.account_label,
    connectionStatus: row.connection_status,
    metadata: (row.metadata as Record<string, unknown> | null) ?? {},
    connectedAt: row.connected_at,
    updatedAt: row.updated_at
  };
}

export function mapEmailThreadRow(
  row: EmailThreadRow & {
    owner?: ProfileLite | null;
    customer?: CustomerLite | null;
  }
): EmailThread {
  return {
    id: row.id,
    orgId: row.org_id,
    ownerId: row.owner_id,
    ownerName: row.owner?.display_name ?? "Unknown",
    customerId: row.customer_id,
    customerName: row.customer?.company_name ?? null,
    opportunityId: row.opportunity_id,
    dealRoomId: row.deal_room_id,
    externalAccountId: row.external_account_id,
    externalThreadRef: row.external_thread_ref,
    subject: row.subject,
    participants: toStringArray(row.participants),
    latestMessageAt: row.latest_message_at,
    threadStatus: row.thread_status,
    sentimentHint: row.sentiment_hint,
    summary: row.summary,
    sourceSnapshot: (row.source_snapshot as Record<string, unknown> | null) ?? {},
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapEmailMessageRow(
  row: EmailMessageRow & {
    sender?: ProfileLite | null;
  }
): EmailMessage {
  return {
    id: row.id,
    orgId: row.org_id,
    threadId: row.thread_id,
    senderUserId: row.sender_user_id,
    senderName: row.sender?.display_name ?? null,
    direction: row.direction,
    externalMessageRef: row.external_message_ref,
    messageSubject: row.message_subject,
    messageBodyText: row.message_body_text,
    messageBodyMarkdown: row.message_body_markdown,
    sentAt: row.sent_at,
    receivedAt: row.received_at,
    status: row.status,
    sourceType: row.source_type,
    aiRunId: row.ai_run_id,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapCalendarEventRow(
  row: CalendarEventRow & {
    owner?: ProfileLite | null;
    customer?: CustomerLite | null;
  }
): CalendarEvent {
  return {
    id: row.id,
    orgId: row.org_id,
    ownerId: row.owner_id,
    ownerName: row.owner?.display_name ?? "Unknown",
    customerId: row.customer_id,
    customerName: row.customer?.company_name ?? null,
    opportunityId: row.opportunity_id,
    dealRoomId: row.deal_room_id,
    externalAccountId: row.external_account_id,
    externalEventRef: row.external_event_ref,
    eventType: row.event_type,
    title: row.title,
    description: row.description,
    attendees: toStringArray(row.attendees),
    startAt: row.start_at,
    endAt: row.end_at,
    meetingStatus: row.meeting_status,
    agendaSummary: row.agenda_summary,
    notesSummary: row.notes_summary,
    sourceSnapshot: (row.source_snapshot as Record<string, unknown> | null) ?? {},
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapDocumentAssetRow(
  row: DocumentAssetRow & {
    owner?: ProfileLite | null;
    customer?: CustomerLite | null;
  }
): DocumentAsset {
  return {
    id: row.id,
    orgId: row.org_id,
    ownerId: row.owner_id,
    ownerName: row.owner?.display_name ?? "Unknown",
    customerId: row.customer_id,
    customerName: row.customer?.company_name ?? null,
    opportunityId: row.opportunity_id,
    dealRoomId: row.deal_room_id,
    sourceType: row.source_type,
    documentType: row.document_type,
    title: row.title,
    fileName: row.file_name,
    mimeType: row.mime_type,
    storagePath: row.storage_path,
    extractedText: row.extracted_text,
    summary: row.summary,
    tags: toStringArray(row.tags),
    linkedPrepCardId: row.linked_prep_card_id,
    linkedDraftId: row.linked_draft_id,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapExternalTouchpointEventRow(
  row: ExternalTouchpointEventRow & {
    owner?: ProfileLite | null;
    customer?: CustomerLite | null;
  }
): ExternalTouchpointEvent {
  return {
    id: row.id,
    orgId: row.org_id,
    ownerId: row.owner_id,
    ownerName: row.owner?.display_name ?? null,
    customerId: row.customer_id,
    customerName: row.customer?.company_name ?? null,
    opportunityId: row.opportunity_id,
    dealRoomId: row.deal_room_id,
    touchpointType: row.touchpoint_type,
    eventType: row.event_type,
    relatedRefType: row.related_ref_type,
    relatedRefId: row.related_ref_id,
    eventSummary: row.event_summary,
    eventPayload: (row.event_payload as Record<string, unknown> | null) ?? {},
    createdAt: row.created_at
  };
}

export function mapOrgSettingsRow(row: OrgSettingsRow): OrgSettings {
  return {
    id: row.id,
    orgId: row.org_id,
    orgDisplayName: row.org_display_name,
    brandName: row.brand_name,
    industryHint: row.industry_hint,
    timezone: row.timezone,
    locale: row.locale,
    defaultCustomerStages: toStringArray(row.default_customer_stages),
    defaultOpportunityStages: toStringArray(row.default_opportunity_stages),
    defaultAlertRules: ((row.default_alert_rules as Record<string, unknown> | null) ?? {}) as Record<string, number>,
    defaultFollowupSlaDays: row.default_followup_sla_days,
    onboardingCompleted: row.onboarding_completed,
    onboardingStepState: ((row.onboarding_step_state as Record<string, unknown> | null) ?? {}) as Record<string, boolean>,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapOrgFeatureFlagRow(row: OrgFeatureFlagRow): OrgFeatureFlag {
  return {
    id: row.id,
    orgId: row.org_id,
    featureKey: row.feature_key,
    isEnabled: row.is_enabled,
    configJson: (row.config_json as Record<string, unknown> | null) ?? {},
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapOrgAiSettingsRow(row: OrgAiSettingsRow): OrgAiSettings {
  return {
    id: row.id,
    orgId: row.org_id,
    provider: row.provider,
    modelDefault: row.model_default,
    modelReasoning: row.model_reasoning,
    fallbackMode: row.fallback_mode,
    autoAnalysisEnabled: row.auto_analysis_enabled,
    autoPlanEnabled: row.auto_plan_enabled,
    autoBriefEnabled: row.auto_brief_enabled,
    autoTouchpointReviewEnabled: row.auto_touchpoint_review_enabled,
    humanReviewRequiredForSensitiveActions: row.human_review_required_for_sensitive_actions,
    maxDailyAiRuns: row.max_daily_ai_runs,
    maxMonthlyAiRuns: row.max_monthly_ai_runs,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapOrgMembershipRow(
  row: OrgMembershipRow & {
    profile?: ProfileRow | null;
  }
): OrgMembership {
  return {
    id: row.id,
    orgId: row.org_id,
    userId: row.user_id,
    role: row.role,
    seatStatus: row.seat_status,
    invitedBy: row.invited_by,
    invitedAt: row.invited_at,
    joinedAt: row.joined_at,
    lastActiveAt: row.last_active_at,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    userName: row.profile?.display_name,
    userTitle: row.profile?.title ?? undefined,
    profileRole: row.profile?.role ?? undefined
  };
}

export function mapOrgInviteRow(row: OrgInviteRow): OrgInvite {
  return {
    id: row.id,
    orgId: row.org_id,
    email: row.email,
    intendedRole: row.intended_role,
    inviteStatus: row.invite_status,
    inviteToken: row.invite_token,
    invitedBy: row.invited_by,
    expiresAt: row.expires_at,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapOrgUsageCounterRow(row: OrgUsageCounterRow): OrgUsageCounter {
  return {
    id: row.id,
    orgId: row.org_id,
    usageDate: row.usage_date,
    usageScope: row.usage_scope,
    aiRunsCount: row.ai_runs_count,
    prepCardsCount: row.prep_cards_count,
    draftsCount: row.drafts_count,
    reportsCount: row.reports_count,
    touchpointEventsCount: row.touchpoint_events_count,
    documentProcessedCount: row.document_processed_count,
    workPlanGenerationsCount: row.work_plan_generations_count,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapUserUsageCounterRow(
  row: UserUsageCounterRow & {
    profile?: ProfileLite | null;
  }
): UserUsageCounter {
  return {
    id: row.id,
    orgId: row.org_id,
    userId: row.user_id,
    usageDate: row.usage_date,
    usageScope: row.usage_scope,
    aiRunsCount: row.ai_runs_count,
    prepCardsCount: row.prep_cards_count,
    draftsCount: row.drafts_count,
    reportsCount: row.reports_count,
    touchpointEventsCount: row.touchpoint_events_count,
    documentProcessedCount: row.document_processed_count,
    workPlanGenerationsCount: row.work_plan_generations_count,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    userName: row.profile?.display_name
  };
}

export function mapOrgPlanProfileRow(row: OrgPlanProfileRow): OrgPlanProfile {
  return {
    id: row.id,
    orgId: row.org_id,
    planTier: row.plan_tier,
    seatLimit: row.seat_limit,
    aiRunLimitMonthly: row.ai_run_limit_monthly,
    documentLimitMonthly: row.document_limit_monthly,
    touchpointLimitMonthly: row.touchpoint_limit_monthly,
    advancedFeaturesEnabled: row.advanced_features_enabled,
    expiresAt: row.expires_at,
    status: row.status,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapOnboardingRunRow(row: OnboardingRunRow): OnboardingRun {
  return {
    id: row.id,
    orgId: row.org_id,
    initiatedBy: row.initiated_by,
    runType: row.run_type,
    status: row.status,
    summary: row.summary,
    detailSnapshot: (row.detail_snapshot as Record<string, unknown> | null) ?? {},
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapOrgConfigAuditLogRow(row: OrgConfigAuditLogRow): OrgConfigAuditLog {
  return {
    id: row.id,
    orgId: row.org_id,
    actorUserId: row.actor_user_id,
    targetType: row.target_type as OrgConfigAuditLog["targetType"],
    targetId: row.target_id,
    targetKey: row.target_key,
    actionType: row.action_type,
    beforeSummary: (row.before_summary as Record<string, unknown> | null) ?? {},
    afterSummary: (row.after_summary as Record<string, unknown> | null) ?? {},
    diagnosticsSummary: (row.diagnostics_summary as Record<string, unknown> | null) ?? {},
    versionNumber: Number(row.version_number ?? 1),
    versionLabel: row.version_label,
    snapshotSummary: (row.snapshot_summary as Record<string, unknown> | null) ?? {},
    createdAt: row.created_at
  };
}

export function mapImportJobRow(row: ImportJobRowDb): ImportJob {
  return {
    id: row.id,
    orgId: row.org_id,
    initiatedBy: row.initiated_by,
    importType: row.import_type,
    sourceType: row.source_type,
    fileName: row.file_name,
    storagePath: row.storage_path,
    jobStatus: row.job_status,
    totalRows: row.total_rows,
    validRows: row.valid_rows,
    invalidRows: row.invalid_rows,
    duplicateRows: row.duplicate_rows,
    importedRows: row.imported_rows,
    skippedRows: row.skipped_rows,
    mergedRows: row.merged_rows,
    errorRows: row.error_rows,
    summary: row.summary,
    detailSnapshot: (row.detail_snapshot as Record<string, unknown> | null) ?? {},
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapImportJobColumnRow(row: ImportJobColumnRow): ImportJobColumn {
  return {
    id: row.id,
    orgId: row.org_id,
    importJobId: row.import_job_id,
    sourceColumnName: row.source_column_name,
    sourceColumnIndex: row.source_column_index,
    detectedType: row.detected_type,
    mappedTargetEntity: row.mapped_target_entity,
    mappedTargetField: row.mapped_target_field,
    mappingConfidence: row.mapping_confidence,
    normalizationRule: (row.normalization_rule as Record<string, unknown> | null) ?? {},
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapImportJobRowRow(row: ImportJobRowRow): ImportJobRow {
  return {
    id: row.id,
    orgId: row.org_id,
    importJobId: row.import_job_id,
    sourceRowNo: row.source_row_no,
    rawPayload: (row.raw_payload as Record<string, unknown> | null) ?? {},
    normalizedPayload: (row.normalized_payload as Record<string, unknown> | null) ?? {},
    rowStatus: row.row_status,
    validationErrors: toStringArray(row.validation_errors),
    duplicateCandidates: Array.isArray(row.duplicate_candidates) ? (row.duplicate_candidates as Array<Record<string, unknown>>) : [],
    mergeResolution: row.merge_resolution,
    importedEntityType: row.imported_entity_type,
    importedEntityId: row.imported_entity_id,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapImportTemplateRow(row: ImportTemplateRow): ImportTemplate {
  return {
    id: row.id,
    orgId: row.org_id,
    templateName: row.template_name,
    importType: row.import_type,
    columnMapping: (row.column_mapping as Record<string, unknown> | null) ?? {},
    normalizationConfig: (row.normalization_config as Record<string, unknown> | null) ?? {},
    isDefault: row.is_default,
    createdBy: row.created_by,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapDedupeMatchGroupRow(row: DedupeMatchGroupRow): DedupeMatchGroup {
  return {
    id: row.id,
    orgId: row.org_id,
    importJobId: row.import_job_id,
    entityType: row.entity_type,
    sourceRowIds: toStringArray(row.source_row_ids),
    existingEntityIds: toStringArray(row.existing_entity_ids),
    matchReason: row.match_reason,
    confidenceScore: Number(row.confidence_score ?? 0),
    resolutionStatus: row.resolution_status,
    resolutionAction: row.resolution_action,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapImportAuditEventRow(row: ImportAuditEventRow): ImportAuditEvent {
  return {
    id: row.id,
    orgId: row.org_id,
    importJobId: row.import_job_id,
    actorUserId: row.actor_user_id,
    eventType: row.event_type,
    eventSummary: row.event_summary,
    eventPayload: (row.event_payload as Record<string, unknown> | null) ?? {},
    createdAt: row.created_at
  };
}

export function mapMobileDraftSyncJobRow(row: MobileDraftSyncJobRow): MobileDraftSyncJob {
  return {
    id: row.id,
    orgId: row.org_id,
    userId: row.user_id,
    draftType: row.draft_type,
    localDraftId: row.local_draft_id,
    syncStatus: row.sync_status,
    targetEntityType: row.target_entity_type,
    targetEntityId: row.target_entity_id,
    summary: row.summary,
    payloadSnapshot: (row.payload_snapshot as Record<string, unknown> | null) ?? {},
    errorMessage: row.error_message,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapMobileDeviceSessionRow(row: MobileDeviceSessionRow): MobileDeviceSession {
  return {
    id: row.id,
    orgId: row.org_id,
    userId: row.user_id,
    deviceLabel: row.device_label,
    installType: row.install_type,
    lastSeenAt: row.last_seen_at,
    appVersion: row.app_version,
    pushCapable: row.push_capable,
    metadata: (row.metadata as Record<string, unknown> | null) ?? {},
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapOfflineActionQueueRow(row: OfflineActionQueueRow): OfflineActionQueueItem {
  return {
    id: row.id,
    orgId: row.org_id,
    userId: row.user_id,
    actionType: row.action_type,
    actionPayload: (row.action_payload as Record<string, unknown> | null) ?? {},
    queueStatus: row.queue_status,
    targetEntityType: row.target_entity_type,
    targetEntityId: row.target_entity_id,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}
