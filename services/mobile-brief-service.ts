import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import { buildFallbackMobileBriefCompactSummary } from "@/lib/mobile-fallback";
import { buildMobilePriorityPreview, clipMobileList } from "@/lib/mobile-summary";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { getActivePromptVersion } from "@/services/ai-prompt-service";
import { createAiRun, updateAiRunStatus } from "@/services/ai-run-service";
import { getBriefingHubData } from "@/services/briefing-hub-service";
import { touchpointEventSummary } from "@/services/external-touchpoint-service";
import { getTodayPlanView } from "@/services/work-plan-service";
import { mobileBriefCompactSummaryResultSchema, type MobileBriefCompactSummaryResult } from "@/types/ai";
import type { Database } from "@/types/database";
import type { MobileBriefingsView } from "@/types/mobile";

type DbClient = ServerSupabaseClient;
type ProfileRow = Database["public"]["Tables"]["profiles"]["Row"];

function nowIso(): string {
  return new Date().toISOString();
}

export async function generateMobileCompactSummary(params: {
  supabase: DbClient;
  profile: ProfileRow;
  payload: Record<string, unknown>;
}): Promise<{
  result: MobileBriefCompactSummaryResult;
  runId: string;
  usedFallback: boolean;
  fallbackReason: string | null;
}> {
  const provider = getAiProvider();
  const prompt = await getActivePromptVersion({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    scenario: "mobile_brief_compact_summary",
    providerId: provider.id
  });

  const model = provider.getDefaultModel({ reasoning: false });
  const run = await createAiRun({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    customerId: null,
    followupId: null,
    triggeredByUserId: params.profile.id,
    triggerSource: "manual",
    scenario: "mobile_brief_compact_summary",
    provider: provider.id,
    model,
    promptVersion: prompt.version,
    inputSnapshot: params.payload
  });

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "running",
    startedAt: nowIso()
  });

  const fallback = buildFallbackMobileBriefCompactSummary({
    focusTheme: typeof params.payload.focus_theme === "string" ? params.payload.focus_theme : null,
    topPriorities: Array.isArray(params.payload.top_priorities)
      ? params.payload.top_priorities.filter((item): item is string => typeof item === "string")
      : [],
    urgentRisks: Array.isArray(params.payload.urgent_risks)
      ? params.payload.urgent_risks.filter((item): item is string => typeof item === "string")
      : []
  });

  let usedFallback = false;
  let fallbackReason: string | null = null;
  let result = fallback;
  let modelUsed = model;
  let outputSnapshot: Record<string, unknown> = {};
  const started = Date.now();

  try {
    if (!provider.isConfigured()) throw new Error("provider_not_configured");

    const response = await provider.chatCompletion({
      scenario: "mobile_brief_compact_summary",
      model,
      systemPrompt: prompt.systemPrompt,
      developerPrompt: `${prompt.developerPrompt}\n\nOutput schema:\n${JSON.stringify(prompt.outputSchema)}`,
      userPrompt: JSON.stringify({
        scenario: "mobile_brief_compact_summary",
        payload: params.payload
      }),
      jsonMode: true,
      strictMode: true
    });
    if (response.error) throw new Error(response.error);

    const parsed = mobileBriefCompactSummaryResultSchema.safeParse(
      response.parsedJson ?? (response.rawText ? JSON.parse(response.rawText) : null)
    );
    if (!parsed.success) throw new Error("mobile_brief_compact_summary_schema_invalid");
    result = parsed.data;
    modelUsed = response.model;
    outputSnapshot = response.rawResponse;
  } catch (error) {
    if (!isRuleFallbackEnabled()) {
      const message = error instanceof Error ? error.message : "mobile_brief_compact_summary_failed";
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
    fallbackReason = error instanceof Error ? error.message : "mobile_brief_compact_summary_fallback";
    outputSnapshot = {
      fallback: true,
      reason: fallbackReason
    };
    modelUsed = "rule-fallback";
  }

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "completed",
    provider: provider.id,
    model: modelUsed,
    outputSnapshot,
    parsedResult: result,
    resultSource: usedFallback ? "fallback" : "provider",
    fallbackReason,
    latencyMs: Date.now() - started,
    completedAt: nowIso(),
    errorMessage: usedFallback ? fallbackReason : null
  });

  return {
    result,
    runId: run.id,
    usedFallback,
    fallbackReason
  };
}

export async function getMobileBriefingsView(params: {
  supabase: DbClient;
  profile: ProfileRow;
  date?: string;
}): Promise<MobileBriefingsView> {
  const [hub, planView, touchpointSummary] = await Promise.all([
    getBriefingHubData({
      supabase: params.supabase,
      profile: params.profile,
      date: params.date,
      prepLimit: 20,
      draftLimit: 20
    }),
    getTodayPlanView({
      supabase: params.supabase,
      orgId: params.profile.org_id,
      userId: params.profile.id,
      date: params.date
    }),
    touchpointEventSummary({
      supabase: params.supabase,
      orgId: params.profile.org_id,
      ownerId: params.profile.role === "manager" ? null : params.profile.id,
      sinceDays: 7
    })
  ]);

  const topPriorities = buildMobilePriorityPreview(
    (planView?.planItems ?? []).map((item) => {
      const work = planView?.workItems.find((candidate) => candidate.id === item.workItemId);
      return { title: work ? work.title : `Task ${item.sequenceNo}` };
    }),
    5
  );
  const urgentRisks = clipMobileList([
    touchpointSummary.waitingReplyThreads > 0 ? `等待回复 ${touchpointSummary.waitingReplyThreads} 条` : "",
    touchpointSummary.upcomingMeetings > 0 ? `待准备会议 ${touchpointSummary.upcomingMeetings} 场` : "",
    touchpointSummary.documentUpdates > 0 ? `文档待处理 ${touchpointSummary.documentUpdates} 项` : ""
  ], 4);

  const compact = await generateMobileCompactSummary({
    supabase: params.supabase,
    profile: params.profile,
    payload: {
      focus_theme: planView?.plan.focusTheme ?? null,
      top_priorities: topPriorities,
      urgent_risks: urgentRisks,
      morning_headline: hub.morningBrief?.headline ?? null
    }
  });

  return {
    compactHeadline: compact.result.compact_headline,
    topPriorities: compact.result.top_priorities,
    urgentRisks: compact.result.urgent_risks,
    oneLineGuidance: compact.result.one_line_guidance,
    morningBrief: hub.morningBrief,
    prepCards: hub.prepCards.slice(0, 12).map((item) => ({
      id: item.id,
      title: item.title,
      summary: item.summary,
      status: item.status,
      cardType: item.cardType
    })),
    drafts: hub.contentDrafts.slice(0, 12)
  };
}
