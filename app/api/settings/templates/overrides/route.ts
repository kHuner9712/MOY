import { z } from "zod";
import { NextResponse } from "next/server";

import { fail, ok } from "@/lib/api-response";
import { isOverrideDriftConflictError } from "@/lib/override-concurrency-guard";
import { buildOrgOverrideWriteDiagnosticsSummary } from "@/lib/org-override-write-governance";
import { getServerAuthContext } from "@/lib/server-auth";
import { getCurrentOrgTemplateContext, upsertOrgTemplateOverride } from "@/services/industry-template-service";
import { assertOrgAdminAccess } from "@/services/org-membership-service";

const requestSchema = z
  .object({
    templateId: z.string().uuid().optional(),
    overrideType: z.enum([
      "customer_stages",
      "opportunity_stages",
      "alert_rules",
      "checkpoints",
      "playbook_seed",
      "prep_preferences",
      "brief_preferences",
      "demo_seed_profile"
    ]),
    overridePayload: z.record(z.unknown()),
    expectedVersion: z
      .object({
        compareToken: z.string().min(1).optional(),
        versionLabel: z.string().min(1).optional(),
        versionNumber: z.number().int().positive().optional(),
        overrideUpdatedAt: z.string().min(1).optional(),
        payloadHash: z.string().min(1).optional()
      })
      .optional()
  })
  .refine(
    (value) =>
      !value.expectedVersion ||
      Boolean(value.expectedVersion.compareToken) ||
      Boolean(value.expectedVersion.versionLabel) ||
      typeof value.expectedVersion.versionNumber === "number" ||
      Boolean(value.expectedVersion.overrideUpdatedAt) ||
      Boolean(value.expectedVersion.payloadHash),
    {
      message: "override_expected_version_required",
      path: ["expectedVersion", "compareToken"]
    }
  );

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const payload = await request.json().catch(() => ({}));
  const parsed = requestSchema.safeParse(payload);
  if (!parsed.success) return fail("Invalid request payload", 400);

  try {
    await assertOrgAdminAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    let templateId = parsed.data.templateId ?? "";
    if (!templateId) {
      const current = await getCurrentOrgTemplateContext({
        supabase: auth.supabase,
        orgId: auth.profile.org_id
      });
      if (!current.template) return fail("No active template for this org", 400);
      templateId = current.template.id;
    }

    const writeResult = await upsertOrgTemplateOverride({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      templateId,
      overrideType: parsed.data.overrideType,
      overridePayload: parsed.data.overridePayload,
      actorUserId: auth.profile.id,
      expectedVersion: parsed.data.expectedVersion ?? null
    });

    return ok({
      override: writeResult.override,
      writeDiagnostics: writeResult.writeDiagnostics,
      diagnosticsSummary: buildOrgOverrideWriteDiagnosticsSummary([writeResult.writeDiagnostics]),
      auditDraft: writeResult.auditDraft,
      persistedAudit: writeResult.persistedAudit,
      concurrency: writeResult.concurrency
    });
  } catch (error) {
    if (isOverrideDriftConflictError(error)) {
      return NextResponse.json(
        {
          success: false,
          data: {
            conflict: error.conflict.conflict,
            conflictReason: error.conflict.conflictReason,
            currentVersion: error.conflict.currentVersion,
            expectedVersion: error.conflict.expectedVersion,
            diagnostics: error.conflict.diagnostics
          },
          error: "override_drift_conflict"
        },
        { status: 409 }
      );
    }
    const message = error instanceof Error ? error.message : "template_override_failed";
    const status =
      message === "org_admin_access_required"
        ? 403
        : message.startsWith("template_override_payload_invalid")
          ? 400
          : message === "override_expected_version_required"
          ? 400
          : 500;
    return fail(message, status);
  }
}
