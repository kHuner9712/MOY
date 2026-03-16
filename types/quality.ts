export type QualityPeriodType = "daily" | "weekly" | "monthly";
export type CoachingReportScope = "user" | "team";
export type CoachingReportStatus = "generating" | "completed" | "failed";

export interface BehaviorQualitySnapshot {
  id: string;
  orgId: string;
  userId: string;
  snapshotDate: string;
  periodType: QualityPeriodType;
  assignedCustomerCount: number;
  activeCustomerCount: number;
  followupCount: number;
  onTimeFollowupRate: number;
  overdueFollowupRate: number;
  followupCompletenessScore: number;
  stageProgressionScore: number;
  riskResponseScore: number;
  highValueFocusScore: number;
  activityQualityScore: number;
  shallowActivityRatio: number;
  stalledCustomerCount: number;
  highRiskUnhandledCount: number;
  summary: string;
  metricsSnapshot: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
}

export interface CoachingReport {
  id: string;
  orgId: string;
  reportScope: CoachingReportScope;
  targetUserId: string | null;
  periodStart: string;
  periodEnd: string;
  status: CoachingReportStatus;
  title: string;
  executiveSummary: string;
  strengths: string[];
  weaknesses: string[];
  coachingActions: string[];
  replicablePatterns: string[];
  riskWarnings: string[];
  contentMarkdown: string;
  sourceSnapshot: Record<string, unknown>;
  generatedBy: string;
  createdAt: string;
  updatedAt: string;
}
