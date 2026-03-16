import type { ZodSchema } from "zod";

import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import { buildFallbackMeetingAgenda, buildFallbackMeetingFollowupSummary } from "@/lib/external-touchpoint-fallback";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { captureActionOutcome } from "@/services/action-outcome-service";
import { getActivePromptVersion } from "@/services/ai-prompt-service";
import { createAiRun, updateAiRunStatus } from "@/services/ai-run-service";
import { mapCalendarEventRow, mapContentDraftRow } from "@/services/mappers";
import { recordExternalTouchpointEvent, runTouchpointRules } from "@/services/external-touchpoint-service";
import { createWorkItem } from "@/services/work-item-service";
import { meetingAgendaGenerationResultSchema, meetingFollowupSummaryResultSchema, type AiScenario, type MeetingAgendaGenerationResult, type MeetingFollowupSummaryResult } from "@/types/ai";
import type { Database } from "@/types/database";
import type { ContentDraft } from "@/types/preparation";
import type { CalendarEvent } from "@/types/touchpoint";
import type { WorkItem } from "@/types/work";

type DbClient = ServerSupabaseClient;
type ProfileRow = Database["public"]["Tables"]["profiles"]["Row"];

interface ProfileLite {
  id: string;
  display_name: string;
}

interface CustomerLite {
  id: string;
  company_name: string;
}

function nowIso(): string {
  return new Date().toISOString();
}

function plusDays(days: number): string {
  const next = new Date();
  next.setDate(next.getDate() + days);
  return next.toISOString();
}

function toStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

async function getCalendarEventById(params: {
  supabase: DbClient;
  orgId: string;
  eventId: string;
}): Promise<CalendarEvent | null> {
  const { data, error } = await params.supabase
    .from("calendar_events")
    .select("*, owner:profiles!calendar_events_owner_id_fkey(id, display_name), customer:customers!calendar_events_customer_id_fkey(id, company_name)")
    .eq("org_id", params.orgId)
    .eq("id", params.eventId)
    .maybeSingle();
  if (error) throw new Error(error.message);
  if (!data) return null;
  return mapCalendarEventRow(
    data as Database["public"]["Tables"]["calendar_events"]["Row"] & { owner?: ProfileLite | null; customer?: CustomerLite | null }
  );
}

async function ensureOpenWorkItemAbsent(params: {
  supabase: DbClient;
  orgId: string;
  sourceRefType: string;
  sourceRefId: string;
}): Promise<boolean> {
  const { data, error } = await params.supabase
    .from("work_items")
    .select("id")
    .eq("org_id", params.orgId)
    .eq("source_ref_type", params.sourceRefType)
    .eq("source_ref_id", params.sourceRefId)
    .in("status", ["todo", "in_progress", "snoozed"])
    .limit(1)
    .maybeSingle();
  if (error) throw new Error(error.message);
  return !data;
}

