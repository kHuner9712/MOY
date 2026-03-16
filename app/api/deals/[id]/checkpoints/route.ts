import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { getDealRoomDetail } from "@/services/deal-command-service";
import { createDealCheckpoint, updateDealCheckpointStatus } from "@/services/deal-checkpoint-service";

interface RouteParams {
  params: { id: string };
}

const requestSchema = z.object({
  checkpointId: z.string().uuid().optional(),
  checkpointType: z.enum([
    "qualification",
    "need_confirmed",
    "proposal_sent",
    "quote_sent",
    "decision_maker_confirmed",
    "budget_confirmed",
    "trial_started",
    "contract_review",
    "closing"
  ]).optional(),
  title: z.string().max(160).optional(),
  description: z.string().max(1000).optional(),
  status: z.enum(["pending", "completed", "blocked", "skipped"]).optional(),
  ownerId: z.string().uuid().optional(),
  dueAt: z.string().datetime().optional(),
  blockedReason: z.string().max(800).optional()
});

export async function POST(request: Request, { params }: RouteParams) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const body = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(body ?? {});
  if (!parsed.success) return fail("Invalid request payload", 400);

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
        return fail("Sales can only operate own/joined deal rooms", 403);
      }
      if (parsed.data.ownerId && parsed.data.ownerId !== auth.profile.id) {
        return fail("Sales cannot assign checkpoint owner to others", 403);
      }
    }

    if (parsed.data.checkpointId) {
      if (!parsed.data.status) return fail("status is required when updating checkpoint", 400);
      const updated = await updateDealCheckpointStatus({
        supabase: auth.supabase,
        orgId: auth.profile.org_id,
        checkpointId: parsed.data.checkpointId,
        status: parsed.data.status,
        actorUserId: auth.profile.id,
        blockedReason: parsed.data.blockedReason ?? null,
        dueAt: parsed.data.dueAt ?? undefined
      });
      return ok(updated);
    }

    if (!parsed.data.checkpointType || !parsed.data.title) {
      return fail("checkpointType and title are required", 400);
    }

    const checkpoint = await createDealCheckpoint({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      dealRoomId: params.id,
      checkpointType: parsed.data.checkpointType,
      title: parsed.data.title,
      description: parsed.data.description,
      status: parsed.data.status,
      ownerId: parsed.data.ownerId ?? auth.profile.id,
      dueAt: parsed.data.dueAt ?? null,
      evidenceSnapshot: parsed.data.blockedReason
        ? {
            blocked_reason: parsed.data.blockedReason
          }
        : {},
      actorUserId: auth.profile.id
    });
    return ok(checkpoint);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "checkpoint_failed", 500);
  }
}

