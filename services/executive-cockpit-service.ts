import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import { buildFallbackAutomationActionRecommendation } from "@/lib/executive-fallback";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { getActivePromptVersion } from "@/services/ai-prompt-service";
import { createAiRun, updateAiRunStatus } from "@/services/ai-run-service";
import { listAutomationRuleRuns } from "@/services/automation-rule-service";
import { generateBusinessEventsFromSignals, listBusinessEvents } from "@/services/business-event-service";
import { listLatestCustomerHealthSnapshots, refreshCustomerHealthSnapshots } from "@/services/customer-health-service";
import { listRenewalWatchItems, refreshRenewalWatchItems } from "@/services/renewal-watch-service";
import {
  applyExecutiveEventPreference,
  applyManagerActionPreference,
  buildManagerVisibilityRuntimeContext,
  buildResolvedOrgRuntimeConfig
} from "@/services/template-org-runtime-bridge-service";
import type { BusinessEvent, ExecutiveCockpitSummary } from "@/types/automation";
import { automationActionRecommendationResultSchema } from "@/types/ai";
import type { DealCheckpointStatus, DealPriorityBand, DealRoomStatus } from "@/types/deal";
import type { TrialConversionStage } from "@/types/commercialization";
import type { WorkItemStatus } from "@/types/work";

type DbClient = ServerSupabaseClient;

type QueryError = { message: string };
type TableQueryResult<T> = { data: T[] | null; error: QueryError | null };

type TrialConversionTrackQueryRow = {
  id: string;
  current_stage: TrialConversionStage;
  conversion_readiness_score: number | null;
  owner_id: string;
};

type WorkItemQueryRow = {
  id: string;
  status: WorkItemStatus;
  owner_id: string;
  due_at: string | null;
};

type DealRoomQueryRow = {
  id: string;
  priority_band: DealPriorityBand;
  room_status: DealRoomStatus;
  manager_attention_needed: boolean;
  owner_id: string;
};

type DealCheckpointQueryRow = {
  id: string;
  status: DealCheckpointStatus;
  deal_room_id: string;
};

const ACTIVE_WORK_ITEM_STATUSES: WorkItemStatus[] = ["todo", "in_progress", "snoozed"];
const ACTIVATED_TRIAL_STAGES: TrialConversionStage[] = [
  "activated",
  "onboarding_started",
  "onboarding_completed",
  "first_value_seen",
  "active_trial",
  "conversion_discussion",
  "verbally_committed",
  "converted"
];
const ONBOARDING_COMPLETED_TRIAL_STAGES: TrialConversionStage[] = [
  "onboarding_completed",
  "first_value_seen",
  "active_trial",
  "conversion_discussion",
  "verbally_committed",
  "converted"
];
const FIRST_VALUE_TRIAL_STAGES: TrialConversionStage[] = [
  "first_value_seen",
  "active_trial",
  "conversion_discussion",
  "verbally_committed",
  "converted"
];

async function resolveExecutiveRuntimePreferenceContext(params: {
  supabase: DbClient;
  orgId: string;
}) {
  const runtimeContext = await buildResolvedOrgRuntimeConfig({
    supabase: params.supabase,
    orgId: params.orgId
  });
  return buildManagerVisibilityRuntimeContext({
    context: runtimeContext
  });
}

async function fetchTrialConversionTracks(params: {
  supabase: DbClient;
  orgId: string;
}): Promise<TableQueryResult<TrialConversionTrackQueryRow>> {
  const res = await params.supabase
    .from("trial_conversion_tracks")
    .select("id,current_stage,conversion_readiness_score,owner_id")
    .eq("org_id", params.orgId);
  return res as TableQueryResult<TrialConversionTrackQueryRow>;
}

async function fetchOpenWorkItems(params: {
  supabase: DbClient;
  orgId: string;
}): Promise<TableQueryResult<WorkItemQueryRow>> {
  const res = await params.supabase
    .from("work_items")
    .select("id,status,owner_id,due_at")
    .eq("org_id", params.orgId)
    .in("status", ACTIVE_WORK_ITEM_STATUSES);
  return res as TableQueryResult<WorkItemQueryRow>;
}

