import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { mapOrgUsageCounterRow, mapUserUsageCounterRow } from "@/services/mappers";
import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import { buildFallbackUsageHealthSummary } from "@/lib/productization-fallback";
import { getActivePromptVersion } from "@/services/ai-prompt-service";
import { createAiRun, updateAiRunStatus } from "@/services/ai-run-service";
import type { Database } from "@/types/database";
import type { EntitlementStatus, OrgUsageCounter, UserUsageCounter } from "@/types/productization";
import { usageHealthSummaryResultSchema, type AiScenario } from "@/types/ai";

type DbClient = ServerSupabaseClient;
type OrgUsageRow = Database["public"]["Tables"]["org_usage_counters"]["Row"];
type UserUsageRow = Database["public"]["Tables"]["user_usage_counters"]["Row"];
type ProfileRow = Database["public"]["Tables"]["profiles"]["Row"];

type UsageCounts = {
  aiRunsCount: number;
  prepCardsCount: number;
  draftsCount: number;
  reportsCount: number;
  touchpointEventsCount: number;
  documentProcessedCount: number;
  workPlanGenerationsCount: number;
};

function nowDateIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function firstDayOfMonth(dateIso: string): string {
  const date = new Date(`${dateIso}T00:00:00.000Z`);
  return new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), 1)).toISOString().slice(0, 10);
}

