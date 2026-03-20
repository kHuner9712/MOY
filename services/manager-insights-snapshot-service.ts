/**
 * v1.5 Manager Insights Snapshot Service
 * 周/月快照生成 + 历史趋势读取
 * 复用 managerInsightsService 的指标逻辑
 * v1.5 新增：calendar-based monthly period、backfill、enhanced signalQuality
 */

import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { managerInsightsService } from "@/services/manager-insights-service";
import type {
  ManagerInsightsSnapshot,
  ManagerInsightsTrends,
  SnapshotType,
  TruthBandDistributionRecord,
  InterventionStatsRecord,
  RiskSignalsRecord,
  RiskImprovementRecord,
  CreateSnapshotResult,
  BackfillSnapshotResult,
} from "@/types/manager-insights-snapshot";
import type { TruthBand } from "@/types/manager-desk";

const MIN_SNAPSHOTS_WEEKLY = 3;
const SUFFICIENT_SNAPSHOTS_WEEKLY = 12;
const MIN_SNAPSHOTS_MONTHLY = 2;
const SUFFICIENT_SNAPSHOTS_MONTHLY = 6;

/**
 * 计算指定 snapshotType 的周期范围（calendar-based）
 * weekly: 最近7天（昨天结束）
 * monthly: 上一个完整自然月（避免生成"未来区间"数据）
 */
function computeCalendarPeriod(snapshotType: SnapshotType): { periodStart: Date; periodEnd: Date } {
  const now = new Date();
  if (snapshotType === "monthly") {
    const prevMonth = new Date(now.getFullYear(), now.getMonth() - 1, 1);
    const year = prevMonth.getFullYear();
    const month = prevMonth.getMonth();
    const periodStart = new Date(year, month, 1);
    const periodEnd = new Date(year, month + 1, 0);
    return { periodStart, periodEnd };
  }
  const periodEnd = new Date(now.getTime() - 1);
  const periodStart = new Date(periodEnd.getTime() - 6 * 24 * 60 * 60 * 1000);
  return { periodStart, periodEnd };
}

export class ManagerInsightsSnapshotService {
  async generateSnapshot(params: {
    supabase: ServerSupabaseClient;
    orgId: string;
    periodDays?: number;
    snapshotType?: SnapshotType;
    periodStart?: string;
    periodEnd?: string;
  }): Promise<CreateSnapshotResult> {
    const { supabase, orgId, periodDays = 7, snapshotType = "weekly", periodStart: periodStartOverride, periodEnd: periodEndOverride } = params;

    let periodStartStr: string;
    let periodEndStr: string;
    let actualPeriodDays: number;

    if (periodStartOverride && periodEndOverride) {
      periodStartStr = periodStartOverride;
      periodEndStr = periodEndOverride;
      const start = new Date(periodStartOverride);
      const end = new Date(periodEndOverride);
      actualPeriodDays = Math.round((end.getTime() - start.getTime()) / (24 * 60 * 60 * 1000)) + 1;
    } else {
      const { periodStart, periodEnd } = computeCalendarPeriod(snapshotType);
      periodStartStr = periodStart.toISOString().slice(0, 10);
      periodEndStr = periodEnd.toISOString().slice(0, 10);
      actualPeriodDays = snapshotType === "monthly"
        ? Math.round((periodEnd.getTime() - periodStart.getTime()) / (24 * 60 * 60 * 1000)) + 1
        : periodDays;
    }

    const existing = await supabase
      .from("manager_insights_snapshots")
      .select("id")
      .eq("org_id", orgId)
      .eq("snapshot_type", snapshotType)
      .eq("period_start", periodStartStr)
      .eq("period_end", periodEndStr)
      .limit(1)
      .maybeSingle();

    const insights = await managerInsightsService.getInsights({
      supabase,
      orgId,
      ownerId: "",
      periodDays: actualPeriodDays,
    });

    const now = new Date();

    if (existing?.data) {
      const { data: updated } = await supabase
        .from("manager_insights_snapshots")
        .update({
          truth_band_distribution: insights.truthBandDistribution as unknown as Record<string, unknown>,
          intervention_stats: insights.interventionAnalytics as unknown as Record<string, unknown>,
          risk_signals: insights.newRiskSignals as unknown as Record<string, unknown>,
          risk_improvement: insights.riskImprovement as unknown as Record<string, unknown>,
        })
        .eq("id", existing.data.id)
        .select("id")
        .single();

      return {
        snapshotId: updated?.id ?? (existing.data.id as string),
        periodStart: periodStartStr,
        periodEnd: periodEndStr,
        snapshotType,
        createdAt: now.toISOString(),
      };
    }

    const { data: inserted, error } = await supabase
      .from("manager_insights_snapshots")
      .insert({
        org_id: orgId,
        period_start: periodStartStr,
        period_end: periodEndStr,
        snapshot_type: snapshotType,
        truth_band_distribution: insights.truthBandDistribution as unknown as Record<string, unknown>,
        intervention_stats: insights.interventionAnalytics as unknown as Record<string, unknown>,
        risk_signals: insights.newRiskSignals as unknown as Record<string, unknown>,
        risk_improvement: insights.riskImprovement as unknown as Record<string, unknown>,
      })
      .select("id")
      .single();

    if (error || !inserted) {
      throw new Error(error?.message ?? "Failed to create snapshot");
    }

    return {
      snapshotId: inserted.id as string,
      periodStart: periodStartStr,
      periodEnd: periodEndStr,
      snapshotType,
      createdAt: now.toISOString(),
    };
  }

