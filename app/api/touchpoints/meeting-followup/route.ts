import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { completeMeetingEvent } from "@/services/calendar-event-service";

const requestSchema = z.object({
  eventId: z.string().uuid(),
  notesSummary: z.string().max(5000).optional(),
  captureOutcome: z.boolean().optional()
});

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const body = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(body ?? {});
  if (!parsed.success) return fail("Invalid request payload", 400);

  try {
    const result = await completeMeetingEvent({
      supabase: auth.supabase,
      profile: auth.profile,
      eventId: parsed.data.eventId,
      notesSummary: parsed.data.notesSummary ?? null,
      captureOutcome: parsed.data.captureOutcome ?? true
    });
    return ok(result);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "meeting_followup_failed", 500);
  }
}
