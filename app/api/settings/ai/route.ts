import { z } from "zod";
import { NextResponse } from "next/server";

import { fail, ok } from "@/lib/api-response";
import { isOrgConfigWriteRejectedError } from "@/lib/org-config-write-governance";
import { isOrgConfigDriftConflictError } from "@/lib/override-concurrency-guard";
import { getServerAuthContext } from "@/lib/server-auth";
import { getOrgAiControlStatus } from "@/services/org-ai-settings-service";
import {
  governedUpdateOrgAiSettings,
  governedUpdateOrgFeatureFlags
} from "@/services/org-config-governance-service";
import { assertOrgAdminAccess, assertOrgManagerAccess } from "@/services/org-membership-service";
import { getOrgFeatureFlags } from "@/services/org-feature-service";
import type {
  OrgAiSettingsGovernancePatch,
  OrgFeatureFlagsGovernancePatch
} from "@/lib/org-config-write-governance";

const updateSchema = z.object({
  provider: z.enum(["deepseek", "openai", "qwen", "zhipu"]).optional(),
  modelDefault: z.string().min(1).max(120).optional(),
  modelReasoning: z.string().min(1).max(120).optional(),
  fallbackMode: z.enum(["strict_provider_first", "provider_then_rules", "rules_only"]).optional(),
  autoAnalysisEnabled: z.boolean().optional(),
  autoPlanEnabled: z.boolean().optional(),
  autoBriefEnabled: z.boolean().optional(),
  autoTouchpointReviewEnabled: z.boolean().optional(),
  humanReviewRequiredForSensitiveActions: z.boolean().optional(),
  maxDailyAiRuns: z.number().int().min(10).max(200000).nullable().optional(),
  maxMonthlyAiRuns: z.number().int().min(100).max(2000000).nullable().optional(),
  featureFlags: z.record(z.string(), z.boolean()).optional()
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
  expectedVersion: z
    .object({
      orgAiSettings: expectedVersionSchema.optional(),
      orgFeatureFlags: expectedVersionSchema.optional()
    })
    .optional()
});

export async function GET() {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  try {
    const membership = await assertOrgManagerAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    const [aiStatus, featureFlags] = await Promise.all([
      getOrgAiControlStatus({
        supabase: auth.supabase,
        orgId: auth.profile.org_id
      }),
      getOrgFeatureFlags({
        supabase: auth.supabase,
        orgId: auth.profile.org_id
      })
    ]);

    return ok({
      role: membership.role,
      canManage: membership.role === "owner" || membership.role === "admin",
      aiStatus,
      featureFlags
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "get_ai_settings_failed";
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

    const { featureFlags, expectedVersion, ...aiPatch } = parsed.data;
    const hasAiPatch = Object.keys(aiPatch).length > 0;
    const hasFeatureFlagPatch = Boolean(featureFlags) && Object.keys(featureFlags ?? {}).length > 0;
    if (!hasAiPatch && !hasFeatureFlagPatch) {
      return fail("Invalid request payload", 400);
    }

    const aiWriteResult = hasAiPatch
      ? await governedUpdateOrgAiSettings({
          supabase: auth.supabase,
          orgId: auth.profile.org_id,
          actorUserId: auth.profile.id,
          patch: aiPatch as OrgAiSettingsGovernancePatch,
          expectedVersion: expectedVersion?.orgAiSettings ?? null
        })
      : null;
    const featureFlagsWriteResult = hasFeatureFlagPatch
      ? await governedUpdateOrgFeatureFlags({
          supabase: auth.supabase,
          orgId: auth.profile.org_id,
          actorUserId: auth.profile.id,
          patch: (featureFlags ?? {}) as OrgFeatureFlagsGovernancePatch,
          expectedVersion: expectedVersion?.orgFeatureFlags ?? null
        })
      : null;

    const [status, featureFlagRows] = await Promise.all([
      getOrgAiControlStatus({
        supabase: auth.supabase,
        orgId: auth.profile.org_id
      }),
      getOrgFeatureFlags({
        supabase: auth.supabase,
        orgId: auth.profile.org_id
      })
    ]);

    return ok({
      settings: aiWriteResult?.payload.settings ?? status.settings,
      status,
      featureFlags: featureFlagRows,
      governance: {
        orgAiSettings: aiWriteResult
          ? {
              writeDiagnostics: aiWriteResult.writeDiagnostics,
              auditDraft: aiWriteResult.auditDraft,
              persistedAudit: aiWriteResult.persistedAudit,
              concurrency: aiWriteResult.concurrency
            }
          : null,
        orgFeatureFlags: featureFlagsWriteResult
          ? {
              writeDiagnostics: featureFlagsWriteResult.writeDiagnostics,
              auditDraft: featureFlagsWriteResult.auditDraft,
              persistedAudit: featureFlagsWriteResult.persistedAudit,
              concurrency: featureFlagsWriteResult.concurrency
            }
          : null
      }
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
    const message = error instanceof Error ? error.message : "update_ai_settings_failed";
    const status =
      message === "org_admin_access_required"
        ? 403
        : message === "org_config_expected_version_required"
          ? 400
          : 500;
    return fail(message, status);
  }
}
