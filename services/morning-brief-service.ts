import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import { buildFallbackMorningBrief } from "@/lib/preparation-fallback";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { getActivePromptVersion } from "@/services/ai-prompt-service";
import { createAiRun, updateAiRunStatus } from "@/services/ai-run-service";
import { mapMorningBriefRow } from "@/services/mappers";
import { getUserMemoryProfile } from "@/services/user-memory-service";
import { morningBriefResultSchema, type AiScenario, type MorningBriefResult } from "@/types/ai";
import type { Database } from "@/types/database";
import type { MorningBrief, MorningBriefType } from "@/types/preparation";

type DbClient = ServerSupabaseClient;
type ProfileRow = Database["public"]["Tables"]["profiles"]["Row"];

function nowIso(): string {
  return new Date().toISOString();
}

function todayDate(): string {
  return new Date().toISOString().slice(0, 10);
}

function scenarioForBrief(briefType: MorningBriefType): AiScenario {
  return briefType === "manager_morning" ? "manager_morning_brief" : "sales_morning_brief";
}

async function collectSalesBriefData(params: {
  supabase: DbClient;
  profile: ProfileRow;
  briefDate: string;
}): Promise<{
  topTasks: string[];
  customersToPrepare: string[];
  topRisks: string[];
  pendingDraftCount: number;
  metricsSnapshot: Record<string, unknown>;
  sourceSnapshot: Record<string, unknown>;
}> {
  const [taskRes, alertRes, draftRes, prepRes, memory] = await Promise.all([
    params.supabase
      .from("work_items")
      .select("id, title, priority_band, customer_id, status, due_at")
      .eq("org_id", params.profile.org_id)
      .eq("owner_id", params.profile.id)
      .in("status", ["todo", "in_progress", "snoozed"])
      .order("priority_score", { ascending: false })
      .limit(30),
    params.supabase
      .from("alerts")
      .select("id, title, severity, status")
      .eq("org_id", params.profile.org_id)
      .eq("owner_id", params.profile.id)
      .in("status", ["open", "watching"])
      .order("created_at", { ascending: false })
      .limit(30),
    params.supabase
      .from("followups")
      .select("id")
      .eq("org_id", params.profile.org_id)
      .eq("owner_id", params.profile.id)
      .eq("draft_status", "draft"),
    params.supabase
      .from("prep_cards")
      .select("id, customer_id, card_type, status")
      .eq("org_id", params.profile.org_id)
      .eq("owner_id", params.profile.id)
      .in("status", ["draft", "ready"])
      .gte("created_at", `${params.briefDate}T00:00:00.000Z`)
      .lte("created_at", `${params.briefDate}T23:59:59.999Z`),
    getUserMemoryProfile({
      supabase: params.supabase,
      orgId: params.profile.org_id,
      userId: params.profile.id
    }).catch(() => null)
  ]);

  if (taskRes.error) throw new Error(taskRes.error.message);
  if (alertRes.error) throw new Error(alertRes.error.message);
  if (draftRes.error) throw new Error(draftRes.error.message);
  if (prepRes.error) throw new Error(prepRes.error.message);

  const tasks = (taskRes.data ?? []) as Array<{
    id: string;
    title: string;
    priority_band: string;
    customer_id: string | null;
    status: string;
    due_at: string | null;
  }>;
  const alerts = (alertRes.data ?? []) as Array<{ id: string; title: string; severity: string; status: string }>;
  const pendingDraftCount = (draftRes.data ?? []).length;
  const prepCards = prepRes.data ?? [];

  const customerIds = Array.from(new Set(tasks.map((item) => item.customer_id).filter((item): item is string => Boolean(item))));
  let customersToPrepare: string[] = [];
  if (customerIds.length > 0) {
    const customerRes = await params.supabase
      .from("customers")
      .select("id, company_name")
      .eq("org_id", params.profile.org_id)
      .in("id", customerIds);
    if (customerRes.error) throw new Error(customerRes.error.message);
    const customerRows = (customerRes.data ?? []) as Array<{ id: string; company_name: string }>;
    const customerNameMap = new Map(customerRows.map((item) => [item.id, item.company_name]));
    customersToPrepare = tasks
      .filter((item) => item.priority_band === "critical" || item.priority_band === "high")
      .map((item) => item.customer_id ? customerNameMap.get(item.customer_id) : null)
      .filter((item): item is string => Boolean(item))
      .slice(0, 6);
  }

  return {
    topTasks: tasks.slice(0, 6).map((item) => item.title),
    customersToPrepare,
    topRisks: alerts.slice(0, 6).map((item) => item.title),
    pendingDraftCount,
    metricsSnapshot: {
      open_tasks: tasks.length,
      high_priority_tasks: tasks.filter((item) => item.priority_band === "critical" || item.priority_band === "high").length,
      open_alerts: alerts.length,
      pending_drafts: pendingDraftCount,
      prep_cards_today: prepCards.length
    },
    sourceSnapshot: {
      top_task_ids: tasks.slice(0, 8).map((item) => item.id),
      alert_ids: alerts.slice(0, 8).map((item) => item.id),
      memory_reminders: memory?.riskBlindSpots?.slice(0, 4) ?? []
    }
  };
}

