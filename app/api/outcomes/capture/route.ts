import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { captureActionOutcome } from "@/services/action-outcome-service";

const requestSchema = z.object({
  ownerId: z.string().uuid().optional(),
  customerId: z.string().uuid().optional(),
  opportunityId: z.string().uuid().optional(),
  workItemId: z.string().uuid().optional(),
  followupId: z.string().uuid().optional(),
  communicationInputId: z.string().uuid().optional(),
  prepCardId: z.string().uuid().optional(),
  contentDraftId: z.string().uuid().optional(),
  outcomeType: z.enum(["followup_result", "quote_result", "meeting_result", "task_result", "manager_intervention_result"]).optional(),
  resultStatus: z.enum(["positive_progress", "neutral", "stalled", "risk_increased", "closed_won", "closed_lost"]).optional(),
  stageChanged: z.boolean().optional(),
  oldStage: z.string().optional(),
  newStage: z.string().optional(),
  customerSentimentShift: z.enum(["improved", "unchanged", "worsened", "unknown"]).optional(),
  keyOutcomeSummary: z.string().max(2000).optional(),
  newObjections: z.array(z.string().max(200)).optional(),
  newRisks: z.array(z.string().max(200)).optional(),
  nextStepDefined: z.boolean().optional(),
  nextStepText: z.string().max(500).optional(),
  followupDueAt: z.string().optional(),
  usedPrepCard: z.boolean().optional(),
  usedDraft: z.boolean().optional(),
  usefulnessRating: z.enum(["helpful", "somewhat_helpful", "not_helpful", "unknown"]).optional(),
  notes: z.string().max(2000).optional(),
  autoInfer: z.boolean().optional(),
  summaryHint: z.string().max(1000).optional(),
  linkAdoptionIds: z.array(z.string().uuid()).optional()
});

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const body = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(body ?? {});
  if (!parsed.success) return fail("Invalid request payload", 400);

  const payload = parsed.data;

  try {
    if (!isManager(auth.profile) && payload.ownerId && payload.ownerId !== auth.profile.id) {
      return fail("Sales can only capture own outcomes", 403);
    }

    if (!isManager(auth.profile) && payload.customerId) {
      const ownerRes = await auth.supabase
        .from("customers")
        .select("owner_id")
        .eq("org_id", auth.profile.org_id)
        .eq("id", payload.customerId)
        .maybeSingle();
      if (ownerRes.error) throw new Error(ownerRes.error.message);
      if ((ownerRes.data as { owner_id: string } | null)?.owner_id !== auth.profile.id) {
        return fail("Sales can only operate own customers", 403);
      }
    }

    if (!isManager(auth.profile) && payload.workItemId) {
      const ownerRes = await auth.supabase
        .from("work_items")
        .select("owner_id")
        .eq("org_id", auth.profile.org_id)
        .eq("id", payload.workItemId)
        .maybeSingle();
      if (ownerRes.error) throw new Error(ownerRes.error.message);
      if ((ownerRes.data as { owner_id: string } | null)?.owner_id !== auth.profile.id) {
        return fail("Sales can only operate own tasks", 403);
      }
    }

    const result = await captureActionOutcome({
      supabase: auth.supabase,
      profile: auth.profile,
      ownerId: payload.ownerId,
      customerId: payload.customerId,
      opportunityId: payload.opportunityId,
      workItemId: payload.workItemId,
      followupId: payload.followupId,
      communicationInputId: payload.communicationInputId,
      prepCardId: payload.prepCardId,
      contentDraftId: payload.contentDraftId,
      outcomeType: payload.outcomeType,
      resultStatus: payload.resultStatus,
      stageChanged: payload.stageChanged,
      oldStage: payload.oldStage,
      newStage: payload.newStage,
      customerSentimentShift: payload.customerSentimentShift,
      keyOutcomeSummary: payload.keyOutcomeSummary,
      newObjections: payload.newObjections,
      newRisks: payload.newRisks,
      nextStepDefined: payload.nextStepDefined,
      nextStepText: payload.nextStepText,
      followupDueAt: payload.followupDueAt,
      usedPrepCard: payload.usedPrepCard,
      usedDraft: payload.usedDraft,
      usefulnessRating: payload.usefulnessRating,
      notes: payload.notes,
      autoInfer: payload.autoInfer,
      summaryHint: payload.summaryHint,
      linkAdoptionIds: payload.linkAdoptionIds
    });

    return ok(result);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "capture_outcome_failed", 500);
  }
}
