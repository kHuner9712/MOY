import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import { scoreToHealthBand } from "@/lib/automation-ops";
import { buildFallbackCustomerHealthSummary } from "@/lib/executive-fallback";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { getActivePromptVersion } from "@/services/ai-prompt-service";
import { createAiRun, updateAiRunStatus } from "@/services/ai-run-service";
import type { CustomerHealthSnapshot, CustomerHealthSummaryResult } from "@/types/automation";
import { customerHealthSummaryResultSchema } from "@/types/ai";

type DbClient = ServerSupabaseClient;

interface CustomerRow {
  id: string;
  org_id: string;
  owner_id: string;
  company_name: string;
  current_stage: string;
  risk_level: "low" | "medium" | "high";
  win_probability: number;
  last_followup_at: string | null;
  next_followup_at: string | null;
  created_at: string;
  updated_at: string;
}

interface CustomerHealthSnapshotRow {
  id: string;
  org_id: string;
  customer_id: string;
  snapshot_date: string;
  lifecycle_type: CustomerHealthSnapshot["lifecycleType"];
  activity_score: number;
  engagement_score: number;
  progression_score: number;
  retention_score: number;
  expansion_score: number;
  overall_health_score: number;
  health_band: CustomerHealthSnapshot["healthBand"];
  risk_flags: string[] | null;
  positive_signals: string[] | null;
  summary: string | null;
  created_at: string;
  updated_at: string;
}

