/**
 * v1.2 Manager Desk 类型定义
 * 经理作战台相关类型
 */

export type TruthBand = "healthy" | "watch" | "suspicious" | "stalled";

export type RiskLevel = "critical" | "high" | "medium" | "low";

export type ManagerDeskInterventionStatus = "new" | "task_created" | "in_progress" | "completed" | "dismissed";

export interface ManagerRiskItem {
  id: string;
  itemType: "customer" | "opportunity" | "deal_room";
  customerId: string;
  customerName: string;
  opportunityId?: string;
  opportunityName?: string;
  dealRoomId?: string;
  riskReason: string;
  riskLevel: RiskLevel;
  lastActivityAt?: string;
  currentStage?: string;
  ownerId: string;
  ownerName: string;
  priorityScore: number;
  suggestedAction: string;
  truthBand?: TruthBand;
  needsIntervention: boolean;
  interventionReason?: string;
  interventionStatus?: ManagerDeskInterventionStatus;
  linkedWorkItemId?: string;
}

export interface PipelineTruthScore {
  customerId: string;
  customerName: string;
  opportunityId?: string;
  opportunityName?: string;
  truthBand: TruthBand;
  signals: TruthSignal[];
  healthScore: number;
  reason: string;
}

export interface TruthSignal {
  type: "touchpoint" | "next_step" | "stage_advance" | "quote" | "checkpoint" | "stagnation" | "no_contact";
  label: string;
  weight: number;
  isPositive: boolean;
  description: string;
}

export interface ManagerIntervention {
  id: string;
  targetType: "customer" | "opportunity" | "deal_room";
  targetId: string;
  targetName: string;
  ownerId: string;
  ownerName: string;
  interventionType: "coach" | "escalate" | "follow_up" | "support";
  reason: string;
  suggestedAction: string;
  talkingPoints: string[];
  followUpItems: string[];
  priority: RiskLevel;
  truthBand?: TruthBand;
  interventionStatus?: ManagerDeskInterventionStatus;
  linkedWorkItemId?: string;
}

export interface ManagerDeskResult {
  riskQueue: ManagerRiskItem[];
  truthScores: PipelineTruthScore[];
  interventions: ManagerIntervention[];
  summary: {
    totalRisks: number;
    criticalCount: number;
    suspiciousCount: number;
    stalledCount: number;
    needsInterventionCount: number;
  };
}

export interface ManagerDeskFilters {
  ownerId?: string;
  riskLevel?: RiskLevel;
  truthBand?: TruthBand;
  sortBy?: "priority" | "lastActivity" | "stage";
}

export const TRUTH_BAND_LABELS: Record<TruthBand, string> = {
  healthy: "健康",
  watch: "观察",
  suspicious: "可疑",
  stalled: "停滞"
};

export const TRUTH_BAND_COLORS: Record<TruthBand, string> = {
  healthy: "text-emerald-600 bg-emerald-50",
  watch: "text-amber-600 bg-amber-50",
  suspicious: "text-orange-600 bg-orange-50",
  stalled: "text-red-600 bg-red-50"
};

export const RISK_LEVEL_LABELS: Record<RiskLevel, string> = {
  critical: "紧急",
  high: "高",
  medium: "中",
  low: "低"
};

export const RISK_LEVEL_COLORS: Record<RiskLevel, string> = {
  critical: "bg-red-500",
  high: "bg-orange-500",
  medium: "bg-amber-500",
  low: "bg-slate-400"
};