async function collectManagerBriefData(params: {
  supabase: DbClient;
  profile: ProfileRow;
  briefDate: string;
}): Promise<{
  topTasks: string[];
  customersToPrepare: string[];
  topRisks: string[];
  pendingDraftCount: number;
  metricsSnapshot: Record<string, unknown>;
  sourceSnapshot: Record<string, unknown>;
}> {
  const [taskRes, alertRes, draftRes, highValueRes, prepRes] = await Promise.all([
    params.supabase
      .from("work_items")
      .select("id, title, priority_band, customer_id, status, owner_id")
      .eq("org_id", params.profile.org_id)
      .in("status", ["todo", "in_progress", "snoozed"])
      .order("priority_score", { ascending: false })
      .limit(60),
    params.supabase
      .from("alerts")
      .select("id, title, severity, status, customer_id")
      .eq("org_id", params.profile.org_id)
      .in("status", ["open", "watching"])
      .order("created_at", { ascending: false })
      .limit(60),
    params.supabase
      .from("followups")
      .select("id")
      .eq("org_id", params.profile.org_id)
      .eq("draft_status", "draft"),
    params.supabase
      .from("customers")
      .select("id, company_name, risk_level, win_probability")
      .eq("org_id", params.profile.org_id)
      .eq("risk_level", "high")
      .gte("win_probability", 70)
      .limit(40),
    params.supabase
      .from("prep_cards")
      .select("id")
      .eq("org_id", params.profile.org_id)
      .eq("card_type", "manager_attention")
      .gte("created_at", `${params.briefDate}T00:00:00.000Z`)
      .lte("created_at", `${params.briefDate}T23:59:59.999Z`)
  ]);

  if (taskRes.error) throw new Error(taskRes.error.message);
  if (alertRes.error) throw new Error(alertRes.error.message);
  if (draftRes.error) throw new Error(draftRes.error.message);
  if (highValueRes.error) throw new Error(highValueRes.error.message);
  if (prepRes.error) throw new Error(prepRes.error.message);

  const tasks = (taskRes.data ?? []) as Array<{
    id: string;
    title: string;
    priority_band: string;
    customer_id: string | null;
    status: string;
    owner_id: string;
  }>;
  const alerts = (alertRes.data ?? []) as Array<{
    id: string;
    title: string;
    severity: string;
    status: string;
    customer_id: string | null;
  }>;
  const highValueCustomers = (highValueRes.data ?? []) as Array<{
    id: string;
    company_name: string;
    risk_level: string;
    win_probability: number;
  }>;
  const pendingDraftCount = (draftRes.data ?? []).length;
  const prepCardsToday = (prepRes.data ?? []).length;

  const customerWithTask = new Set(tasks.map((item) => item.customer_id).filter((item): item is string => Boolean(item)));
  const unattendedCustomers = highValueCustomers.filter((item) => !customerWithTask.has(item.id)).map((item) => item.company_name);

  return {
    topTasks: tasks.slice(0, 8).map((item) => item.title),
    customersToPrepare: unattendedCustomers.slice(0, 8),
    topRisks: alerts.slice(0, 8).map((item) => item.title),
    pendingDraftCount,
    metricsSnapshot: {
      team_open_tasks: tasks.length,
      team_critical_tasks: tasks.filter((item) => item.priority_band === "critical").length,
      team_open_alerts: alerts.length,
      unattended_high_value_customers: unattendedCustomers.length,
      pending_drafts: pendingDraftCount,
      manager_attention_cards_today: prepCardsToday
    },
    sourceSnapshot: {
      top_task_ids: tasks.slice(0, 10).map((item) => item.id),
      risk_alert_ids: alerts.slice(0, 10).map((item) => item.id),
      unattended_customer_names: unattendedCustomers.slice(0, 10)
    }
  };
}

