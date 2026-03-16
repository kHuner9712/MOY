import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { mapOrgSettingsRow } from "@/services/mappers";
import type { Database } from "@/types/database";
import type { OrgSettings } from "@/types/productization";

type DbClient = ServerSupabaseClient;
type OrgSettingsRow = Database["public"]["Tables"]["org_settings"]["Row"];

const DEFAULT_ONBOARDING_STEPS: Record<string, boolean> = {
  org_profile: false,
  ai_setup: false,
  team_invite: false,
  first_data: false,
  first_plan_or_brief: false,
  first_deal_room: false,
  manager_view: false
};

export async function getOrgSettings(params: { supabase: DbClient; orgId: string }): Promise<OrgSettings> {
  const res = await params.supabase.from("org_settings").select("*").eq("org_id", params.orgId).maybeSingle();
  if (res.error) throw new Error(res.error.message);

  const row = (res.data ?? null) as OrgSettingsRow | null;
  if (row) return mapOrgSettingsRow(row);

  const orgRes = await params.supabase.from("organizations").select("name").eq("id", params.orgId).single();
  if (orgRes.error) throw new Error(orgRes.error.message);

  const insertRes = await params.supabase
    .from("org_settings")
    .insert({
      org_id: params.orgId,
      org_display_name: (orgRes.data as { name: string }).name,
      brand_name: "MOY",
      timezone: "Asia/Shanghai",
      locale: "zh-CN",
      onboarding_step_state: DEFAULT_ONBOARDING_STEPS
    })
    .select("*")
    .single();

  if (insertRes.error) throw new Error(insertRes.error.message);
  return mapOrgSettingsRow(insertRes.data as OrgSettingsRow);
}

export async function updateOrgSettings(params: {
  supabase: DbClient;
  orgId: string;
  patch: Partial<{
    orgDisplayName: string;
    brandName: string;
    industryHint: string | null;
    timezone: string;
    locale: string;
    defaultCustomerStages: string[];
    defaultOpportunityStages: string[];
    defaultAlertRules: Record<string, number>;
    defaultFollowupSlaDays: number;
    onboardingCompleted: boolean;
    onboardingStepState: Record<string, boolean>;
  }>;
}): Promise<OrgSettings> {
  const updatePayload: Database["public"]["Tables"]["org_settings"]["Update"] = {};

  if (params.patch.orgDisplayName !== undefined) updatePayload.org_display_name = params.patch.orgDisplayName;
  if (params.patch.brandName !== undefined) updatePayload.brand_name = params.patch.brandName;
  if (params.patch.industryHint !== undefined) updatePayload.industry_hint = params.patch.industryHint;
  if (params.patch.timezone !== undefined) updatePayload.timezone = params.patch.timezone;
  if (params.patch.locale !== undefined) updatePayload.locale = params.patch.locale;
  if (params.patch.defaultCustomerStages !== undefined) updatePayload.default_customer_stages = params.patch.defaultCustomerStages;
  if (params.patch.defaultOpportunityStages !== undefined) updatePayload.default_opportunity_stages = params.patch.defaultOpportunityStages;
  if (params.patch.defaultAlertRules !== undefined) updatePayload.default_alert_rules = params.patch.defaultAlertRules;
  if (params.patch.defaultFollowupSlaDays !== undefined) updatePayload.default_followup_sla_days = params.patch.defaultFollowupSlaDays;
  if (params.patch.onboardingCompleted !== undefined) updatePayload.onboarding_completed = params.patch.onboardingCompleted;
  if (params.patch.onboardingStepState !== undefined) updatePayload.onboarding_step_state = params.patch.onboardingStepState;

  const res = await params.supabase
    .from("org_settings")
    .update(updatePayload)
    .eq("org_id", params.orgId)
    .select("*")
    .single();

  if (res.error) throw new Error(res.error.message);
  return mapOrgSettingsRow(res.data as OrgSettingsRow);
}

export async function patchOnboardingSteps(params: {
  supabase: DbClient;
  orgId: string;
  steps: Record<string, boolean>;
  completeIfThreshold?: boolean;
}): Promise<OrgSettings> {
  const current = await getOrgSettings({
    supabase: params.supabase,
    orgId: params.orgId
  });

  const merged = {
    ...DEFAULT_ONBOARDING_STEPS,
    ...current.onboardingStepState,
    ...params.steps
  };

  const completedCount = Object.values(merged).filter(Boolean).length;
  const totalCount = Object.keys(DEFAULT_ONBOARDING_STEPS).length;
  const shouldComplete = params.completeIfThreshold ? completedCount >= Math.ceil(totalCount * 0.72) : current.onboardingCompleted;

  return updateOrgSettings({
    supabase: params.supabase,
    orgId: params.orgId,
    patch: {
      onboardingStepState: merged,
      onboardingCompleted: shouldComplete
    }
  });
}
