import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { getDealRoomDetail } from "@/services/deal-command-service";

interface RouteParams {
  params: { id: string };
}

export async function GET(_request: Request, { params }: RouteParams) {
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

    return ok(detail);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "get_deal_room_failed", 500);
  }
}

