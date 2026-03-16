import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { getOrgAiControlStatus, updateOrgAiSettings } from "@/services/org-ai-settings-service";
import { assertOrgAdminAccess, assertOrgManagerAccess } from "@/services/org-membership-service";
import { getOrgFeatureFlags, updateOrgFeatureFlag } from "@/services/org-feature-service";
import type { OrgFeatureKey } from "@/types/productization";

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
  featureFlags: z
    .record(
      z.enum([
        "ai_auto_analysis",
        "ai_auto_planning",
        "ai_morning_brief",
        "ai_deal_command",
        "external_touchpoints",
        "prep_cards",
        "playbooks",
        "manager_quality_view",
        "outcome_learning",
        "demo_seed_tools"
      ]),
      z.boolean()
    )
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
  const parsed = updateSchema.safeParse(payload ?? {});
  if (!parsed.success) return fail("Invalid request payload", 400);

  try {
    await assertOrgAdminAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    const settings = await updateOrgAiSettings({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      patch: parsed.data
    });

    if (parsed.data.featureFlags) {
      const updates = Object.entries(parsed.data.featureFlags).map(([featureKey, isEnabled]) =>
        updateOrgFeatureFlag({
          supabase: auth.supabase,
          orgId: auth.profile.org_id,
          featureKey: featureKey as OrgFeatureKey,
          isEnabled
        })
      );
      await Promise.all(updates);
    }

    const [status, featureFlags] = await Promise.all([
      getOrgAiControlStatus({
        supabase: auth.supabase,
        orgId: auth.profile.org_id
      }),
      getOrgFeatureFlags({
        supabase: auth.supabase,
        orgId: auth.profile.org_id
      })
    ]);

    return ok({ settings, status, featureFlags });
  } catch (error) {
    const message = error instanceof Error ? error.message : "update_ai_settings_failed";
    const status = message === "org_admin_access_required" ? 403 : 500;
    return fail(message, status);
  }
}
