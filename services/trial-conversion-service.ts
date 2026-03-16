import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import { buildFallbackTrialConversionReview } from "@/lib/commercialization-fallback";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { getActivePromptVersion } from "@/services/ai-prompt-service";
import { createAiRun, updateAiRunStatus } from "@/services/ai-run-service";
import { appendConversionEvent } from "@/services/inbound-lead-service";
import type { TrialConversionReviewResult, TrialConversionStage, TrialConversionTrack } from "@/types/commercialization";
import { trialConversionReviewResultSchema } from "@/types/ai";

type DbClient = ServerSupabaseClient;

interface TrialConversionTrackRow {
  id: string;
  org_id: string;
  target_org_id: string;
  lead_id: string | null;
  owner_id: string;
  current_stage: TrialConversionStage;
  activation_score: number;
  engagement_score: number;
  conversion_readiness_score: number;
  risk_flags: string[] | null;
  next_action: string | null;
  next_action_due_at: string | null;
  summary: string | null;
  created_at: string;
  updated_at: string;
}

function nowIso(): string {
  return new Date().toISOString();
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

function mapTrackRow(row: TrialConversionTrackRow): TrialConversionTrack {
  return {
    id: row.id,
    orgId: row.org_id,
    targetOrgId: row.target_org_id,
    leadId: row.lead_id,
    ownerId: row.owner_id,
    currentStage: row.current_stage,
    activationScore: Number(row.activation_score ?? 0),
    engagementScore: Number(row.engagement_score ?? 0),
    conversionReadinessScore: Number(row.conversion_readiness_score ?? 0),
    riskFlags: asStringArray(row.risk_flags),
    nextAction: row.next_action,
    nextActionDueAt: row.next_action_due_at,
    summary: row.summary,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export async function listTrialConversionTracks(params: {
  supabase: DbClient;
  orgId: string;
  ownerId?: string;
  stages?: TrialConversionStage[];
  limit?: number;
}): Promise<TrialConversionTrack[]> {
  let query = (params.supabase as any)
    .from("trial_conversion_tracks")
    .select("*, owner:profiles!trial_conversion_tracks_owner_id_fkey(display_name)")
    .eq("org_id", params.orgId)
    .order("updated_at", { ascending: false })
    .limit(params.limit ?? 80);
  if (params.ownerId) query = query.eq("owner_id", params.ownerId);
  if (params.stages?.length) query = query.in("current_stage", params.stages);

  const res = await query;
  if (res.error) throw new Error(res.error.message);

  return ((res.data ?? []) as Array<TrialConversionTrackRow & { owner?: { display_name: string } | null }>).map((row) => ({
    ...mapTrackRow(row),
    ownerName: row.owner?.display_name ?? undefined
  }));
}

export async function getTrialConversionTrackById(params: {
  supabase: DbClient;
  orgId: string;
  trackId: string;
}): Promise<TrialConversionTrack | null> {
  const res = await (params.supabase as any)
    .from("trial_conversion_tracks")
    .select("*, owner:profiles!trial_conversion_tracks_owner_id_fkey(display_name)")
    .eq("org_id", params.orgId)
    .eq("id", params.trackId)
    .maybeSingle();
  if (res.error) throw new Error(res.error.message);
  if (!res.data) return null;

  const row = res.data as TrialConversionTrackRow & { owner?: { display_name: string } | null };
  return {
    ...mapTrackRow(row),
    ownerName: row.owner?.display_name ?? undefined
  };
}

export async function upsertTrialConversionTrack(params: {
  supabase: DbClient;
  orgId: string;
  targetOrgId: string;
  leadId: string | null;
  ownerId: string;
}): Promise<TrialConversionTrack> {
  const existing = await (params.supabase as any)
    .from("trial_conversion_tracks")
    .select("*")
    .eq("org_id", params.orgId)
    .eq("target_org_id", params.targetOrgId)
    .maybeSingle();
  if (existing.error) throw new Error(existing.error.message);
  if (existing.data) return mapTrackRow(existing.data as TrialConversionTrackRow);

  const insertRes = await (params.supabase as any)
    .from("trial_conversion_tracks")
    .insert({
      org_id: params.orgId,
      target_org_id: params.targetOrgId,
      lead_id: params.leadId,
      owner_id: params.ownerId,
      current_stage: "invited",
      activation_score: 10,
      engagement_score: 0,
      conversion_readiness_score: 8,
      risk_flags: ["activation_pending"],
      summary: "Trial created, waiting activation."
    })
    .select("*")
    .single();
  if (insertRes.error) throw new Error(insertRes.error.message);
  return mapTrackRow(insertRes.data as TrialConversionTrackRow);
}

async function collectTargetOrgUsageSnapshot(params: {
  supabase: DbClient;
  targetOrgId: string;
}): Promise<{
  onboardingCompleted: boolean;
  onboardingStarted: boolean;
  customers: number;
  followups: number;
  dealRooms: number;
  briefs: number;
  plans: number;
  workItems: number;
}> {
  const [settingsRes, customersRes, followupsRes, dealRoomsRes, briefsRes, plansRes, workItemsRes] = await Promise.all([
    (params.supabase as any).from("org_settings").select("onboarding_completed, onboarding_step_state").eq("org_id", params.targetOrgId).maybeSingle(),
    (params.supabase as any).from("customers").select("id", { count: "exact", head: true }).eq("org_id", params.targetOrgId),
    (params.supabase as any).from("followups").select("id", { count: "exact", head: true }).eq("org_id", params.targetOrgId),
    (params.supabase as any).from("deal_rooms").select("id", { count: "exact", head: true }).eq("org_id", params.targetOrgId),
    (params.supabase as any).from("morning_briefs").select("id", { count: "exact", head: true }).eq("org_id", params.targetOrgId),
    (params.supabase as any).from("daily_work_plans").select("id", { count: "exact", head: true }).eq("org_id", params.targetOrgId),
    (params.supabase as any).from("work_items").select("id", { count: "exact", head: true }).eq("org_id", params.targetOrgId)
  ]);
  if (settingsRes.error) throw new Error(settingsRes.error.message);
  if (customersRes.error) throw new Error(customersRes.error.message);
  if (followupsRes.error) throw new Error(followupsRes.error.message);
  if (dealRoomsRes.error) throw new Error(dealRoomsRes.error.message);
  if (briefsRes.error) throw new Error(briefsRes.error.message);
  if (plansRes.error) throw new Error(plansRes.error.message);
  if (workItemsRes.error) throw new Error(workItemsRes.error.message);

  const onboardingState = (settingsRes.data?.onboarding_step_state ?? {}) as Record<string, boolean>;
  const onboardingStarted =
    Boolean(settingsRes.data?.onboarding_completed) ||
    Object.values(onboardingState).some((value) => value === true);

  return {
    onboardingCompleted: Boolean(settingsRes.data?.onboarding_completed),
    onboardingStarted,
    customers: customersRes.count ?? 0,
    followups: followupsRes.count ?? 0,
    dealRooms: dealRoomsRes.count ?? 0,
    briefs: briefsRes.count ?? 0,
    plans: plansRes.count ?? 0,
    workItems: workItemsRes.count ?? 0
  };
}

function buildRuleBasedTrackSnapshot(params: {
  usage: Awaited<ReturnType<typeof collectTargetOrgUsageSnapshot>>;
}): {
  activationScore: number;
  engagementScore: number;
  readinessScore: number;
  stage: TrialConversionStage;
  riskFlags: string[];
  nextAction: string;
  summary: string;
} {
  const { usage } = params;
  let activationScore = 20;
  if (usage.onboardingStarted) activationScore += 30;
  if (usage.onboardingCompleted) activationScore += 30;
  if (usage.customers > 0) activationScore += 10;
  if (usage.dealRooms > 0 || usage.briefs > 0) activationScore += 10;
  activationScore = Math.max(0, Math.min(100, activationScore));

  let engagementScore =
    Math.min(30, usage.followups * 6) +
    Math.min(20, usage.workItems * 2) +
    Math.min(20, usage.plans * 5) +
    Math.min(15, usage.briefs * 5) +
    Math.min(15, usage.dealRooms * 6);
  engagementScore = Math.max(0, Math.min(100, engagementScore));

  let readinessScore = Math.round(activationScore * 0.45 + engagementScore * 0.55);
  const riskFlags: string[] = [];
  if (!usage.onboardingStarted) riskFlags.push("onboarding_not_started");
  if (usage.onboardingStarted && !usage.onboardingCompleted) riskFlags.push("onboarding_incomplete");
  if (usage.customers === 0) riskFlags.push("no_customers_imported");
  if (usage.followups === 0) riskFlags.push("no_followups_recorded");
  if (usage.briefs === 0) riskFlags.push("briefings_not_used");
  if (usage.dealRooms === 0) riskFlags.push("deal_rooms_not_started");

  if (riskFlags.includes("onboarding_not_started")) readinessScore -= 12;
  if (riskFlags.includes("no_customers_imported")) readinessScore -= 8;
  readinessScore = Math.max(0, Math.min(100, readinessScore));

  let stage: TrialConversionStage = "activated";
  if (!usage.onboardingStarted) stage = "activated";
  else if (!usage.onboardingCompleted) stage = "onboarding_started";
  else if (readinessScore >= 85) stage = "conversion_discussion";
  else if (readinessScore >= 65) stage = "active_trial";
  else stage = "first_value_seen";

  const nextAction =
    stage === "conversion_discussion"
      ? "Run value review and schedule conversion discussion."
      : stage === "active_trial"
        ? "Drive one high-value workflow and capture executive outcome."
        : "Complete onboarding and first data import this week.";

  return {
    activationScore,
    engagementScore,
    readinessScore,
    stage,
    riskFlags,
    nextAction,
    summary: `Activation ${activationScore}/100, engagement ${engagementScore}/100, readiness ${readinessScore}/100.`
  };
}

async function ensureConversionMilestoneEvent(params: {
  supabase: DbClient;
  orgId: string;
  targetOrgId: string;
  leadId: string | null;
  eventType: "onboarding_completed" | "first_deal_created" | "first_brief_generated";
  eventSummary: string;
  eventPayload?: Record<string, unknown>;
}): Promise<void> {
  const existing = await (params.supabase as any)
    .from("conversion_events")
    .select("id")
    .eq("org_id", params.orgId)
    .eq("target_org_id", params.targetOrgId)
    .eq("event_type", params.eventType)
    .limit(1)
    .maybeSingle();
  if (existing.error) throw new Error(existing.error.message);
  if (existing.data) return;

  await appendConversionEvent({
    supabase: params.supabase,
    orgId: params.orgId,
    leadId: params.leadId,
    targetOrgId: params.targetOrgId,
    eventType: params.eventType,
    eventSummary: params.eventSummary,
    eventPayload: params.eventPayload ?? {}
  });
}

export async function runTrialConversionReview(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  targetOrgId: string;
  ruleSnapshot: ReturnType<typeof buildRuleBasedTrackSnapshot>;
  usageSnapshot: Awaited<ReturnType<typeof collectTargetOrgUsageSnapshot>>;
}): Promise<{
  result: TrialConversionReviewResult;
  runId: string;
  usedFallback: boolean;
  fallbackReason: string | null;
}> {
  const provider = getAiProvider();
  const model = provider.getDefaultModel({ reasoning: false });
  const prompt = await getActivePromptVersion({
    supabase: params.supabase,
    orgId: params.orgId,
    scenario: "trial_conversion_review",
    providerId: provider.id
  });

  const fallback = buildFallbackTrialConversionReview({
    activationScore: params.ruleSnapshot.activationScore,
    engagementScore: params.ruleSnapshot.engagementScore,
    readinessScore: params.ruleSnapshot.readinessScore,
    riskFlags: params.ruleSnapshot.riskFlags
  });

  const run = await createAiRun({
    supabase: params.supabase,
    orgId: params.orgId,
    triggeredByUserId: params.actorUserId,
    triggerSource: "manager_review",
    scenario: "trial_conversion_review",
    provider: provider.id,
    model,
    promptVersion: prompt.version,
    inputSnapshot: {
      target_org_id: params.targetOrgId,
      usage_snapshot: params.usageSnapshot,
      rule_snapshot: params.ruleSnapshot
    }
  });

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "running",
    startedAt: nowIso()
  });

  let result = fallback;
  let usedFallback = false;
  let fallbackReason: string | null = null;
  let responseModel = model;
  let outputSnapshot: Record<string, unknown> = { fallback: true, reason: "not_started", payload: fallback };

  try {
    if (!provider.isConfigured()) throw new Error("provider_not_configured");

    const response = await provider.chatCompletion({
      scenario: "trial_conversion_review",
      model,
      systemPrompt: prompt.systemPrompt,
      developerPrompt: `${prompt.developerPrompt}\n\nOutput schema:\n${JSON.stringify(prompt.outputSchema)}`,
      userPrompt: JSON.stringify({
        target_org_id: params.targetOrgId,
        usage_snapshot: params.usageSnapshot,
        rule_snapshot: params.ruleSnapshot
      }),
      jsonMode: true,
      strictMode: true
    });

    if (response.error) throw new Error(response.error);
    const candidate = response.parsedJson ?? (response.rawText ? JSON.parse(response.rawText) : null);
    const parsed = trialConversionReviewResultSchema.safeParse(candidate);
    if (!parsed.success) throw new Error("trial_conversion_review_schema_invalid");

    result = {
      activationHealth: parsed.data.activation_health,
      readinessAssessment: parsed.data.readiness_assessment,
      riskFactors: parsed.data.risk_factors,
      recommendedConversionActions: parsed.data.recommended_conversion_actions,
      recommendedOwnerFollowup: parsed.data.recommended_owner_followup
    };
    responseModel = response.model;
    outputSnapshot = response.rawResponse;
  } catch (error) {
    if (!isRuleFallbackEnabled()) {
      const message = error instanceof Error ? error.message : "trial_conversion_review_failed";
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
    fallbackReason = error instanceof Error ? error.message : "trial_conversion_review_fallback";
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
      activation_health: result.activationHealth,
      readiness_assessment: result.readinessAssessment,
      risk_factors: result.riskFactors,
      recommended_conversion_actions: result.recommendedConversionActions,
      recommended_owner_followup: result.recommendedOwnerFollowup
    },
    resultSource: usedFallback ? "fallback" : "provider",
    fallbackReason,
    errorMessage: usedFallback ? fallbackReason : null,
    completedAt: nowIso()
  });

  return {
    result,
    runId: run.id,
    usedFallback,
    fallbackReason
  };
}

