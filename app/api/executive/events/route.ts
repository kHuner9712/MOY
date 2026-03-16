import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { listBusinessEvents } from "@/services/business-event-service";
import { assertOrgManagerAccess } from "@/services/org-membership-service";

const querySchema = z.object({
  status: z.array(z.enum(["open", "acknowledged", "resolved", "ignored"]))
    .or(z.enum(["open", "acknowledged", "resolved", "ignored"]))
    .optional(),
  eventType: z.array(
    z.enum([
      "first_value_reached",
      "health_declined",
      "renewal_risk_detected",
      "expansion_signal",
      "trial_stalled",
      "trial_activated",
      "onboarding_stuck",
      "deal_blocked",
      "no_recent_touchpoint",
      "manager_attention_escalated",
      "renewal_due_soon",
      "conversion_signal"
    ])
  ).or(
    z.enum([
      "first_value_reached",
      "health_declined",
      "renewal_risk_detected",
      "expansion_signal",
      "trial_stalled",
      "trial_activated",
      "onboarding_stuck",
      "deal_blocked",
      "no_recent_touchpoint",
      "manager_attention_escalated",
      "renewal_due_soon",
      "conversion_signal"
    ])
  ).optional(),
  limit: z.coerce.number().int().positive().max(500).optional()
});

function ensureArray<T>(input: T | T[] | undefined): T[] | undefined {
  if (input === undefined) return undefined;
  return Array.isArray(input) ? input : [input];
}

export async function GET(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const url = new URL(request.url);
  const parsed = querySchema.safeParse({
    status: url.searchParams.getAll("status"),
    eventType: url.searchParams.getAll("eventType"),
    limit: url.searchParams.get("limit") ?? undefined
  });
  if (!parsed.success) return fail("Invalid query", 400);

  try {
    await assertOrgManagerAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    const events = await listBusinessEvents({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      statuses: ensureArray(parsed.data.status),
      eventTypes: ensureArray(parsed.data.eventType),
      limit: parsed.data.limit ?? 120
    });

    return ok({ events });
  } catch (error) {
    const message = error instanceof Error ? error.message : "get_executive_events_failed";
    const status = message === "org_manager_access_required" ? 403 : 500;
    return fail(message, status);
  }
}
