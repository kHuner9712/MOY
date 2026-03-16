import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import { buildFallbackTeamRhythmInsight } from "@/lib/team-rhythm-fallback";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { getActivePromptVersion } from "@/services/ai-prompt-service";
import { managerTeamRhythmInsightResultSchema, type AiScenario, type ManagerTeamRhythmInsightResult } from "@/types/ai";
import type { Database, Json } from "@/types/database";
import type { TeamRhythmUserRow } from "@/types/work";

type DbClient = ServerSupabaseClient;

function nowIso(): string {
  return new Date().toISOString();
}

function getRange(periodType: "daily" | "weekly"): { periodStart: string; periodEnd: string } {
  const end = new Date();
  const start = new Date();
  if (periodType === "weekly") {
    start.setDate(end.getDate() - 6);
  }
  return {
    periodStart: start.toISOString().slice(0, 10),
    periodEnd: end.toISOString().slice(0, 10)
  };
}

async function createRun(params: {
  supabase: DbClient;
  orgId: string;
  userId: string | null;
  inputSnapshot: Json;
}): Promise<string> {
  const { data, error } = await params.supabase
    .from("work_agent_runs")
    .insert({
      org_id: params.orgId,
      user_id: params.userId,
      run_scope: "manager_team_plan",
      status: "queued",
      input_snapshot: params.inputSnapshot
    })
    .select("id")
    .single();
  if (error || !data) throw new Error(error?.message ?? "Failed to create manager rhythm run");
  return data.id as string;
}

async function updateRun(params: {
  supabase: DbClient;
  runId: string;
  status: Database["public"]["Enums"]["work_agent_run_status"];
  provider?: Database["public"]["Enums"]["ai_provider"] | null;
  model?: string | null;
  resultSource?: Database["public"]["Enums"]["ai_result_source"];
  fallbackReason?: string | null;
  errorMessage?: string | null;
  outputSnapshot?: Json;
  parsedResult?: Json;
  startedAt?: string | null;
  completedAt?: string | null;
}): Promise<void> {
  const payload: Database["public"]["Tables"]["work_agent_runs"]["Update"] = {
    status: params.status
  };
  if (params.provider !== undefined) payload.provider = params.provider;
  if (params.model !== undefined) payload.model = params.model;
  if (params.resultSource !== undefined) payload.result_source = params.resultSource;
  if (params.fallbackReason !== undefined) payload.fallback_reason = params.fallbackReason;
  if (params.errorMessage !== undefined) payload.error_message = params.errorMessage;
  if (params.outputSnapshot !== undefined) payload.output_snapshot = params.outputSnapshot;
  if (params.parsedResult !== undefined) payload.parsed_result = params.parsedResult;
  if (params.startedAt !== undefined) payload.started_at = params.startedAt;
  if (params.completedAt !== undefined) payload.completed_at = params.completedAt;
  const { error } = await params.supabase.from("work_agent_runs").update(payload).eq("id", params.runId);
  if (error) throw new Error(error.message);
}