export async function generateMorningBrief(params: {
  supabase: DbClient;
  profile: ProfileRow;
  briefType?: MorningBriefType;
  briefDate?: string;
}): Promise<{ brief: MorningBrief; usedFallback: boolean; runId: string }> {
  const briefDate = params.briefDate ?? todayDate();
  const briefType: MorningBriefType = params.briefType ?? (params.profile.role === "manager" ? "manager_morning" : "sales_morning");
  const scenario = scenarioForBrief(briefType);
  const provider = getAiProvider();
  const model = provider.getDefaultModel({ reasoning: true });

  const snapshots =
    briefType === "manager_morning"
      ? await collectManagerBriefData({
          supabase: params.supabase,
          profile: params.profile,
          briefDate
        })
      : await collectSalesBriefData({
          supabase: params.supabase,
          profile: params.profile,
          briefDate
        });

  const { data: baseBriefRaw, error: baseBriefError } = await params.supabase
    .from("morning_briefs")
    .upsert(
      {
        org_id: params.profile.org_id,
        target_user_id: params.profile.id,
        brief_type: briefType,
        brief_date: briefDate,
        status: "generating",
        generated_by: params.profile.id
      },
      { onConflict: "org_id,brief_type,brief_date,target_user_id" }
    )
    .select("*")
    .single();
  if (baseBriefError || !baseBriefRaw) throw new Error(baseBriefError?.message ?? "Failed to create morning brief");

  const prompt = await getActivePromptVersion({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    scenario,
    providerId: provider.id
  });

  const run = await createAiRun({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    customerId: null,
    followupId: null,
    triggeredByUserId: params.profile.id,
    triggerSource: "manual",
    scenario,
    provider: provider.id,
    model,
    promptVersion: prompt.version,
    inputSnapshot: {
      brief_type: briefType,
      brief_date: briefDate,
      metrics_snapshot: snapshots.metricsSnapshot,
      source_snapshot: snapshots.sourceSnapshot
    }
  });

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "running",
    startedAt: nowIso()
  });

  const startedAt = Date.now();
  let result: MorningBriefResult;
  let usedFallback = false;
  let fallbackReason: string | null = null;
  let outputSnapshot: Record<string, unknown> = {};
  let responseProvider = provider.id;
  let responseModel = model;

  try {
    if (!provider.isConfigured()) throw new Error("provider_not_configured");
    const response = await provider.chatCompletion({
      scenario,
      model,
      systemPrompt: prompt.systemPrompt,
      developerPrompt: `${prompt.developerPrompt}\n\nOutput schema:\n${JSON.stringify(prompt.outputSchema)}`,
      userPrompt: JSON.stringify({
        scenario,
        payload: {
          brief_type: briefType,
          brief_date: briefDate,
          metrics_snapshot: snapshots.metricsSnapshot,
          source_snapshot: snapshots.sourceSnapshot,
          top_tasks: snapshots.topTasks,
          customers_to_prepare: snapshots.customersToPrepare,
          top_risks: snapshots.topRisks,
          pending_drafts_count: snapshots.pendingDraftCount
        }
      }),
      jsonMode: true,
      strictMode: true,
      useReasonerModel: true
    });

    responseProvider = response.provider;
    responseModel = response.model;
    outputSnapshot = response.rawResponse;
    if (response.error) throw new Error(response.error);

    const parsed = morningBriefResultSchema.safeParse(response.parsedJson ?? (response.rawText ? JSON.parse(response.rawText) : null));
    if (!parsed.success) throw new Error("morning_brief_schema_invalid");
    result = parsed.data;
  } catch (error) {
    if (!isRuleFallbackEnabled()) {
      const message = error instanceof Error ? error.message : "morning_brief_failed";
      await updateAiRunStatus({
        supabase: params.supabase,
        runId: run.id,
        status: "failed",
        provider: provider.id,
        model,
        errorMessage: message,
        completedAt: nowIso()
      });
      throw error;
    }
    usedFallback = true;
    fallbackReason = error instanceof Error ? error.message : "morning_brief_fallback";
    result = buildFallbackMorningBrief({
      briefType,
      topTasks: snapshots.topTasks,
      topRisks: snapshots.topRisks,
      customersToPrepare: snapshots.customersToPrepare,
      pendingDraftCount: snapshots.pendingDraftCount
    });
    outputSnapshot = {
      fallback: true,
      reason: fallbackReason
    };
    responseModel = "rule-fallback";
  }

  const { data: updatedBriefRaw, error: updateBriefError } = await params.supabase
    .from("morning_briefs")
    .update({
      status: "completed",
      headline: result.headline,
      executive_summary: result.action_note,
      brief_payload: result,
      source_snapshot: {
        ...snapshots.sourceSnapshot,
        metrics_snapshot: snapshots.metricsSnapshot
      },
      ai_run_id: run.id,
      updated_at: nowIso()
    })
    .eq("id", baseBriefRaw.id)
    .select("*")
    .single();

  if (updateBriefError || !updatedBriefRaw) {
    throw new Error(updateBriefError?.message ?? "Failed to persist morning brief");
  }

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "completed",
    provider: responseProvider,
    model: responseModel,
    outputSnapshot,
    parsedResult: result,
    resultSource: usedFallback ? "fallback" : "provider",
    fallbackReason,
    latencyMs: Date.now() - startedAt,
    completedAt: nowIso(),
    errorMessage: usedFallback ? fallbackReason : null
  });

  console.info("[briefing.morning.generate]", {
    org_id: params.profile.org_id,
    user_id: params.profile.id,
    scenario,
    provider: responseProvider,
    model: responseModel,
    status: "completed",
    duration_ms: Date.now() - startedAt,
    fallback_reason: fallbackReason
  });

  return {
    brief: mapMorningBriefRow(updatedBriefRaw as Database["public"]["Tables"]["morning_briefs"]["Row"]),
    usedFallback,
    runId: run.id
  };
}