function addDays(dateIso: string, days: number): string {
  const date = new Date(`${dateIso}T00:00:00.000Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function monthEndExclusive(dateIso: string): string {
  const date = new Date(`${dateIso}T00:00:00.000Z`);
  return new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 1)).toISOString().slice(0, 10);
}

async function countTableRows(params: {
  supabase: DbClient;
  table:
    | "ai_runs"
    | "prep_cards"
    | "content_drafts"
    | "generated_reports"
    | "external_touchpoint_events"
    | "document_assets"
    | "work_agent_runs";
  orgId: string;
  column: string;
  fromDate: string;
  toDateExclusive: string;
  userColumn?: string;
  userId?: string;
}): Promise<number> {
  let query = params.supabase
    .from(params.table)
    .select("id", { count: "exact", head: true })
    .eq("org_id", params.orgId)
    .gte(params.column, `${params.fromDate}T00:00:00.000Z`)
    .lt(params.column, `${params.toDateExclusive}T00:00:00.000Z`);

  if (params.userColumn && params.userId) {
    query = query.eq(params.userColumn, params.userId);
  }

  const res = await query;
  if (res.error) throw new Error(res.error.message);
  return res.count ?? 0;
}

async function computeUsageCounts(params: {
  supabase: DbClient;
  orgId: string;
  fromDate: string;
  toDateExclusive: string;
  userId?: string;
}): Promise<UsageCounts> {
  const [aiRunsCount, prepCardsCount, draftsCount, reportsCount, touchpointEventsCount, documentProcessedCount, workPlanGenerationsCount] =
    await Promise.all([
      countTableRows({
        supabase: params.supabase,
        table: "ai_runs",
        orgId: params.orgId,
        column: "created_at",
        fromDate: params.fromDate,
        toDateExclusive: params.toDateExclusive,
        userColumn: "triggered_by_user_id",
        userId: params.userId
      }),
      countTableRows({
        supabase: params.supabase,
        table: "prep_cards",
        orgId: params.orgId,
        column: "created_at",
        fromDate: params.fromDate,
        toDateExclusive: params.toDateExclusive,
        userColumn: "owner_id",
        userId: params.userId
      }),
      countTableRows({
        supabase: params.supabase,
        table: "content_drafts",
        orgId: params.orgId,
        column: "created_at",
        fromDate: params.fromDate,
        toDateExclusive: params.toDateExclusive,
        userColumn: "owner_id",
        userId: params.userId
      }),
      countTableRows({
        supabase: params.supabase,
        table: "generated_reports",
        orgId: params.orgId,
        column: "created_at",
        fromDate: params.fromDate,
        toDateExclusive: params.toDateExclusive,
        userColumn: "generated_by",
        userId: params.userId
      }),
      countTableRows({
        supabase: params.supabase,
        table: "external_touchpoint_events",
        orgId: params.orgId,
        column: "created_at",
        fromDate: params.fromDate,
        toDateExclusive: params.toDateExclusive,
        userColumn: "owner_id",
        userId: params.userId
      }),
      countTableRows({
        supabase: params.supabase,
        table: "document_assets",
        orgId: params.orgId,
        column: "created_at",
        fromDate: params.fromDate,
        toDateExclusive: params.toDateExclusive,
        userColumn: "owner_id",
        userId: params.userId
      }),
      countTableRows({
        supabase: params.supabase,
        table: "work_agent_runs",
        orgId: params.orgId,
        column: "created_at",
        fromDate: params.fromDate,
        toDateExclusive: params.toDateExclusive,
        userColumn: "user_id",
        userId: params.userId
      })
    ]);

  return {
    aiRunsCount,
    prepCardsCount,
    draftsCount,
    reportsCount,
    touchpointEventsCount,
    documentProcessedCount,
    workPlanGenerationsCount
  };
}

export async function syncUsageCounters(params: {
  supabase: DbClient;
  orgId: string;
  date?: string;
  scopes?: Array<"daily" | "monthly">;
}): Promise<{
  orgCounters: OrgUsageCounter[];
  userCounters: UserUsageCounter[];
}> {
  const date = params.date ?? nowDateIso();
  const scopes = params.scopes ?? ["daily", "monthly"];

  const usersRes = await params.supabase
    .from("org_memberships")
    .select("user_id")
    .eq("org_id", params.orgId)
    .in("seat_status", ["active", "invited"]);
  if (usersRes.error) throw new Error(usersRes.error.message);
  const userIds = Array.from(new Set(((usersRes.data ?? []) as Array<{ user_id: string }>).map((item) => item.user_id)));

  const orgCounterRows: OrgUsageRow[] = [];
  const userCounterRows: UserUsageRow[] = [];

  for (const scope of scopes) {
    const fromDate = scope === "daily" ? date : firstDayOfMonth(date);
    const toDateExclusive = scope === "daily" ? addDays(date, 1) : monthEndExclusive(date);

    const orgCounts = await computeUsageCounts({
      supabase: params.supabase,
      orgId: params.orgId,
      fromDate,
      toDateExclusive
    });

    const orgUpsert = await params.supabase
      .from("org_usage_counters")
      .upsert(
        {
          org_id: params.orgId,
          usage_date: fromDate,
          usage_scope: scope,
          ai_runs_count: orgCounts.aiRunsCount,
          prep_cards_count: orgCounts.prepCardsCount,
          drafts_count: orgCounts.draftsCount,
          reports_count: orgCounts.reportsCount,
          touchpoint_events_count: orgCounts.touchpointEventsCount,
          document_processed_count: orgCounts.documentProcessedCount,
          work_plan_generations_count: orgCounts.workPlanGenerationsCount
        },
        {
          onConflict: "org_id,usage_date,usage_scope"
        }
      )
      .select("*")
      .single();

    if (orgUpsert.error) throw new Error(orgUpsert.error.message);
    orgCounterRows.push(orgUpsert.data as OrgUsageRow);

    for (const userId of userIds) {
      const userCounts = await computeUsageCounts({
        supabase: params.supabase,
        orgId: params.orgId,
        fromDate,
        toDateExclusive,
        userId
      });

      const upsertRes = await params.supabase
        .from("user_usage_counters")
        .upsert(
          {
            org_id: params.orgId,
            user_id: userId,
            usage_date: fromDate,
            usage_scope: scope,
            ai_runs_count: userCounts.aiRunsCount,
            prep_cards_count: userCounts.prepCardsCount,
            drafts_count: userCounts.draftsCount,
            reports_count: userCounts.reportsCount,
            touchpoint_events_count: userCounts.touchpointEventsCount,
            document_processed_count: userCounts.documentProcessedCount,
            work_plan_generations_count: userCounts.workPlanGenerationsCount
          },
          {
            onConflict: "org_id,user_id,usage_date,usage_scope"
          }
        )
        .select("*")
        .single();

      if (upsertRes.error) throw new Error(upsertRes.error.message);
      userCounterRows.push(upsertRes.data as UserUsageRow);
    }
  }

  const profileRes = await params.supabase.from("profiles").select("id,display_name").eq("org_id", params.orgId);
  if (profileRes.error) throw new Error(profileRes.error.message);
  const profileMap = new Map<string, { id: string; display_name: string }>();
  for (const profile of (profileRes.data ?? []) as Array<{ id: string; display_name: string }>) {
    profileMap.set(profile.id, profile);
  }

  return {
    orgCounters: orgCounterRows.map((row) => mapOrgUsageCounterRow(row)),
    userCounters: userCounterRows.map((row) =>
      mapUserUsageCounterRow({
        ...row,
        profile: profileMap.get(row.user_id) ?? null
      })
    )
  };
}

export async function getUsageCounters(params: {
  supabase: DbClient;
  orgId: string;
  date?: string;
  refresh?: boolean;
}): Promise<{
  daily: OrgUsageCounter | null;
  monthly: OrgUsageCounter | null;
  topUsersMonthly: UserUsageCounter[];
}> {
  const date = params.date ?? nowDateIso();
  if (params.refresh !== false) {
    await syncUsageCounters({
      supabase: params.supabase,
      orgId: params.orgId,
      date,
      scopes: ["daily", "monthly"]
    });
  }

  const dailyDate = date;
  const monthlyDate = firstDayOfMonth(date);

  const [dailyRes, monthlyRes, usersRes] = await Promise.all([
    params.supabase.from("org_usage_counters").select("*").eq("org_id", params.orgId).eq("usage_scope", "daily").eq("usage_date", dailyDate).maybeSingle(),
    params.supabase.from("org_usage_counters").select("*").eq("org_id", params.orgId).eq("usage_scope", "monthly").eq("usage_date", monthlyDate).maybeSingle(),
    params.supabase
      .from("user_usage_counters")
      .select("*")
      .eq("org_id", params.orgId)
      .eq("usage_scope", "monthly")
      .eq("usage_date", monthlyDate)
  ]);

  if (dailyRes.error) throw new Error(dailyRes.error.message);
  if (monthlyRes.error) throw new Error(monthlyRes.error.message);
  if (usersRes.error) throw new Error(usersRes.error.message);

  const profileRes = await params.supabase.from("profiles").select("id,display_name").eq("org_id", params.orgId);
  if (profileRes.error) throw new Error(profileRes.error.message);
  const profileMap = new Map<string, { id: string; display_name: string }>();
  for (const profile of (profileRes.data ?? []) as Array<{ id: string; display_name: string }>) {
    profileMap.set(profile.id, profile);
  }

  const userCounters = ((usersRes.data ?? []) as UserUsageRow[])
    .map((row) =>
      mapUserUsageCounterRow({
        ...row,
        profile: profileMap.get(row.user_id) ?? null
      })
    )
    .sort((a, b) => {
      const scoreA = a.aiRunsCount + a.prepCardsCount + a.reportsCount + a.workPlanGenerationsCount;
      const scoreB = b.aiRunsCount + b.prepCardsCount + b.reportsCount + b.workPlanGenerationsCount;
      return scoreB - scoreA;
    })
    .slice(0, 8);

  return {
    daily: dailyRes.data ? mapOrgUsageCounterRow(dailyRes.data as OrgUsageRow) : null,
    monthly: monthlyRes.data ? mapOrgUsageCounterRow(monthlyRes.data as OrgUsageRow) : null,
    topUsersMonthly: userCounters
  };
}

export async function generateUsageHealthSummary(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  entitlement: EntitlementStatus;
  monthlyUsage: UsageCounts;
  featureFlags: Record<string, boolean>;
}): Promise<{
  summary: {
    usageSummary: string;
    hotFeatures: string[];
    underusedFeatures: string[];
    quotaRisks: string[];
    recommendedAdjustments: string[];
  };
  usedFallback: boolean;
  fallbackReason: string | null;
}> {
  const scenario: AiScenario = "usage_health_summary";
  const provider = getAiProvider();
  const model = provider.getDefaultModel({ reasoning: false });

  const prompt = await getActivePromptVersion({
    supabase: params.supabase,
    orgId: params.orgId,
    scenario,
    providerId: provider.id
  });

  const run = await createAiRun({
    supabase: params.supabase,
    orgId: params.orgId,
    triggerSource: "manual",
    scenario,
    provider: provider.id,
    model,
    promptVersion: prompt.version,
    triggeredByUserId: params.actorUserId,
    inputSnapshot: {
      entitlement: params.entitlement,
      monthly_usage: params.monthlyUsage,
      feature_flags: params.featureFlags
    }
  });

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "running",
    startedAt: new Date().toISOString()
  });

  try {
    if (!provider.isConfigured()) throw new Error("provider_not_configured");

    const response = await provider.chatCompletion({
      scenario,
      model,
      systemPrompt: prompt.systemPrompt,
      developerPrompt: `${prompt.developerPrompt}\n\nOutput schema:\n${JSON.stringify(prompt.outputSchema)}`,
      userPrompt: JSON.stringify({
        entitlement: params.entitlement,
        monthly_usage: params.monthlyUsage,
        feature_flags: params.featureFlags
      }),
      jsonMode: true,
      strictMode: true
    });

    const parsed = usageHealthSummaryResultSchema.safeParse(
      response.parsedJson ?? (response.rawText ? JSON.parse(response.rawText) : null)
    );
    if (!parsed.success) throw new Error("usage_health_summary_schema_invalid");

    await updateAiRunStatus({
      supabase: params.supabase,
      runId: run.id,
      status: "completed",
      provider: response.provider,
      model: response.model,
      outputSnapshot: response.rawResponse,
      parsedResult: parsed.data,
      latencyMs: response.latencyMs,
      resultSource: "provider",
      completedAt: new Date().toISOString()
    });

    return {
      summary: {
        usageSummary: parsed.data.usage_summary,
        hotFeatures: parsed.data.hot_features,
        underusedFeatures: parsed.data.underused_features,
        quotaRisks: parsed.data.quota_risks,
        recommendedAdjustments: parsed.data.recommended_adjustments
      },
      usedFallback: false,
      fallbackReason: null
    };
  } catch (error) {
    if (!isRuleFallbackEnabled()) {
      const message = error instanceof Error ? error.message : "usage_health_summary_failed";
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

    const fallbackReason = error instanceof Error ? error.message : "usage_health_summary_fallback";
    const fallback = buildFallbackUsageHealthSummary({
      entitlement: params.entitlement,
      monthlyUsage: params.monthlyUsage
    });

    await updateAiRunStatus({
      supabase: params.supabase,
      runId: run.id,
      status: "completed",
      provider: provider.id,
      model: "rule-fallback",
      outputSnapshot: {
        fallback: true,
        reason: fallbackReason,
        payload: fallback
      },
      parsedResult: {
        usage_summary: fallback.usageSummary,
        hot_features: fallback.hotFeatures,
        underused_features: fallback.underusedFeatures,
        quota_risks: fallback.quotaRisks,
        recommended_adjustments: fallback.recommendedAdjustments
      },
      errorMessage: fallbackReason,
      resultSource: "fallback",
      fallbackReason,
      completedAt: new Date().toISOString()
    });

    return {
      summary: fallback,
      usedFallback: true,
      fallbackReason
    };
  }
}
