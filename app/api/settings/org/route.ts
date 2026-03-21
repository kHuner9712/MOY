import { z } from "zod";
import { NextResponse } from "next/server";

import { fail, ok } from "@/lib/api-response";
import { isOrgConfigWriteRejectedError } from "@/lib/org-config-write-governance";
import { isOrgConfigDriftConflictError } from "@/lib/override-concurrency-guard";
import { getServerAuthContext } from "@/lib/server-auth";
import { assertOrgAdminAccess, assertOrgManagerAccess } from "@/services/org-membership-service";
import { governedUpdateOrgSettings } from "@/services/org-config-governance-service";
import { getOrgSettings } from "@/services/org-settings-service";
import type { OrgSettingsGovernancePatch } from "@/lib/org-config-write-governance";

const updateSchema = z.object({
  orgDisplayName: z.string().min(1).max(120).optional(),
  brandName: z.string().min(1).max(80).optional(),
  industryHint: z.string().max(120).nullable().optional(),
  timezone: z.string().min(2).max(64).optional(),
  locale: z.string().min(2).max(24).optional(),
  defaultCustomerStages: z.array(z.string().min(1)).min(3).optional(),
  defaultOpportunityStages: z.array(z.string().min(1)).min(3).optional(),
  defaultAlertRules: z.record(z.string(), z.number().int().min(1).max(90)).optional(),
  defaultFollowupSlaDays: z.number().int().min(1).max(30).optional(),
  onboardingCompleted: z.boolean().optional(),
  onboardingStepState: z.record(z.string(), z.boolean()).optional()
}).passthrough();

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
      message: "org_config_expected_version_required"
    }
  );

const requestSchema = updateSchema.extend({
  expectedVersion: expectedVersionSchema.optional()
});

export async function GET() {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  try {
    await assertOrgManagerAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    const settings = await getOrgSettings({
      supabase: auth.supabase,
      orgId: auth.profile.org_id
    });

    return ok({ settings });
  } catch (error) {
    const message = error instanceof Error ? error.message : "get_org_settings_failed";
    const status = message === "org_manager_access_required" ? 403 : 500;
    return fail(message, status);
  }
}

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const payload = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(payload ?? {});
  if (!parsed.success) return fail("Invalid request payload", 400);

  try {
    await assertOrgAdminAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    const { expectedVersion, ...patch } = parsed.data;
    const writeResult = await governedUpdateOrgSettings({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      actorUserId: auth.profile.id,
      patch: patch as OrgSettingsGovernancePatch,
      expectedVersion: expectedVersion ?? null
    });

    return ok({
      settings: writeResult.payload.settings,
      writeDiagnostics: writeResult.writeDiagnostics,
      auditDraft: writeResult.auditDraft,
      persistedAudit: writeResult.persistedAudit,
      concurrency: writeResult.concurrency
    });
  } catch (error) {
    if (isOrgConfigDriftConflictError(error)) {
      return NextResponse.json(
        {
          success: false,
          data: {
            conflict: true,
            conflictReason: error.conflict.conflictReason,
            currentVersion: error.conflict.currentVersion,
            expectedVersion: error.conflict.expectedVersion,
            diagnostics: error.conflict.diagnostics
          },
          error: "org_config_drift_conflict"
        },
        { status: 409 }
      );
    }
    if (isOrgConfigWriteRejectedError(error)) {
      return NextResponse.json(
        {
          success: false,
          data: {
            rejected: true,
            targetType: error.rejection.targetType,
            reason: error.rejection.reason,
            diagnostics: error.rejection.diagnostics,
            acceptedFields: error.rejection.acceptedFields,
            ignoredFields: error.rejection.ignoredFields,
            forbiddenFields: error.rejection.forbiddenFields,
            runtimeImpactSummary: error.rejection.runtimeImpactSummary
          },
          error: "org_config_write_rejected"
        },
        { status: 400 }
      );
    }
    const message = error instanceof Error ? error.message : "update_org_settings_failed";
    const status =
      message === "org_admin_access_required"
        ? 403
        : message === "org_config_expected_version_required"
          ? 400
          : 500;
    return fail(message, status);
  }
}
