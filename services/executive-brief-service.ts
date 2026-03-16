import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import { buildFallbackExecutiveBriefSummary } from "@/lib/executive-fallback";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { getActivePromptVersion } from "@/services/ai-prompt-service";
import { createAiRun, updateAiRunStatus } from "@/services/ai-run-service";
import { listBusinessEvents } from "@/services/business-event-service";
import { listLatestCustomerHealthSnapshots } from "@/services/customer-health-service";
import { listRenewalWatchItems } from "@/services/renewal-watch-service";
import type { ExecutiveBrief, ExecutiveBriefSummaryResult } from "@/types/automation";
import { executiveBriefSummaryResultSchema } from "@/types/ai";

type DbClient = ServerSupabaseClient;

interface ExecutiveBriefRow {
  id: string;
  org_id: string;
  brief_type: ExecutiveBrief["briefType"];
  target_user_id: string | null;
  status: ExecutiveBrief["status"];
  headline: string | null;
  summary: string | null;
  brief_payload: Record<string, unknown> | null;
  source_snapshot: Record<string, unknown> | null;
  ai_run_id: string | null;
  created_at: string;
  updated_at: string;
}

function asRecord(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function mapRow(row: ExecutiveBriefRow): ExecutiveBrief {
  return {
    id: row.id,
    orgId: row.org_id,
    briefType: row.brief_type,
    targetUserId: row.target_user_id,
    status: row.status,
    headline: row.headline,
    summary: row.summary,
    briefPayload: asRecord(row.brief_payload),
    sourceSnapshot: asRecord(row.source_snapshot),
    aiRunId: row.ai_run_id,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export async function listExecutiveBriefs(params: {
  supabase: DbClient;
  orgId: string;
  targetUserId?: string;
  briefTypes?: ExecutiveBrief["briefType"][];
  limit?: number;
}): Promise<ExecutiveBrief[]> {
  let query = (params.supabase as any)
    .from("executive_briefs")
    .select("*")
    .eq("org_id", params.orgId)
    .order("created_at", { ascending: false })
    .limit(params.limit ?? 50);
  if (params.targetUserId) query = query.eq("target_user_id", params.targetUserId);
  if (params.briefTypes?.length) query = query.in("brief_type", params.briefTypes);

  const res = await query;
  if (res.error) throw new Error(res.error.message);
  return ((res.data ?? []) as ExecutiveBriefRow[]).map(mapRow);
}

export async function generateExecutiveBrief(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  briefType: ExecutiveBrief["briefType"];
  targetUserId?: string | null;
  ownerId?: string;
}): Promise<{
  brief: ExecutiveBrief;
  usedFallback: boolean;
  fallbackReason: string | null;
}> {
  const [events, health, renewals] = await Promise.all([
    listBusinessEvents({
      supabase: params.supabase,
      orgId: params.orgId,
      statuses: ["open", "acknowledged"],
      ownerId: params.ownerId,
      limit: 200
    }),
    listLatestCustomerHealthSnapshots({
      supabase: params.supabase,
      orgId: params.orgId,
      ownerId: params.ownerId,
      limit: 200
    }),
    listRenewalWatchItems({
      supabase: params.supabase,
      orgId: params.orgId,
      ownerId: params.ownerId,
      limit: 200
    })
  ]);

  const criticalRisks = events.filter((item) => item.severity === "critical").length;
  const trialStalled = events.filter((item) => item.eventType === "trial_stalled" && item.status !== "resolved").length;
  const dealBlocked = events.filter((item) => item.eventType === "deal_blocked" && item.status !== "resolved").length;
  const renewalAtRisk = renewals.filter((item) => item.renewalStatus === "at_risk").length;

  const fallback = buildFallbackExecutiveBriefSummary({
    openEvents: events.length,
    criticalRisks,
    trialStalled,
    dealBlocked,
    renewalAtRisk
  });

  const inputSnapshot = {
    brief_type: params.briefType,
    events: events.slice(0, 80).map((item) => ({
      id: item.id,
      event_type: item.eventType,
      severity: item.severity,
      summary: item.eventSummary,
      status: item.status
    })),
    health_band_distribution: {
      healthy: health.filter((item) => item.healthBand === "healthy").length,
      watch: health.filter((item) => item.healthBand === "watch").length,
      at_risk: health.filter((item) => item.healthBand === "at_risk").length,
      critical: health.filter((item) => item.healthBand === "critical").length
    },
    renewal_watch: renewals.slice(0, 80).map((item) => ({
      customer_id: item.customerId,
      renewal_status: item.renewalStatus,
      recommendation: item.recommendationSummary
    }))
  } as Record<string, unknown>;

  const provider = getAiProvider();
  const model = provider.getDefaultModel({ reasoning: true });
  const prompt = await getActivePromptVersion({
    supabase: params.supabase,
    orgId: params.orgId,
    scenario: "executive_brief_summary",
    providerId: provider.id
  });

  const run = await createAiRun({
    supabase: params.supabase,
    orgId: params.orgId,
    triggeredByUserId: params.actorUserId,
    triggerSource: "manager_review",
    scenario: "executive_brief_summary",
    provider: provider.id,
    model,
    promptVersion: prompt.version,
    inputSnapshot
  });

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "running",
    startedAt: new Date().toISOString()
  });

  let result: ExecutiveBriefSummaryResult = fallback;
  let usedFallback = false;
  let fallbackReason: string | null = null;
  let outputSnapshot: Record<string, unknown> = { fallback: true, reason: "not_started" };
  let responseModel = model;

  try {
    if (!provider.isConfigured()) throw new Error("provider_not_configured");

    const response = await provider.chatCompletion({
      scenario: "executive_brief_summary",
      model,
      systemPrompt: prompt.systemPrompt,
      developerPrompt: `${prompt.developerPrompt}\n\nOutput schema:\n${JSON.stringify(prompt.outputSchema)}`,
      userPrompt: JSON.stringify(inputSnapshot),
      jsonMode: true,
      strictMode: true,
      useReasonerModel: true
    });

    if (response.error) throw new Error(response.error);
    const candidate = response.parsedJson ?? (response.rawText ? JSON.parse(response.rawText) : null);
    const parsed = executiveBriefSummaryResultSchema.safeParse(candidate);
    if (!parsed.success) throw new Error("executive_brief_summary_schema_invalid");

    result = {
      headline: parsed.data.headline,
      topRisks: parsed.data.top_risks,
      topOpportunities: parsed.data.top_opportunities,
      suggestedActions: parsed.data.suggested_actions,
      watchItems: parsed.data.watch_items
    };
    outputSnapshot = response.rawResponse;
    responseModel = response.model;
  } catch (error) {
    if (!isRuleFallbackEnabled()) {
      const message = error instanceof Error ? error.message : "executive_brief_summary_failed";
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
    fallbackReason = error instanceof Error ? error.message : "executive_brief_summary_fallback";
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
      headline: result.headline,
      top_risks: result.topRisks,
      top_opportunities: result.topOpportunities,
      suggested_actions: result.suggestedActions,
      watch_items: result.watchItems
    },
    resultSource: usedFallback ? "fallback" : "provider",
    fallbackReason,
    errorMessage: usedFallback ? fallbackReason : null,
    completedAt: new Date().toISOString()
  });

  const insertRes = await (params.supabase as any)
    .from("executive_briefs")
    .insert({
      org_id: params.orgId,
      brief_type: params.briefType,
      target_user_id: params.targetUserId ?? null,
      status: "completed",
      headline: result.headline,
      summary: result.topRisks[0] ?? result.headline,
      brief_payload: {
        top_risks: result.topRisks,
        top_opportunities: result.topOpportunities,
        suggested_actions: result.suggestedActions,
        watch_items: result.watchItems,
        used_fallback: usedFallback,
        fallback_reason: fallbackReason
      },
      source_snapshot: inputSnapshot,
      ai_run_id: run.id
    })
    .select("*")
    .single();
  if (insertRes.error) throw new Error(insertRes.error.message);

  return {
    brief: mapRow(insertRes.data as ExecutiveBriefRow),
    usedFallback,
    fallbackReason
  };
}
