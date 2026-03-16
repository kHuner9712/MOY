import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { checkOrgFeatureAccess } from "@/services/feature-access-service";
import { getOrgAiSettings } from "@/services/org-ai-settings-service";
import { canRunAiByEntitlement, getEntitlementStatus } from "@/services/plan-entitlement-service";
import { generateTodayPlan } from "@/services/work-agent-service";

const requestSchema = z.object({
  force: z.boolean().optional()
});

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const payload = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(payload ?? {});
  if (!parsed.success) return fail("Invalid request payload", 400);

  try {
    const featureAccess = await checkOrgFeatureAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      featureKey: "ai_auto_planning"
    });
    if (!featureAccess.allowed) {
      return fail(featureAccess.reason ?? "Auto planning feature is disabled", 403);
    }

    const [aiSettings, entitlement] = await Promise.all([
      getOrgAiSettings({ supabase: auth.supabase, orgId: auth.profile.org_id }),
      getEntitlementStatus({ supabase: auth.supabase, orgId: auth.profile.org_id, refreshUsage: true })
    ]);

    if (!aiSettings.autoPlanEnabled) {
      return fail("Organization disabled auto planning in AI settings", 403);
    }

    const aiQuota = canRunAiByEntitlement(entitlement);
    if (!aiQuota.allowed) {
      return fail(`${aiQuota.reason ?? "AI quota reached"}. Please use rule fallback mode or adjust plan quota.`, 429);
    }

    const result = await generateTodayPlan({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id,
      userName: auth.profile.display_name,
      triggeredBy: auth.profile.id,
      force: parsed.data.force ?? true
    });
    return ok(result);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "generate_today_plan_failed", 500);
  }
}
