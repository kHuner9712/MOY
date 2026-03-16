import { fail, ok } from "@/lib/api-response";
import { canAccessCustomer } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { listBusinessEvents } from "@/services/business-event-service";
import { getCustomerLatestHealthSnapshot } from "@/services/customer-health-service";
import { listRenewalWatchItems } from "@/services/renewal-watch-service";

export async function GET(_: Request, { params }: { params: { id: string } }) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  try {
    const customerRes = await auth.supabase
      .from("customers")
      .select("id,org_id,owner_id")
      .eq("org_id", auth.profile.org_id)
      .eq("id", params.id)
      .maybeSingle();
    if (customerRes.error) throw new Error(customerRes.error.message);
    if (!customerRes.data) return fail("Customer not found", 404);
    if (!canAccessCustomer(auth.profile, customerRes.data)) return fail("No permission to access this customer", 403);

    const [snapshot, events, renewalWatch] = await Promise.all([
      getCustomerLatestHealthSnapshot({
        supabase: auth.supabase,
        orgId: auth.profile.org_id,
        customerId: params.id
      }),
      listBusinessEvents({
        supabase: auth.supabase,
        orgId: auth.profile.org_id,
        limit: 120
      }),
      listRenewalWatchItems({
        supabase: auth.supabase,
        orgId: auth.profile.org_id,
        limit: 200
      })
    ]);

    const relatedEvents = events
      .filter((item) => {
        if (item.entityType === "customer" && item.entityId === params.id) return true;
        const customerId = typeof item.eventPayload.customer_id === "string" ? item.eventPayload.customer_id : null;
        return customerId === params.id;
      })
      .slice(0, 20);

    const renewal = renewalWatch.find((item) => item.customerId === params.id) ?? null;

    return ok({
      customerId: params.id,
      snapshot,
      relatedEvents,
      renewalWatch: renewal
    });
  } catch (error) {
    return fail(error instanceof Error ? error.message : "get_customer_health_summary_failed", 500);
  }
}

