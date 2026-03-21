/**
 * v1.5 Manager Insights Snapshot & Trends Tests
 * 覆盖：weekly/monthly period、signalQuality 分层阈值、isEarlySignal、backfill 结构
 */

import { describe, it, expect } from "vitest";
import type { TruthBand } from "@/types/manager-desk";
import type {
  TruthBandDistributionRecord,
  InterventionStatsRecord,
  RiskImprovementRecord,
  SnapshotType,
} from "@/types/manager-insights-snapshot";

const TRUTH_BAND_LABELS: Record<TruthBand, string> = {
  healthy: "健康",
  watch: "观察",
  suspicious: "可疑",
  stalled: "停滞",
};

type Snapshot = {
  periodStart: string;
  periodEnd: string;
  truthBandDistribution: TruthBandDistributionRecord[];
  interventionStats: InterventionStatsRecord;
  riskImprovement: RiskImprovementRecord;
};

function computeCalendarPeriod(snapshotType: SnapshotType): { periodStart: Date; periodEnd: Date } {
  const now = new Date();
  if (snapshotType === "monthly") {
    const year = now.getFullYear();
    const month = now.getMonth();
    const periodStart = new Date(year, month, 1);
    const periodEnd = new Date(year, month + 1, 0);
    return { periodStart, periodEnd };
  }
  const periodEnd = new Date(now.getTime() - 1);
  const periodStart = new Date(periodEnd.getTime() - 6 * 24 * 60 * 60 * 1000);
  return { periodStart, periodEnd };
}

function computeTruthBandTrends(snapshots: Snapshot[]) {
  const bands: TruthBand[] = ["healthy", "watch", "suspicious", "stalled"];
  const bandLabels: Record<TruthBand, string> = TRUTH_BAND_LABELS;

  if (snapshots.length < 2) {
    return bands.map((band) => ({
      band,
      label: bandLabels[band],
      direction: "stable" as const,
      changeCount: 0,
      changePercentage: 0,
      isEarlySignal: true,
    }));
  }

  const latest = snapshots[0].truthBandDistribution;
  const previous = snapshots[1].truthBandDistribution;

  return bands.map((band) => {
    const latestRec = latest.find((r) => r.band === band);
    const previousRec = previous.find((r) => r.band === band);
    const latestCount = latestRec?.count ?? 0;
    const previousCount = previousRec?.count ?? 0;
    const changeCount = latestCount - previousCount;
    const changePercentage = previousCount > 0 ? (changeCount / previousCount) * 100 : latestCount > 0 ? 100 : 0;

    let direction: "up" | "down" | "stable";
    if (Math.abs(changeCount) <= 1) direction = "stable";
    else if (band === "stalled" || band === "suspicious") direction = changeCount > 0 ? "up" : "down";
    else direction = changeCount > 0 ? "up" : "down";

    return { band, label: bandLabels[band], direction, changeCount, changePercentage, isEarlySignal: snapshots.length < 4 };
  });
}

function computeInterventionTrends(snapshots: Snapshot[]) {
  if (snapshots.length < 2) {
    return {
      createdTrend: "stable" as const,
      completedTrend: "stable" as const,
      completionRateTrend: "stable" as const,
      dismissRateTrend: "stable" as const,
      isEarlySignal: true,
    };
  }

  const latest = snapshots[0].interventionStats;
  const previous = snapshots[1].interventionStats;

  const trend = (curr: number, prev: number): "up" | "down" | "stable" => {
    const diff = curr - prev;
    if (Math.abs(diff) < 1) return "stable";
    return diff > 0 ? "up" : "down";
  };

  return {
    createdTrend: trend(latest.totalCreated, previous.totalCreated),
    completedTrend: trend(latest.totalCompleted, previous.totalCompleted),
    completionRateTrend: trend(latest.completionRate, previous.completionRate),
    dismissRateTrend: trend(latest.dismissRate, previous.dismissRate),
    isEarlySignal: snapshots.length < 4,
  };
}

function computeImprovementTrend(snapshots: Snapshot[]) {
  if (snapshots.length < 2) {
    return { direction: "stable" as const, changeRate: 0, isEarlySignal: true };
  }

  const latest = snapshots[0].riskImprovement.improvementRate;
  const previous = snapshots[1].riskImprovement.improvementRate;
  const changeRate = previous > 0 ? ((latest - previous) / previous) * 100 : 0;

  return {
    direction: Math.abs(changeRate) < 5 ? ("stable" as const) : changeRate > 0 ? ("up" as const) : ("down" as const),
    changeRate,
    isEarlySignal: snapshots.length < 4,
  };
}

