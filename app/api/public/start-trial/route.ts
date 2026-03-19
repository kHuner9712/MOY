import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { createSupabaseAdminClient, hasSupabaseAdminEnv } from "@/lib/supabase/admin";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { buildPublicSubmissionFingerprint, createInboundLead, ensurePublicFormAllowed, getSelfSalesOrgId } from "@/services/inbound-lead-service";
import { createTrialRequest } from "@/services/trial-request-service";

const requestSchema = z.object({
  contactName: z.string().min(1).max(80),
  companyName: z.string().min(1).max(120),
  email: z.string().email(),
  phone: z.string().max(40).optional().nullable(),
  industryHint: z.string().max(80).optional().nullable(),
  teamSizeHint: z.string().max(40).optional().nullable(),
  preferredTemplateKey: z.string().max(80).optional().nullable(),
  needImportData: z.boolean().optional().default(false),
  useCaseHint: z.string().max(1000).optional().nullable(),
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
      source: "website_trial",
      ip,
      userAgent
    });

    await ensurePublicFormAllowed({
      supabase,
      orgId,
      email: parsed.data.email,
      leadSource: "website_trial",
      fingerprint: requestFingerprint
    });

    let templateId: string | null = null;
    if (parsed.data.preferredTemplateKey) {
      const templateRes = await (supabase as any)
        .from("industry_templates")
        .select("id")
        .eq("template_key", parsed.data.preferredTemplateKey)
        .neq("status", "archived")
        .limit(1)
        .maybeSingle();
      if (!templateRes.error && templateRes.data) {
        templateId = String(templateRes.data.id);
      }
    }

    const leadCreated = await createInboundLead({
      supabase,
      orgId,
      actorUserId: null,
      leadSource: "website_trial",
      companyName: parsed.data.companyName,
      contactName: parsed.data.contactName,
      email: parsed.data.email,
      phone: parsed.data.phone ?? null,
      industryHint: parsed.data.industryHint ?? null,
      teamSizeHint: parsed.data.teamSizeHint ?? null,
      useCaseHint: parsed.data.useCaseHint ?? null,
      sourceCampaign: parsed.data.sourceCampaign ?? null,
      landingPage: parsed.data.landingPage ?? "/start-trial",
      payloadSnapshot: {
        preferred_template_key: parsed.data.preferredTemplateKey ?? null,
        need_import_data: parsed.data.needImportData,
        request_fingerprint: requestFingerprint
      },
      createPipelineDraft: true
    });

    let trialRequestId: string | null = null;
    let warning: string | null = null;
    try {
      const trial = await createTrialRequest({
        supabase,
        orgId,
        leadId: leadCreated.lead.id,
        requestedByEmail: parsed.data.email,
        requestedTemplateId: templateId,
        actorUserId: leadCreated.assignment.ownerId,
        createWorkItem: true
      });
      trialRequestId = trial.trialRequest.id;
    } catch (error) {
      warning = error instanceof Error ? error.message : "trial_request_create_failed";
    }

    return ok({
      accepted: true,
      leadId: leadCreated.lead.id,
      trialRequestId,
      assignedOwnerId: leadCreated.assignment.ownerId,
      qualificationFitScore: leadCreated.qualification.result.fitScore,
      pipelineCreated: leadCreated.pipelineCreated,
      warning
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "start_trial_failed";
    const selfSalesEnvMissing = message.includes("SELF_SALES_ORG_ID") || message.includes("self_sales_org_env_missing");
    const status =
      message === "lead_submission_too_frequent" ? 429 : selfSalesEnvMissing || message === "self_sales_org_not_found" ? 503 : 500;
    return fail(message, status);
  }
}