  async backfillHistoricalSnapshots(params: {
    supabase: ServerSupabaseClient;
    orgId: string;
    snapshotType: SnapshotType;
    periodsToBackfill: number;
  }): Promise<BackfillSnapshotResult> {
    const { supabase, orgId, snapshotType, periodsToBackfill } = params;
    const results: Array<{ periodStart: string; periodEnd: string; snapshotId: string; status: "created" | "skipped" | "error"; error?: string }> = [];
    const now = new Date();

    for (let i = 0; i < periodsToBackfill; i++) {
      let targetDate: Date;
      if (snapshotType === "monthly") {
        targetDate = new Date(now.getFullYear(), now.getMonth() - i, 1);
      } else {
        targetDate = new Date(now.getTime() - i * 7 * 24 * 60 * 60 * 1000);
      }

      let periodStart: Date;
      let periodEnd: Date;

      if (snapshotType === "monthly") {
        const year = targetDate.getFullYear();
        const month = targetDate.getMonth();
        periodStart = new Date(year, month, 1);
        periodEnd = new Date(year, month + 1, 0);
      } else {
        periodEnd = new Date(now.getTime() - (i * 7 + 1) * 24 * 60 * 60 * 1000);
        periodStart = new Date(periodEnd.getTime() - 6 * 24 * 60 * 60 * 1000);
      }

      const periodStartStr = periodStart.toISOString().slice(0, 10);
      const periodEndStr = periodEnd.toISOString().slice(0, 10);

      try {
        const existing = await supabase
          .from("manager_insights_snapshots")
          .select("id")
          .eq("org_id", orgId)
          .eq("snapshot_type", snapshotType)
          .eq("period_start", periodStartStr)
          .eq("period_end", periodEndStr)
          .limit(1)
          .maybeSingle();

        if (existing?.data) {
          results.push({ periodStart: periodStartStr, periodEnd: periodEndStr, snapshotId: existing.data.id as string, status: "skipped" });
          continue;
        }

        const actualPeriodDays = snapshotType === "monthly"
          ? Math.round((periodEnd.getTime() - periodStart.getTime()) / (24 * 60 * 60 * 1000)) + 1
          : 7;

        const insights = await managerInsightsService.getInsights({
          supabase,
          orgId,
          ownerId: "",
          periodDays: actualPeriodDays,
        });

        const { data: inserted, error } = await supabase
          .from("manager_insights_snapshots")
          .insert({
            org_id: orgId,
            period_start: periodStartStr,
            period_end: periodEndStr,
            snapshot_type: snapshotType,
            truth_band_distribution: insights.truthBandDistribution as unknown as Record<string, unknown>,
            intervention_stats: insights.interventionAnalytics as unknown as Record<string, unknown>,
            risk_signals: insights.newRiskSignals as unknown as Record<string, unknown>,
            risk_improvement: insights.riskImprovement as unknown as Record<string, unknown>,
          })
          .select("id")
          .single();

        if (error || !inserted) {
          results.push({ periodStart: periodStartStr, periodEnd: periodEndStr, snapshotId: "", status: "error", error: error?.message ?? "Insert failed" });
        } else {
          results.push({ periodStart: periodStartStr, periodEnd: periodEndStr, snapshotId: inserted.id as string, status: "created" });
        }
      } catch (err) {
        results.push({ periodStart: periodStartStr, periodEnd: periodEndStr, snapshotId: "", status: "error", error: err instanceof Error ? err.message : "Unknown error" });
      }
    }

    return {
      snapshotType,
      totalRequested: periodsToBackfill,
      createdCount: results.filter((r) => r.status === "created").length,
      skippedCount: results.filter((r) => r.status === "skipped").length,
      errorCount: results.filter((r) => r.status === "error").length,
      results,
    };
  }