function mapHealthRow(row: CustomerHealthSnapshotRow, customerName?: string, ownerId?: string): CustomerHealthSnapshot {
  return {
    id: row.id,
    orgId: row.org_id,
    customerId: row.customer_id,
    customerName,
    customerOwnerId: ownerId,
    snapshotDate: row.snapshot_date,
    lifecycleType: row.lifecycle_type,
    activityScore: Number(row.activity_score ?? 0),
    engagementScore: Number(row.engagement_score ?? 0),
    progressionScore: Number(row.progression_score ?? 0),
    retentionScore: Number(row.retention_score ?? 0),
    expansionScore: Number(row.expansion_score ?? 0),
    overallHealthScore: Number(row.overall_health_score ?? 0),
    healthBand: row.health_band,
    riskFlags: Array.isArray(row.risk_flags) ? row.risk_flags : [],
    positiveSignals: Array.isArray(row.positive_signals) ? row.positive_signals : [],
    summary: row.summary,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

function daysSince(iso: string | null): number {
  if (!iso) return 999;
  const diff = Date.now() - new Date(iso).getTime();
  return Math.max(0, Math.floor(diff / (24 * 60 * 60 * 1000)));
}

function deriveLifecycleType(customer: CustomerRow): CustomerHealthSnapshot["lifecycleType"] {
  if (customer.current_stage === "won") return "active_customer";
  if (customer.current_stage === "lost") return "prospect";
  return "prospect";
}

function computeHealthBase(params: {
  customer: CustomerRow;
  followupCount30d: number;
  touchpointCount30d: number;
  openAlerts: number;
  blockedCheckpoints: number;
}): {
  activityScore: number;
  engagementScore: number;
  progressionScore: number;
  retentionScore: number;
  expansionScore: number;
  overallHealthScore: number;
  riskFlags: string[];
  positiveSignals: string[];
  healthBand: CustomerHealthSnapshot["healthBand"];
} {
  const riskFlags: string[] = [];
  const positiveSignals: string[] = [];

  const daysNoFollowup = daysSince(params.customer.last_followup_at);
  let activityScore = 80;
  if (daysNoFollowup > 3) activityScore -= 15;
  if (daysNoFollowup > 7) activityScore -= 20;
  if (daysNoFollowup > 14) activityScore -= 20;
  if (params.followupCount30d >= 4) activityScore += 10;
  if (params.touchpointCount30d >= 3) activityScore += 8;
  activityScore = Math.max(0, Math.min(100, activityScore));

  let engagementScore = 50 + Math.min(30, params.touchpointCount30d * 6) + Math.min(20, params.followupCount30d * 3);
  if (params.openAlerts > 2) engagementScore -= 15;
  engagementScore = Math.max(0, Math.min(100, engagementScore));

  let progressionScore = 55;
  if (["proposal", "negotiation", "won"].includes(params.customer.current_stage)) progressionScore += 15;
  if (params.customer.current_stage === "won") progressionScore += 20;
  if (params.blockedCheckpoints > 0) progressionScore -= 20;
  progressionScore = Math.max(0, Math.min(100, progressionScore));

  let retentionScore = params.customer.current_stage === "won" ? 68 : 52;
  if (params.openAlerts === 0) retentionScore += 10;
  if (daysNoFollowup > 10) retentionScore -= 15;
  retentionScore = Math.max(0, Math.min(100, retentionScore));

  let expansionScore = params.customer.win_probability >= 75 ? 70 : 50;
  if (params.customer.risk_level === "low") expansionScore += 12;
  if (params.customer.risk_level === "high") expansionScore -= 18;
  expansionScore = Math.max(0, Math.min(100, expansionScore));

  let overall = Math.round(activityScore * 0.23 + engagementScore * 0.22 + progressionScore * 0.25 + retentionScore * 0.2 + expansionScore * 0.1);
  if (params.customer.risk_level === "high") overall -= 10;
  if (params.blockedCheckpoints > 0) overall -= 8;
  overall = Math.max(0, Math.min(100, overall));

  if (daysNoFollowup > 7) riskFlags.push("followup_inactive");
  if (params.openAlerts > 0) riskFlags.push("open_alerts_present");
  if (params.blockedCheckpoints > 0) riskFlags.push("blocked_checkpoint_present");
  if (params.customer.risk_level === "high") riskFlags.push("customer_risk_high");

  if (params.followupCount30d >= 4) positiveSignals.push("followup_cadence_active");
  if (params.touchpointCount30d >= 3) positiveSignals.push("external_touchpoint_active");
  if (["proposal", "negotiation", "won"].includes(params.customer.current_stage)) positiveSignals.push("pipeline_stage_progressed");

  return {
    activityScore,
    engagementScore,
    progressionScore,
    retentionScore,
    expansionScore,
    overallHealthScore: overall,
    riskFlags,
    positiveSignals,
    healthBand: scoreToHealthBand(overall)
  };
}

async function runCustomerHealthSummary(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  customer: CustomerRow;
  base: ReturnType<typeof computeHealthBase>;
  contextSnapshot: Record<string, unknown>;
}): Promise<{
  summary: CustomerHealthSummaryResult;
  usedFallback: boolean;
  fallbackReason: string | null;
}> {
  const fallback = buildFallbackCustomerHealthSummary({
    customerName: params.customer.company_name,
    healthScore: params.base.overallHealthScore,
    healthBand: params.base.healthBand,
    riskFlags: params.base.riskFlags,
    positiveSignals: params.base.positiveSignals
  });

  const provider = getAiProvider();
  const model = provider.getDefaultModel({ reasoning: false });
  const prompt = await getActivePromptVersion({
    supabase: params.supabase,
    orgId: params.orgId,
    scenario: "customer_health_summary",
    providerId: provider.id
  });

  const run = await createAiRun({
    supabase: params.supabase,
    orgId: params.orgId,
    customerId: params.customer.id,
    triggeredByUserId: params.actorUserId,
    triggerSource: "manager_review",
    scenario: "customer_health_summary",
    provider: provider.id,
    model,
    promptVersion: prompt.version,
    inputSnapshot: params.contextSnapshot
  });

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "running",
    startedAt: new Date().toISOString()
  });

  let summary = fallback;
  let usedFallback = false;
  let fallbackReason: string | null = null;
  let outputSnapshot: Record<string, unknown> = {
    fallback: true,
    reason: "not_started"
  };
  let responseModel = model;

  try {
    if (!provider.isConfigured()) throw new Error("provider_not_configured");

    const response = await provider.chatCompletion({
      scenario: "customer_health_summary",
      model,
      systemPrompt: prompt.systemPrompt,
      developerPrompt: `${prompt.developerPrompt}\n\nOutput schema:\n${JSON.stringify(prompt.outputSchema)}`,
      userPrompt: JSON.stringify(params.contextSnapshot),
      jsonMode: true,
      strictMode: true
    });

    if (response.error) throw new Error(response.error);
    const candidate = response.parsedJson ?? (response.rawText ? JSON.parse(response.rawText) : null);
    const parsed = customerHealthSummaryResultSchema.safeParse(candidate);
    if (!parsed.success) throw new Error("customer_health_summary_schema_invalid");

    summary = {
      healthSummary: parsed.data.health_summary,
      riskFlags: parsed.data.risk_flags,
      positiveSignals: parsed.data.positive_signals,
      recommendedActions: parsed.data.recommended_actions
    };
    outputSnapshot = response.rawResponse;
    responseModel = response.model;
  } catch (error) {
    if (!isRuleFallbackEnabled()) {
      const message = error instanceof Error ? error.message : "customer_health_summary_failed";
      await updateAiRunStatus({
        supabase: params.supabase,
        runId: run.id,
        status: "failed",
        provider: provider.id,
        model,
        errorMessage: message,
        completedAt: new Date().toISOString()
      });
      throw error;
    }
    usedFallback = true;
    fallbackReason = error instanceof Error ? error.message : "customer_health_summary_fallback";
    outputSnapshot = {
      fallback: true,
      reason: fallbackReason,
      payload: fallback
    };
    responseModel = "rule-fallback";
  }

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "completed",
    provider: provider.id,
    model: responseModel,
    outputSnapshot,
    parsedResult: {
      health_summary: summary.healthSummary,
      risk_flags: summary.riskFlags,
      positive_signals: summary.positiveSignals,
      recommended_actions: summary.recommendedActions
    },
    resultSource: usedFallback ? "fallback" : "provider",
    fallbackReason,
    errorMessage: usedFallback ? fallbackReason : null,
    completedAt: new Date().toISOString()
  });

  return {
    summary,
    usedFallback,
    fallbackReason
  };
}

