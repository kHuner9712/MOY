import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { updateBusinessEventStatus } from "@/services/business-event-service";
import { assertOrgManagerAccess } from "@/services/org-membership-service";

export async function POST(_: Request, { params }: { params: { id: string } }) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  try {
    await assertOrgManagerAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    const event = await updateBusinessEventStatus({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      eventId: params.id,
      status: "ignored"
    });

    return ok({ event });
  } catch (error) {
    const message = error instanceof Error ? error.message : "ignore_business_event_failed";
    const status = message === "org_manager_access_required" ? 403 : message === "business_event_not_found" ? 404 : 500;
    return fail(message, status);
  }
}