  async getTrends(params: {
    supabase: ServerSupabaseClient;
    orgId: string;
    snapshotType?: SnapshotType;
    periods?: number;
  }): Promise<ManagerInsightsTrends> {
    const { supabase, orgId, snapshotType = "weekly", periods = 8 } = params;

    const { data, error } = await supabase
      .from("manager_insights_snapshots")
      .select("*")
      .eq("org_id", orgId)
      .eq("snapshot_type", snapshotType)
      .order("period_start", { ascending: false })
      .limit(periods);

    if (error) throw new Error(error.message);

    const snapshots = (data ?? []) as Array<{
      period_start: string;
      period_end: string;
      truth_band_distribution: TruthBandDistributionRecord[];
      intervention_stats: InterventionStatsRecord;
      risk_improvement: RiskImprovementRecord;
    }>;

    const now = new Date();
    const periodStartStr = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);

    const snapshotTrends = snapshots.map((s) => ({
      periodStart: s.period_start,
      periodEnd: s.period_end,
      truthBandDistribution: s.truth_band_distribution as TruthBandDistributionRecord[],
      interventionStats: s.intervention_stats as InterventionStatsRecord,
      riskImprovement: s.risk_improvement as RiskImprovementRecord,
    }));

    const truthBandTrends = this.computeTruthBandTrends(snapshotTrends);
    const interventionTrends = this.computeInterventionTrends(snapshotTrends);
    const improvementTrend = this.computeImprovementTrend(snapshotTrends);

    const { signalQuality, signalQualityNote } = this.assessSignalQuality(snapshots.length, snapshotType);

    const currentPeriod = snapshots.length > 0 ? snapshots[0].period_start : "";

    return {
      snapshotType,
      currentPeriod,
      snapshots: snapshotTrends,
      truthBandTrends,
      interventionTrends,
      improvementTrend,
      signalQuality,
      signalQualityNote,
    };
  }

  private computeTruthBandTrends(
    snapshots: Array<{
      periodStart: string;
      periodEnd: string;
      truthBandDistribution: TruthBandDistributionRecord[];
      interventionStats: InterventionStatsRecord;
      riskImprovement: RiskImprovementRecord;
    }>
  ) {
    const bands: TruthBand[] = ["healthy", "watch", "suspicious", "stalled"];
    const bandLabels: Record<TruthBand, string> = {
      healthy: "健康",
      watch: "观察",
      suspicious: "可疑",
      stalled: "停滞",
    };

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

      return {
        band,
        label: bandLabels[band],
        direction,
        changeCount,
        changePercentage,
        isEarlySignal: snapshots.length < 4,
      };
    });
  }

  private computeInterventionTrends(
    snapshots: Array<{
      periodStart: string;
      periodEnd: string;
      truthBandDistribution: TruthBandDistributionRecord[];
      interventionStats: InterventionStatsRecord;
      riskImprovement: RiskImprovementRecord;
    }>
  ) {
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

  private computeImprovementTrend(
    snapshots: Array<{
      periodStart: string;
      periodEnd: string;
      truthBandDistribution: TruthBandDistributionRecord[];
      interventionStats: InterventionStatsRecord;
      riskImprovement: RiskImprovementRecord;
    }>
  ) {
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

  private assessSignalQuality(snapshotCount: number, snapshotType: SnapshotType): {
    signalQuality: "sufficient" | "early" | "insufficient";
    signalQualityNote: string;
  } {
    const minWeekly = MIN_SNAPSHOTS_WEEKLY;
    const sufficientWeekly = SUFFICIENT_SNAPSHOTS_WEEKLY;
    const minMonthly = MIN_SNAPSHOTS_MONTHLY;
    const sufficientMonthly = SUFFICIENT_SNAPSHOTS_MONTHLY;

    if (snapshotType === "monthly") {
      if (snapshotCount >= sufficientMonthly) {
        return {
          signalQuality: "sufficient",
          signalQualityNote: "月度数据量充足（6+ 个月），趋势分析具有参考价值",
        };
      }
      if (snapshotCount >= minMonthly) {
        return {
          signalQuality: "early",
          signalQualityNote: `当前仅有 ${snapshotCount} 个月度快照，趋势为早期信号，请结合定性判断参考`,
        };
      }
      return {
        signalQuality: "insufficient",
        signalQualityNote: `月度数据不足（需要至少 ${minMonthly} 个月，当前 ${snapshotCount} 个），请先生成月度快照`,
      };
    }

    if (snapshotCount >= sufficientWeekly) {
      return {
        signalQuality: "sufficient",
        signalQualityNote: "周数据量充足（12+ 周），趋势分析具有参考价值",
      };
    }
    if (snapshotCount >= minWeekly) {
      return {
        signalQuality: "early",
        signalQualityNote: `当前仅有 ${snapshotCount} 个周快照，趋势为早期信号，请谨慎参考`,
      };
    }
    return {
      signalQuality: "insufficient",
      signalQualityNote: `周快照数据不足（需要至少 ${minWeekly} 个，当前 ${snapshotCount} 个），趋势暂无参考意义`,
    };
  }
}

export const managerInsightsSnapshotService = new ManagerInsightsSnapshotService();
