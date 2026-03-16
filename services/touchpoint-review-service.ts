import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import { buildFallbackExternalTouchpointReview } from "@/lib/external-touchpoint-fallback";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { getActivePromptVersion } from "@/services/ai-prompt-service";
import { createAiRun, updateAiRunStatus } from "@/services/ai-run-service";
import { touchpointEventSummary } from "@/services/external-touchpoint-service";
import { externalTouchpointReviewResultSchema, type ExternalTouchpointReviewResult } from "@/types/ai";
import type { Database } from "@/types/database";

type DbClient = ServerSupabaseClient;
type ProfileRow = Database["public"]["Tables"]["profiles"]["Row"];

function nowIso(): string {
  return new Date().toISOString();
}

export async function generateTouchpointReview(params: {
  supabase: DbClient;
  profile: ProfileRow;
  ownerId?: string | null;
  customerId?: string | null;
  dealRoomId?: string | null;
  sinceDays?: number;
}): Promise<{
  result: ExternalTouchpointReviewResult;
  runId: string;
  usedFallback: boolean;
  fallbackReason: string | null;
}> {
  const ownerId = params.profile.role === "manager" ? params.ownerId : params.profile.id;
  const sinceDays = params.sinceDays ?? 7;

  const [summary, waitingThreadsRes, upcomingMeetingsRes, highPriorityDealsRes] = await Promise.all([
    touchpointEventSummary({
      supabase: params.supabase,
      orgId: params.profile.org_id,
      ownerId: ownerId ?? undefined,
      sinceDays
    }),
    (async () => {
      let query = params.supabase
        .from("email_threads")
        .select("id, subject, latest_message_at, owner_id, deal_room_id")
        .eq("org_id", params.profile.org_id)
        .eq("thread_status", "waiting_reply")
        .order("latest_message_at", { ascending: true, nullsFirst: true })
        .limit(12);
      if (ownerId !== undefined && ownerId !== null) query = query.eq("owner_id", ownerId);
      if (params.customerId !== undefined) query = query.eq("customer_id", params.customerId);
      if (params.dealRoomId !== undefined) query = query.eq("deal_room_id", params.dealRoomId);
      return query;
    })(),
    (async () => {
      let query = params.supabase
        .from("calendar_events")
        .select("id, title, start_at, owner_id, deal_room_id")
        .eq("org_id", params.profile.org_id)
        .eq("meeting_status", "scheduled")
        .gte("start_at", nowIso())
        .order("start_at", { ascending: true })
        .limit(12);
      if (ownerId !== undefined && ownerId !== null) query = query.eq("owner_id", ownerId);
      if (params.customerId !== undefined) query = query.eq("customer_id", params.customerId);
      if (params.dealRoomId !== undefined) query = query.eq("deal_room_id", params.dealRoomId);
      return query;
    })(),
    (async () => {
      let query = params.supabase
        .from("deal_rooms")
        .select("id, title, priority_band, room_status, owner_id")
        .eq("org_id", params.profile.org_id)
        .in("priority_band", ["strategic", "critical"])
        .in("room_status", ["active", "watchlist", "escalated", "blocked"])
        .order("updated_at", { ascending: false })
        .limit(12);
      if (ownerId !== undefined && ownerId !== null) query = query.eq("owner_id", ownerId);
      if (params.customerId !== undefined) query = query.eq("customer_id", params.customerId);
      if (params.dealRoomId !== undefined) query = query.eq("id", params.dealRoomId);
      return query;
    })()
  ]);

  if (waitingThreadsRes.error) throw new Error(waitingThreadsRes.error.message);
  if (upcomingMeetingsRes.error) throw new Error(upcomingMeetingsRes.error.message);
  if (highPriorityDealsRes.error) throw new Error(highPriorityDealsRes.error.message);

  const waitingThreadRows = (waitingThreadsRes.data ?? []) as Array<{
    id: string;
    subject: string;
    latest_message_at: string | null;
  }>;
  const upcomingMeetingRows = (upcomingMeetingsRes.data ?? []) as Array<{
    id: string;
    title: string;
    start_at: string;
  }>;
  const highPriorityDeals = (highPriorityDealsRes.data ?? []) as Array<{
    id: string;
    title: string;
    priority_band: Database["public"]["Enums"]["deal_room_priority_band"];
    room_status: Database["public"]["Enums"]["deal_room_status"];
  }>;
  let noRecentTouchpointDeals = 0;
  for (const deal of highPriorityDeals) {
    const latestEventRes = await params.supabase
      .from("external_touchpoint_events")
      .select("created_at")
      .eq("org_id", params.profile.org_id)
      .eq("deal_room_id", deal.id)
      .order("created_at", { ascending: false })
      .limit(1)
      .maybeSingle();
    if (latestEventRes.error) throw new Error(latestEventRes.error.message);
    const latest = latestEventRes.data?.created_at ? new Date(latestEventRes.data.created_at).getTime() : 0;
    const cutoff = Date.now() - sinceDays * 24 * 60 * 60 * 1000;
    if (!latest || latest < cutoff) noRecentTouchpointDeals += 1;
  }

  const inputSnapshot = {
    summary,
    waiting_reply_threads: waitingThreadRows.map((item) => ({
      id: item.id,
      subject: item.subject,
      latest_message_at: item.latest_message_at
    })),
    upcoming_meetings: upcomingMeetingRows.map((item) => ({
      id: item.id,
      title: item.title,
      start_at: item.start_at
    })),
    high_priority_deals: highPriorityDeals.map((item) => ({
      id: item.id,
      title: item.title,
      priority_band: item.priority_band,
      room_status: item.room_status
    })),
    no_recent_touchpoint_deal_count: noRecentTouchpointDeals,
    since_days: sinceDays
  } as Record<string, unknown>;

  const provider = getAiProvider();
  const model = provider.getDefaultModel({ reasoning: true });
  const prompt = await getActivePromptVersion({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    scenario: "external_touchpoint_review",
    providerId: provider.id
  });

  const run = await createAiRun({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    customerId: params.customerId ?? null,
    followupId: null,
    triggeredByUserId: params.profile.id,
    triggerSource: "manual",
    scenario: "external_touchpoint_review",
    provider: provider.id,
    model,
    promptVersion: prompt.version,
    inputSnapshot
  });

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "running",
    startedAt: nowIso()
  });

  const startedAt = Date.now();
  let usedFallback = false;
  let fallbackReason: string | null = null;
  let outputSnapshot: Record<string, unknown> = {};
  let responseModel = model;
  let result: ExternalTouchpointReviewResult;

  try {
    if (!provider.isConfigured()) throw new Error("provider_not_configured");

    const response = await provider.chatCompletion({
      scenario: "external_touchpoint_review",
      model,
      systemPrompt: prompt.systemPrompt,
      developerPrompt: `${prompt.developerPrompt}\n\nOutput schema:\n${JSON.stringify(prompt.outputSchema)}`,
      userPrompt: JSON.stringify({
        scenario: "external_touchpoint_review",
        payload: inputSnapshot
      }),
      jsonMode: true,
      strictMode: true,
      useReasonerModel: true
    });
    if (response.error) throw new Error(response.error);
    const candidate = response.parsedJson ?? (response.rawText ? JSON.parse(response.rawText) : null);
    const parsed = externalTouchpointReviewResultSchema.safeParse(candidate);
    if (!parsed.success) throw new Error("external_touchpoint_review_schema_invalid");
    result = parsed.data;
    outputSnapshot = response.rawResponse;
    responseModel = response.model;
  } catch (error) {
    if (!isRuleFallbackEnabled()) {
      const message = error instanceof Error ? error.message : "external_touchpoint_review_failed";
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
    fallbackReason = error instanceof Error ? error.message : "external_touchpoint_review_fallback";
    result = buildFallbackExternalTouchpointReview({
      totalEvents: summary.totalEvents,
      waitingReplyThreads: summary.waitingReplyThreads,
      scheduledMeetings: summary.upcomingMeetings,
      highPriorityDealWithoutTouchpoint: noRecentTouchpointDeals
    });
    outputSnapshot = {
      fallback: true,
      reason: fallbackReason
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
    parsedResult: result as Record<string, unknown>,
    resultSource: usedFallback ? "fallback" : "provider",
    fallbackReason,
    latencyMs: Date.now() - startedAt,
    completedAt: nowIso(),
    errorMessage: usedFallback ? fallbackReason : null
  });

  console.info("[touchpoint.review]", {
    org_id: params.profile.org_id,
    user_id: params.profile.id,
    customer_id: params.customerId ?? null,
    deal_room_id: params.dealRoomId ?? null,
    scenario: "external_touchpoint_review",
    provider: provider.id,
    model: responseModel,
    status: "completed",
    duration_ms: Date.now() - startedAt,
    fallback_reason: fallbackReason
  });

  return {
    result,
    runId: run.id,
    usedFallback,
    fallbackReason
  };
}
