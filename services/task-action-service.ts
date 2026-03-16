import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import { buildFallbackTaskActionSuggestion } from "@/lib/daily-plan-fallback";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { getActivePromptVersion } from "@/services/ai-prompt-service";
import { getUserMemoryProfile } from "@/services/user-memory-service";
import { getWorkItemById } from "@/services/work-item-service";
import { taskActionSuggestionResultSchema, type AiScenario, type TaskActionSuggestionResult } from "@/types/ai";
import type { Database, Json } from "@/types/database";

type DbClient = ServerSupabaseClient;

function nowIso(): string {
  return new Date().toISOString();
}

async function createRun(params: {
  supabase: DbClient;
  orgId: string;
  userId: string;
  inputSnapshot: Json;
}): Promise<string> {
  const { data, error } = await params.supabase
    .from("work_agent_runs")
    .insert({
      org_id: params.orgId,
      user_id: params.userId,
      run_scope: "user_daily_plan",
      status: "queued",
      input_snapshot: params.inputSnapshot
    })
    .select("id")
    .single();
  if (error || !data) throw new Error(error?.message ?? "Failed to create task action run");
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
  const payload: Database["public"]["Tables"]["work_agent_runs"]["Update"] = { status: params.status };
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

export async function generateTaskActionSuggestion(params: {
  supabase: DbClient;
  orgId: string;
  userId: string;
  workItemId: string;
}): Promise<{
  runId: string;
  result: TaskActionSuggestionResult;
  usedFallback: boolean;
  fallbackReason: string | null;
}> {
  const workItem = await getWorkItemById({
    supabase: params.supabase,
    orgId: params.orgId,
    workItemId: params.workItemId
  });
  if (!workItem) throw new Error("Work item not found");

  const memoryProfile = await getUserMemoryProfile({
    supabase: params.supabase,
    orgId: params.orgId,
    userId: params.userId
  }).catch(() => null);

  const runId = await createRun({
    supabase: params.supabase,
    orgId: params.orgId,
    userId: params.userId,
    inputSnapshot: {
      scenario: "task_action_suggestion",
      work_item_id: params.workItemId,
      work_item_title: workItem.title
    }
  });

  await updateRun({
    supabase: params.supabase,
    runId,
    status: "running",
    startedAt: nowIso()
  });

  const scenario: AiScenario = "task_action_suggestion";
  const provider = getAiProvider();
  const model = provider.getDefaultModel({ reasoning: true });
  const prompt = await getActivePromptVersion({
    supabase: params.supabase,
    orgId: params.orgId,
    scenario,
    providerId: provider.id
  });

  let result: TaskActionSuggestionResult;
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
          work_item: workItem,
          memory_summary: memoryProfile?.summary ?? "",
          memory_tactics: memoryProfile?.effectiveTactics ?? [],
          memory_blind_spots: memoryProfile?.riskBlindSpots ?? []
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

    const parsed = taskActionSuggestionResultSchema.safeParse(
      response.parsedJson ?? (response.rawText ? JSON.parse(response.rawText) : null)
    );
    if (!parsed.success) throw new Error("task_action_suggestion_schema_invalid");
    result = parsed.data;
  } catch (error) {
    if (!isRuleFallbackEnabled()) {
      const message = error instanceof Error ? error.message : "task_action_suggestion_failed";
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
    fallbackReason = error instanceof Error ? error.message : "task_action_suggestion_fallback";
    result = buildFallbackTaskActionSuggestion({
      title: workItem.title,
      rationale: workItem.rationale,
      dueAt: workItem.dueAt
    });
    responseModel = "rule-fallback";
    outputSnapshot = {
      fallback: true,
      reason: fallbackReason
    };
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
    parsedResult: result as unknown as Json,
    errorMessage: usedFallback ? fallbackReason : null,
    completedAt: nowIso()
  });

  return {
    runId,
    result,
    usedFallback,
    fallbackReason
  };
}
