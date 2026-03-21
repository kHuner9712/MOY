import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import { buildFallbackGrowthPipelineSummary } from "@/lib/commercialization-fallback";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { getActivePromptVersion } from "@/services/ai-prompt-service";
import { createAiRun, updateAiRunStatus } from "@/services/ai-run-service";
import { listConversionEvents, listInboundLeads } from "@/services/inbound-lead-service";
import { listTrialConversionTracks } from "@/services/trial-conversion-service";
import { listDemoRequests } from "@/services/demo-request-service";
import { listTrialRequests } from "@/services/trial-request-service";
import {
  buildResolvedOrgRuntimeConfig,
  buildPromptAugmentationContext,
  summarizeResolvedIndustryTemplateContext
} from "@/services/template-org-runtime-bridge-service";
import { growthPipelineSummaryResultSchema } from "@/types/ai";
import type {
  ConversionEvent,
  GrowthSummary,
  InboundLead,
  InboundLeadSource,
  TrialConversionTrack,
  TrialRequest
} from "@/types/commercialization";

type DbClient = ServerSupabaseClient;

function withinDays(isoText: string, periodDays: number): boolean {
  const threshold = Date.now() - periodDays * 24 * 60 * 60 * 1000;
  return new Date(isoText).getTime() >= threshold;
}

function safePercent(numerator: number, denominator: number): number {
  if (denominator <= 0) return 0;
  return Number((numerator / denominator).toFixed(4));
}

function nowIso(): string {
  return new Date().toISOString();
}

function normalizeIndustry(value: string | null | undefined): string {
  const text = (value ?? "").trim();
  return text.length > 0 ? text : "unknown";
}