export async function refreshTrialConversionTrack(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  trackId: string;
}): Promise<{
  track: TrialConversionTrack;
  review: TrialConversionReviewResult;
  usedFallback: boolean;
  fallbackReason: string | null;
}> {
  const track = await getTrialConversionTrackById({
    supabase: params.supabase,
    orgId: params.orgId,
    trackId: params.trackId
  });
  if (!track) throw new Error("trial_conversion_track_not_found");

  const usageSnapshot = await collectTargetOrgUsageSnapshot({
    supabase: params.supabase,
    targetOrgId: track.targetOrgId
  });
  const ruleSnapshot = buildRuleBasedTrackSnapshot({
    usage: usageSnapshot
  });
  const review = await runTrialConversionReview({
    supabase: params.supabase,
    orgId: params.orgId,
    actorUserId: params.actorUserId,
    targetOrgId: track.targetOrgId,
    usageSnapshot,
    ruleSnapshot
  });

  if (usageSnapshot.onboardingCompleted) {
    await ensureConversionMilestoneEvent({
      supabase: params.supabase,
      orgId: params.orgId,
      targetOrgId: track.targetOrgId,
      leadId: track.leadId,
      eventType: "onboarding_completed",
      eventSummary: "Target trial org has completed onboarding."
    }).catch(() => null);
  }
  if (usageSnapshot.dealRooms > 0) {
    await ensureConversionMilestoneEvent({
      supabase: params.supabase,
      orgId: params.orgId,
      targetOrgId: track.targetOrgId,
      leadId: track.leadId,
      eventType: "first_deal_created",
      eventSummary: "Target trial org created first deal room."
    }).catch(() => null);
  }
  if (usageSnapshot.briefs > 0) {
    await ensureConversionMilestoneEvent({
      supabase: params.supabase,
      orgId: params.orgId,
      targetOrgId: track.targetOrgId,
      leadId: track.leadId,
      eventType: "first_brief_generated",
      eventSummary: "Target trial org generated first morning brief."
    }).catch(() => null);
  }

  const updateRes = await (params.supabase as any)
    .from("trial_conversion_tracks")
    .update({
      current_stage: ruleSnapshot.stage,
      activation_score: ruleSnapshot.activationScore,
      engagement_score: ruleSnapshot.engagementScore,
      conversion_readiness_score: ruleSnapshot.readinessScore,
      risk_flags: ruleSnapshot.riskFlags,
      next_action: review.result.recommendedOwnerFollowup[0] ?? ruleSnapshot.nextAction,
      next_action_due_at: new Date(Date.now() + 3 * 24 * 60 * 60 * 1000).toISOString(),
      summary: review.result.readinessAssessment
    })
    .eq("org_id", params.orgId)
    .eq("id", track.id)
    .select("*")
    .single();
  if (updateRes.error) throw new Error(updateRes.error.message);

  return {
    track: mapTrackRow(updateRes.data as TrialConversionTrackRow),
    review: review.result,
    usedFallback: review.usedFallback,
    fallbackReason: review.fallbackReason
  };
}
