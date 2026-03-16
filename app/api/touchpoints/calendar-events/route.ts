import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { completeMeetingEvent, createCalendarEvent, generateMeetingAgenda } from "@/services/calendar-event-service";
import { generatePrepCard } from "@/services/preparation-engine-service";

const requestSchema = z.object({
  eventId: z.string().uuid().optional(),
  ownerId: z.string().uuid().optional(),
  customerId: z.string().uuid().optional(),
  opportunityId: z.string().uuid().optional(),
  dealRoomId: z.string().uuid().optional(),
  eventType: z.enum(["customer_meeting", "demo", "proposal_review", "internal_strategy", "manager_intervention"]).optional(),
  title: z.string().max(300).optional(),
  description: z.string().max(3000).optional(),
  attendees: z.array(z.string().min(1)).optional(),
  startAt: z.string().optional(),
  endAt: z.string().optional(),
  meetingStatus: z.enum(["scheduled", "completed", "cancelled", "no_show"]).optional(),
  agendaSummary: z.string().max(3000).optional(),
  notesSummary: z.string().max(5000).optional(),
  autoGeneratePrep: z.boolean().optional(),
  autoGenerateAgenda: z.boolean().optional(),
  completeAndGenerateFollowup: z.boolean().optional(),
  captureOutcome: z.boolean().optional()
});

function isValidDateTime(value: string | undefined): boolean {
  if (!value) return false;
  return !Number.isNaN(new Date(value).getTime());
}

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const body = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(body ?? {});
  if (!parsed.success) return fail("Invalid request payload", 400);
  const payload = parsed.data;

  try {
    if (!isManager(auth.profile) && payload.customerId) {
      const customerRes = await auth.supabase
        .from("customers")
        .select("owner_id")
        .eq("org_id", auth.profile.org_id)
        .eq("id", payload.customerId)
        .maybeSingle();
      if (customerRes.error) throw new Error(customerRes.error.message);
      const customerOwnerId = (customerRes.data as { owner_id: string } | null)?.owner_id ?? null;
      if (!customerOwnerId || customerOwnerId !== auth.profile.id) return fail("No permission for this customer", 403);
    }

    if (payload.completeAndGenerateFollowup) {
      if (!payload.eventId) return fail("eventId is required when completeAndGenerateFollowup=true", 400);
      const result = await completeMeetingEvent({
        supabase: auth.supabase,
        profile: auth.profile,
        eventId: payload.eventId,
        notesSummary: payload.notesSummary ?? null,
        captureOutcome: payload.captureOutcome ?? true
      });
      return ok(result);
    }

    if (!payload.eventType || !payload.title || !payload.startAt || !payload.endAt) {
      return fail("eventType/title/startAt/endAt are required", 400);
    }
    if (!isValidDateTime(payload.startAt) || !isValidDateTime(payload.endAt)) {
      return fail("Invalid datetime format for startAt/endAt", 400);
    }

    const ownerId = isManager(auth.profile) ? payload.ownerId ?? auth.profile.id : auth.profile.id;
    const event = await createCalendarEvent({
      supabase: auth.supabase,
      profile: auth.profile,
      ownerId,
      customerId: payload.customerId ?? null,
      opportunityId: payload.opportunityId ?? null,
      dealRoomId: payload.dealRoomId ?? null,
      eventType: payload.eventType,
      title: payload.title,
      description: payload.description ?? "",
      attendees: payload.attendees ?? [],
      startAt: payload.startAt,
      endAt: payload.endAt,
      meetingStatus: payload.meetingStatus ?? "scheduled",
      agendaSummary: payload.agendaSummary ?? "",
      notesSummary: payload.notesSummary ?? ""
    });

    let prepCard = null;
    if ((payload.autoGeneratePrep ?? true) && payload.customerId && ["customer_meeting", "demo", "proposal_review"].includes(payload.eventType)) {
      prepCard = await generatePrepCard({
        supabase: auth.supabase,
        profile: auth.profile,
        cardType: "meeting_prep",
        customerId: payload.customerId,
        opportunityId: payload.opportunityId ?? null,
        meetingPurpose: payload.title,
        triggerSource: "manual"
      }).catch(() => null);
    }

    let agenda = null;
    if (payload.autoGenerateAgenda ?? true) {
      agenda = await generateMeetingAgenda({
        supabase: auth.supabase,
        profile: auth.profile,
        eventId: event.id
      }).catch(() => null);
    }

    return ok({
      event,
      prepCard: prepCard?.prepCard ?? null,
      agendaResult: agenda?.result ?? null,
      agendaRunId: agenda?.runId ?? null,
      agendaUsedFallback: agenda?.usedFallback ?? false
    });
  } catch (error) {
    return fail(error instanceof Error ? error.message : "calendar_event_operation_failed", 500);
  }
}
