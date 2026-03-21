import { z } from "zod";
import { NextResponse } from "next/server";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { executeOrgConfigRollback } from "@/services/org-config-rollback-service";
import { assertOrgAdminAccess } from "@/services/org-membership-service";

const expectedVersionSchema = z
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
  );

const requestSchema = z
  .object({
    targetType: z.enum(["org_settings", "org_ai_settings", "org_feature_flags"]),
    targetAuditId: z.string().uuid().optional(),
    targetVersionLabel: z.string().min(1).optional(),
    targetVersionNumber: z.number().int().positive().optional(),
    expectedVersion: expectedVersionSchema
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

    const execution = await executeOrgConfigRollback({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      targetType: parsed.data.targetType,
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
          error: "org_config_drift_conflict"
        },
        { status: 409 }
      );
    }

    return ok({
      role: membership.role,
      execution
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "execute_org_config_rollback_failed";
    const status =
      message === "org_admin_access_required"
        ? 403
        : message === "rollback_expected_version_required"
          ? 400
          : 500;
    return fail(message, status);
  }
}