export async function listMorningBriefs(params: {
  supabase: DbClient;
  orgId: string;
  targetUserId?: string | null;
  briefType?: MorningBriefType;
  limit?: number;
}): Promise<MorningBrief[]> {
  let query = params.supabase
    .from("morning_briefs")
    .select("*")
    .eq("org_id", params.orgId)
    .order("brief_date", { ascending: false })
    .order("created_at", { ascending: false })
    .limit(params.limit ?? 20);
  if (params.targetUserId !== undefined) query = query.eq("target_user_id", params.targetUserId);
  if (params.briefType) query = query.eq("brief_type", params.briefType);
  const { data, error } = await query;
  if (error) throw new Error(error.message);
  const rows = (data ?? []) as Database["public"]["Tables"]["morning_briefs"]["Row"][];
  return rows.map((item) => mapMorningBriefRow(item));
}

export async function getMorningBriefByDate(params: {
  supabase: DbClient;
  orgId: string;
  targetUserId: string;
  briefType: MorningBriefType;
  briefDate: string;
}): Promise<MorningBrief | null> {
  const { data, error } = await params.supabase
    .from("morning_briefs")
    .select("*")
    .eq("org_id", params.orgId)
    .eq("target_user_id", params.targetUserId)
    .eq("brief_type", params.briefType)
    .eq("brief_date", params.briefDate)
    .maybeSingle();
  if (error) throw new Error(error.message);
  if (!data) return null;
  return mapMorningBriefRow(data as Database["public"]["Tables"]["morning_briefs"]["Row"]);
}
