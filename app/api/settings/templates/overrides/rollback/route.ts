import { z } from "zod";
import { NextResponse } from "next/server";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { getCurrentOrgTemplateContext } from "@/services/industry-template-service";
import {
  executeOrgTemplateOverrideRollback
} from "@/services/org-template-override-rollback-service";
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
    targetAuditId: z.string().uuid().optional(),
    targetVersionLabel: z.string().min(1).optional(),
    targetVersionNumber: z.number().int().positive().optional(),
    expectedVersion: z
      .object({
        compareToken: z.string().min(1).optional(),
        versionLabel: z.string().min(1).optional(),
        versionNumber: z.number().int().positive().optional(),
        overrideUpdatedAt: z.string().min(1).optional(),
        payloadHash: z.string().min(1).optional()
      })
      .refine(
        (value) =>
          Boolean(value.compareToken) ||
          Boolean(value.versionLabel) ||
          typeof value.versionNumber === "number" ||
          Boolean(value.overrideUpdatedAt) ||
          Boolean(value.payloadHash),
        {
          message: "rollback_expected_version_required",
          path: ["compareToken"]
        }
      )
  })
  .refine(
    (value) =>
      Boolean(value.targetAuditId) ||
      Boolean(value.targetVersionLabel) ||
      typeof value.targetVersionNumber === "number",
    {
      message: "rollback_selector_required",
      path: ["targetAuditId"]
    }
  );

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const payload = await request.json().catch(() => ({}));
  const parsed = requestSchema.safeParse(payload);
  if (!parsed.success) return fail("Invalid request payload", 400);

  try {
    const membership = await assertOrgAdminAccess({
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

    const execution = await executeOrgTemplateOverrideRollback({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      templateId,
      overrideType: parsed.data.overrideType,
      selector: {
        targetAuditId: parsed.data.targetAuditId ?? null,
        targetVersionLabel: parsed.data.targetVersionLabel ?? null,
        targetVersionNumber: parsed.data.targetVersionNumber ?? null
      },
      expectedVersion: parsed.data.expectedVersion,
      actorUserId: auth.profile.id
    });

    if (execution.status === "conflict") {
      return NextResponse.json(
        {
          success: false,
          data: {
            role: membership.role,
            execution,
            conflict: execution.conflict?.conflict ?? true,
            conflictReason: execution.conflict?.conflictReason ?? execution.reason,
            currentVersion: execution.conflict?.currentVersion ?? null,
            expectedVersion: execution.conflict?.expectedVersion ?? parsed.data.expectedVersion,
            diagnostics: execution.conflict?.diagnostics ?? execution.diagnostics
          },
          error: "override_drift_conflict"
        },
        { status: 409 }
      );
    }

    return ok({
      role: membership.role,
      execution
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "execute_template_override_rollback_failed";
    const status =
      message === "org_admin_access_required"
        ? 403
        : message.startsWith("template_override_payload_invalid")
          ? 400
          : 500;
    return fail(message, status);
  }
}
