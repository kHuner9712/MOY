import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import {
  buildExpectedVersionFromConcurrencyBaseline,
  buildOverrideConcurrencyBaseline
} from "@/lib/override-concurrency-guard";
import { prepareOrgTemplateOverrideWrite } from "@/lib/org-override-write-governance";
import { getServerAuthContext } from "@/lib/server-auth";
import {
  getCurrentOrgTemplateContext,
  getOrgTemplateOverrideByType
} from "@/services/industry-template-service";
import {
  getLatestOrgTemplateOverrideAuditVersion,
  listRecentOrgConfigAuditLogs
} from "@/services/org-config-audit-service";
import { assertOrgManagerAccess } from "@/services/org-membership-service";

const requestSchema = z.object({
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
  ])
});

function asObject(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

function toNullableString(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const payload = await request.json().catch(() => ({}));
  const parsed = requestSchema.safeParse(payload);
  if (!parsed.success) return fail("Invalid request payload", 400);

  try {
    const membership = await assertOrgManagerAccess({
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
      if (!current.assignment?.templateId) return fail("No active template for this org", 400);
      templateId = current.assignment.templateId;
    }

    const [currentOverride, latestVersion, recentAudits] = await Promise.all([
      getOrgTemplateOverrideByType({
        supabase: auth.supabase,
        orgId: auth.profile.org_id,
        templateId,
        overrideType: parsed.data.overrideType
      }),
      getLatestOrgTemplateOverrideAuditVersion({
        supabase: auth.supabase,
        orgId: auth.profile.org_id,
        templateId,
        overrideType: parsed.data.overrideType
      }),
      listRecentOrgConfigAuditLogs({
        supabase: auth.supabase,
        orgId: auth.profile.org_id,
        targetType: "org_template_override",
        targetKey: `${templateId}:${parsed.data.overrideType}`,
        limit: 5
      })
    ]);

    const currentDiagnostics = currentOverride
      ? prepareOrgTemplateOverrideWrite({
          overrideType: parsed.data.overrideType,
          overridePayload: currentOverride.overridePayload
        }).writeDiagnostics
      : null;
    const baseline = buildOverrideConcurrencyBaseline({
      templateId,
      overrideType: parsed.data.overrideType,
      auditAvailability: latestVersion.availability,
      currentVersionLabel: latestVersion.item?.versionLabel ?? null,
      currentVersionNumber: latestVersion.item?.versionNumber ?? null,
      currentOverrideUpdatedAt: currentOverride?.updatedAt ?? null,
      currentPayload: currentOverride?.overridePayload ?? null
    });

    return ok({
      role: membership.role,
      state: {
        templateId,
        overrideType: parsed.data.overrideType,
        currentOverride: {
          exists: Boolean(currentOverride),
          id: currentOverride?.id ?? null,
          createdBy: currentOverride?.createdBy ?? null,
          createdAt: currentOverride?.createdAt ?? null,
          updatedAt: currentOverride?.updatedAt ?? null,
          payload: currentOverride?.overridePayload ?? {}
        },
        currentDiagnostics,
        concurrencyBaseline: baseline,
        expectedVersion: buildExpectedVersionFromConcurrencyBaseline(baseline),
        latestPersistedVersion: {
          availability: latestVersion.availability,
          versionLabel: latestVersion.item?.versionLabel ?? null,
          versionNumber: latestVersion.item?.versionNumber ?? null,
          note: latestVersion.note
        },
        recentAudits: {
          availability: recentAudits.availability,
          note: recentAudits.note,
          items: recentAudits.items.map((item) => {
            const diagnosticsSummary = asObject(item.diagnosticsSummary);
            return {
              id: item.id,
              createdAt: item.createdAt,
              actorUserId: item.actorUserId,
              actionType: item.actionType,
              versionLabel: item.versionLabel,
              versionNumber: item.versionNumber,
              runtimeImpactSummary: toNullableString(diagnosticsSummary.runtimeImpactSummary),
              diagnosticsPreview: asStringArray(diagnosticsSummary.diagnostics).slice(0, 8)
            };
          })
        }
      }
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "get_template_override_state_failed";
    const status = message === "org_manager_access_required" ? 403 : 500;
    return fail(message, status);
  }
}