export async function refreshCustomerHealthSnapshots(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  customerIds?: string[];
}): Promise<{
  snapshots: CustomerHealthSnapshot[];
  processed: number;
}> {
  let customerQuery = (params.supabase as any)
    .from("customers")
    .select("id,org_id,owner_id,company_name,current_stage,risk_level,win_probability,last_followup_at,next_followup_at,created_at,updated_at")
    .eq("org_id", params.orgId)
    .order("updated_at", { ascending: false })
    .limit(300);

  if (params.customerIds?.length) customerQuery = customerQuery.in("id", params.customerIds);
  const customerRes = await customerQuery;
  if (customerRes.error) throw new Error(customerRes.error.message);

  const customers = (customerRes.data ?? []) as CustomerRow[];
  const snapshots: CustomerHealthSnapshot[] = [];

  for (const customer of customers) {
    const [followupRes, touchpointRes, alertRes, blockedCheckpointRes] = await Promise.all([
      (params.supabase as any)
        .from("followups")
        .select("id", { count: "exact", head: true })
        .eq("org_id", params.orgId)
        .eq("customer_id", customer.id)
        .gte("created_at", new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString()),
      (params.supabase as any)
        .from("external_touchpoint_events")
        .select("id", { count: "exact", head: true })
        .eq("org_id", params.orgId)
        .eq("customer_id", customer.id)
        .gte("created_at", new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString()),
      (params.supabase as any)
        .from("alerts")
        .select("id", { count: "exact", head: true })
        .eq("org_id", params.orgId)
        .eq("customer_id", customer.id)
        .in("status", ["open", "watching"]),
      (params.supabase as any)
        .from("deal_checkpoints")
        .select("id", { count: "exact", head: true })
        .eq("org_id", params.orgId)
        .eq("status", "blocked")
        .in(
          "deal_room_id",
          (
            (
              await (params.supabase as any)
                .from("deal_rooms")
                .select("id")
                .eq("org_id", params.orgId)
                .eq("customer_id", customer.id)
            ).data ?? []
          ).map((item: { id: string }) => item.id)
        )
    ]);

    if (followupRes.error) throw new Error(followupRes.error.message);
    if (touchpointRes.error) throw new Error(touchpointRes.error.message);
    if (alertRes.error) throw new Error(alertRes.error.message);
    if (blockedCheckpointRes.error) throw new Error(blockedCheckpointRes.error.message);

    const base = computeHealthBase({
      customer,
      followupCount30d: followupRes.count ?? 0,
      touchpointCount30d: touchpointRes.count ?? 0,
      openAlerts: alertRes.count ?? 0,
      blockedCheckpoints: blockedCheckpointRes.count ?? 0
    });

    const contextSnapshot = {
      customer: {
        id: customer.id,
        company_name: customer.company_name,
        stage: customer.current_stage,
        risk_level: customer.risk_level,
        win_probability: customer.win_probability,
        last_followup_at: customer.last_followup_at,
        next_followup_at: customer.next_followup_at
      },
      metrics: {
        followup_count_30d: followupRes.count ?? 0,
        touchpoint_count_30d: touchpointRes.count ?? 0,
        open_alerts: alertRes.count ?? 0,
        blocked_checkpoints: blockedCheckpointRes.count ?? 0
      },
      base_assessment: base
    };

    const aiSummary = await runCustomerHealthSummary({
      supabase: params.supabase,
      orgId: params.orgId,
      actorUserId: params.actorUserId,
      customer,
      base,
      contextSnapshot
    });

    const snapshotDate = new Date().toISOString().slice(0, 10);
    const upsertRes = await (params.supabase as any)
      .from("customer_health_snapshots")
      .upsert(
        {
          org_id: params.orgId,
          customer_id: customer.id,
          snapshot_date: snapshotDate,
          lifecycle_type: deriveLifecycleType(customer),
          activity_score: base.activityScore,
          engagement_score: base.engagementScore,
          progression_score: base.progressionScore,
          retention_score: base.retentionScore,
          expansion_score: base.expansionScore,
          overall_health_score: base.overallHealthScore,
          health_band: base.healthBand,
          risk_flags: aiSummary.summary.riskFlags,
          positive_signals: aiSummary.summary.positiveSignals,
          summary: aiSummary.summary.healthSummary
        },
        { onConflict: "org_id,customer_id,snapshot_date" }
      )
      .select("*")
      .single();

    if (upsertRes.error) throw new Error(upsertRes.error.message);

    snapshots.push(
      mapHealthRow(upsertRes.data as CustomerHealthSnapshotRow, customer.company_name, customer.owner_id)
    );
  }

  return {
    snapshots,
    processed: snapshots.length
  };
}

