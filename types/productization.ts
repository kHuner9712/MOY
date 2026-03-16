export type OrgMemberRole = "owner" | "admin" | "manager" | "sales" | "viewer";
export type OrgSeatStatus = "invited" | "active" | "suspended" | "removed";
export type OrgInviteStatus = "pending" | "accepted" | "expired" | "revoked";

export type OrgFeatureKey =
  | "ai_auto_analysis"
  | "ai_auto_planning"
  | "ai_morning_brief"
  | "ai_deal_command"
  | "external_touchpoints"
  | "prep_cards"
  | "playbooks"
  | "manager_quality_view"
  | "outcome_learning"
  | "demo_seed_tools";

export type OrgAiFallbackMode = "strict_provider_first" | "provider_then_rules" | "rules_only";
export type OrgUsageScope = "daily" | "monthly";
export type OrgPlanTier = "demo" | "trial" | "starter" | "growth" | "enterprise";
export type OrgPlanStatus = "active" | "paused" | "expired";
export type OnboardingRunType = "first_time_setup" | "demo_seed" | "trial_bootstrap" | "reinitialize_demo";
export type OnboardingRunStatus = "queued" | "running" | "completed" | "failed";

export interface OrgSettings {
  id: string;
  orgId: string;
  orgDisplayName: string;
  brandName: string;
  industryHint: string | null;
  timezone: string;
  locale: string;
  defaultCustomerStages: string[];
  defaultOpportunityStages: string[];
  defaultAlertRules: Record<string, number>;
  defaultFollowupSlaDays: number;
  onboardingCompleted: boolean;
  onboardingStepState: Record<string, boolean>;
  createdAt: string;
  updatedAt: string;
}

export interface OrgFeatureFlag {
  id: string;
  orgId: string;
  featureKey: OrgFeatureKey;
  isEnabled: boolean;
  configJson: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
}

export interface OrgAiSettings {
  id: string;
  orgId: string;
  provider: "deepseek" | "openai" | "qwen" | "zhipu";
  modelDefault: string;
  modelReasoning: string;
  fallbackMode: OrgAiFallbackMode;
  autoAnalysisEnabled: boolean;
  autoPlanEnabled: boolean;
  autoBriefEnabled: boolean;
  autoTouchpointReviewEnabled: boolean;
  humanReviewRequiredForSensitiveActions: boolean;
  maxDailyAiRuns: number | null;
  maxMonthlyAiRuns: number | null;
  createdAt: string;
  updatedAt: string;
}

export interface OrgMembership {
  id: string;
  orgId: string;
  userId: string;
  role: OrgMemberRole;
  seatStatus: OrgSeatStatus;
  invitedBy: string | null;
  invitedAt: string | null;
  joinedAt: string | null;
  lastActiveAt: string | null;
  createdAt: string;
  updatedAt: string;
  userName?: string;
  userEmail?: string;
  userTitle?: string;
  profileRole?: "sales" | "manager";
}

export interface OrgInvite {
  id: string;
  orgId: string;
  email: string;
  intendedRole: OrgMemberRole;
  inviteStatus: OrgInviteStatus;
  inviteToken: string;
  invitedBy: string | null;
  expiresAt: string;
  createdAt: string;
  updatedAt: string;
}

export interface OrgUsageCounter {
  id: string;
  orgId: string;
  usageDate: string;
  usageScope: OrgUsageScope;
  aiRunsCount: number;
  prepCardsCount: number;
  draftsCount: number;
  reportsCount: number;
  touchpointEventsCount: number;
  documentProcessedCount: number;
  workPlanGenerationsCount: number;
  createdAt: string;
  updatedAt: string;
}

export interface UserUsageCounter {
  id: string;
  orgId: string;
  userId: string;
  usageDate: string;
  usageScope: OrgUsageScope;
  aiRunsCount: number;
  prepCardsCount: number;
  draftsCount: number;
  reportsCount: number;
  touchpointEventsCount: number;
  documentProcessedCount: number;
  workPlanGenerationsCount: number;
  createdAt: string;
  updatedAt: string;
  userName?: string;
}

export interface OrgPlanProfile {
  id: string;
  orgId: string;
  planTier: OrgPlanTier;
  seatLimit: number;
  aiRunLimitMonthly: number;
  documentLimitMonthly: number;
  touchpointLimitMonthly: number;
  advancedFeaturesEnabled: boolean;
  expiresAt: string | null;
  status: OrgPlanStatus;
  createdAt: string;
  updatedAt: string;
}

export interface OnboardingRun {
  id: string;
  orgId: string;
  initiatedBy: string;
  runType: OnboardingRunType;
  status: OnboardingRunStatus;
  summary: string;
  detailSnapshot: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
}

export interface EntitlementStatus {
  planTier: OrgPlanTier;
  status: OrgPlanStatus;
  seatLimit: number;
  seatUsed: number;
  aiRunLimitMonthly: number;
  aiRunUsedMonthly: number;
  documentLimitMonthly: number;
  documentUsedMonthly: number;
  touchpointLimitMonthly: number;
  touchpointUsedMonthly: number;
  remainingAiRunsMonthly: number;
  quotaNearLimit: boolean;
  quotaExceeded: boolean;
  advancedFeaturesEnabled: boolean;
}

