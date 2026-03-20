/**
 * v1.3 Manager Insights Service
 * 介入效果分析 + Truth Band 分布 + 团队轻聚合
 */

import type { ServerSupabaseClient } from "@/lib/supabase/types";
import type { TruthBand, ManagerDeskInterventionStatus } from "@/types/manager-desk";

export interface ManagerInsightsResult {
  periodStart: string;
  periodEnd: string;
  truthBandDistribution: {
    band: TruthBand;
    label: string;
    count: number;
    percentage: number;
  }[];
  interventionAnalytics: {
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
  };
  riskImprovement: {
    resolvedBlockedCount: number;
    resolvedEscalatedCount: number;
    totalTrackedRooms: number;
    improvementRate: number;
    notes: string;
  };
  newRiskSignals: {
    newCriticalCount: number;
    newHighRiskCount: number;
    newBlockedCount: number;
    newStalledCount: number;
  };
}

const TRUTH_BAND_LABELS: Record<TruthBand, string> = {
  healthy: "健康",
  watch: "观察",
  suspicious: "可疑",
  stalled: "停滞"
};

export class ManagerInsightsService {
  async getInsights(params: {
    supabase: ServerSupabaseClient;
    orgId: string;
    ownerId: string;
    periodDays?: number;
  }): Promise<ManagerInsightsResult> {
    const { supabase, orgId, ownerId, periodDays = 7 } = params;
    const now = new Date();
    const periodStart = new Date(now.getTime() - periodDays * 24 * 60 * 60 * 1000);
    const periodStartIso = periodStart.toISOString();
    const periodEndIso = now.toISOString();
    const thirtyDaysAgo = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000).toISOString();

    const [
      interventionRecordsRes,
      workItemsRes,
      dealRoomsRes,
      businessEventsRes,
      recentBusinessEventsRes
    ] = await Promise.all([
      supabase
        .from("manager_desk_intervention_records")
        .select("resolution_status, risk_reason, resolved_at, work_item_id, resolved_by")
        .eq("org_id", orgId)
        .gte("created_at", periodStartIso),
      supabase
        .from("work_items")
        .select("id, status, work_type, created_at, completed_at, owner_id")
        .eq("org_id", orgId)
        .eq("source_ref_type", "manager_desk_intervention")
        .gte("created_at", periodStartIso),
      supabase
        .from("deal_rooms")
        .select("id, room_status, updated_at, created_at")
        .eq("org_id", orgId)
        .in("room_status", ["active", "watchlist", "escalated", "blocked"]),
      supabase
        .from("business_events")
        .select("id, event_type, severity, status, created_at")
        .eq("org_id", orgId)
        .in("severity", ["critical", "high"])
        .gte("created_at", thirtyDaysAgo),
      supabase
        .from("business_events")
        .select("id, event_type, severity, status, created_at")
        .eq("org_id", orgId)
        .in("severity", ["critical", "high"])
        .gte("created_at", periodStartIso)
    ]);

    const interventions = (interventionRecordsRes.data ?? []) as Array<{
      resolution_status: string;
      risk_reason: string | null;
      resolved_at: string;
      work_item_id: string | null;
      resolved_by: string | null;
    }>;
    const workItems = (workItemsRes.data ?? []) as Array<{
      id: string;
      status: string;
      work_type: string | null;
      created_at: string;
      completed_at: string | null;
      owner_id: string;
    }>;
    const dealRooms = (dealRoomsRes.data ?? []) as Array<{
      id: string;
      room_status: string;
      updated_at: string;
      created_at: string;
    }>;
    const recentEvents = (recentBusinessEventsRes.data ?? []) as Array<{
      id: string;
      event_type: string;
      severity: string;
      status: string;
      created_at: string;
    }>;

    const completedInterventions = interventions.filter(i => i.resolution_status === "completed").length;
    const dismissedInterventions = interventions.filter(i => i.resolution_status === "dismissed").length;
    const totalTracked = interventions.length;
    const completionRate = totalTracked > 0 ? completedInterventions / totalTracked : 0;
    const dismissRate = totalTracked > 0 ? dismissedInterventions / totalTracked : 0;

    const riskReasonMap = new Map<string, number>();
    for (const i of interventions) {
      if (i.risk_reason) {
        riskReasonMap.set(i.risk_reason, (riskReasonMap.get(i.risk_reason) ?? 0) + 1);
      }
    }
    const byRiskReason = Array.from(riskReasonMap.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, 6)
      .map(([reason, count]) => ({ reason, count }));