export async function listLatestCustomerHealthSnapshots(params: {
  supabase: DbClient;
  orgId: string;
  ownerId?: string;
  healthBands?: CustomerHealthSnapshot["healthBand"][];
  limit?: number;
}): Promise<CustomerHealthSnapshot[]> {
  const customerRes = await (params.supabase as any)
    .from("customers")
    .select("id,company_name,owner_id")
    .eq("org_id", params.orgId)
    .order("updated_at", { ascending: false })
    .limit(1000);
  if (customerRes.error) throw new Error(customerRes.error.message);
  const customerMap = new Map<string, { company_name: string; owner_id: string }>();
  for (const row of (customerRes.data ?? []) as Array<{ id: string; company_name: string; owner_id: string }>) {
    customerMap.set(row.id, { company_name: row.company_name, owner_id: row.owner_id });
  }

  let query = (params.supabase as any)
    .from("customer_health_snapshots")
    .select("*")
    .eq("org_id", params.orgId)
    .order("snapshot_date", { ascending: false })
    .order("created_at", { ascending: false })
    .limit(1500);
  if (params.healthBands?.length) query = query.in("health_band", params.healthBands);

  const res = await query;
  if (res.error) throw new Error(res.error.message);

  const latestByCustomer = new Map<string, CustomerHealthSnapshot>();
  for (const row of (res.data ?? []) as CustomerHealthSnapshotRow[]) {
    if (latestByCustomer.has(row.customer_id)) continue;
    const customer = customerMap.get(row.customer_id);
    if (params.ownerId && customer?.owner_id !== params.ownerId) continue;
    latestByCustomer.set(
      row.customer_id,
      mapHealthRow(row, customer?.company_name, customer?.owner_id)
    );
  }

  return Array.from(latestByCustomer.values())
    .sort((a, b) => b.overallHealthScore - a.overallHealthScore)
    .slice(0, params.limit ?? 200);
}

export async function getCustomerLatestHealthSnapshot(params: {
  supabase: DbClient;
  orgId: string;
  customerId: string;
}): Promise<CustomerHealthSnapshot | null> {
  const res = await (params.supabase as any)
    .from("customer_health_snapshots")
    .select("*")
    .eq("org_id", params.orgId)
    .eq("customer_id", params.customerId)
    .order("snapshot_date", { ascending: false })
    .order("created_at", { ascending: false })
    .limit(1)
    .maybeSingle();
  if (res.error) throw new Error(res.error.message);
  if (!res.data) return null;

  const customerRes = await (params.supabase as any)
    .from("customers")
    .select("company_name,owner_id")
    .eq("org_id", params.orgId)
    .eq("id", params.customerId)
    .maybeSingle();
  if (customerRes.error) throw new Error(customerRes.error.message);

  return mapHealthRow(
    res.data as CustomerHealthSnapshotRow,
    customerRes.data?.company_name,
    customerRes.data?.owner_id
  );
}
