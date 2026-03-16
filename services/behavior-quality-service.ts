import { computeBehaviorQualityMetrics } from "@/lib/behavior-quality";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { mapBehaviorQualitySnapshotRow } from "@/services/mappers";
import type { Database } from "@/types/database";
import type { BehaviorQualitySnapshot, QualityPeriodType } from "@/types/quality";

type DbClient = ServerSupabaseClient;
type SnapshotRow = Database["public"]["Tables"]["behavior_quality_snapshots"]["Row"];

function toSummary(metrics: ReturnType<typeof computeBehaviorQualityMetrics>): string {
  const qualityBand =
    metrics.activityQualityScore >= 80 ? "高质量推进" : metrics.activityQualityScore >= 60 ? "中等质量推进" : "推进质量偏弱";
  return `${qualityBand}：准时率 ${(metrics.onTimeFollowupRate * 100).toFixed(0)}%，浅层忙碌占比 ${(metrics.shallowActivityRatio * 100).toFixed(
    0
  )}%，高风险未处理 ${metrics.highRiskUnhandledCount} 个。`;
}

export async function compileBehaviorQualitySnapshot(params: {
  supabase: DbClient;
  orgId: string;
  userId: string;
  periodType: QualityPeriodType;
  periodStart: string;
  periodEnd: string;
}): Promise<BehaviorQualitySnapshot> {
  const [customersRes, followupsRes, opportunitiesRes, alertsRes] = await Promise.all([
    params.supabase.from("customers").select("id, current_stage, win_probability, last_followup_at, next_followup_at, created_at").eq("org_id", params.orgId).eq("owner_id", params.userId),
    params.supabase
      .from("followups")
      .select("id, customer_id, created_at, summary, customer_needs, objections, next_step, next_followup_at, draft_status, ai_summary")
      .eq("org_id", params.orgId)
      .eq("owner_id", params.userId),
    params.supabase.from("opportunities").select("id, customer_id, stage, updated_at").eq("org_id", params.orgId).eq("owner_id", params.userId),
    params.supabase.from("alerts").select("id, customer_id, severity, status, created_at, updated_at").eq("org_id", params.orgId).eq("owner_id", params.userId)
  ]);

  if (customersRes.error) throw new Error(customersRes.error.message);
  if (followupsRes.error) throw new Error(followupsRes.error.message);
  if (opportunitiesRes.error) throw new Error(opportunitiesRes.error.message);
  if (alertsRes.error) throw new Error(alertsRes.error.message);

  const metrics = computeBehaviorQualityMetrics({
    periodStart: params.periodStart,
    periodEnd: params.periodEnd,
    customers: (customersRes.data ?? []).map((item: any) => ({
      id: item.id,
      stage: item.current_stage,
      winProbability: Number(item.win_probability ?? 0),
      lastFollowupAt: item.last_followup_at ?? item.created_at,
      nextFollowupAt: item.next_followup_at ?? item.created_at
    })),
    followups: (followupsRes.data ?? []).map((item: any) => ({
      id: item.id,
      customerId: item.customer_id,
      createdAt: item.created_at,
      summary: item.summary ?? "",
      customerNeeds: item.customer_needs ?? "",
      objections: item.objections ?? "",
      nextPlan: item.next_step ?? "",
      nextFollowupAt: item.next_followup_at,
      draftStatus: item.draft_status,
      aiSummary: item.ai_summary
    })),
    opportunities: (opportunitiesRes.data ?? []).map((item: any) => ({
      id: item.id,
      customerId: item.customer_id,
      stage: item.stage,
      updatedAt: item.updated_at
    })),
    alerts: (alertsRes.data ?? []).map((item: any) => ({
      id: item.id,
      customerId: item.customer_id,
      severity: item.severity,
      status: item.status,
      createdAt: item.created_at,
      updatedAt: item.updated_at
    }))
  });

  const summary = toSummary(metrics);

  const upsertPayload: Database["public"]["Tables"]["behavior_quality_snapshots"]["Insert"] = {
    org_id: params.orgId,
    user_id: params.userId,
    snapshot_date: params.periodEnd,
    period_type: params.periodType,
    assigned_customer_count: metrics.assignedCustomerCount,
    active_customer_count: metrics.activeCustomerCount,
    followup_count: metrics.followupCount,
    on_time_followup_rate: metrics.onTimeFollowupRate,
    overdue_followup_rate: metrics.overdueFollowupRate,
    followup_completeness_score: metrics.followupCompletenessScore,
    stage_progression_score: metrics.stageProgressionScore,
    risk_response_score: metrics.riskResponseScore,
    high_value_focus_score: metrics.highValueFocusScore,
    activity_quality_score: metrics.activityQualityScore,
    shallow_activity_ratio: metrics.shallowActivityRatio,
    stalled_customer_count: metrics.stalledCustomerCount,
    high_risk_unhandled_count: metrics.highRiskUnhandledCount,
    summary,
    metrics_snapshot: {
      period_start: params.periodStart,
      period_end: params.periodEnd,
      ...metrics
    }
  };

  const { data: upserted, error: upsertError } = await params.supabase
    .from("behavior_quality_snapshots")
    .upsert(upsertPayload, {
      onConflict: "org_id,user_id,snapshot_date,period_type"
    })
    .select("*")
    .single();

  if (upsertError || !upserted) {
    throw new Error(upsertError?.message ?? "Failed to upsert behavior quality snapshot");
  }

  return mapBehaviorQualitySnapshotRow(upserted as SnapshotRow);
}

export async function listQualitySnapshots(params: {
  supabase: DbClient;
  orgId: string;
  periodType?: QualityPeriodType;
  userId?: string;
  limit?: number;
}): Promise<BehaviorQualitySnapshot[]> {
  let query = params.supabase
    .from("behavior_quality_snapshots")
    .select("*")
    .eq("org_id", params.orgId)
    .order("snapshot_date", { ascending: false })
    .limit(params.limit ?? 60);

  if (params.periodType) {
    query = query.eq("period_type", params.periodType);
  }
  if (params.userId) {
    query = query.eq("user_id", params.userId);
  }

  const { data, error } = await query;
  if (error) throw new Error(error.message);

  const rows = (data ?? []) as SnapshotRow[];
  return rows.map((item) => mapBehaviorQualitySnapshotRow(item));
}