function assessSignalQuality(snapshotCount: number, snapshotType: SnapshotType) {
  const MIN_SNAPSHOTS_WEEKLY = 3;
  const SUFFICIENT_SNAPSHOTS_WEEKLY = 12;
  const MIN_SNAPSHOTS_MONTHLY = 2;
  const SUFFICIENT_SNAPSHOTS_MONTHLY = 6;

  if (snapshotType === "monthly") {
    if (snapshotCount >= SUFFICIENT_SNAPSHOTS_MONTHLY) {
      return { signalQuality: "sufficient" as const, signalQualityNote: "月度数据量充足（6+ 个月），趋势分析具有参考价值" };
    }
    if (snapshotCount >= MIN_SNAPSHOTS_MONTHLY) {
      return {
        signalQuality: "early" as const,
        signalQualityNote: `当前仅有 ${snapshotCount} 个月度快照，趋势为早期信号，请结合定性判断参考`,
      };
    }
    return {
      signalQuality: "insufficient" as const,
      signalQualityNote: `月度数据不足（需要至少 ${MIN_SNAPSHOTS_MONTHLY} 个月，当前 ${snapshotCount} 个），请先生成月度快照`,
    };
  }

  if (snapshotCount >= SUFFICIENT_SNAPSHOTS_WEEKLY) {
    return { signalQuality: "sufficient" as const, signalQualityNote: "周数据量充足（12+ 周），趋势分析具有参考价值" };
  }
  if (snapshotCount >= MIN_SNAPSHOTS_WEEKLY) {
    return {
      signalQuality: "early" as const,
      signalQualityNote: `当前仅有 ${snapshotCount} 个周快照，趋势为早期信号，请谨慎参考`,
    };
  }
  return {
    signalQuality: "insufficient" as const,
    signalQualityNote: `周快照数据不足（需要至少 ${MIN_SNAPSHOTS_WEEKLY} 个，当前 ${snapshotCount} 个），趋势暂无参考意义`,
  };
}