export interface OnboardingChecklistItem {
  key: string;
  title: string;
  completed: boolean;
  detail: string;
}

export interface OnboardingChecklist {
  items: OnboardingChecklistItem[];
  completedCount: number;
  totalCount: number;
  progress: number;
  completed: boolean;
}

export interface UsageHealthSummaryResult {
  usageSummary: string;
  hotFeatures: string[];
  underusedFeatures: string[];
  quotaRisks: string[];
  recommendedAdjustments: string[];
}

export interface OnboardingRecommendationResult {
  nextBestSetupSteps: string[];
  missingFoundations: string[];
  recommendedDemoFlow: string[];
  recommendedTeamActions: string[];
  risksIfSkipped: string[];
}

export type IndustryFamily =
  | "generic"
  | "b2b_software"
  | "education_training"
  | "manufacturing"
  | "channel_sales"
  | "consulting_services";

export type IndustryTemplateStatus = "active" | "draft" | "archived";
export type OrgTemplateAssignmentStatus = "active" | "pending_preview" | "archived";
export type TemplateApplyMode = "onboarding_default" | "demo_seed" | "manual_apply" | "trial_bootstrap";
export type TemplateApplyStrategy = "additive_only" | "merge_prefer_existing" | "template_override_existing";

export type ScenarioPackType =
  | "objections"
  | "decision_chain"
  | "quote_strategy"
  | "meeting_goals"
  | "risk_signals"
  | "manager_interventions"
  | "followup_patterns";

export type OrgTemplateOverrideType =
  | "customer_stages"
  | "opportunity_stages"
  | "alert_rules"
  | "checkpoints"
  | "playbook_seed"
  | "prep_preferences"
  | "brief_preferences"
  | "demo_seed_profile";

export type TemplateApplicationRunType = "preview" | "apply" | "reapply" | "demo_seed_apply";
export type TemplateApplicationRunStatus = "queued" | "running" | "completed" | "failed";

export interface IndustryTemplate {
  id: string;
  templateKey: string;
  displayName: string;
  industryFamily: IndustryFamily;
  status: IndustryTemplateStatus;
  summary: string;
  templatePayload: Record<string, unknown>;
  isSystemTemplate: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface ScenarioPack {
  id: string;
  templateId: string;
  packType: ScenarioPackType;
  title: string;
  summary: string;
  packPayload: Record<string, unknown>;
  status: IndustryTemplateStatus;
  createdAt: string;
  updatedAt: string;
}

export interface OrgTemplateAssignment {
  id: string;
  orgId: string;
  templateId: string;
  assignmentStatus: OrgTemplateAssignmentStatus;
  applyMode: TemplateApplyMode;
  applyStrategy: TemplateApplyStrategy;
  appliedBy: string;
  appliedAt: string;
  createdAt: string;
  updatedAt: string;
}

export interface OrgTemplateOverride {
  id: string;
  orgId: string;
  templateId: string;
  overrideType: OrgTemplateOverrideType;
  overridePayload: Record<string, unknown>;
  createdBy: string;
  createdAt: string;
  updatedAt: string;
}

export interface TemplateApplicationRun {
  id: string;
  orgId: string;
  templateId: string;
  initiatedBy: string;
  runType: TemplateApplicationRunType;
  applyMode: TemplateApplyMode;
  applyStrategy: TemplateApplyStrategy;
  status: TemplateApplicationRunStatus;
  summary: string;
  diffSnapshot: Record<string, unknown>;
  resultSnapshot: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
}

export interface SeededPlaybookTemplate {
  id: string;
  templateId: string;
  playbookType: "objection_handling" | "customer_segment" | "quote_strategy" | "meeting_strategy" | "followup_rhythm" | "risk_recovery";
  title: string;
  summary: string;
  payload: Record<string, unknown>;
  status: IndustryTemplateStatus;
  createdAt: string;
  updatedAt: string;
}

export interface ScenarioPackSeed {
  packType: ScenarioPackType;
  title: string;
  summary: string;
  packPayload: Record<string, unknown>;
}

export interface SeededPlaybookTemplateSeed {
  playbookType: "objection_handling" | "customer_segment" | "quote_strategy" | "meeting_strategy" | "followup_rhythm" | "risk_recovery";
  title: string;
  summary: string;
  payload: Record<string, unknown>;
}

export interface IndustryTemplateSeed {
  templateKey: string;
  displayName: string;
  industryFamily: IndustryFamily;
  summary: string;
  templatePayload: Record<string, unknown>;
  scenarioPacks: ScenarioPackSeed[];
  seededPlaybookTemplates: SeededPlaybookTemplateSeed[];
}

export interface TemplateFitRecommendation {
  recommendedTemplateKey: string;
  fitReasons: string[];
  risksOfMismatch: string[];
  recommendedApplyMode: TemplateApplyMode;
  recommendedOverrides: string[];
}

export interface TemplateApplicationSummary {
  whatWillChange: string[];
  whatWillNotChange: string[];
  cautionNotes: string[];
  recommendedNextSteps: string[];
}

export interface IndustryTemplateContext {
  assignment: OrgTemplateAssignment | null;
  template: IndustryTemplate | null;
  scenarioPacks: ScenarioPack[];
  seededPlaybookTemplates: SeededPlaybookTemplate[];
  overrides: OrgTemplateOverride[];
}