async function fetchDealRooms(params: {
  supabase: DbClient;
  orgId: string;
}): Promise<TableQueryResult<DealRoomQueryRow>> {
  const res = await params.supabase
    .from("deal_rooms")
    .select("id,priority_band,room_status,manager_attention_needed,owner_id")
    .eq("org_id", params.orgId);
  return res as TableQueryResult<DealRoomQueryRow>;
}

async function fetchBlockedDealCheckpoints(params: {
  supabase: DbClient;
  orgId: string;
}): Promise<TableQueryResult<DealCheckpointQueryRow>> {
  const res = await params.supabase
    .from("deal_checkpoints")
    .select("id,status,deal_room_id")
    .eq("org_id", params.orgId)
    .eq("status", "blocked");
  return res as TableQueryResult<DealCheckpointQueryRow>;
}

async function getAutomationActionRecommendation(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  event: BusinessEvent;
}): Promise<string> {
  const fallback = buildFallbackAutomationActionRecommendation({
    eventType: params.event.eventType,
    severity: params.event.severity
  });

  const provider = getAiProvider();
  const model = provider.getDefaultModel({ reasoning: false });
  const prompt = await getActivePromptVersion({
    supabase: params.supabase,
    orgId: params.orgId,
    scenario: "automation_action_recommendation",
    providerId: provider.id
  });

  const run = await createAiRun({
    supabase: params.supabase,
    orgId: params.orgId,
    customerId: params.event.entityType === "customer" ? params.event.entityId : null,
    triggeredByUserId: params.actorUserId,
    triggerSource: "manager_review",
    scenario: "automation_action_recommendation",
    provider: provider.id,
    model,
    promptVersion: prompt.version,
    inputSnapshot: {
      business_event: {
        id: params.event.id,
        type: params.event.eventType,
        severity: params.event.severity,
        summary: params.event.eventSummary,
        payload: params.event.eventPayload
      }
    }
  });

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "running",
    startedAt: new Date().toISOString()
  });

  let text = `${fallback.suggestedAction} (${fallback.whyItMatters})`;
  let usedFallback = false;
  let fallbackReason: string | null = null;
  let outputSnapshot: Record<string, unknown> = { fallback: true, reason: "not_started" };
  let responseModel = model;

  try {
    if (!provider.isConfigured()) throw new Error("provider_not_configured");

    const response = await provider.chatCompletion({
      scenario: "automation_action_recommendation",
      model,
      systemPrompt: prompt.systemPrompt,
      developerPrompt: `${prompt.developerPrompt}\n\nOutput schema:\n${JSON.stringify(prompt.outputSchema)}`,
      userPrompt: JSON.stringify({
        event_type: params.event.eventType,
        severity: params.event.severity,
        summary: params.event.eventSummary,
        payload: params.event.eventPayload
      }),
      jsonMode: true,
      strictMode: true
    });

    if (response.error) throw new Error(response.error);
    const candidate = response.parsedJson ?? (response.rawText ? JSON.parse(response.rawText) : null);
    const parsed = automationActionRecommendationResultSchema.safeParse(candidate);
    if (!parsed.success) throw new Error("automation_action_recommendation_schema_invalid");

    text = `${parsed.data.suggested_action} (${parsed.data.why_it_matters})`;
    outputSnapshot = response.rawResponse;
    responseModel = response.model;
  } catch (error) {
    if (!isRuleFallbackEnabled()) {
      const message = error instanceof Error ? error.message : "automation_action_recommendation_failed";
      await updateAiRunStatus({
        supabase: params.supabase,
        runId: run.id,
        status: "failed",
        provider: provider.id,
        model,
        errorMessage: message,
        completedAt: new Date().toISOString()
      });
      throw error;
    }
    usedFallback = true;
    fallbackReason = error instanceof Error ? error.message : "automation_action_recommendation_fallback";
    outputSnapshot = { fallback: true, reason: fallbackReason, payload: fallback };
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
      suggested_action: text,
      used_fallback: usedFallback,
      fallback_reason: fallbackReason
    },
    resultSource: usedFallback ? "fallback" : "provider",
    fallbackReason,
    errorMessage: usedFallback ? fallbackReason : null,
    completedAt: new Date().toISOString()
  });

  return text;
}

