import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { getDecisionSupport, getDealRoomDetail } from "@/services/deal-command-service";
import { createDecisionRecord, updateDecisionStatus } from "@/services/decision-record-service";

interface RouteParams {
  params: { id: string };
}

const requestSchema = z.object({
  decisionId: z.string().uuid().optional(),
  decisionType: z.enum(["quote_strategy", "discount_exception", "trial_offer", "manager_intervention", "resource_support", "contract_risk", "stage_commitment"]).optional(),
  title: z.string().min(1).max(160).optional(),
  contextSummary: z.string().max(1000).optional(),
  optionsConsidered: z.array(z.string()).optional(),
  recommendedOption: z.string().max(400).optional(),
  decisionReason: z.string().max(1000).optional(),
  status: z.enum(["proposed", "approved", "rejected", "superseded", "completed"]).optional(),
  decidedBy: z.string().uuid().optional(),
  dueAt: z.string().datetime().optional(),
  ownerIdForLinkedTask: z.string().uuid().optional(),
  includeDecisionSupport: z.boolean().optional()
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

    if (parsed.data.decisionId) {
      if (!parsed.data.status) return fail("status is required when updating decision", 400);
      if (!isManager(auth.profile)) return fail("Only manager can change decision status", 403);

      const updated = await updateDecisionStatus({
        supabase: auth.supabase,
        orgId: auth.profile.org_id,
        decisionId: parsed.data.decisionId,
        status: parsed.data.status,
        actorUserId: auth.profile.id,
        ownerIdForLinkedTask: parsed.data.ownerIdForLinkedTask ?? detail.room.ownerId,
        decisionReason: parsed.data.decisionReason ?? null
      });
      return ok({
        decision: updated
      });
    }

    if (!parsed.data.decisionType || !parsed.data.title) {
      return fail("decisionType and title are required", 400);
    }

    if (!isManager(auth.profile) && parsed.data.status && parsed.data.status !== "proposed") {
      return fail("Sales can only create proposed decisions", 403);
    }

    const decision = await createDecisionRecord({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      dealRoomId: params.id,
      customerId: detail.room.customerId,
      opportunityId: detail.room.opportunityId,
      decisionType: parsed.data.decisionType,
      title: parsed.data.title,
      contextSummary: parsed.data.contextSummary,
      optionsConsidered: parsed.data.optionsConsidered,
      recommendedOption: parsed.data.recommendedOption ?? null,
      decisionReason: parsed.data.decisionReason ?? null,
      status: parsed.data.status ?? "proposed",
      decidedBy: parsed.data.decidedBy ?? null,
      requestedBy: auth.profile.id,
      ownerIdForLinkedTask: parsed.data.ownerIdForLinkedTask ?? detail.room.ownerId,
      dueAt: parsed.data.dueAt ?? null
    });

    let support: Awaited<ReturnType<typeof getDecisionSupport>> | null = null;
    if (parsed.data.includeDecisionSupport) {
      support = await getDecisionSupport({
        supabase: auth.supabase,
        orgId: auth.profile.org_id,
        dealRoomId: params.id,
        actorUserId: auth.profile.id,
        decisionType: parsed.data.decisionType,
        options: parsed.data.optionsConsidered ?? [],
        knownRisks: detail.room.currentBlockers,
        contextSummary: parsed.data.contextSummary
      });
    }

    return ok({
      decision,
      decisionSupport: support
    });
  } catch (error) {
    return fail(error instanceof Error ? error.message : "decision_record_failed", 500);
  }
}

