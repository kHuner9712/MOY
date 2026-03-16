import type {
  EntitlementStatus,
  OnboardingChecklist,
  OnboardingRecommendationResult,
  TemplateApplicationSummary,
  TemplateApplyMode,
  TemplateFitRecommendation,
  UsageHealthSummaryResult
} from "@/types/productization";

export function buildFallbackOnboardingRecommendation(params: {
  checklist: OnboardingChecklist;
  featureFlags: Record<string, boolean>;
  hasAiConfigured: boolean;
}): OnboardingRecommendationResult {
  const missing = params.checklist.items.filter((item) => !item.completed).map((item) => item.title);

  const nextSteps: string[] = [];
  if (!params.checklist.items.find((item) => item.key === "org_profile")?.completed) {
    nextSteps.push("Complete organization profile and default sales stages first.");
  }
  if (!params.hasAiConfigured) {
    nextSteps.push("Configure DeepSeek key to unlock automatic analysis and briefs.");
  }
  if (!params.checklist.items.find((item) => item.key === "team_invite")?.completed) {
    nextSteps.push("Invite at least 1 manager and 2 sales members for collaborative workflow demo.");
  }
  if (!params.checklist.items.find((item) => item.key === "first_data")?.completed) {
    nextSteps.push("Create first customer set or run demo seed to populate core modules.");
  }

  if (nextSteps.length === 0) {
    nextSteps.push("Your foundation is ready. Focus on daily task planning and deal room execution.");
  }

  const disabledFeatures = Object.entries(params.featureFlags)
    .filter(([, enabled]) => !enabled)
    .map(([key]) => key);

  return {
    nextBestSetupSteps: nextSteps,
    missingFoundations: missing,
    recommendedDemoFlow: [
      "Open /today and generate today's task plan.",
      "Use /capture to convert one communication note into structured followup.",
      "Open /deals and create one strategic deal room.",
      "Generate /briefings morning brief for sales and manager roles.",
      "Review /manager/outcomes to show closed-loop effectiveness."
    ],
    recommendedTeamActions: [
      "Set owner/admin to monitor usage and quota weekly.",
      "Assign one manager as onboarding champion to run first deal room simulation.",
      "Ask each sales rep to complete one prep card + one outcome capture loop."
    ],
    risksIfSkipped: [
      ...(!params.hasAiConfigured ? ["AI-dependent actions will downgrade to rule fallback, reducing demo impact."] : []),
      ...(disabledFeatures.length > 0 ? [`Disabled features may hide key workflow value: ${disabledFeatures.join(", ")}.`] : []),
      ...(missing.length > 0 ? ["Incomplete onboarding may cause fragmented data flow across modules."] : [])
    ]
  };
}

export function buildFallbackUsageHealthSummary(params: {
  entitlement: EntitlementStatus;
  monthlyUsage: {
    aiRunsCount: number;
    prepCardsCount: number;
    draftsCount: number;
    reportsCount: number;
    touchpointEventsCount: number;
    documentProcessedCount: number;
    workPlanGenerationsCount: number;
  };
}): UsageHealthSummaryResult {
  const hotFeatures: string[] = [];
  const underusedFeatures: string[] = [];

  if (params.monthlyUsage.aiRunsCount > 0) hotFeatures.push("AI analysis and generation");
  if (params.monthlyUsage.touchpointEventsCount > 0) hotFeatures.push("External touchpoint tracking");
  if (params.monthlyUsage.workPlanGenerationsCount > 0) hotFeatures.push("Today plan orchestration");

  if (params.monthlyUsage.prepCardsCount < 5) underusedFeatures.push("Preparation cards");
  if (params.monthlyUsage.reportsCount < 3) underusedFeatures.push("Morning/report generation");
  if (params.monthlyUsage.draftsCount < 5) underusedFeatures.push("Action content drafts");

  const quotaRisks: string[] = [];
  if (params.entitlement.quotaExceeded) {
    quotaRisks.push("At least one monthly quota has been exceeded.");
  }
  if (params.entitlement.quotaNearLimit) {
    quotaRisks.push("Usage is near monthly limit for one or more modules.");
  }
  if (params.entitlement.seatUsed >= params.entitlement.seatLimit) {
    quotaRisks.push("Seat capacity is full.");
  }

  return {
    usageSummary: `Plan ${params.entitlement.planTier} is ${params.entitlement.status}. AI usage ${params.entitlement.aiRunUsedMonthly}/${params.entitlement.aiRunLimitMonthly}.`,
    hotFeatures,
    underusedFeatures,
    quotaRisks,
    recommendedAdjustments: [
      "Prioritize high-value AI generation scenarios when near quota.",
      "Enable onboarding checklist nudges for underused modules.",
      "Review seat allocation and suspend inactive invited users if needed."
    ]
  };
}

export function buildFallbackTemplateFitRecommendation(params: {
  industryHint: string | null;
  availableTemplateKeys: string[];
  teamSize: number;
}): TemplateFitRecommendation {
  const hint = (params.industryHint ?? "").toLowerCase();

  let recommendedTemplateKey = "generic";
  if (hint.includes("软件") || hint.includes("saas") || hint.includes("tob") || hint.includes("to b")) recommendedTemplateKey = "b2b_software";
  else if (hint.includes("教育") || hint.includes("培训") || hint.includes("课程")) recommendedTemplateKey = "education_training";
  else if (hint.includes("制造") || hint.includes("工业") || hint.includes("工厂")) recommendedTemplateKey = "manufacturing";
  else if (hint.includes("渠道") || hint.includes("代理") || hint.includes("经销")) recommendedTemplateKey = "channel_sales";
  else if (hint.includes("咨询") || hint.includes("服务")) recommendedTemplateKey = "consulting_services";

  if (!params.availableTemplateKeys.includes(recommendedTemplateKey)) {
    recommendedTemplateKey = params.availableTemplateKeys.includes("generic") ? "generic" : params.availableTemplateKeys[0] ?? "generic";
  }

  const mode: TemplateApplyMode = params.teamSize <= 2 ? "trial_bootstrap" : "onboarding_default";

  return {
    recommendedTemplateKey,
    fitReasons: [
      "Based on industry hint keyword matching and current org setup completeness.",
      "Template chosen with conservative rule-first strategy (fallback mode)."
    ],
    risksOfMismatch: [
      "If template differs from actual sales motion, stage/alert defaults may create noisy suggestions.",
      "Apply in additive or merge mode first to avoid disruptive overwrites."
    ],
    recommendedApplyMode: mode,
    recommendedOverrides: ["Check stage aliases", "Tune alert thresholds", "Adjust manager intervention signals"]
  };
}

export function buildFallbackTemplateApplicationSummary(params: {
  changedKeys: string[];
  unchangedKeys: string[];
  strategy: "additive_only" | "merge_prefer_existing" | "template_override_existing";
}): TemplateApplicationSummary {
  return {
    whatWillChange: params.changedKeys.length > 0 ? params.changedKeys : ["No major config changes detected in fallback summary."],
    whatWillNotChange: params.unchangedKeys.length > 0 ? params.unchangedKeys : ["Existing records and historical pipeline data are preserved."],
    cautionNotes: [
      `Apply strategy=${params.strategy}.`,
      "Review stage and alert diffs before reapplying template to existing org data."
    ],
    recommendedNextSteps: [
      "Run onboarding checklist refresh.",
      "Generate first today plan and manager brief after template apply.",
      "Validate playbook/prep seed and adjust overrides as needed."
    ]
  };
}