async function runGrowthPipelineSummaryAi(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  periodDays: number;
  leads: InboundLead[];
  demoRequested: number;
  demoCompleted: number;
  trialRequested: number;
  trialActivated: number;
  convertedCount: number;
  bySource: Array<{ source: InboundLeadSource; count: number }>;
  byIndustry: Array<{ industry: string; leadCount: number; trialCount: number; convertedCount: number }>;
}): Promise<{
  summary: GrowthSummary["aiSummary"];
  usedFallback: boolean;
  fallbackReason: string | null;
}> {
  const runtimeTemplateContext = await buildResolvedOrgRuntimeConfig({
    supabase: params.supabase,
    orgId: params.orgId
  });
  const promptAugmentation = buildPromptAugmentationContext({
    scenario: "growth_pipeline_summary",
    context: runtimeTemplateContext
  });

  const provider = getAiProvider();
  const model = provider.getDefaultModel({ reasoning: false });
  const prompt = await getActivePromptVersion({
    supabase: params.supabase,
    orgId: params.orgId,
    scenario: "growth_pipeline_summary",
    providerId: provider.id
  });

  const fallback = buildFallbackGrowthPipelineSummary({
    leadsTotal: params.leads.length,
    demoRequested: params.demoRequested,
    demoCompleted: params.demoCompleted,
    trialRequested: params.trialRequested,
    trialActivated: params.trialActivated,
    convertedCount: params.convertedCount
  });

  const run = await createAiRun({
    supabase: params.supabase,
    orgId: params.orgId,
    triggeredByUserId: params.actorUserId,
    triggerSource: "manager_review",
    scenario: "growth_pipeline_summary",
    provider: provider.id,
    model,
    promptVersion: prompt.version,
    inputSnapshot: {
      period_days: params.periodDays,
      lead_count: params.leads.length,
      demo_requested: params.demoRequested,
      demo_completed: params.demoCompleted,
      trial_requested: params.trialRequested,
      trial_activated: params.trialActivated,
      converted_count: params.convertedCount,
      by_source: params.bySource,
      by_industry: params.byIndustry,
      runtime_template_context: summarizeResolvedIndustryTemplateContext(runtimeTemplateContext)
    }
  });

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "running",
    startedAt: nowIso()
  });

  let usedFallback = false;
  let fallbackReason: string | null = null;
  let outputSnapshot: Record<string, unknown> = {
    fallback: true,
    reason: "not_started"
  };
  let result = fallback;
  let responseModel = model;

  try {
    if (!provider.isConfigured()) throw new Error("provider_not_configured");

    const response = await provider.chatCompletion({
      scenario: "growth_pipeline_summary",
      model,
      systemPrompt: prompt.systemPrompt,
      developerPrompt: `${prompt.developerPrompt}${promptAugmentation ? `\n\n${promptAugmentation}` : ""}\n\nOutput schema:\n${JSON.stringify(prompt.outputSchema)}`,
      userPrompt: JSON.stringify({
        period_days: params.periodDays,
        lead_count: params.leads.length,
        demo_requested: params.demoRequested,
        demo_completed: params.demoCompleted,
        trial_requested: params.trialRequested,
        trial_activated: params.trialActivated,
        converted_count: params.convertedCount,
        by_source: params.bySource,
        by_industry: params.byIndustry,
        runtime_template_context: summarizeResolvedIndustryTemplateContext(runtimeTemplateContext)
      }),
      jsonMode: true,
      strictMode: true
    });

    if (response.error) throw new Error(response.error);

    const candidate = response.parsedJson ?? (response.rawText ? JSON.parse(response.rawText) : null);
    const parsed = growthPipelineSummaryResultSchema.safeParse(candidate);
    if (!parsed.success) throw new Error("growth_pipeline_summary_schema_invalid");

    result = {
      funnelSummary: parsed.data.funnel_summary,
      bestChannels: parsed.data.best_channels,
      weakPoints: parsed.data.weak_points,
      highPotentialSegments: parsed.data.high_potential_segments,
      nextBestActions: parsed.data.next_best_actions
    };
    responseModel = response.model;
    outputSnapshot = response.rawResponse;
  } catch (error) {
    if (!isRuleFallbackEnabled()) {
      const message = error instanceof Error ? error.message : "growth_pipeline_summary_failed";
      await updateAiRunStatus({
        supabase: params.supabase,
        runId: run.id,
        status: "failed",
        provider: provider.id,
        model,
        errorMessage: message,
        completedAt: nowIso()
      });
      throw error;
    }
    usedFallback = true;
    fallbackReason = error instanceof Error ? error.message : "growth_pipeline_summary_fallback";
    outputSnapshot = {
      fallback: true,
      reason: fallbackReason,
      payload: fallback
    };
    responseModel = "rule-fallback";
  }

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "completed",
    provider: provider.id,
    model: responseModel,
    outputSnapshot,
    parsedResult: {
      funnel_summary: result.funnelSummary,
      best_channels: result.bestChannels,
      weak_points: result.weakPoints,
      high_potential_segments: result.highPotentialSegments,
      next_best_actions: result.nextBestActions
    },
    resultSource: usedFallback ? "fallback" : "provider",
    fallbackReason,
    errorMessage: usedFallback ? fallbackReason : null,
    completedAt: nowIso()
  });

  return {
    summary: result,
    usedFallback,
    fallbackReason
  };
}