export async function generateManagerRhythmInsight(params: {
  supabase: DbClient;
  orgId: string;
  periodType?: "daily" | "weekly";
  triggeredByUserId: string;
}): Promise<{
  periodType: "daily" | "weekly";
  periodStart: string;
  periodEnd: string;
  userRows: TeamRhythmUserRow[];
  teamTotals: {
    totalTasks: number;
    doneTasks: number;
    overdueTasks: number;
    criticalOpenTasks: number;
    completionRate: number;
    overdueRate: number;
    prepCoverageRate: number;
    highValueWithoutPrepCount: number;
  };
  unattendedCriticalCustomers: string[];
  overloadedUsers: TeamRhythmUserRow[];
  stableUsers: TeamRhythmUserRow[];
  aiInsight: ManagerTeamRhythmInsightResult;
  usedFallback: boolean;
  runId: string;
}> {
  const periodType = params.periodType ?? "daily";
  const { periodStart, periodEnd } = getRange(periodType);

  const [profilesRes, tasksRes, customersRes, prepRes] = await Promise.all([
    params.supabase.from("profiles").select("id, display_name").eq("org_id", params.orgId).eq("role", "sales").eq("is_active", true),
    params.supabase
      .from("work_items")
      .select("*")
      .eq("org_id", params.orgId)
      .gte("created_at", `${periodStart}T00:00:00.000Z`)
      .lte("created_at", `${periodEnd}T23:59:59.999Z`),
    params.supabase
      .from("customers")
      .select("id, company_name, current_stage, risk_level, win_probability")
      .eq("org_id", params.orgId)
      .gte("win_probability", 70)
      .eq("risk_level", "high")
      .in("current_stage", ["lead", "initial_contact", "needs_confirmed", "proposal", "negotiation"]),
    params.supabase
      .from("prep_cards")
      .select("id, work_item_id, customer_id, status")
      .eq("org_id", params.orgId)
      .in("status", ["draft", "ready", "stale"])
  ]);

  if (profilesRes.error) throw new Error(profilesRes.error.message);
  if (tasksRes.error) throw new Error(tasksRes.error.message);
  if (customersRes.error) throw new Error(customersRes.error.message);
  if (prepRes.error) throw new Error(prepRes.error.message);

  const profiles = (profilesRes.data ?? []) as Array<{ id: string; display_name: string }>;
  const tasks = (tasksRes.data ?? []) as Database["public"]["Tables"]["work_items"]["Row"][];
  const highRiskCustomers = (customersRes.data ?? []) as Array<{ id: string; company_name: string }>;
  const prepCards = (prepRes.data ?? []) as Array<{ work_item_id: string | null; customer_id: string | null }>;

  const userRows: TeamRhythmUserRow[] = profiles.map((profile) => {
    const rows = tasks.filter((item) => item.owner_id === profile.id);
    const todoCount = rows.filter((item) => item.status === "todo").length;
    const inProgressCount = rows.filter((item) => item.status === "in_progress").length;
    const doneCount = rows.filter((item) => item.status === "done").length;
    const overdueCount = rows.filter((item) => item.status !== "done" && item.status !== "cancelled" && item.due_at && new Date(item.due_at).getTime() < Date.now()).length;
    const criticalOpenCount = rows.filter((item) => (item.status === "todo" || item.status === "in_progress" || item.status === "snoozed") && item.priority_band === "critical").length;
    const denominator = todoCount + inProgressCount + doneCount;
    const completionRate = denominator === 0 ? 0 : doneCount / denominator;
    const overdueRate = denominator === 0 ? 0 : overdueCount / denominator;
    const backlogScore = Number((todoCount + inProgressCount + overdueCount * 0.8 + criticalOpenCount * 1.2).toFixed(2));
    return {
      userId: profile.id,
      userName: profile.display_name,
      todoCount,
      inProgressCount,
      doneCount,
      overdueCount,
      criticalOpenCount,
      completionRate,
      overdueRate,
      backlogScore
    };
  });

  const totalTasks = tasks.length;
  const doneTasks = tasks.filter((item) => item.status === "done").length;
  const overdueTasks = tasks.filter((item) => item.status !== "done" && item.status !== "cancelled" && item.due_at && new Date(item.due_at).getTime() < Date.now()).length;
  const criticalOpenTasks = tasks.filter((item) => (item.status === "todo" || item.status === "in_progress" || item.status === "snoozed") && item.priority_band === "critical").length;
  const completionRate = totalTasks === 0 ? 0 : doneTasks / totalTasks;
  const overdueRate = totalTasks === 0 ? 0 : overdueTasks / totalTasks;
  const openTaskIds = new Set(tasks.filter((item) => item.status !== "done" && item.status !== "cancelled").map((item) => item.id));
  const taskWithPrepCount = new Set(
    prepCards
      .map((item) => item.work_item_id)
      .filter((item): item is string => typeof item === "string")
      .filter((item) => openTaskIds.has(item))
  ).size;
  const prepCoverageRate = openTaskIds.size === 0 ? 0 : taskWithPrepCount / openTaskIds.size;

  const highValueWithoutPrepCount = highRiskCustomers.filter((item) => {
    const hasPrep = prepCards.some((prep) => prep.customer_id === item.id);
    return !hasPrep;
  }).length;

  const customerWithOpenTask = new Set(
    tasks
      .filter((item) => item.customer_id && item.status !== "done" && item.status !== "cancelled")
      .map((item) => item.customer_id as string)
  );
  const unattendedCriticalCustomers = highRiskCustomers
    .filter((item) => !customerWithOpenTask.has(item.id))
    .map((item) => item.company_name)
    .slice(0, 20);

  const overloadedUsers = userRows.filter((item) => item.backlogScore >= 10 || item.overdueRate >= 0.35).sort((a, b) => b.backlogScore - a.backlogScore);
  const stableUsers = userRows.filter((item) => item.completionRate >= 0.65 && item.overdueRate <= 0.2).sort((a, b) => b.completionRate - a.completionRate);

  const runId = await createRun({
    supabase: params.supabase,
    orgId: params.orgId,
    userId: params.triggeredByUserId,
    inputSnapshot: {
      period_type: periodType,
      period_start: periodStart,
      period_end: periodEnd,
      user_row_count: userRows.length
    }
  });

  await updateRun({
    supabase: params.supabase,
    runId,
    status: "running",
    startedAt: nowIso()
  });

  const scenario: AiScenario = "manager_team_rhythm_insight";
  const provider = getAiProvider();
  const model = provider.getDefaultModel({ reasoning: true });
  const prompt = await getActivePromptVersion({
    supabase: params.supabase,
    orgId: params.orgId,
    scenario,
    providerId: provider.id
  });

  let aiInsight: ManagerTeamRhythmInsightResult;
  let usedFallback = false;
  let fallbackReason: string | null = null;
  let outputSnapshot: Json = {};
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
          period_type: periodType,
          period_start: periodStart,
          period_end: periodEnd,
          team_totals: {
            total_tasks: totalTasks,
            done_tasks: doneTasks,
            overdue_tasks: overdueTasks,
            critical_open_tasks: criticalOpenTasks,
            completion_rate: completionRate,
            overdue_rate: overdueRate
          },
          user_rows: userRows,
          unattended_critical_customers: unattendedCriticalCustomers
        }
      }),
      jsonMode: true,
      strictMode: true,
      useReasonerModel: true
    });
    responseProvider = response.provider;
    responseModel = response.model;
    outputSnapshot = response.rawResponse as Json;
    if (response.error) throw new Error(response.error);
    const parsed = managerTeamRhythmInsightResultSchema.safeParse(
      response.parsedJson ?? (response.rawText ? JSON.parse(response.rawText) : null)
    );
    if (!parsed.success) throw new Error("manager_team_rhythm_insight_schema_invalid");
    aiInsight = parsed.data;
  } catch (error) {
    if (!isRuleFallbackEnabled()) {
      const message = error instanceof Error ? error.message : "manager_team_rhythm_insight_failed";
      await updateRun({
        supabase: params.supabase,
        runId,
        status: "failed",
        provider: provider.id,
        model,
        errorMessage: message,
        completedAt: nowIso()
      });
      throw error;
    }
    usedFallback = true;
    fallbackReason = error instanceof Error ? error.message : "manager_team_rhythm_insight_fallback";
    aiInsight = buildFallbackTeamRhythmInsight({
      rows: userRows,
      overdueTasks,
      unattendedCriticalCustomers
    });
    responseModel = "rule-fallback";
    outputSnapshot = {
      fallback: true,
      reason: fallbackReason,
      user_rows: userRows
    } as unknown as Json;
  }

  await updateRun({
    supabase: params.supabase,
    runId,
    status: "completed",
    provider: responseProvider,
    model: responseModel,
    resultSource: usedFallback ? "fallback" : "provider",
    fallbackReason,
    outputSnapshot,
    parsedResult: {
      team_execution_summary: aiInsight.team_execution_summary,
      support_count: aiInsight.who_needs_support.length
    } as Json,
    errorMessage: usedFallback ? fallbackReason : null,
    completedAt: nowIso()
  });

  return {
    periodType,
    periodStart,
    periodEnd,
    userRows,
    teamTotals: {
      totalTasks,
      doneTasks,
      overdueTasks,
      criticalOpenTasks,
      completionRate,
      overdueRate,
      prepCoverageRate,
      highValueWithoutPrepCount
    },
    unattendedCriticalCustomers,
    overloadedUsers,
    stableUsers,
    aiInsight,
    usedFallback,
    runId
  };
}