export async function getExecutiveCockpitSummary(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  ownerId?: string;
  refreshSignals?: boolean;
  allowAiRecommendations?: boolean;
}): Promise<ExecutiveCockpitSummary> {
  if (params.refreshSignals) {
    await refreshCustomerHealthSnapshots({
      supabase: params.supabase,
      orgId: params.orgId,
      actorUserId: params.actorUserId
    }).catch(() => null);
    await refreshRenewalWatchItems({
      supabase: params.supabase,
      orgId: params.orgId
    }).catch(() => null);
    await generateBusinessEventsFromSignals({
      supabase: params.supabase,
      orgId: params.orgId,
      ownerId: params.ownerId
    }).catch(() => null);
  }

  const [events, health, renewals, trialTracksRes, workItemsRes, dealRoomsRes, blockedCheckpointsRes, runs, runtimePreferenceContext] = await Promise.all([
    listBusinessEvents({
      supabase: params.supabase,
      orgId: params.orgId,
      ownerId: params.ownerId,
      statuses: ["open", "acknowledged"],
      limit: 300
    }),
    listLatestCustomerHealthSnapshots({
      supabase: params.supabase,
      orgId: params.orgId,
      ownerId: params.ownerId,
      limit: 300
    }),
    listRenewalWatchItems({
      supabase: params.supabase,
      orgId: params.orgId,
      ownerId: params.ownerId,
      limit: 300
    }),
    fetchTrialConversionTracks({
      supabase: params.supabase,
      orgId: params.orgId
    }),
    fetchOpenWorkItems({
      supabase: params.supabase,
      orgId: params.orgId
    }),
    fetchDealRooms({
      supabase: params.supabase,
      orgId: params.orgId
    }),
    fetchBlockedDealCheckpoints({
      supabase: params.supabase,
      orgId: params.orgId
    }),
    listAutomationRuleRuns({
      supabase: params.supabase,
      orgId: params.orgId,
      limit: 12
    }),
    resolveExecutiveRuntimePreferenceContext({
      supabase: params.supabase,
      orgId: params.orgId
    })
  ]);

  for (const res of [trialTracksRes, workItemsRes, dealRoomsRes, blockedCheckpointsRes]) {
    if (res.error) throw new Error(res.error.message);
  }

  const trialTracks = trialTracksRes.data ?? [];
  const filteredTracks = params.ownerId ? trialTracks.filter((item) => item.owner_id === params.ownerId) : trialTracks;

  const workItems = workItemsRes.data ?? [];
  const filteredWork = params.ownerId ? workItems.filter((item) => item.owner_id === params.ownerId) : workItems;

  const dealRooms = dealRoomsRes.data ?? [];
  const filteredDeals = params.ownerId ? dealRooms.filter((item) => item.owner_id === params.ownerId) : dealRooms;

  const overdueWork = filteredWork.filter((item) => item.due_at && new Date(item.due_at).getTime() < Date.now()).length;
  const strategicDeals = filteredDeals.filter((item) => ["strategic", "critical"].includes(String(item.priority_band))).length;
  const managerAttentionDeals = filteredDeals.filter((item) => Boolean(item.manager_attention_needed)).length;

  const healthBandDistribution: ExecutiveCockpitSummary["healthBandDistribution"] = [
    { band: "healthy", count: health.filter((item) => item.healthBand === "healthy").length },
    { band: "watch", count: health.filter((item) => item.healthBand === "watch").length },
    { band: "at_risk", count: health.filter((item) => item.healthBand === "at_risk").length },
    { band: "critical", count: health.filter((item) => item.healthBand === "critical").length }
  ];

  const baseSortedOpenEvents = events
    .filter((item) => item.status === "open")
    .sort((a, b) => (a.severity === b.severity ? 0 : a.severity === "critical" ? -1 : b.severity === "critical" ? 1 : a.severity === "warning" ? -1 : 1));
  const sortedOpenEvents = applyExecutiveEventPreference({
    events: baseSortedOpenEvents,
    context: runtimePreferenceContext
  });

  const topActions: string[] = [];
  const recommendationCandidates = sortedOpenEvents.slice(0, 5);
  const allowAiRecommendations = params.allowAiRecommendations !== false;
  let remainingAiRecommendationBudget = 2;

  for (const event of recommendationCandidates) {
    const shouldUseFallbackOnly =
      !allowAiRecommendations || remainingAiRecommendationBudget <= 0 || event.severity === "info";

    if (shouldUseFallbackOnly) {
      const fallback = buildFallbackAutomationActionRecommendation({
        eventType: event.eventType,
        severity: event.severity
      });
      topActions.push(`${fallback.suggestedAction} (${fallback.whyItMatters})`);
      continue;
    }

    try {
      const rec = await getAutomationActionRecommendation({
        supabase: params.supabase,
        orgId: params.orgId,
        actorUserId: params.actorUserId,
        event
      });
      remainingAiRecommendationBudget -= 1;
      topActions.push(rec);
    } catch {
      const fallback = buildFallbackAutomationActionRecommendation({
        eventType: event.eventType,
        severity: event.severity
      });
      topActions.push(`${fallback.suggestedAction} (${fallback.whyItMatters})`);
    }
  }
  const preferredActions = applyManagerActionPreference({
    actions: topActions,
    context: runtimePreferenceContext,
    limit: 2
  });

  const followupTimelinessScore = Math.max(0, Math.min(100, 100 - Math.round((overdueWork / Math.max(filteredWork.length, 1)) * 100)));
  const recentEvents = applyExecutiveEventPreference({
    events: events.slice(0, 20),
    context: runtimePreferenceContext
  });

  const summary: ExecutiveCockpitSummary = {
    openEvents: events.filter((item) => item.status === "open").length,
    criticalRisks: events.filter((item) => item.status !== "resolved" && item.severity === "critical").length,
    trialStalled: events.filter((item) => item.eventType === "trial_stalled" && item.status !== "resolved").length,
    dealBlocked: events.filter((item) => item.eventType === "deal_blocked" && item.status !== "resolved").length,
    renewalAtRisk: renewals.filter((item) => item.renewalStatus === "at_risk").length,
    managerAttentionRequired: events.filter((item) => item.eventType === "manager_attention_escalated" && item.status !== "resolved").length,
    healthBandDistribution,
    dealHealth: {
      strategicDeals,
      blockedCheckpoints: (blockedCheckpointsRes.data ?? []).length,
      managerAttentionDeals
    },
    trialHealth: {
      activated: filteredTracks.filter((item) => ACTIVATED_TRIAL_STAGES.includes(item.current_stage)).length,
      onboardingCompleted: filteredTracks.filter((item) => ONBOARDING_COMPLETED_TRIAL_STAGES.includes(item.current_stage)).length,
      firstValue: filteredTracks.filter((item) => FIRST_VALUE_TRIAL_STAGES.includes(item.current_stage)).length,
      conversionRisk: filteredTracks.filter((item) => Number(item.conversion_readiness_score ?? 0) < 55).length
    },
    teamExecution: {
      overdueWork,
      followupTimelinessScore,
      shallowActivityRatio: Number((overdueWork / Math.max(filteredWork.length, 1)).toFixed(2))
    },
    recentRuleRuns: runs,
    recentEvents,
    recommendations: preferredActions.length > 0 ? preferredActions : ["No urgent action recommendation right now."]
  };

  return summary;
}

export async function getExecutiveHealthSummary(params: {
  supabase: DbClient;
  orgId: string;
  ownerId?: string;
}): Promise<{
  healthSnapshots: Awaited<ReturnType<typeof listLatestCustomerHealthSnapshots>>;
  renewalWatch: Awaited<ReturnType<typeof listRenewalWatchItems>>;
}> {
  const [healthSnapshots, renewalWatch] = await Promise.all([
    listLatestCustomerHealthSnapshots({
      supabase: params.supabase,
      orgId: params.orgId,
      ownerId: params.ownerId,
      limit: 200
    }),
    listRenewalWatchItems({
      supabase: params.supabase,
      orgId: params.orgId,
      ownerId: params.ownerId,
      limit: 200
    })
  ]);

  return {
    healthSnapshots,
    renewalWatch
  };
}
