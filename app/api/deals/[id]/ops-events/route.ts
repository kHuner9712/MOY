import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { listBusinessEvents } from "@/services/business-event-service";
import { getDealRoomDetail } from "@/services/deal-command-service";

export async function GET(_: Request, { params }: { params: { id: string } }) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  try {
    const detail = await getDealRoomDetail({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      dealRoomId: params.id
    });
    if (!detail) return fail("Deal room not found", 404);

    if (!isManager(auth.profile)) {
      const isParticipant = detail.participants.some((item) => item.userId === auth.profile.id && item.isActive);
      if (!isParticipant && detail.room.ownerId !== auth.profile.id) {
        return fail("Sales can only access own or joined deal rooms", 403);
      }
    }

    const events = await listBusinessEvents({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      limit: 200
    });

    const related = events
      .filter((item) => {
        if (item.entityType === "deal_room" && item.entityId === detail.room.id) return true;
        const payload = item.eventPayload;
        const payloadDealId = typeof payload.deal_room_id === "string" ? payload.deal_room_id : null;
        const payloadCustomerId = typeof payload.customer_id === "string" ? payload.customer_id : null;
        return payloadDealId === detail.room.id || payloadCustomerId === detail.room.customerId;
      })
      .slice(0, 30);

    const recommendedActions = Array.from(
      new Set(
        related
          .map((item) => (typeof item.eventPayload.recommended_action === "string" ? item.eventPayload.recommended_action : null))
          .filter((item): item is string => Boolean(item))
      )
    ).slice(0, 8);

    return ok({
      dealRoomId: detail.room.id,
      customerId: detail.room.customerId,
      events: related,
      recommendedActions
    });
  } catch (error) {
    return fail(error instanceof Error ? error.message : "get_deal_ops_events_failed", 500);
  }
}

