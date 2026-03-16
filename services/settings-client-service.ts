import type {
  EntitlementStatus,
  IndustryTemplate,
  IndustryTemplateContext,
  OrgTemplateOverride,
  TemplateApplicationRun,
  TemplateApplyMode,
  TemplateApplyStrategy,
  TemplateFitRecommendation,
  TemplateApplicationSummary,
  OnboardingChecklist,
  OnboardingRecommendationResult,
  OnboardingRun,
  OrgAiSettings,
  OrgFeatureFlag,
  OrgFeatureKey,
  OrgInvite,
  OrgMembership,
  OrgSettings,
  OrgUsageCounter,
  UserUsageCounter
} from "@/types/productization";

interface ApiPayload<T> {
  success: boolean;
  data: T | null;
  error: string | null;
}

async function readPayload<T>(response: Response): Promise<T> {
  const payload = (await response.json()) as ApiPayload<T>;
  if (!response.ok || !payload.success || !payload.data) {
    throw new Error(payload.error ?? "Request failed");
  }
  return payload.data;
}

export const settingsClientService = {
  async getOrgSettings(): Promise<{ settings: OrgSettings }> {
    const response = await fetch("/api/settings/org", { method: "GET" });
    return readPayload<{ settings: OrgSettings }>(response);
  },

  async updateOrgSettings(patch: Partial<{
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
  }>): Promise<{ settings: OrgSettings }> {
    const response = await fetch("/api/settings/org", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch)
    });
    return readPayload<{ settings: OrgSettings }>(response);
  },

  async getTeamSettings(): Promise<{
    role: string;
    canManageTeam: boolean;
    canViewUsage: boolean;
    members: OrgMembership[];
    invites: OrgInvite[];
  }> {
    const response = await fetch("/api/settings/team", { method: "GET" });
    return readPayload(response);
  },

  async inviteMember(payload: { email: string; intendedRole: OrgMembership["role"]; expiresAt?: string }): Promise<{ invite: OrgInvite; inviteLink: string }> {
    const response = await fetch("/api/settings/team/invite", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return readPayload(response);
  },

  async updateMemberRole(payload: { membershipId: string; role: OrgMembership["role"] }): Promise<{ membership: OrgMembership }> {
    const response = await fetch("/api/settings/team/update-role", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return readPayload(response);
  },

  async updateMemberSeatStatus(payload: { membershipId: string; seatStatus: OrgMembership["seatStatus"] }): Promise<{ membership: OrgMembership }> {
    const response = await fetch("/api/settings/team/update-seat-status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return readPayload(response);
  },

  async getAiSettings(): Promise<{
    role: string;
    canManage: boolean;
    aiStatus: {
      settings: OrgAiSettings;
      providerConfigured: boolean;
      providerReason: string | null;
    };
    featureFlags: OrgFeatureFlag[];
  }> {
    const response = await fetch("/api/settings/ai", { method: "GET" });
    return readPayload(response);
  },

  async updateAiSettings(patch: Partial<{
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
    featureFlags: Partial<Record<OrgFeatureKey, boolean>>;
  }>): Promise<{
    settings: OrgAiSettings;
    status: {
      settings: OrgAiSettings;
      providerConfigured: boolean;
      providerReason: string | null;
    };
    featureFlags: OrgFeatureFlag[];
  }> {
    const response = await fetch("/api/settings/ai", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch)
    });
    return readPayload(response);
  },

  async getUsageSettings(): Promise<{
    usage: {
      daily: OrgUsageCounter | null;
      monthly: OrgUsageCounter | null;
      topUsersMonthly: UserUsageCounter[];
    };
    entitlement: EntitlementStatus;
    featureFlags: Record<string, boolean>;
    summary: {
      summary: {
        usageSummary: string;
        hotFeatures: string[];
        underusedFeatures: string[];
        quotaRisks: string[];
        recommendedAdjustments: string[];
      };
      usedFallback: boolean;
      fallbackReason: string | null;
    };
  }> {
    const response = await fetch("/api/settings/usage", { method: "GET" });
    return readPayload(response);
  },

  async getOnboardingSettings(): Promise<{
    role: string;
    checklist: OnboardingChecklist;
    settings: OrgSettings;
    aiStatus: {
      settings: OrgAiSettings;
      providerConfigured: boolean;
      providerReason: string | null;
    };
    featureFlags: Record<string, boolean>;
    entitlement: EntitlementStatus;
    latestRuns: OnboardingRun[];
    currentTemplate: {
      templateKey: string;
      displayName: string;
    } | null;
    recommendation: OnboardingRecommendationResult;
    recommendationUsedFallback: boolean;
    recommendationFallbackReason: string | null;
  }> {
    const response = await fetch("/api/settings/onboarding", { method: "GET" });
    return readPayload(response);
  },

  async runOnboarding(payload: { runType: "first_time_setup" | "trial_bootstrap" | "demo_seed" | "reinitialize_demo" }): Promise<{
    run: OnboardingRun;
    message: string;
    partialSuccess: boolean;
    detail: Record<string, unknown>;
  }> {
    const response = await fetch("/api/settings/onboarding/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return readPayload(response);
  },

  async runDemoSeed(): Promise<{
    runId: string;
    status: "completed" | "failed";
    partialSuccess: boolean;
    summary: string;
    steps: Array<{
      name: string;
      success: boolean;
      inserted: number;
      message: string;
    }>;
    inserted: Record<string, number>;
  }> {
    const response = await fetch("/api/settings/demo-seed/run", {
      method: "POST"
    });
    return readPayload(response);
  },

  async getTemplateCenter(): Promise<{
    role: string;
    templates: IndustryTemplate[];
    currentTemplate: IndustryTemplate | null;
    currentAssignment: IndustryTemplateContext["assignment"];
    recentRuns: TemplateApplicationRun[];
  }> {
    const response = await fetch("/api/settings/templates", { method: "GET" });
    return readPayload(response);
  },

  async getTemplateDetail(templateIdOrKey: string): Promise<{
    template: IndustryTemplate;
    scenarioPacks: IndustryTemplateContext["scenarioPacks"];
    seededPlaybookTemplates: IndustryTemplateContext["seededPlaybookTemplates"];
  }> {
    const response = await fetch(`/api/settings/templates/${encodeURIComponent(templateIdOrKey)}`, { method: "GET" });
    return readPayload(response);
  },

  async getCurrentTemplate(): Promise<IndustryTemplateContext & { role: string }> {
    const response = await fetch("/api/settings/templates/current", { method: "GET" });
    return readPayload(response);
  },

  async recommendTemplate(industryHint?: string | null): Promise<{
    recommendation: TemplateFitRecommendation;
    usedFallback: boolean;
    fallbackReason: string | null;
  }> {
    const response = await fetch("/api/settings/templates/recommend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ industryHint: industryHint ?? null })
    });
    return readPayload(response);
  },

  async previewTemplateApply(payload: {
    templateIdOrKey: string;
    applyMode?: TemplateApplyMode;
    applyStrategy?: TemplateApplyStrategy;
  }): Promise<{
    run: TemplateApplicationRun;
    diff: {
      changedKeys: string[];
      unchangedKeys: string[];
      notes: string[];
    };
    summary: TemplateApplicationSummary;
    summaryUsedFallback: boolean;
    summaryFallbackReason: string | null;
    template: IndustryTemplate;
  }> {
    const response = await fetch("/api/settings/templates/preview-apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return readPayload(response);
  },

  async applyTemplate(payload: {
    templateIdOrKey: string;
    applyMode?: TemplateApplyMode;
    applyStrategy?: TemplateApplyStrategy;
    generateDemoSeed?: boolean;
    overrides?: Array<{
      overrideType: OrgTemplateOverride["overrideType"];
      overridePayload: Record<string, unknown>;
    }>;
  }): Promise<{
    run: TemplateApplicationRun;
    appliedTemplateKey: string;
    playbookSeed: {
      createdCount: number;
      skippedCount: number;
      createdPlaybookIds: string[];
    };
    demoSeed: {
      executed: boolean;
      summary: string | null;
      partialSuccess: boolean;
    };
  }> {
    const response = await fetch("/api/settings/templates/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return readPayload(response);
  },

  async upsertTemplateOverride(payload: {
    templateId?: string;
    overrideType: OrgTemplateOverride["overrideType"];
    overridePayload: Record<string, unknown>;
  }): Promise<{ override: OrgTemplateOverride }> {
    const response = await fetch("/api/settings/templates/overrides", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return readPayload(response);
  }
};