export async function getGrowthSummary(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  periodDays?: number;
  ownerId?: string;
}): Promise<GrowthSummary> {
  const periodDays = params.periodDays ?? 30;

  const [allLeads, allDemoRequests, allTrialRequests, allTracksRaw, recentEventsRaw] = await Promise.all([
    listInboundLeads({
      supabase: params.supabase,
      orgId: params.orgId,
      ownerId: params.ownerId,
      limit: 500
    }),
    listDemoRequests({
      supabase: params.supabase,
      orgId: params.orgId,
      limit: 500
    }),
    listTrialRequests({
      supabase: params.supabase,
      orgId: params.orgId,
      limit: 500
    }),
    listTrialConversionTracks({
      supabase: params.supabase,
      orgId: params.orgId,
      limit: 500
    }),
    listConversionEvents({
      supabase: params.supabase,
      orgId: params.orgId,
      limit: 120
    })
  ]);
  const allTracks = params.ownerId ? allTracksRaw.filter((item) => item.ownerId === params.ownerId) : allTracksRaw;

  const leads = allLeads.filter((item) => withinDays(item.createdAt, periodDays));
  const leadIdSet = new Set(leads.map((item) => item.id));
  const demos = allDemoRequests.filter((item) => withinDays(item.createdAt, periodDays));
  const filteredDemos = params.ownerId ? demos.filter((item) => leadIdSet.has(item.leadId)) : demos;
  const trialRequests = allTrialRequests.filter((item) => withinDays(item.createdAt, periodDays));
  const filteredTrialRequests = params.ownerId ? trialRequests.filter((item) => leadIdSet.has(item.leadId)) : trialRequests;
  const recentEvents = recentEventsRaw.filter((item) => withinDays(item.createdAt, periodDays));
  const filteredEvents = params.ownerId ? recentEvents.filter((item) => !item.leadId || leadIdSet.has(item.leadId)) : recentEvents;

  const leadsTotal = leads.length;
  const leadsNew = leads.filter((item) => item.status === "new").length;
  const leadsQualified = leads.filter((item) => item.status === "qualified" || item.status === "demo_scheduled").length;
  const demoRequested = filteredDemos.length;
  const demoCompleted = filteredDemos.filter((item) => item.demoStatus === "completed").length;
  const trialRequested = filteredTrialRequests.length;
  const trialActivated = filteredTrialRequests.filter((item) => item.requestStatus === "activated").length;
  const onboardingCompletedCount = allTracks.filter((item) => item.currentStage === "onboarding_completed" || item.currentStage === "first_value_seen" || item.currentStage === "active_trial" || item.currentStage === "conversion_discussion" || item.currentStage === "verbally_committed" || item.currentStage === "converted").length;
  const conversionReadyCount = allTracks.filter((item) => item.conversionReadinessScore >= 70).length;
  const convertedCount = allTracks.filter((item) => item.currentStage === "converted").length;

  const sourceCounter = new Map<InboundLeadSource, number>();
  for (const lead of leads) {
    sourceCounter.set(lead.leadSource, (sourceCounter.get(lead.leadSource) ?? 0) + 1);
  }
  const bySource = Array.from(sourceCounter.entries()).map(([source, count]) => ({ source, count })).sort((a, b) => b.count - a.count);

  const trialByLead = new Map<string, TrialRequest>();
  for (const trial of filteredTrialRequests) {
    trialByLead.set(trial.leadId, trial);
  }
  const industryCounter = new Map<string, { leadCount: number; trialCount: number; convertedCount: number }>();
  for (const lead of leads) {
    const key = normalizeIndustry(lead.industryHint);
    const current = industryCounter.get(key) ?? { leadCount: 0, trialCount: 0, convertedCount: 0 };
    current.leadCount += 1;
    const trial = trialByLead.get(lead.id);
    if (trial) current.trialCount += 1;
    if (lead.status === "converted_to_customer") current.convertedCount += 1;
    industryCounter.set(key, current);
  }
  const byIndustry = Array.from(industryCounter.entries())
    .map(([industry, value]) => ({
      industry,
      leadCount: value.leadCount,
      trialCount: value.trialCount,
      convertedCount: value.convertedCount
    }))
    .sort((a, b) => b.leadCount - a.leadCount);

  const aiSummary = await runGrowthPipelineSummaryAi({
    supabase: params.supabase,
    orgId: params.orgId,
    actorUserId: params.actorUserId,
    periodDays,
    leads,
    demoRequested,
    demoCompleted,
    trialRequested,
    trialActivated,
    convertedCount,
    bySource,
    byIndustry
  });

  const highRiskTracks = allTracks
    .filter((item) => item.conversionReadinessScore < 55 || item.riskFlags.length > 1)
    .sort((a, b) => a.conversionReadinessScore - b.conversionReadinessScore)
    .slice(0, 12);

  return {
    periodDays,
    leadsTotal,
    leadsNew,
    leadsQualified,
    demoRequested,
    demoCompleted,
    demoCompletionRate: safePercent(demoCompleted, demoRequested),
    trialRequested,
    trialActivated,
    trialActivationRate: safePercent(trialActivated, trialRequested),
    onboardingCompletedCount,
    onboardingCompletionRate: safePercent(onboardingCompletedCount, Math.max(trialActivated, 1)),
    conversionReadyCount,
    convertedCount,
    bySource,
    byIndustry,
    highRiskTracks,
    recentEvents: filteredEvents.slice(0, 30) as ConversionEvent[],
    aiSummary: aiSummary.summary,
    aiSummaryUsedFallback: aiSummary.usedFallback,
    aiSummaryFallbackReason: aiSummary.fallbackReason
  };
}