describe("Truth Band trend computation", () => {
  it("should compute direction up for stalled increase", () => {
    const snapshots: Snapshot[] = [
      {
        periodStart: "2026-03-14",
        periodEnd: "2026-03-21",
        truthBandDistribution: [
          { band: "healthy" as TruthBand, label: "健康", count: 5, percentage: 0.5 },
          { band: "stalled" as TruthBand, label: "停滞", count: 5, percentage: 0.5 }
        ],
        interventionStats: { totalCreated: 10, totalCompleted: 7, totalDismissed: 2, completionRate: 0.7, dismissRate: 0.2, byType: { coach: 5, escalate: 3, followUp: 1, support: 1 }, byRiskReason: [] },
        riskImprovement: { resolvedBlockedCount: 1, resolvedEscalatedCount: 0, improvementRate: 0.1, notes: "近似" }
      },
      {
        periodStart: "2026-03-07",
        periodEnd: "2026-03-14",
        truthBandDistribution: [
          { band: "healthy" as TruthBand, label: "健康", count: 8, percentage: 0.8 },
          { band: "stalled" as TruthBand, label: "停滞", count: 2, percentage: 0.2 }
        ],
        interventionStats: { totalCreated: 10, totalCompleted: 7, totalDismissed: 2, completionRate: 0.7, dismissRate: 0.2, byType: { coach: 5, escalate: 3, followUp: 1, support: 1 }, byRiskReason: [] },
        riskImprovement: { resolvedBlockedCount: 1, resolvedEscalatedCount: 0, improvementRate: 0.1, notes: "近似" }
      }
    ];
    const trends = computeTruthBandTrends(snapshots);
    const stalledTrend = trends.find((t) => t.band === "stalled")!;
    expect(stalledTrend.direction).toBe("up");
    expect(stalledTrend.changeCount).toBe(3);
  });

  it("should return stable for single snapshot", () => {
    const snapshots: Snapshot[] = [
      {
        periodStart: "2026-03-14",
        periodEnd: "2026-03-21",
        truthBandDistribution: [{ band: "healthy" as TruthBand, label: "健康", count: 10, percentage: 1 }],
        interventionStats: { totalCreated: 10, totalCompleted: 7, totalDismissed: 2, completionRate: 0.7, dismissRate: 0.2, byType: { coach: 5, escalate: 3, followUp: 1, support: 1 }, byRiskReason: [] },
        riskImprovement: { resolvedBlockedCount: 1, resolvedEscalatedCount: 0, improvementRate: 0.1, notes: "近似" }
      }
    ];
    const trends = computeTruthBandTrends(snapshots);
    expect(trends.every((t) => t.direction === "stable")).toBe(true);
    expect(trends.every((t) => t.changeCount === 0)).toBe(true);
    expect(trends.every((t) => t.isEarlySignal)).toBe(true);
  });

  it("should compute correct change percentages", () => {
    const snapshots: Snapshot[] = [
      {
        periodStart: "2026-03-14",
        periodEnd: "2026-03-21",
        truthBandDistribution: [{ band: "healthy" as TruthBand, label: "健康", count: 10, percentage: 1 }],
        interventionStats: { totalCreated: 10, totalCompleted: 7, totalDismissed: 2, completionRate: 0.7, dismissRate: 0.2, byType: { coach: 5, escalate: 3, followUp: 1, support: 1 }, byRiskReason: [] },
        riskImprovement: { resolvedBlockedCount: 1, resolvedEscalatedCount: 0, improvementRate: 0.1, notes: "近似" }
      },
      {
        periodStart: "2026-03-07",
        periodEnd: "2026-03-14",
        truthBandDistribution: [{ band: "healthy" as TruthBand, label: "健康", count: 5, percentage: 0.5 }],
        interventionStats: { totalCreated: 10, totalCompleted: 7, totalDismissed: 2, completionRate: 0.7, dismissRate: 0.2, byType: { coach: 5, escalate: 3, followUp: 1, support: 1 }, byRiskReason: [] },
        riskImprovement: { resolvedBlockedCount: 1, resolvedEscalatedCount: 0, improvementRate: 0.1, notes: "近似" }
      }
    ];
    const trends = computeTruthBandTrends(snapshots);
    const healthyTrend = trends.find((t) => t.band === "healthy")!;
    expect(healthyTrend.changeCount).toBe(5);
    expect(healthyTrend.changePercentage).toBe(100);
  });

  it("should set isEarlySignal false when snapshots >= 4", () => {
    const snapshots: Snapshot[] = Array.from({ length: 4 }, (_, i) => ({
      periodStart: `2026-03-${7 + i * 7}`,
      periodEnd: `2026-03-${14 + i * 7}`,
      truthBandDistribution: [{ band: "healthy" as TruthBand, label: "健康", count: 10, percentage: 1 }],
      interventionStats: { totalCreated: 10, totalCompleted: 7, totalDismissed: 2, completionRate: 0.7, dismissRate: 0.2, byType: { coach: 5, escalate: 3, followUp: 1, support: 1 }, byRiskReason: [] },
      riskImprovement: { resolvedBlockedCount: 1, resolvedEscalatedCount: 0, improvementRate: 0.1, notes: "近似" }
    }));
    const trends = computeTruthBandTrends(snapshots);
    expect(trends.every((t) => !t.isEarlySignal)).toBe(true);
  });
});

