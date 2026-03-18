import { getAiRuntimeEnv, hasDeepSeekApiKey } from "@/lib/env";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { mapOrgAiSettingsRow } from "@/services/mappers";
import type { Database } from "@/types/database";
import type { OrgAiSettings } from "@/types/productization";

type DbClient = ServerSupabaseClient;
type AiSettingsRow = Database["public"]["Tables"]["org_ai_settings"]["Row"];

function isDeepSeekConfigured(): boolean {
  return hasDeepSeekApiKey();
}

export function resolveProviderConfigStatus(provider: string): { provider: string; configured: boolean; reason: string | null } {
  if (provider === "deepseek") {
    const configured = isDeepSeekConfigured();
    return {
      provider,
      configured,
      reason: configured ? null : "Missing DEEPSEEK_API_KEY"
    };
  }

  return {
    provider,
    configured: false,
    reason: `Provider ${provider} not configured on server`
  };
}

export async function getOrgAiSettings(params: {
  supabase: DbClient;
  orgId: string;
}): Promise<OrgAiSettings> {
  const res = await params.supabase.from("org_ai_settings").select("*").eq("org_id", params.orgId).maybeSingle();
  if (res.error) throw new Error(res.error.message);

  const row = (res.data ?? null) as AiSettingsRow | null;
  if (row) return mapOrgAiSettingsRow(row);

  const aiEnv = getAiRuntimeEnv("org_ai_settings_default");
  const insertRes = await params.supabase
    .from("org_ai_settings")
    .insert({
      org_id: params.orgId,
      provider: "deepseek",
      model_default: aiEnv.deepseekModel ?? "deepseek-chat",
      model_reasoning: aiEnv.deepseekReasonerModel ?? "deepseek-reasoner",
      fallback_mode: "provider_then_rules",
      auto_analysis_enabled: true,
      auto_plan_enabled: true,
      auto_brief_enabled: true,
      auto_touchpoint_review_enabled: true,
      human_review_required_for_sensitive_actions: true,
      max_daily_ai_runs: 300,
      max_monthly_ai_runs: 5000
    })
    .select("*")
    .single();

  if (insertRes.error) throw new Error(insertRes.error.message);
  return mapOrgAiSettingsRow(insertRes.data as AiSettingsRow);
}

export async function updateOrgAiSettings(params: {
  supabase: DbClient;
  orgId: string;
  patch: Partial<{
    provider: OrgAiSettings["provider"];
    modelDefault: string;
    modelReasoning: string;
    fallbackMode: OrgAiSettings["fallbackMode"];
    autoAnalysisEnabled: boolean;
    autoPlanEnabled: boolean;
    autoBriefEnabled: boolean;
    autoTouchpointReviewEnabled: boolean;
    humanReviewRequiredForSensitiveActions: boolean;
    maxDailyAiRuns: number | null;
    maxMonthlyAiRuns: number | null;
  }>;
}): Promise<OrgAiSettings> {
  const payload: Database["public"]["Tables"]["org_ai_settings"]["Update"] = {};

  if (params.patch.provider !== undefined) payload.provider = params.patch.provider;
  if (params.patch.modelDefault !== undefined) payload.model_default = params.patch.modelDefault;
  if (params.patch.modelReasoning !== undefined) payload.model_reasoning = params.patch.modelReasoning;
  if (params.patch.fallbackMode !== undefined) payload.fallback_mode = params.patch.fallbackMode;
  if (params.patch.autoAnalysisEnabled !== undefined) payload.auto_analysis_enabled = params.patch.autoAnalysisEnabled;
  if (params.patch.autoPlanEnabled !== undefined) payload.auto_plan_enabled = params.patch.autoPlanEnabled;
  if (params.patch.autoBriefEnabled !== undefined) payload.auto_brief_enabled = params.patch.autoBriefEnabled;
  if (params.patch.autoTouchpointReviewEnabled !== undefined) payload.auto_touchpoint_review_enabled = params.patch.autoTouchpointReviewEnabled;
  if (params.patch.humanReviewRequiredForSensitiveActions !== undefined)
    payload.human_review_required_for_sensitive_actions = params.patch.humanReviewRequiredForSensitiveActions;
  if (params.patch.maxDailyAiRuns !== undefined) payload.max_daily_ai_runs = params.patch.maxDailyAiRuns;
  if (params.patch.maxMonthlyAiRuns !== undefined) payload.max_monthly_ai_runs = params.patch.maxMonthlyAiRuns;

  const res = await params.supabase
    .from("org_ai_settings")
    .update(payload)
    .eq("org_id", params.orgId)
    .select("*")
    .single();

  if (res.error) throw new Error(res.error.message);
  return mapOrgAiSettingsRow(res.data as AiSettingsRow);
}

export async function getOrgAiControlStatus(params: {
  supabase: DbClient;
  orgId: string;
}): Promise<{
  settings: OrgAiSettings;
  providerConfigured: boolean;
  providerReason: string | null;
}> {
  const settings = await getOrgAiSettings(params);
  const providerStatus = resolveProviderConfigStatus(settings.provider);

  return {
    settings,
    providerConfigured: providerStatus.configured,
    providerReason: providerStatus.reason
  };
}
