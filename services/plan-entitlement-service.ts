import {
  canProcessDocumentsByEntitlement,
  canRunAiByEntitlement,
  canUseTouchpointsByEntitlement,
  hasSeatCapacity
} from "@/lib/plan-entitlement-utils";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { mapOrgPlanProfileRow } from "@/services/mappers";
import { getUsageCounters } from "@/services/usage-metering-service";
import type { Database } from "@/types/database";
import type { EntitlementStatus, OrgPlanProfile } from "@/types/productization";

type DbClient = ServerSupabaseClient;
type PlanRow = Database["public"]["Tables"]["org_plan_profiles"]["Row"];

function defaultPlanByTier(tier: OrgPlanProfile["planTier"]): {
  seatLimit: number;
  aiRuns: number;
  documents: number;
  touchpoints: number;
  advanced: boolean;
} {
  switch (tier) {
    case "demo":
      return { seatLimit: 10, aiRuns: 800, documents: 300, touchpoints: 1200, advanced: true };
    case "trial":
      return { seatLimit: 8, aiRuns: 1500, documents: 500, touchpoints: 2500, advanced: true };
    case "starter":
      return { seatLimit: 15, aiRuns: 4000, documents: 1200, touchpoints: 8000, advanced: false };
    case "growth":
      return { seatLimit: 40, aiRuns: 15000, documents: 5000, touchpoints: 30000, advanced: true };
    case "enterprise":
      return { seatLimit: 200, aiRuns: 100000, documents: 30000, touchpoints: 200000, advanced: true };
    default:
      return { seatLimit: 8, aiRuns: 1500, documents: 500, touchpoints: 2500, advanced: true };
  }
}

export async function getOrgPlanProfile(params: {
  supabase: DbClient;
  orgId: string;
}): Promise<OrgPlanProfile> {
  const res = await params.supabase.from("org_plan_profiles").select("*").eq("org_id", params.orgId).maybeSingle();
  if (res.error) throw new Error(res.error.message);

  const row = (res.data ?? null) as PlanRow | null;
  if (row) return mapOrgPlanProfileRow(row);

  const orgRes = await params.supabase.from("organizations").select("slug").eq("id", params.orgId).maybeSingle();
  if (orgRes.error) throw new Error(orgRes.error.message);
  const slug = String(orgRes.data?.slug ?? "").toLowerCase();
  const fallbackTier: OrgPlanProfile["planTier"] = slug.includes("demo") ? "demo" : slug.includes("trial") ? "trial" : "starter";

  const defaults = defaultPlanByTier(fallbackTier);
  const insertRes = await params.supabase
    .from("org_plan_profiles")
    .insert({
      org_id: params.orgId,
      plan_tier: fallbackTier,
      seat_limit: defaults.seatLimit,
      ai_run_limit_monthly: defaults.aiRuns,
      document_limit_monthly: defaults.documents,
      touchpoint_limit_monthly: defaults.touchpoints,
      advanced_features_enabled: defaults.advanced,
      status: "active"
    })
    .select("*")
    .single();
  if (insertRes.error) throw new Error(insertRes.error.message);

  return mapOrgPlanProfileRow(insertRes.data as PlanRow);
}

export async function getEntitlementStatus(params: {
  supabase: DbClient;
  orgId: string;
  refreshUsage?: boolean;
}): Promise<EntitlementStatus> {
  const plan = await getOrgPlanProfile({
    supabase: params.supabase,
    orgId: params.orgId
  });

  const usage = await getUsageCounters({
    supabase: params.supabase,
    orgId: params.orgId,
    refresh: params.refreshUsage ?? true
  });

  const seatCountRes = await params.supabase
    .from("org_memberships")
    .select("id", { count: "exact", head: true })
    .eq("org_id", params.orgId)
    .in("seat_status", ["active", "invited"]);

  if (seatCountRes.error) throw new Error(seatCountRes.error.message);

  const seatUsed = seatCountRes.count ?? 0;
  const aiRunUsedMonthly = usage.monthly?.aiRunsCount ?? 0;
  const documentUsedMonthly = usage.monthly?.documentProcessedCount ?? 0;
  const touchpointUsedMonthly = usage.monthly?.touchpointEventsCount ?? 0;

  const remainingAiRunsMonthly = Math.max(0, plan.aiRunLimitMonthly - aiRunUsedMonthly);
  const quotaExceeded =
    aiRunUsedMonthly >= plan.aiRunLimitMonthly ||
    documentUsedMonthly >= plan.documentLimitMonthly ||
    touchpointUsedMonthly >= plan.touchpointLimitMonthly ||
    seatUsed > plan.seatLimit;

  const quotaNearLimit =
    aiRunUsedMonthly >= Math.floor(plan.aiRunLimitMonthly * 0.85) ||
    documentUsedMonthly >= Math.floor(plan.documentLimitMonthly * 0.85) ||
    touchpointUsedMonthly >= Math.floor(plan.touchpointLimitMonthly * 0.85) ||
    seatUsed >= Math.floor(plan.seatLimit * 0.9);

  return {
    planTier: plan.planTier,
    status: plan.status,
    seatLimit: plan.seatLimit,
    seatUsed,
    aiRunLimitMonthly: plan.aiRunLimitMonthly,
    aiRunUsedMonthly,
    documentLimitMonthly: plan.documentLimitMonthly,
    documentUsedMonthly,
    touchpointLimitMonthly: plan.touchpointLimitMonthly,
    touchpointUsedMonthly,
    remainingAiRunsMonthly,
    quotaNearLimit,
    quotaExceeded,
    advancedFeaturesEnabled: plan.advancedFeaturesEnabled
  };
}
export { canProcessDocumentsByEntitlement, canRunAiByEntitlement, canUseTouchpointsByEntitlement, hasSeatCapacity };