describe("Intervention trends computation", () => {
  it("should detect created trend up", () => {
    const snapshots: Snapshot[] = [
      { periodStart: "2026-03-14", periodEnd: "2026-03-21", truthBandDistribution: [], interventionStats: { totalCreated: 15, totalCompleted: 7, totalDismissed: 2, completionRate: 0.7, dismissRate: 0.2, byType: { coach: 5, escalate: 3, followUp: 1, support: 1 }, byRiskReason: [] }, riskImprovement: { resolvedBlockedCount: 1, resolvedEscalatedCount: 0, improvementRate: 0.1, notes: "近似" } },
      { periodStart: "2026-03-07", periodEnd: "2026-03-14", truthBandDistribution: [], interventionStats: { totalCreated: 5, totalCompleted: 3, totalDismissed: 1, completionRate: 0.6, dismissRate: 0.2, byType: { coach: 2, escalate: 2, followUp: 1, support: 0 }, byRiskReason: [] }, riskImprovement: { resolvedBlockedCount: 0, resolvedEscalatedCount: 0, improvementRate: 0, notes: "近似" } }
    ];
    const trends = computeInterventionTrends(snapshots);
    expect(trends.createdTrend).toBe("up");
    expect(trends.completedTrend).toBe("up");
  });

  it("should return stable for minimal change", () => {
    const snapshots: Snapshot[] = [
      { periodStart: "2026-03-14", periodEnd: "2026-03-21", truthBandDistribution: [], interventionStats: { totalCreated: 10, totalCompleted: 7, totalDismissed: 2, completionRate: 0.7, dismissRate: 0.2, byType: { coach: 5, escalate: 3, followUp: 1, support: 1 }, byRiskReason: [] }, riskImprovement: { resolvedBlockedCount: 1, resolvedEscalatedCount: 0, improvementRate: 0.1, notes: "近似" } },
      { periodStart: "2026-03-07", periodEnd: "2026-03-14", truthBandDistribution: [], interventionStats: { totalCreated: 10, totalCompleted: 7, totalDismissed: 2, completionRate: 0.7, dismissRate: 0.2, byType: { coach: 5, escalate: 3, followUp: 1, support: 1 }, byRiskReason: [] }, riskImprovement: { resolvedBlockedCount: 1, resolvedEscalatedCount: 0, improvementRate: 0.1, notes: "近似" } }
    ];
    const trends = computeInterventionTrends(snapshots);
    expect(trends.createdTrend).toBe("stable");
    expect(trends.completionRateTrend).toBe("stable");
  });

  it("should handle zero previous value", () => {
    const snapshots: Snapshot[] = [
      { periodStart: "2026-03-14", periodEnd: "2026-03-21", truthBandDistribution: [], interventionStats: { totalCreated: 10, totalCompleted: 7, totalDismissed: 2, completionRate: 0.7, dismissRate: 0.2, byType: { coach: 5, escalate: 3, followUp: 1, support: 1 }, byRiskReason: [] }, riskImprovement: { resolvedBlockedCount: 1, resolvedEscalatedCount: 0, improvementRate: 0.1, notes: "近似" } },
      { periodStart: "2026-03-07", periodEnd: "2026-03-14", truthBandDistribution: [], interventionStats: { totalCreated: 0, totalCompleted: 0, totalDismissed: 0, completionRate: 0, dismissRate: 0, byType: { coach: 0, escalate: 0, followUp: 0, support: 0 }, byRiskReason: [] }, riskImprovement: { resolvedBlockedCount: 0, resolvedEscalatedCount: 0, improvementRate: 0, notes: "近似" } }
    ];
    const trends = computeInterventionTrends(snapshots);
    expect(trends.createdTrend).toBe("up");
    expect(trends.completionRateTrend).toBe("up");
  });
});

describe("Improvement trend computation", () => {
  it("should detect improvement increase", () => {
    const snapshots: Snapshot[] = [
      { periodStart: "2026-03-14", periodEnd: "2026-03-21", truthBandDistribution: [], interventionStats: { totalCreated: 10, totalCompleted: 7, totalDismissed: 2, completionRate: 0.7, dismissRate: 0.2, byType: { coach: 5, escalate: 3, followUp: 1, support: 1 }, byRiskReason: [] }, riskImprovement: { resolvedBlockedCount: 4, resolvedEscalatedCount: 1, improvementRate: 0.4, notes: "近似" } },
      { periodStart: "2026-03-07", periodEnd: "2026-03-14", truthBandDistribution: [], interventionStats: { totalCreated: 10, totalCompleted: 7, totalDismissed: 2, completionRate: 0.7, dismissRate: 0.2, byType: { coach: 5, escalate: 3, followUp: 1, support: 1 }, byRiskReason: [] }, riskImprovement: { resolvedBlockedCount: 1, resolvedEscalatedCount: 0, improvementRate: 0.1, notes: "近似" } }
    ];
    const trend = computeImprovementTrend(snapshots);
    expect(trend.direction).toBe("up");
    expect(trend.changeRate).toBeCloseTo(300, 0);
  });

  it("should return stable for minimal change", () => {
    const snapshots: Snapshot[] = [
      { periodStart: "2026-03-14", periodEnd: "2026-03-21", truthBandDistribution: [], interventionStats: { totalCreated: 10, totalCompleted: 7, totalDismissed: 2, completionRate: 0.7, dismissRate: 0.2, byType: { coach: 5, escalate: 3, followUp: 1, support: 1 }, byRiskReason: [] }, riskImprovement: { resolvedBlockedCount: 2, resolvedEscalatedCount: 0, improvementRate: 0.2, notes: "近似" } },
      { periodStart: "2026-03-07", periodEnd: "2026-03-14", truthBandDistribution: [], interventionStats: { totalCreated: 10, totalCompleted: 7, totalDismissed: 2, completionRate: 0.7, dismissRate: 0.2, byType: { coach: 5, escalate: 3, followUp: 1, support: 1 }, byRiskReason: [] }, riskImprovement: { resolvedBlockedCount: 1, resolvedEscalatedCount: 0, improvementRate: 0.1, notes: "近似" } }
    ];
    const trend = computeImprovementTrend(snapshots);
    expect(trend.direction).toBe("stable");
  });
});

