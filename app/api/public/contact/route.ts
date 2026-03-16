import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { createSupabaseAdminClient, hasSupabaseAdminEnv } from "@/lib/supabase/admin";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { buildPublicSubmissionFingerprint, createInboundLead, ensurePublicFormAllowed, getSelfSalesOrgId } from "@/services/inbound-lead-service";

const requestSchema = z.object({
  contactName: z.string().min(1).max(80),
  companyName: z.string().min(1).max(120),
  email: z.string().email(),
  phone: z.string().max(40).optional().nullable(),
  industryHint: z.string().max(80).optional().nullable(),
  teamSizeHint: z.string().max(40).optional().nullable(),
  message: z.string().max(1200).optional().nullable(),
  sourceCampaign: z.string().max(120).optional().nullable(),
  landingPage: z.string().max(220).optional().nullable(),
  website: z.string().optional()
});

export async function POST(request: Request) {
  const payload = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(payload ?? {});
  if (!parsed.success) return fail("Invalid request payload", 400);
  if (parsed.data.website && parsed.data.website.trim().length > 0) {
    return ok({ accepted: true, ignored: true });
  }

  if (!hasSupabaseAdminEnv()) return fail("public_form_service_unavailable", 503);

  try {
    const supabase = createSupabaseAdminClient() as unknown as ServerSupabaseClient;
    const orgId = await getSelfSalesOrgId();
    const ip = request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ?? request.headers.get("x-real-ip") ?? null;
    const userAgent = request.headers.get("user-agent");
    const requestFingerprint = buildPublicSubmissionFingerprint({
      email: parsed.data.email,
      source: "website_contact",
      ip,
      userAgent
    });

    await ensurePublicFormAllowed({
      supabase,
      orgId,
      email: parsed.data.email,
      leadSource: "website_contact",
      fingerprint: requestFingerprint
    });

    const leadCreated = await createInboundLead({
      supabase,
      orgId,
      actorUserId: null,
      leadSource: "website_contact",
      companyName: parsed.data.companyName,
      contactName: parsed.data.contactName,
      email: parsed.data.email,
      phone: parsed.data.phone ?? null,
      industryHint: parsed.data.industryHint ?? null,
      teamSizeHint: parsed.data.teamSizeHint ?? null,
      useCaseHint: parsed.data.message ?? null,
      sourceCampaign: parsed.data.sourceCampaign ?? null,
      landingPage: parsed.data.landingPage ?? "/contact",
      notes: parsed.data.message ?? null,
      payloadSnapshot: {
        request_fingerprint: requestFingerprint
      },
      createPipelineDraft: false
    });

    return ok({
      accepted: true,
      leadId: leadCreated.lead.id,
      assignedOwnerId: leadCreated.assignment.ownerId
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "contact_form_failed";
    const status =
      message === "lead_submission_too_frequent" ? 429 : message === "self_sales_org_env_missing" || message === "self_sales_org_not_found" ? 503 : 500;
    return fail(message, status);
  }
}