async function runTouchpointScenario<T>(params: {
  supabase: DbClient;
  profile: ProfileRow;
  scenario: AiScenario;
  customerId?: string | null;
  inputSnapshot: Record<string, unknown>;
  schema: ZodSchema<T>;
  fallbackBuilder: () => T;
}): Promise<{
  runId: string;
  result: T;
  usedFallback: boolean;
  fallbackReason: string | null;
}> {
  const provider = getAiProvider();
  const model = provider.getDefaultModel({ reasoning: true });

  const prompt = await getActivePromptVersion({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    scenario: params.scenario,
    providerId: provider.id
  });

  const run = await createAiRun({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    customerId: params.customerId ?? null,
    followupId: null,
    triggeredByUserId: params.profile.id,
    triggerSource: "manual",
    scenario: params.scenario,
    provider: provider.id,
    model,
    promptVersion: prompt.version,
    inputSnapshot: params.inputSnapshot
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
  let result: T;
  let outputSnapshot: Record<string, unknown> = {};
  let responseModel = model;

  try {
    if (!provider.isConfigured()) throw new Error("provider_not_configured");

    const response = await provider.chatCompletion({
      scenario: params.scenario,
      model,
      systemPrompt: prompt.systemPrompt,
      developerPrompt: `${prompt.developerPrompt}\n\nOutput schema:\n${JSON.stringify(prompt.outputSchema)}`,
      userPrompt: JSON.stringify({
        scenario: params.scenario,
        payload: params.inputSnapshot
      }),
      jsonMode: true,
      strictMode: true,
      useReasonerModel: true
    });

    if (response.error) throw new Error(response.error);
    const candidate = response.parsedJson ?? (response.rawText ? JSON.parse(response.rawText) : null);
    const parsed = params.schema.safeParse(candidate);
    if (!parsed.success) throw new Error(`${params.scenario}_schema_invalid`);

    result = parsed.data;
    outputSnapshot = response.rawResponse;
    responseModel = response.model;
  } catch (error) {
    if (!isRuleFallbackEnabled()) {
      const message = error instanceof Error ? error.message : `${params.scenario}_failed`;
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
    fallbackReason = error instanceof Error ? error.message : `${params.scenario}_fallback`;
    result = params.fallbackBuilder();
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

  console.info("[touchpoint.ai]", {
    org_id: params.profile.org_id,
    user_id: params.profile.id,
    customer_id: params.customerId ?? null,
    scenario: params.scenario,
    provider: provider.id,
    model: responseModel,
    status: "completed",
    duration_ms: Date.now() - startedAt,
    fallback_reason: fallbackReason
  });

  return {
    runId: run.id,
    result,
    usedFallback,
    fallbackReason
  };
}

export async function listCalendarEvents(params: {
  supabase: DbClient;
  orgId: string;
  ownerId?: string | null;
  customerId?: string | null;
  dealRoomId?: string | null;
  status?: Database["public"]["Enums"]["calendar_meeting_status"];
  limit?: number;
}): Promise<CalendarEvent[]> {
  let query = params.supabase
    .from("calendar_events")
    .select("*, owner:profiles!calendar_events_owner_id_fkey(id, display_name), customer:customers!calendar_events_customer_id_fkey(id, company_name)")
    .eq("org_id", params.orgId)
    .order("start_at", { ascending: true })
    .limit(params.limit ?? 80);
  if (params.ownerId !== undefined) query = query.eq("owner_id", params.ownerId);
  if (params.customerId !== undefined) query = query.eq("customer_id", params.customerId);
  if (params.dealRoomId !== undefined) query = query.eq("deal_room_id", params.dealRoomId);
  if (params.status) query = query.eq("meeting_status", params.status);
  const { data, error } = await query;
  if (error) throw new Error(error.message);
  return (data ?? []).map(
    (row: Database["public"]["Tables"]["calendar_events"]["Row"] & { owner?: ProfileLite | null; customer?: CustomerLite | null }) =>
      mapCalendarEventRow(row)
  );
}

export async function createCalendarEvent(params: {
  supabase: DbClient;
  profile: ProfileRow;
  ownerId?: string;
  customerId?: string | null;
  opportunityId?: string | null;
  dealRoomId?: string | null;
  externalAccountId?: string | null;
  externalEventRef?: string | null;
  eventType: Database["public"]["Enums"]["calendar_event_type"];
  title: string;
  description?: string;
  attendees?: string[];
  startAt: string;
  endAt: string;
  meetingStatus?: Database["public"]["Enums"]["calendar_meeting_status"];
  agendaSummary?: string;
  notesSummary?: string;
  sourceSnapshot?: Record<string, unknown>;
}): Promise<CalendarEvent> {
  const payload: Database["public"]["Tables"]["calendar_events"]["Insert"] = {
    org_id: params.profile.org_id,
    owner_id: params.ownerId ?? params.profile.id,
    customer_id: params.customerId ?? null,
    opportunity_id: params.opportunityId ?? null,
    deal_room_id: params.dealRoomId ?? null,
    external_account_id: params.externalAccountId ?? null,
    external_event_ref: params.externalEventRef ?? null,
    event_type: params.eventType,
    title: params.title,
    description: params.description ?? "",
    attendees: (params.attendees ?? []) as Database["public"]["Tables"]["calendar_events"]["Insert"]["attendees"],
    start_at: params.startAt,
    end_at: params.endAt,
    meeting_status: params.meetingStatus ?? "scheduled",
    agenda_summary: params.agendaSummary ?? "",
    notes_summary: params.notesSummary ?? "",
    source_snapshot: (params.sourceSnapshot ?? {}) as Database["public"]["Tables"]["calendar_events"]["Insert"]["source_snapshot"]
  };

  const { data, error } = await params.supabase
    .from("calendar_events")
    .insert(payload)
    .select("*, owner:profiles!calendar_events_owner_id_fkey(id, display_name), customer:customers!calendar_events_customer_id_fkey(id, company_name)")
    .single();
  if (error || !data) throw new Error(error?.message ?? "create_calendar_event_failed");

  const event = mapCalendarEventRow(
    data as Database["public"]["Tables"]["calendar_events"]["Row"] & { owner?: ProfileLite | null; customer?: CustomerLite | null }
  );

  await recordExternalTouchpointEvent({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    ownerId: event.ownerId,
    customerId: event.customerId,
    opportunityId: event.opportunityId,
    dealRoomId: event.dealRoomId,
    touchpointType: "meeting",
    eventType: "meeting_scheduled",
    relatedRefType: "calendar_event",
    relatedRefId: event.id,
    eventSummary: `Meeting scheduled: ${event.title}`,
    eventPayload: {
      event_type: event.eventType,
      start_at: event.startAt,
      end_at: event.endAt,
      attendees: event.attendees
    }
  });

  await runTouchpointRules({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    actorUserId: params.profile.id
  }).catch(() => null);

  return event;
}

export async function generateMeetingAgenda(params: {
  supabase: DbClient;
  profile: ProfileRow;
  eventId: string;
}): Promise<{
  event: CalendarEvent;
  result: MeetingAgendaGenerationResult;
  runId: string;
  usedFallback: boolean;
  fallbackReason: string | null;
}> {
  const event = await getCalendarEventById({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    eventId: params.eventId
  });
  if (!event) throw new Error("calendar_event_not_found");

  const [customerRes, roomRes, recentFollowupsRes] = await Promise.all([
    event.customerId
      ? params.supabase
          .from("customers")
          .select("id, company_name, current_stage, risk_level, win_probability, ai_summary, ai_suggestion")
          .eq("org_id", params.profile.org_id)
          .eq("id", event.customerId)
          .maybeSingle()
      : Promise.resolve({ data: null, error: null }),
    event.dealRoomId
      ? params.supabase
          .from("deal_rooms")
          .select("id, title, room_status, current_goal, current_blockers, next_milestone")
          .eq("org_id", params.profile.org_id)
          .eq("id", event.dealRoomId)
          .maybeSingle()
      : Promise.resolve({ data: null, error: null }),
    event.customerId
      ? params.supabase
          .from("followups")
          .select("id, summary, objections, next_step, created_at")
          .eq("org_id", params.profile.org_id)
          .eq("customer_id", event.customerId)
          .eq("draft_status", "confirmed")
          .order("created_at", { ascending: false })
          .limit(6)
      : Promise.resolve({ data: [], error: null })
  ]);
  if (customerRes.error) throw new Error(customerRes.error.message);
  if (roomRes.error) throw new Error(roomRes.error.message);
  if (recentFollowupsRes.error) throw new Error(recentFollowupsRes.error.message);

  const inputSnapshot = {
    event: {
      id: event.id,
      event_type: event.eventType,
      title: event.title,
      description: event.description,
      attendees: event.attendees,
      start_at: event.startAt
    },
    customer: customerRes.data ?? null,
    deal_room: roomRes.data ?? null,
    recent_followups: recentFollowupsRes.data ?? []
  } as Record<string, unknown>;

  const execution = await runTouchpointScenario({
    supabase: params.supabase,
    profile: params.profile,
    scenario: "meeting_agenda_generation",
    customerId: event.customerId,
    inputSnapshot,
    schema: meetingAgendaGenerationResultSchema,
    fallbackBuilder: () =>
      buildFallbackMeetingAgenda({
        customerName: customerRes.data?.company_name ?? "Customer",
        meetingType: event.eventType
      })
  });

  const { error: updateError } = await params.supabase
    .from("calendar_events")
    .update({
      agenda_summary: execution.result.meeting_goal,
      source_snapshot: {
        ...(event.sourceSnapshot ?? {}),
        meeting_agenda: execution.result
      },
      updated_at: nowIso()
    })
    .eq("org_id", params.profile.org_id)
    .eq("id", event.id);
  if (updateError) throw new Error(updateError.message);

  const refreshed = await getCalendarEventById({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    eventId: event.id
  });
  if (!refreshed) throw new Error("calendar_event_not_found_after_update");

  const normalizedResult: MeetingAgendaGenerationResult = {
    meeting_goal: execution.result.meeting_goal,
    agenda_points: execution.result.agenda_points ?? [],
    must_cover: execution.result.must_cover ?? [],
    risk_notes: execution.result.risk_notes ?? [],
    expected_next_step: execution.result.expected_next_step ?? []
  };

  return {
    event: refreshed,
    result: normalizedResult,
    runId: execution.runId,
    usedFallback: execution.usedFallback,
    fallbackReason: execution.fallbackReason
  };
}

export async function completeMeetingEvent(params: {
  supabase: DbClient;
  profile: ProfileRow;
  eventId: string;
  notesSummary?: string | null;
  captureOutcome?: boolean;
}): Promise<{
  event: CalendarEvent;
  summaryResult: MeetingFollowupSummaryResult;
  runId: string;
  usedFallback: boolean;
  fallbackReason: string | null;
  draft: ContentDraft;
  linkedWorkItem: WorkItem | null;
}> {
  const event = await getCalendarEventById({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    eventId: params.eventId
  });
  if (!event) throw new Error("calendar_event_not_found");

  const notesSummary = params.notesSummary?.trim() || event.notesSummary || "";

  const inputSnapshot = {
    event: {
      id: event.id,
      title: event.title,
      event_type: event.eventType,
      attendees: event.attendees,
      start_at: event.startAt,
      end_at: event.endAt
    },
    notes_summary: notesSummary,
    previous_agenda_summary: event.agendaSummary
  } as Record<string, unknown>;

  const execution = await runTouchpointScenario({
    supabase: params.supabase,
    profile: params.profile,
    scenario: "meeting_followup_summary",
    customerId: event.customerId,
    inputSnapshot,
    schema: meetingFollowupSummaryResultSchema,
    fallbackBuilder: () =>
      buildFallbackMeetingFollowupSummary({
        meetingTitle: event.title,
        notesSummary
      })
  });
  const summaryResult: MeetingFollowupSummaryResult = {
    meeting_summary: execution.result.meeting_summary,
    decisions_made: execution.result.decisions_made ?? [],
    next_actions: execution.result.next_actions ?? [],
    followup_message_draft_hint: execution.result.followup_message_draft_hint,
    checkpoint_update_hint: execution.result.checkpoint_update_hint ?? []
  };

  const { error: updateError } = await params.supabase
    .from("calendar_events")
    .update({
      meeting_status: "completed",
      notes_summary: summaryResult.meeting_summary,
      source_snapshot: {
        ...(event.sourceSnapshot ?? {}),
        meeting_followup_summary: summaryResult
      },
      updated_at: nowIso()
    })
    .eq("org_id", params.profile.org_id)
    .eq("id", event.id);
  if (updateError) throw new Error(updateError.message);

  await recordExternalTouchpointEvent({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    ownerId: event.ownerId,
    customerId: event.customerId,
    opportunityId: event.opportunityId,
    dealRoomId: event.dealRoomId,
    touchpointType: "meeting",
    eventType: "meeting_completed",
    relatedRefType: "calendar_event",
    relatedRefId: event.id,
    eventSummary: `Meeting completed: ${event.title}`,
    eventPayload: summaryResult
  });

  const draftInsert: Database["public"]["Tables"]["content_drafts"]["Insert"] = {
    org_id: params.profile.org_id,
    owner_id: event.ownerId,
    customer_id: event.customerId,
    opportunity_id: event.opportunityId,
    prep_card_id: null,
    work_item_id: null,
    draft_type: "meeting_summary",
    status: "draft",
    title: `${event.title} | Meeting Summary`,
    content_markdown: [
      `## ${event.title} Meeting Summary`,
      "",
      summaryResult.meeting_summary,
      "",
      "### Decisions",
      ...summaryResult.decisions_made.map((item) => `- ${item}`),
      "",
      "### Next Actions",
      ...summaryResult.next_actions.map((item) => `- ${item}`)
    ].join("\n"),
    content_text: `${summaryResult.meeting_summary}\n${summaryResult.next_actions.join("\n")}`,
    rationale: "Generated from meeting_followup_summary scenario.",
    source_snapshot: {
      event_id: event.id,
      checkpoint_update_hint: summaryResult.checkpoint_update_hint,
      followup_message_draft_hint: summaryResult.followup_message_draft_hint
    } as Database["public"]["Tables"]["content_drafts"]["Insert"]["source_snapshot"],
    generated_by: params.profile.id,
    ai_run_id: execution.runId
  };
  const draftRes = await params.supabase.from("content_drafts").insert(draftInsert).select("*").single();
  if (draftRes.error || !draftRes.data) throw new Error(draftRes.error?.message ?? "create_meeting_summary_draft_failed");
  const draft = mapContentDraftRow(draftRes.data as Database["public"]["Tables"]["content_drafts"]["Row"]);

  let linkedWorkItem: WorkItem | null = null;
  if (event.customerId) {
    const canCreateFollowupTask = await ensureOpenWorkItemAbsent({
      supabase: params.supabase,
      orgId: params.profile.org_id,
      sourceRefType: "meeting_followup",
      sourceRefId: event.id
    });
    if (canCreateFollowupTask) {
      linkedWorkItem = await createWorkItem({
        supabase: params.supabase,
        orgId: params.profile.org_id,
        ownerId: event.ownerId,
        customerId: event.customerId,
        opportunityId: event.opportunityId,
        sourceType: "manual",
        workType: "followup_call",
        title: `[Meeting] Follow-up: ${event.title}`,
        description: summaryResult.followup_message_draft_hint,
        rationale: "Meeting completed; follow-up should be sent within 24 hours.",
        priorityScore: 76,
        priorityBand: "high",
        dueAt: plusDays(1),
        sourceRefType: "meeting_followup",
        sourceRefId: event.id,
        createdBy: params.profile.id
      });
    }
  }

  if (params.captureOutcome ?? true) {
    await captureActionOutcome({
      supabase: params.supabase,
      profile: params.profile,
      ownerId: event.ownerId,
      customerId: event.customerId,
      opportunityId: event.opportunityId,
      workItemId: linkedWorkItem?.id ?? null,
      prepCardId: null,
      contentDraftId: draft.id,
      outcomeType: "meeting_result",
      resultStatus: "neutral",
      keyOutcomeSummary: summaryResult.meeting_summary,
      nextStepDefined: summaryResult.next_actions.length > 0,
      nextStepText: summaryResult.next_actions[0] ?? null,
      usedPrepCard: false,
      usedDraft: true,
      usefulnessRating: "unknown",
      autoInfer: false
    }).catch(() => null);
  }

  await runTouchpointRules({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    actorUserId: params.profile.id
  }).catch(() => null);

  const refreshed = await getCalendarEventById({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    eventId: event.id
  });
  if (!refreshed) throw new Error("calendar_event_not_found_after_complete");

  return {
    event: refreshed,
    summaryResult,
    runId: execution.runId,
    usedFallback: execution.usedFallback,
    fallbackReason: execution.fallbackReason,
    draft,
    linkedWorkItem
  };
}