describe("Signal quality assessment", () => {
  it("should return insufficient for weekly < 3 snapshots", () => {
    const result = assessSignalQuality(2, "weekly");
    expect(result.signalQuality).toBe("insufficient");
    expect(result.signalQualityNote).toContain("周快照数据不足");
  });

  it("should return early for weekly 3-11 snapshots", () => {
    const result = assessSignalQuality(5, "weekly");
    expect(result.signalQuality).toBe("early");
    expect(result.signalQualityNote).toContain("早期信号");
  });

  it("should return sufficient for weekly >= 12 snapshots", () => {
    const result = assessSignalQuality(12, "weekly");
    expect(result.signalQuality).toBe("sufficient");
    expect(result.signalQualityNote).toContain("充足");
  });

  it("should return insufficient for monthly < 2 snapshots", () => {
    const result = assessSignalQuality(1, "monthly");
    expect(result.signalQuality).toBe("insufficient");
    expect(result.signalQualityNote).toContain("月度数据不足");
  });

  it("should return early for monthly 2-5 snapshots", () => {
    const result = assessSignalQuality(3, "monthly");
    expect(result.signalQuality).toBe("early");
    expect(result.signalQualityNote).toContain("早期信号");
  });

  it("should return sufficient for monthly >= 6 snapshots", () => {
    const result = assessSignalQuality(6, "monthly");
    expect(result.signalQuality).toBe("sufficient");
    expect(result.signalQualityNote).toContain("充足");
  });

  it("should differentiate weekly and monthly thresholds", () => {
    const weeklyInsufficient = assessSignalQuality(2, "weekly");
    const monthlyInsufficient = assessSignalQuality(2, "monthly");
    expect(weeklyInsufficient.signalQuality).toBe("insufficient");
    expect(monthlyInsufficient.signalQuality).toBe("insufficient");
    expect(weeklyInsufficient.signalQualityNote).not.toBe(monthlyInsufficient.signalQualityNote);
  });
});

describe("Calendar period calculation", () => {
  it("should calculate weekly period as last 7 days ending yesterday", () => {
    const { periodStart, periodEnd } = computeCalendarPeriod("weekly");
    const now = new Date();
    const diffMs = now.getTime() - periodEnd.getTime();
    const diffDays = diffMs / (1000 * 60 * 60 * 24);
    expect(diffDays).toBeGreaterThan(0);
    expect(diffDays).toBeLessThan(2);
    const periodDays = Math.round((periodEnd.getTime() - periodStart.getTime()) / (24 * 60 * 60 * 1000)) + 1;
    expect(periodDays).toBe(7);
  });

  it("should calculate monthly period as previous calendar month", () => {
    const now = new Date();
    const { periodStart, periodEnd } = computeCalendarPeriod("monthly");
    const expectedPrevMonth = now.getMonth() === 0 ? 11 : now.getMonth() - 1;
    const expectedPrevYear = now.getMonth() === 0 ? now.getFullYear() - 1 : now.getFullYear();
    expect(periodStart.getDate()).toBe(1);
    expect(periodStart.getMonth()).toBe(expectedPrevMonth);
    expect(periodStart.getFullYear()).toBe(expectedPrevYear);
    expect(periodEnd.getMonth()).toBe(expectedPrevMonth);
    expect(periodEnd.getFullYear()).toBe(expectedPrevYear);
    const daysInPrevMonth = new Date(expectedPrevYear, expectedPrevMonth + 1, 0).getDate();
    expect(periodEnd.getDate()).toBe(daysInPrevMonth);
  });

  it("should calculate monthly period never generates future interval", () => {
    const now = new Date();
    const { periodEnd } = computeCalendarPeriod("monthly");
    expect(periodEnd.getTime()).toBeLessThan(now.getTime());
  });

  it("should handle January correctly (previous month is December of previous year)", () => {
    const jan1 = new Date("2026-01-15T12:00:00Z");
    const originalDate = Date;
    jest.useFakeTimers();
    jest.setSystemTime(jan1);
    const { periodStart, periodEnd } = computeCalendarPeriod("monthly");
    expect(periodStart.getFullYear()).toBe(2025);
    expect(periodStart.getMonth()).toBe(11);
    expect(periodEnd.getFullYear()).toBe(2025);
    expect(periodEnd.getMonth()).toBe(11);
    expect(periodEnd.getDate()).toBe(31);
    jest.useRealTimers();
  });

  it("should handle different month lengths (Feb 28/29)", () => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date("2026-03-01T12:00:00Z"));
    const { periodEnd: feb28 } = computeCalendarPeriod("monthly");
    expect(feb28.getDate()).toBe(28);
    expect(feb28.getMonth()).toBe(1);
    jest.setSystemTime(new Date("2025-03-01T12:00:00Z"));
    const { periodEnd: feb29 } = computeCalendarPeriod("monthly");
    expect(feb29.getDate()).toBe(29);
    expect(feb29.getMonth()).toBe(1);
    jest.useRealTimers();
  });

  it("should handle 31-day months correctly", () => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date("2026-04-01T12:00:00Z"));
    const { periodEnd } = computeCalendarPeriod("monthly");
    expect(periodEnd.getDate()).toBe(31);
    expect(periodEnd.getMonth()).toBe(2);
    jest.useRealTimers();
  });

  it("should handle 30-day months correctly", () => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date("2026-06-01T12:00:00Z"));
    const { periodEnd } = computeCalendarPeriod("monthly");
    expect(periodEnd.getDate()).toBe(30);
    expect(periodEnd.getMonth()).toBe(4);
    jest.useRealTimers();
  });

