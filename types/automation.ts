export type AutomationRuleScope =
  | "customer_health"
  | "deal_progress"
  | "trial_conversion"
  | "onboarding"
  | "retention"
  | "external_touchpoint"
  | "manager_attention";

export type AutomationTriggerType = "threshold" | "inactivity" | "missing_step" | "health_score" | "event_sequence";
export type AutomationRuleSeverity = "info" | "warning" | "critical";
export type AutomationRuleRunStatus = "running" | "completed" | "failed";

export type BusinessEventEntityType =
  | "customer"
  | "opportunity"
  | "deal_room"
  | "trial_org"
  | "work_item"
  | "onboarding_run"
  | "touchpoint";

export type BusinessEventType =
  | "first_value_reached"
  | "health_declined"
  | "renewal_risk_detected"
  | "expansion_signal"
  | "trial_stalled"
  | "trial_activated"
  | "onboarding_stuck"
  | "deal_blocked"
  | "no_recent_touchpoint"
  | "manager_attention_escalated"
  | "renewal_due_soon"
  | "conversion_signal";

export type BusinessEventSeverity = "info" | "warning" | "critical";
export type BusinessEventStatus = "open" | "acknowledged" | "resolved" | "ignored";

export type CustomerLifecycleType = "prospect" | "active_customer" | "trial_customer" | "renewing_customer";
export type CustomerHealthBand = "healthy" | "watch" | "at_risk" | "critical";

export type ExecutiveBriefType = "executive_daily" | "executive_weekly" | "retention_watch" | "trial_watch" | "deal_watch";
export type ExecutiveBriefStatus = "generating" | "completed" | "failed";

export type RenewalWatchStatus = "watch" | "due_soon" | "at_risk" | "expansion_candidate" | "renewed" | "churned";

export interface AutomationRule {
  id: string;
  orgId: string;
  ruleKey: string;
  ruleName: string;
  ruleScope: AutomationRuleScope;
  triggerType: AutomationTriggerType;
  conditionsJson: Record<string, unknown>;
  actionJson: Record<string, unknown>;
  severity: AutomationRuleSeverity;
  isEnabled: boolean;
  createdBy: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface AutomationRuleRun {
  id: string;
  orgId: string;
  ruleId: string;
  runStatus: AutomationRuleRunStatus;
  matchedCount: number;
  createdActionCount: number;
  summary: string | null;
  detailSnapshot: Record<string, unknown>;
  startedAt: string;
  completedAt: string | null;
  createdAt: string;
}

export interface BusinessEvent {
  id: string;
  orgId: string;
  entityType: BusinessEventEntityType;
  entityId: string;
  eventType: BusinessEventType;
  severity: BusinessEventSeverity;
  eventSummary: string;
  eventPayload: Record<string, unknown>;
  status: BusinessEventStatus;
  createdAt: string;
  updatedAt: string;
}

export interface CustomerHealthSnapshot {
  id: string;
  orgId: string;
  customerId: string;
  customerName?: string;
  customerOwnerId?: string;
  snapshotDate: string;
  lifecycleType: CustomerLifecycleType;
  activityScore: number;
  engagementScore: number;
  progressionScore: number;
  retentionScore: number;
  expansionScore: number;
  overallHealthScore: number;
  healthBand: CustomerHealthBand;
  riskFlags: string[];
  positiveSignals: string[];
  summary: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface ExecutiveBrief {
  id: string;
  orgId: string;
  briefType: ExecutiveBriefType;
  targetUserId: string | null;
  status: ExecutiveBriefStatus;
  headline: string | null;
  summary: string | null;
  briefPayload: Record<string, unknown>;
  sourceSnapshot: Record<string, unknown>;
  aiRunId: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface RenewalWatchItem {
  id: string;
  orgId: string;
  customerId: string;
  customerName?: string;
  ownerId: string | null;
  ownerName?: string;
  renewalStatus: RenewalWatchStatus;
  renewalDueAt: string | null;
  productScope: string | null;
  healthSnapshotId: string | null;
  recommendationSummary: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface CustomerHealthSummaryResult {
  healthSummary: string;
  riskFlags: string[];
  positiveSignals: string[];
  recommendedActions: string[];
}

export interface ExecutiveBriefSummaryResult {
  headline: string;
  topRisks: string[];
  topOpportunities: string[];
  suggestedActions: string[];
  watchItems: string[];
}

export interface AutomationActionRecommendationResult {
  whyItMatters: string;
  suggestedAction: string;
  urgency: "low" | "medium" | "high";
  ownerHint: string;
}

export interface RetentionWatchReviewResult {
  atRiskCustomers: string[];
  expansionCandidates: string[];
  recommendedRetentionMoves: string[];
  recommendedOwnerActions: string[];
}

export interface ExecutiveCockpitSummary {
  openEvents: number;
  criticalRisks: number;
  trialStalled: number;
  dealBlocked: number;
  renewalAtRisk: number;
  managerAttentionRequired: number;
  healthBandDistribution: Array<{ band: CustomerHealthBand; count: number }>;
  dealHealth: {
    strategicDeals: number;
    blockedCheckpoints: number;
    managerAttentionDeals: number;
  };
  trialHealth: {
    activated: number;
    onboardingCompleted: number;
    firstValue: number;
    conversionRisk: number;
  };
  teamExecution: {
    overdueWork: number;
    followupTimelinessScore: number;
    shallowActivityRatio: number;
  };
  recentRuleRuns: AutomationRuleRun[];
  recentEvents: BusinessEvent[];
  recommendations: string[];
}

export interface AutomationRuleSeed {
  ruleKey: string;
  ruleName: string;
  ruleScope: AutomationRuleScope;
  triggerType: AutomationTriggerType;
  severity: AutomationRuleSeverity;
  conditionsJson: Record<string, unknown>;
  actionJson: Record<string, unknown>;
}
