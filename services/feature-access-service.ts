import { deriveAiActionAccess, deriveFeatureAccess } from "@/lib/feature-access-utils";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { getOrgAiControlStatus } from "@/services/org-ai-settings-service";
import { getOrgFeatureFlagMap } from "@/services/org-feature-service";
import { canRunAiByEntitlement, getEntitlementStatus } from "@/services/plan-entitlement-service";
import type { OrgFeatureKey } from "@/types/productization";

type DbClient = ServerSupabaseClient;
export { deriveAiActionAccess, deriveFeatureAccess };

export async function checkOrgFeatureAccess(params: {
  supabase: DbClient;
  orgId: string;
  featureKey: OrgFeatureKey;
}): Promise<{
  allowed: boolean;
  reason: string | null;
}> {
  const flagMap = await getOrgFeatureFlagMap({
    supabase: params.supabase,
    orgId: params.orgId
  });

  const entitlement = await getEntitlementStatus({
    supabase: params.supabase,
    orgId: params.orgId,
    refreshUsage: false
  });

  return deriveFeatureAccess({
    featureKey: params.featureKey,
    featureEnabled: Boolean(flagMap[params.featureKey]),
    planStatus: entitlement.status
  });
}

export async function checkOrgAiActionAccess(params: {
  supabase: DbClient;
  orgId: string;
  featureKey: OrgFeatureKey;
}): Promise<{
  allowed: boolean;
  reason: string | null;
  fallbackOnly: boolean;
}> {
  const feature = await checkOrgFeatureAccess({
    supabase: params.supabase,
    orgId: params.orgId,
    featureKey: params.featureKey
  });

  const [aiStatus, entitlement] = await Promise.all([
    getOrgAiControlStatus({
      supabase: params.supabase,
      orgId: params.orgId
    }),
    getEntitlementStatus({
      supabase: params.supabase,
      orgId: params.orgId,
      refreshUsage: true
    })
  ]);

  const quotaCheck = canRunAiByEntitlement(entitlement);
  return deriveAiActionAccess({
    featureAllowed: feature.allowed,
    featureReason: feature.reason,
    providerConfigured: aiStatus.providerConfigured,
    providerReason: aiStatus.providerReason,
    quotaAllowed: quotaCheck.allowed,
    quotaReason: quotaCheck.reason
  });
}

export async function checkOrgAiScenarioAccess(params: {
  supabase: DbClient;
  orgId: string;
  featureKey: OrgFeatureKey;
  settingKey: "autoAnalysisEnabled" | "autoPlanEnabled" | "autoBriefEnabled" | "autoTouchpointReviewEnabled";
  refreshUsage?: boolean;
}): Promise<{
  allowed: boolean;
  reason: string | null;
  fallbackOnly: boolean;
}> {
  const feature = await checkOrgFeatureAccess({
    supabase: params.supabase,
    orgId: params.orgId,
    featureKey: params.featureKey
  });
  if (!feature.allowed) {
    return {
      allowed: false,
      reason: feature.reason ?? "Feature disabled",
      fallbackOnly: true
    };
  }

  const [aiStatus, entitlement] = await Promise.all([
    getOrgAiControlStatus({
      supabase: params.supabase,
      orgId: params.orgId
    }),
    getEntitlementStatus({
      supabase: params.supabase,
      orgId: params.orgId,
      refreshUsage: params.refreshUsage ?? true
    })
  ]);

  const settingEnabled = Boolean(aiStatus.settings[params.settingKey]);
  if (!settingEnabled) {
    return {
      allowed: false,
      reason: `Organization disabled ${params.settingKey}`,
      fallbackOnly: true
    };
  }

  const quotaCheck = canRunAiByEntitlement(entitlement);
  if (!quotaCheck.allowed) {
    return {
      allowed: false,
      reason: quotaCheck.reason ?? "AI quota reached",
      fallbackOnly: true
    };
  }

  if (!aiStatus.providerConfigured) {
    return {
      allowed: false,
      reason: aiStatus.providerReason ?? "AI provider not configured",
      fallbackOnly: true
    };
  }

  return {
    allowed: true,
    reason: null,
    fallbackOnly: false
  };
}