describe("Backfill period correctness", () => {
  it("should backfill monthly starting from previous month", () => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date("2026-04-15T12:00:00Z"));
    const results: Array<{ periodStart: Date; periodEnd: Date }> = [];
    for (let i = 0; i < 3; i++) {
      const targetDate = new Date(2026, 4 - i, 1);
      const year = targetDate.getFullYear();
      const month = targetDate.getMonth();
      results.push({
        periodStart: new Date(year, month, 1),
        periodEnd: new Date(year, month + 1, 0),
      });
    }
    expect(results[0].periodStart.getMonth()).toBe(3);
    expect(results[0].periodEnd.getMonth()).toBe(3);
    expect(results[1].periodStart.getMonth()).toBe(2);
    expect(results[2].periodStart.getMonth()).toBe(1);
    jest.useRealTimers();
  });

  it("should never include current month in monthly backfill", () => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date("2026-04-15T12:00:00Z"));
    for (let i = 0; i < 12; i++) {
      const targetDate = new Date(2026, 4 - i, 1);
      const periodEnd = new Date(targetDate.getFullYear(), targetDate.getMonth() + 1, 0);
      const now = new Date();
      expect(periodEnd.getTime()).toBeLessThan(now.getTime());
    }
    jest.useRealTimers();
  });
});

describe("Period format validation", () => {
  it("should format period as YYYY-MM-DD", () => {
    const { periodStart } = computeCalendarPeriod("weekly");
    const periodStartStr = periodStart.toISOString().slice(0, 10);
    expect(periodStartStr).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });
});

describe("ManagerInsightsTrends result structure", () => {
  it("should have all required fields including isEarlySignal", () => {
    const result = {
      snapshotType: "weekly" as const,
      currentPeriod: "2026-03-14",
      snapshots: [],
      truthBandTrends: [
        { band: "healthy" as TruthBand, label: "健康", direction: "stable" as const, changeCount: 0, changePercentage: 0, isEarlySignal: true }
      ],
      interventionTrends: {
        createdTrend: "stable" as const,
        completedTrend: "stable" as const,
        completionRateTrend: "stable" as const,
        dismissRateTrend: "stable" as const,
        isEarlySignal: true,
      },
      improvementTrend: { direction: "stable" as const, changeRate: 0, isEarlySignal: true },
      signalQuality: "insufficient" as const,
      signalQualityNote: "周快照数据不足"
    };
    expect(result.truthBandTrends).toHaveLength(4);
    expect(result.interventionTrends).toHaveProperty("createdTrend");
    expect(result.interventionTrends).toHaveProperty("isEarlySignal");
    expect(result.improvementTrend).toHaveProperty("isEarlySignal");
    expect(result.improvementTrend).toHaveProperty("direction");
    expect(result).toHaveProperty("signalQuality");
    expect(result).toHaveProperty("signalQualityNote");
  });
});