    const totalWorkItemsCreated = workItems.length;
    const totalWorkItemsCompleted = workItems.filter(w => w.status === "done").length;
    const byType = {
      coach: workItems.filter(w => w.work_type === "manager_checkin").length,
      escalate: workItems.filter(w => w.work_type === "revive_stalled_deal").length,
      followUp: workItems.filter(w => w.work_type === "follow_up").length,
      support: workItems.filter(w => w.work_type === "support").length
    };

    const resolvedBlocked = dealRooms.filter(
      r => r.room_status !== "blocked" &&
        new Date(r.updated_at) >= new Date(periodStartIso)
    ).length;
    const totalBlocked = dealRooms.filter(r => r.room_status === "blocked").length;
    const resolvedEscalated = dealRooms.filter(
      r => r.room_status !== "escalated" &&
        new Date(r.updated_at) >= new Date(periodStartIso)
    ).length;
    const improvementRate = totalBlocked > 0 ? resolvedBlocked / totalBlocked : 0;

    const newCriticalCount = recentEvents.filter(e => e.severity === "critical" && e.status === "open").length;
    const newHighRiskCount = recentEvents.filter(e => e.severity === "high" && e.status === "open").length;

    const truthBandDistribution = this.computeTruthBandDistribution(dealRooms);

    const newBlockedCount = dealRooms.filter(
      r => r.room_status === "blocked" &&
        new Date(r.created_at) >= new Date(periodStartIso)
    ).length;
    const newStalledCount = dealRooms.filter(
      r => {
        const updatedDate = new Date(r.updated_at).getTime();
        const daysSince = (now.getTime() - updatedDate) / (1000 * 60 * 60 * 24);
        return daysSince > 30 && new Date(r.created_at) >= new Date(periodStartIso);
      }
    ).length;

    return {
      periodStart: periodStartIso.slice(0, 10),
      periodEnd: periodEndIso.slice(0, 10),
      truthBandDistribution,
      interventionAnalytics: {
        totalCreated: totalWorkItemsCreated,
        totalCompleted: totalWorkItemsCompleted,
        totalDismissed: dismissedInterventions,
        completionRate,
        dismissRate,
        byType,
        byRiskReason
      },
      riskImprovement: {
        resolvedBlockedCount: resolvedBlocked,
        resolvedEscalatedCount: resolvedEscalated,
        totalTrackedRooms: dealRooms.length,
        improvementRate,
        notes: "风险下降 = 周期内从 blocked/escalated 状态变更为 active/watchlist 的房间数 / 原有 blocked 数（近似口径，非严格因果归因）"
      },
      newRiskSignals: {
        newCriticalCount,
        newHighRiskCount,
        newBlockedCount,
        newStalledCount
      }
    };
  }

  private computeTruthBandDistribution(
    dealRooms: Array<{ id: string; room_status: string; updated_at: string }>
  ): ManagerInsightsResult["truthBandDistribution"] {
    const now = Date.now();
    const bands: Record<TruthBand, number> = { healthy: 0, watch: 0, suspicious: 0, stalled: 0 };
    const STALL_THRESHOLD = 30;
    const SUSPICIOUS_THRESHOLD = 14;
    const WATCH_THRESHOLD = 7;

    for (const room of dealRooms) {
      if (room.room_status === "blocked" || room.room_status === "escalated") {
        bands.stalled++;
        continue;
      }
      const updatedMs = new Date(room.updated_at).getTime();
      const daysSince = (now - updatedMs) / (1000 * 60 * 60 * 24);
      if (daysSince > STALL_THRESHOLD) bands.stalled++;
      else if (daysSince > SUSPICIOUS_THRESHOLD) bands.suspicious++;
      else if (daysSince > WATCH_THRESHOLD) bands.watch++;
      else bands.healthy++;
    }

    const total = dealRooms.length;
    return (["healthy", "watch", "suspicious", "stalled"] as TruthBand[]).map(band => ({
      band,
      label: TRUTH_BAND_LABELS[band],
      count: bands[band],
      percentage: total > 0 ? bands[band] / total : 0
    }));
  }
}

export const managerInsightsService = new ManagerInsightsService();
