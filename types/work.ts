export type WorkItemSourceType =
  | "alert"
  | "followup_due"
  | "ai_suggested"
  | "manager_assigned"
  | "report_generated"
  | "draft_confirmation"
  | "manual";

export type WorkType =
  | "followup_call"
  | "send_quote"
  | "confirm_decision_maker"
  | "schedule_demo"
  | "prepare_proposal"
  | "revive_stalled_deal"
  | "resolve_alert"
  | "confirm_capture_draft"
  | "review_customer"
  | "manager_checkin";

export type WorkPriorityBand = "low" | "medium" | "high" | "critical";
export type WorkItemStatus = "todo" | "in_progress" | "done" | "snoozed" | "cancelled";
export type DailyPlanStatus = "draft" | "active" | "completed" | "archived";
export type PlanTimeBlock = "early_morning" | "morning" | "noon" | "afternoon" | "evening";
export type TaskActionType =
  | "created"
  | "reprioritized"
  | "started"
  | "completed"
  | "snoozed"
  | "cancelled"
  | "converted_to_followup"
  | "converted_to_alert_resolution"
  | "marked_blocked";

export type WorkAgentRunScope = "user_daily_plan" | "manager_team_plan" | "alert_reprioritization" | "weekly_task_review";
export type WorkAgentRunStatus = "queued" | "running" | "completed" | "failed";

export interface WorkItem {
  id: string;
  orgId: string;
  ownerId: string;
  ownerName: string;
  customerId: string | null;
  customerName: string | null;
  opportunityId: string | null;
  sourceType: WorkItemSourceType;
  workType: WorkType;
  title: string;
  description: string;
  rationale: string;
  priorityScore: number;
  priorityBand: WorkPriorityBand;
  status: WorkItemStatus;
  scheduledFor: string | null;
  dueAt: string | null;
  completedAt: string | null;
  snoozedUntil: string | null;
  sourceRefType: string | null;
  sourceRefId: string | null;
  aiGenerated: boolean;
  aiRunId: string | null;
  createdBy: string;
  createdAt: string;
  updatedAt: string;
}

export interface DailyWorkPlan {
  id: string;
  orgId: string;
  userId: string;
  planDate: string;
  status: DailyPlanStatus;
  summary: string | null;
  totalItems: number;
  criticalItems: number;
  focusTheme: string | null;
  sourceSnapshot: Record<string, unknown>;
  generatedBy: string;
  createdAt: string;
  updatedAt: string;
}

export interface DailyWorkPlanItem {
  id: string;
  orgId: string;
  planId: string;
  workItemId: string;
  sequenceNo: number;
  plannedTimeBlock: PlanTimeBlock | null;
  recommendationReason: string;
  mustDo: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface TaskExecutionLog {
  id: string;
  orgId: string;
  workItemId: string;
  userId: string;
  actionType: TaskActionType;
  actionNote: string | null;
  beforeSnapshot: Record<string, unknown>;
  afterSnapshot: Record<string, unknown>;
  createdAt: string;
}

export interface WorkAgentRun {
  id: string;
  orgId: string;
  userId: string | null;
  runScope: WorkAgentRunScope;
  status: WorkAgentRunStatus;
  inputSnapshot: Record<string, unknown>;
  outputSnapshot: Record<string, unknown>;
  parsedResult: Record<string, unknown>;
  provider: string | null;
  model: string | null;
  resultSource: "provider" | "fallback";
  fallbackReason: string | null;
  errorMessage: string | null;
  startedAt: string | null;
  completedAt: string | null;
  createdAt: string;
}

export interface TodayPlanView {
  plan: DailyWorkPlan;
  planItems: DailyWorkPlanItem[];
  workItems: WorkItem[];
  latestRun: WorkAgentRun | null;
  usedFallback: boolean;
}

export interface TeamRhythmUserRow {
  userId: string;
  userName: string;
  todoCount: number;
  inProgressCount: number;
  doneCount: number;
  overdueCount: number;
  criticalOpenCount: number;
  completionRate: number;
  overdueRate: number;
  backlogScore: number;
}
