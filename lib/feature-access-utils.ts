import type { OrgFeatureKey } from "@/types/productization";

export function deriveFeatureAccess(params: {
  featureKey: OrgFeatureKey;
  featureEnabled: boolean;
  planStatus: "active" | "paused" | "expired";
}): { allowed: boolean; reason: string | null } {
  if (!params.featureEnabled) {
    return {
      allowed: false,
      reason: `Feature ${params.featureKey} is disabled by organization settings`
    };
  }

  if (params.planStatus !== "active") {
    return {
      allowed: false,
      reason: "Organization plan is not active"
    };
  }

  return {
    allowed: true,
    reason: null
  };
}

export function deriveAiActionAccess(params: {
  featureAllowed: boolean;
  featureReason: string | null;
  providerConfigured: boolean;
  providerReason: string | null;
  quotaAllowed: boolean;
  quotaReason: string | null;
}): {
  allowed: boolean;
  reason: string | null;
  fallbackOnly: boolean;
} {
  if (!params.featureAllowed) {
    return {
      allowed: false,
      reason: params.featureReason,
      fallbackOnly: true
    };
  }

  if (!params.quotaAllowed) {
    return {
      allowed: false,
      reason: params.quotaReason,
      fallbackOnly: true
    };
  }

  if (!params.providerConfigured) {
    return {
      allowed: false,
      reason: params.providerReason ?? "AI provider not configured",
      fallbackOnly: true
    };
  }

  return {
    allowed: true,
    reason: null,
    fallbackOnly: false
  };
}
