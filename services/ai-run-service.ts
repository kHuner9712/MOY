import type { ServerSupabaseClient } from "@/lib/supabase/types";
import type { AiProviderId, AiResultSource, AiRun, AiRunStatus, AiScenario, AiTriggerSource } from "@/types/ai";
import type { Database } from "@/types/database";

type DbClient = ServerSupabaseClient;
type AiRunRow = Database["public"]["Tables"]["ai_runs"]["Row"];

function mapAiRunRow(row: AiRunRow): AiRun {
  return {
    id: row.id,
    org_id: row.org_id,
    customer_id: row.customer_id,
    followup_id: row.followup_id,
    triggered_by_user_id: row.triggered_by_user_id,
    trigger_source: row.trigger_source,
    scenario: row.scenario,
    provider: row.provider,
    model: row.model,
    prompt_version: row.prompt_version,
    status: row.status,
    input_snapshot: (row.input_snapshot as Record<string, unknown> | null) ?? null,
    output_snapshot: (row.output_snapshot as Record<string, unknown> | null) ?? null,
    parsed_result: (row.parsed_result as Record<string, unknown> | null) ?? null,
    error_message: row.error_message,
    latency_ms: row.latency_ms,
    result_source: row.result_source,
    fallback_reason: row.fallback_reason,
    started_at: row.started_at,
    completed_at: row.completed_at,
    created_at: row.created_at
  };
}

export async function createAiRun(params: {
  supabase: DbClient;
  orgId: string;
  customerId?: string | null;
  followupId?: string | null;
  triggeredByUserId?: string | null;
  triggerSource: AiTriggerSource;
  scenario: AiScenario;
  provider: AiProviderId;
  model: string;
  promptVersion: string;
  inputSnapshot: Record<string, unknown>;
}): Promise<AiRun> {
  const { data, error } = await params.supabase
    .from("ai_runs")
    .insert({
      org_id: params.orgId,
      customer_id: params.customerId ?? null,
      followup_id: params.followupId ?? null,
      triggered_by_user_id: params.triggeredByUserId ?? null,
      trigger_source: params.triggerSource,
      scenario: params.scenario,
      provider: params.provider,
      model: params.model,
      prompt_version: params.promptVersion,
      status: "queued",
      input_snapshot: params.inputSnapshot
    })
    .select("*")
    .single();

  if (error || !data) {
    throw new Error(error?.message ?? "Failed to create ai run");
  }

  return mapAiRunRow(data as AiRunRow);
}

export async function updateAiRunStatus(params: {
  supabase: DbClient;
  runId: string;
  status: AiRunStatus;
  provider?: AiProviderId;
  model?: string;
  outputSnapshot?: Record<string, unknown> | null;
  parsedResult?: Record<string, unknown> | null;
  errorMessage?: string | null;
  latencyMs?: number | null;
  resultSource?: AiResultSource;
  fallbackReason?: string | null;
  startedAt?: string | null;
  completedAt?: string | null;
}): Promise<void> {
  const payload: Database["public"]["Tables"]["ai_runs"]["Update"] = {
    status: params.status
  };

  if (params.provider !== undefined) payload.provider = params.provider;
  if (params.model !== undefined) payload.model = params.model;
  if (params.outputSnapshot !== undefined) payload.output_snapshot = params.outputSnapshot as any;
  if (params.parsedResult !== undefined) payload.parsed_result = params.parsedResult as any;
  if (params.errorMessage !== undefined) payload.error_message = params.errorMessage;
  if (params.latencyMs !== undefined) payload.latency_ms = params.latencyMs;
  if (params.resultSource !== undefined) payload.result_source = params.resultSource;
  if (params.fallbackReason !== undefined) payload.fallback_reason = params.fallbackReason;
  if (params.startedAt !== undefined) payload.started_at = params.startedAt;
  if (params.completedAt !== undefined) payload.completed_at = params.completedAt;

  const { error } = await params.supabase.from("ai_runs").update(payload).eq("id", params.runId);
  if (error) {
    throw new Error(error.message);
  }
}

export async function listCustomerAiRuns(params: {
  supabase: DbClient;
  customerId: string;
  limit?: number;
}): Promise<AiRun[]> {
  const { data, error } = await params.supabase
    .from("ai_runs")
    .select("*")
    .eq("customer_id", params.customerId)
    .order("created_at", { ascending: false })
    .limit(params.limit ?? 8);

  if (error) {
    throw new Error(error.message);
  }

  return (data ?? []).map((row: AiRunRow) => mapAiRunRow(row));
}


