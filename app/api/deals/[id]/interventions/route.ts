import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { getInterventionRecommendation, getDealRoomDetail } from "@/services/deal-command-service";
import { createInterventionRequest, updateInterventionRequestStatus } from "@/services/intervention-request-service";

interface RouteParams {
  params: { id: string };
}

const requestSchema = z.object({
  interventionRequestId: z.string().uuid().optional(),
  requestType: z.enum(["manager_join_call", "pricing_support", "proposal_review", "objection_help", "contract_support", "executive_escalation"]).optional(),
  targetUserId: z.string().uuid().optional(),
  priorityBand: z.enum(["low", "medium", "high", "critical"]).optional(),
  status: z.enum(["open", "accepted", "declined", "completed", "expired"]).optional(),
  requestSummary: z.string().min(1).max(1000).optional(),
  contextSnapshot: z.record(z.unknown()).optional(),
  dueAt: z.string().datetime().optional(),
  note: z.string().max(500).optional(),
  includeRecommendation: z.boolean().optional()
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
    }

    if (parsed.data.interventionRequestId) {
      if (!parsed.data.status) return fail("status is required when updating intervention", 400);
      const updated = await updateInterventionRequestStatus({
        supabase: auth.supabase,
        orgId: auth.profile.org_id,
        interventionRequestId: parsed.data.interventionRequestId,
        status: parsed.data.status,
        actorUserId: auth.profile.id,
        note: parsed.data.note ?? null
      });
      return ok({
        intervention: updated
      });
    }

    if (!parsed.data.requestType || !parsed.data.requestSummary) {
      return fail("requestType and requestSummary are required", 400);
    }

    const intervention = await createInterventionRequest({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      dealRoomId: params.id,
      requestedBy: auth.profile.id,
      targetUserId: parsed.data.targetUserId ?? null,
      requestType: parsed.data.requestType,
      priorityBand: parsed.data.priorityBand,
      requestSummary: parsed.data.requestSummary,
      contextSnapshot: parsed.data.contextSnapshot ?? {},
      dueAt: parsed.data.dueAt ?? null
    });

    let recommendation: Awaited<ReturnType<typeof getInterventionRecommendation>> | null = null;
    if (parsed.data.includeRecommendation) {
      recommendation = await getInterventionRecommendation({
        supabase: auth.supabase,
        orgId: auth.profile.org_id,
        dealRoomId: params.id,
        actorUserId: auth.profile.id
      });
    }

    return ok({
      intervention,
      recommendation
    });
  } catch (error) {
    return fail(error instanceof Error ? error.message : "intervention_request_failed", 500);
  }
}

