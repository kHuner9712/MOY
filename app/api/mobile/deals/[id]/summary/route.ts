import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { getDealRoomById } from "@/services/deal-room-service";
import { getMobileTouchpointView } from "@/services/mobile-touchpoint-service";

export async function GET(
  _request: Request,
  context: { params: { id: string } }
) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const dealRoomId = context.params.id;

  try {
    const room = await getDealRoomById({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      dealRoomId
    });
    if (!room) return fail("Deal room not found", 404);

    if (auth.profile.role !== "manager") {
      const participantRes = await auth.supabase
        .from("deal_participants")
        .select("id")
        .eq("org_id", auth.profile.org_id)
        .eq("deal_room_id", room.id)
        .eq("user_id", auth.profile.id)
        .eq("is_active", true)
        .maybeSingle();
      if (participantRes.error) throw new Error(participantRes.error.message);
      if (room.ownerId !== auth.profile.id && !participantRes.data) return fail("No permission for this deal", 403);
    }

    const [checkpointsRes, decisionsRes, interventionsRes, tasksRes, prepCustomerRes, prepOpportunityRes, outcomesRes, touchpoints] = await Promise.all([
      auth.supabase
        .from("deal_checkpoints")
        .select("id, checkpoint_type, status, title, due_at, completed_at")
        .eq("org_id", auth.profile.org_id)
        .eq("deal_room_id", room.id)
        .order("created_at", { ascending: true })
        .limit(16),
      auth.supabase
        .from("decision_records")
        .select("id, decision_type, status, title, due_at, completed_at")
        .eq("org_id", auth.profile.org_id)
        .eq("deal_room_id", room.id)
        .order("created_at", { ascending: false })
        .limit(10),
      auth.supabase
        .from("intervention_requests")
        .select("id, request_type, status, priority_band, request_summary, due_at")
        .eq("org_id", auth.profile.org_id)
        .eq("deal_room_id", room.id)
        .order("created_at", { ascending: false })
        .limit(10),
      auth.supabase
        .from("work_items")
        .select("id, title, status, priority_band, due_at, work_type")
        .eq("org_id", auth.profile.org_id)
        .eq("customer_id", room.customerId)
        .order("updated_at", { ascending: false })
        .limit(14),
      auth.supabase
        .from("prep_cards")
        .select("id, card_type, status, title, summary, updated_at")
        .eq("org_id", auth.profile.org_id)
        .eq("customer_id", room.customerId)
        .order("updated_at", { ascending: false })
        .limit(10),
      room.opportunityId
        ? auth.supabase
            .from("prep_cards")
            .select("id, card_type, status, title, summary, updated_at")
            .eq("org_id", auth.profile.org_id)
            .eq("opportunity_id", room.opportunityId)
            .order("updated_at", { ascending: false })
            .limit(10)
        : Promise.resolve({ data: [], error: null }),
      auth.supabase
        .from("action_outcomes")
        .select("id, outcome_type, result_status, key_outcome_summary, created_at")
        .eq("org_id", auth.profile.org_id)
        .eq("customer_id", room.customerId)
        .order("created_at", { ascending: false })
        .limit(10),
      getMobileTouchpointView({
        supabase: auth.supabase,
        profile: auth.profile,
        dealRoomId: room.id
      })
    ]);

    if (checkpointsRes.error) throw new Error(checkpointsRes.error.message);
    if (decisionsRes.error) throw new Error(decisionsRes.error.message);
    if (interventionsRes.error) throw new Error(interventionsRes.error.message);
    if (tasksRes.error) throw new Error(tasksRes.error.message);
    if (prepCustomerRes.error) throw new Error(prepCustomerRes.error.message);
    if (prepOpportunityRes.error) throw new Error(prepOpportunityRes.error.message);
    if (outcomesRes.error) throw new Error(outcomesRes.error.message);

    const prepRows = [
      ...((prepCustomerRes.data ?? []) as Array<{
        id: string;
        card_type: string;
        status: string;
        title: string;
        summary: string | null;
        updated_at: string;
      }>),
      ...((prepOpportunityRes.data ?? []) as Array<{
        id: string;
        card_type: string;
        status: string;
        title: string;
        summary: string | null;
        updated_at: string;
      }>)
    ];

    const prepCards = prepRows
      .filter((item, index, arr) => arr.findIndex((other) => other.id === item.id) === index)
      .slice(0, 10);

    return ok({
      room: {
        id: room.id,
        title: room.title,
        commandSummary: room.commandSummary,
        currentGoal: room.currentGoal,
        currentBlockers: room.currentBlockers,
        nextMilestone: room.nextMilestone,
        nextMilestoneDueAt: room.nextMilestoneDueAt,
        roomStatus: room.roomStatus,
        priorityBand: room.priorityBand,
        managerAttentionNeeded: room.managerAttentionNeeded
      },
      checkpoints: checkpointsRes.data ?? [],
      decisions: decisionsRes.data ?? [],
      interventions: interventionsRes.data ?? [],
      tasks: tasksRes.data ?? [],
      prepCards,
      outcomes: outcomesRes.data ?? [],
      touchpoints
    });
  } catch (error) {
    return fail(error instanceof Error ? error.message : "mobile_deal_summary_failed", 500);
  }
}
