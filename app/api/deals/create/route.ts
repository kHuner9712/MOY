import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { createDealRoom } from "@/services/deal-room-service";

const requestSchema = z.object({
  customerId: z.string().uuid(),
  opportunityId: z.string().uuid().optional(),
  ownerId: z.string().uuid().optional(),
  title: z.string().min(1).max(120).optional(),
  priorityBand: z.enum(["normal", "important", "strategic", "critical"]).optional(),
  roomStatus: z.enum(["active", "watchlist", "escalated", "blocked", "won", "lost", "archived"]).optional(),
  currentGoal: z.string().max(500).optional(),
  currentBlockers: z.array(z.string()).optional(),
  nextMilestone: z.string().max(300).optional(),
  nextMilestoneDueAt: z.string().datetime().optional(),
  managerAttentionNeeded: z.boolean().optional()
});

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const body = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(body ?? {});
  if (!parsed.success) return fail("Invalid request payload", 400);

  if (!isManager(auth.profile) && parsed.data.ownerId && parsed.data.ownerId !== auth.profile.id) {
    return fail("Sales cannot assign room owner", 403);
  }

  try {
    const result = await createDealRoom({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      ownerId: parsed.data.ownerId ?? auth.profile.id,
      createdBy: auth.profile.id,
      customerId: parsed.data.customerId,
      opportunityId: parsed.data.opportunityId ?? null,
      title: parsed.data.title,
      roomStatus: parsed.data.roomStatus,
      priorityBand: parsed.data.priorityBand,
      currentGoal: parsed.data.currentGoal,
      currentBlockers: parsed.data.currentBlockers,
      nextMilestone: parsed.data.nextMilestone ?? null,
      nextMilestoneDueAt: parsed.data.nextMilestoneDueAt ?? null,
      managerAttentionNeeded: parsed.data.managerAttentionNeeded,
      sourceSnapshot: {
        trigger: "manual_create",
        created_by: auth.profile.id
      }
    });
    return ok(result);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "create_deal_room_failed", 500);
  }
}

