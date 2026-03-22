import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { buildPublicCommercialEntryContext, toPublicCommercialEntryTracePayload } from "@/lib/commercial-entry";
import { createSupabaseAdminClient, hasSupabaseAdminEnv } from "@/lib/supabase/admin";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { createDemoRequest } from "@/services/demo-request-service";
import { buildPublicSubmissionFingerprint, createInboundLead, ensurePublicFormAllowed, getSelfSalesOrgId } from "@/services/inbound-lead-service";

const requestSchema = z.object({
  contactName: z.string().min(1).max(80),
  companyName: z.string().min(1).max(120),
  email: z.string().email(),
  phone: z.string().max(40).optional().nullable(),
  industryHint: z.string().max(80).optional().nullable(),
  teamSizeHint: z.string().max(40).optional().nullable(),
  useCaseHint: z.string().max(1000).optional().nullable(),
  sourceCampaign: z.string().max(120).optional().nullable(),
  landingPage: z.string().max(220).optional().nullable(),
  referrer: z.string().max(400).optional().nullable(),
  utmSource: z.string().max(120).optional().nullable(),
  utmMedium: z.string().max(120).optional().nullable(),
  utmCampaign: z.string().max(120).optional().nullable(),
  utmTerm: z.string().max(120).optional().nullable(),
  utmContent: z.string().max(120).optional().nullable(),
  locale: z.string().max(32).optional().nullable(),
  timezone: z.string().max(80).optional().nullable(),
  submittedAt: z.string().max(64).optional().nullable(),
  entryTraceId: z.string().max(120).optional().nullable(),
  preferredTimeText: z.string().max(180).optional().nullable(),
  scenarioFocus: z.string().max(200).optional().nullable(),
  website: z.string().optional() // honeypot
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
      source: "website_demo",
      ip,
      userAgent
    });
    const entryContext = buildPublicCommercialEntryContext({
      source: "website_demo",
      fallbackLandingPage: "/request-demo",
      input: {
        sourceCampaign: parsed.data.sourceCampaign ?? null,
        landingPage: parsed.data.landingPage ?? null,
        referrer: parsed.data.referrer ?? null,
        utmSource: parsed.data.utmSource ?? null,
        utmMedium: parsed.data.utmMedium ?? null,
        utmCampaign: parsed.data.utmCampaign ?? null,
        utmTerm: parsed.data.utmTerm ?? null,
        utmContent: parsed.data.utmContent ?? null,
        locale: parsed.data.locale ?? null,
        timezone: parsed.data.timezone ?? null,
        submittedAt: parsed.data.submittedAt ?? null,
        entryTraceId: parsed.data.entryTraceId ?? null
      }
    });
    const entryTracePayload = toPublicCommercialEntryTracePayload(entryContext);

    await ensurePublicFormAllowed({
      supabase,
      orgId,
      email: parsed.data.email,
      leadSource: "website_demo",
      fingerprint: requestFingerprint
    });

    const leadCreated = await createInboundLead({
      supabase,
      orgId,
      actorUserId: null,
      leadSource: "website_demo",
      companyName: parsed.data.companyName,
      contactName: parsed.data.contactName,
      email: parsed.data.email,
      phone: parsed.data.phone ?? null,
      industryHint: parsed.data.industryHint ?? null,
      teamSizeHint: parsed.data.teamSizeHint ?? null,
      useCaseHint: parsed.data.useCaseHint ?? null,
      sourceCampaign: entryContext.sourceCampaign,
      landingPage: entryContext.landingPage,
      payloadSnapshot: {
        scenario_focus: parsed.data.scenarioFocus ?? null,
        preferred_time_text: parsed.data.preferredTimeText ?? null,
        commercialization_intent: {
          intent_type: "guided_demo",
          scenario_focus: parsed.data.scenarioFocus ?? null,
          preferred_time_text: parsed.data.preferredTimeText ?? null
        },
        entry_trace: entryTracePayload,
        request_fingerprint: requestFingerprint
      },
      createPipelineDraft: true
    });

    let demoRequestId: string | null = null;
    let warning: string | null = null;
    try {
      const demo = await createDemoRequest({
        supabase,
        orgId,
        leadId: leadCreated.lead.id,
        requestedByEmail: parsed.data.email,
        preferredTimeText: parsed.data.preferredTimeText ?? null,
        actorUserId: leadCreated.assignment.ownerId,
        createWorkItem: true
      });
      demoRequestId = demo.demoRequest.id;
    } catch (error) {
      warning = error instanceof Error ? error.message : "demo_request_create_failed";
    }

    return ok({
      accepted: true,
      leadId: leadCreated.lead.id,
      demoRequestId,
      assignedOwnerId: leadCreated.assignment.ownerId,
      qualificationFitScore: leadCreated.qualification.result.fitScore,
      pipelineCreated: leadCreated.pipelineCreated,
      entryTraceId: entryContext.entryTraceId,
      handoff: {
        leadStatus: leadCreated.lead.status,
        pipelineStatus: leadCreated.pipelineCreated ? "pipeline_ready" : "pipeline_not_created",
        downstreamStatus: demoRequestId ? "demo_request_created" : "demo_request_pending_manual_retry"
      },
      warning
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "request_demo_failed";
    const selfSalesEnvMissing = message.includes("SELF_SALES_ORG_ID") || message.includes("self_sales_org_env_missing");
    const status =
      message === "lead_submission_too_frequent" ? 429 : selfSalesEnvMissing || message === "self_sales_org_not_found" ? 503 : 500;
    return fail(message, status);
  }
}
