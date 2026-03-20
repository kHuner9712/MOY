/**
 * v1.4 Manager Insights Snapshot 类型定义
 */

import type { TruthBand } from "@/types/manager-desk";

export type SnapshotType = "weekly" | "monthly";

export interface TruthBandDistributionRecord {
  band: TruthBand;
  label: string;
  count: number;
  percentage: number;
}

export interface InterventionStatsRecord {
  totalCreated: number;
  totalCompleted: number;
  totalDismissed: number;
  completionRate: number;
  dismissRate: number;
  byType: {
    coach: number;
    escalate: number;
    followUp: number;
    support: number;
  };
  byRiskReason: Array<{ reason: string; count: number }>;
}

export interface RiskSignalsRecord {
  newCriticalCount: number;
  newHighRiskCount: number;
  newBlockedCount: number;
  newStalledCount: number;
  totalActiveRooms: number;
}

export interface RiskImprovementRecord {
  resolvedBlockedCount: number;
  resolvedEscalatedCount: number;
  improvementRate: number;
  notes: string;
}

export interface ManagerInsightsSnapshot {
  id: string;
  orgId: string;
  periodStart: string;
  periodEnd: string;
  snapshotType: SnapshotType;
  truthBandDistribution: TruthBandDistributionRecord[];
  interventionStats: InterventionStatsRecord;
  riskSignals: RiskSignalsRecord;
  riskImprovement: RiskImprovementRecord;
  notes?: string;
  createdAt: string;
}

export interface ManagerInsightsTrends {
  snapshotType: SnapshotType;
  currentPeriod: string;
  snapshots: Array<{
    periodStart: string;
    periodEnd: string;
    truthBandDistribution: TruthBandDistributionRecord[];
    interventionStats: InterventionStatsRecord;
    riskImprovement: RiskImprovementRecord;
  }>;
  truthBandTrends: {
    band: TruthBand;
    label: string;
    direction: "up" | "down" | "stable";
    changeCount: number;
    changePercentage: number;
    isEarlySignal: boolean;
  }[];
  interventionTrends: {
    createdTrend: "up" | "down" | "stable";
    completedTrend: "up" | "down" | "stable";
    completionRateTrend: "up" | "down" | "stable";
    dismissRateTrend: "up" | "down" | "stable";
    isEarlySignal: boolean;
  };
  improvementTrend: {
    direction: "up" | "down" | "stable";
    changeRate: number;
    isEarlySignal: boolean;
  };
  signalQuality: "sufficient" | "early" | "insufficient";
  signalQualityNote: string;
}

export interface BackfillSnapshotResult {
  snapshotType: SnapshotType;
  totalRequested: number;
  createdCount: number;
  skippedCount: number;
  errorCount: number;
  results: Array<{
    periodStart: string;
    periodEnd: string;
    snapshotId: string;
    status: "created" | "skipped" | "error";
    error?: string;
  }>;
}

export interface CreateSnapshotResult {
  snapshotId: string;
  periodStart: string;
  periodEnd: string;
  snapshotType: SnapshotType;
  createdAt: string;
}
