import { fail, ok } from "@/lib/api-response";
import { createSupabaseAdminClient, hasSupabaseAdminEnv } from "@/lib/supabase/admin";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { getServerAuthContext } from "@/lib/server-auth";
import { getInboundLeadById } from "@/services/inbound-lead-service";
import { canViewOrgUsage, getCurrentOrgMembership } from "@/services/org-membership-service";
import { refreshTrialConversionTrack } from "@/services/trial-conversion-service";
import { activateTrialRequest, getTrialRequestById } from "@/services/trial-request-service";

interface RouteContext {
  params: {
    id: string;
  };
}

export async function POST(_request: Request, context: RouteContext) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  try {
    const membership = await getCurrentOrgMembership({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });
    if (!membership || membership.seatStatus !== "active") return fail("forbidden", 403);

    const trialRequest = await getTrialRequestById({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      trialRequestId: context.params.id
    });
    if (!trialRequest) return fail("trial_request_not_found", 404);

    const lead = await getInboundLeadById({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      leadId: trialRequest.leadId
    });
    if (!lead) return fail("inbound_lead_not_found", 404);

    const viewAll = canViewOrgUsage(membership.role);
    if (!viewAll && lead.assignedOwnerId !== auth.profile.id) {
      return fail("forbidden", 403);
    }

    const supabaseForWrite = hasSupabaseAdminEnv()
      ? (createSupabaseAdminClient() as unknown as ServerSupabaseClient)
      : auth.supabase;

    const activation = await activateTrialRequest({
      supabase: supabaseForWrite,
      orgId: auth.profile.org_id,
      trialRequestId: trialRequest.id,
      actorUserId: auth.profile.id,
      requestedTemplateId: trialRequest.requestedTemplateId
    });

    let review: {
      usedFallback: boolean;
      fallbackReason: string | null;
    } | null = null;
    try {
      const reviewRes = await refreshTrialConversionTrack({
        supabase: supabaseForWrite,
        orgId: auth.profile.org_id,
        actorUserId: auth.profile.id,
        trackId: activation.conversionTrackId
      });
      review = {
        usedFallback: reviewRes.usedFallback,
        fallbackReason: reviewRes.fallbackReason
      };
    } catch {
      review = null;
    }

    return ok({
      ...activation,
      review
    });
  } catch (error) {
    return fail(error instanceof Error ? error.message : "activate_trial_failed", 500);
  }
}
