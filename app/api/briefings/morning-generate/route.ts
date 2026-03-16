import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { isManager } from "@/lib/permissions";
import { getServerAuthContext } from "@/lib/server-auth";
import { checkOrgFeatureAccess } from "@/services/feature-access-service";
import { generateMorningBrief } from "@/services/morning-brief-service";
import { getOrgAiSettings } from "@/services/org-ai-settings-service";
import { canRunAiByEntitlement, getEntitlementStatus } from "@/services/plan-entitlement-service";
import { trackSuggestionAdoption } from "@/services/suggestion-adoption-service";

const requestSchema = z.object({
  briefType: z.enum(["sales_morning", "manager_morning"]).optional(),
  briefDate: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional()
});

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const body = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(body ?? {});
  if (!parsed.success) return fail("Invalid request payload", 400);

  const briefType = parsed.data.briefType ?? (auth.profile.role === "manager" ? "manager_morning" : "sales_morning");
  if (briefType === "manager_morning" && !isManager(auth.profile)) {
    return fail("Manager access required", 403);
  }

  try {
    const featureAccess = await checkOrgFeatureAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      featureKey: "ai_morning_brief"
    });
    if (!featureAccess.allowed) {
      return fail(featureAccess.reason ?? "Morning brief feature is disabled", 403);
    }

    const [aiSettings, entitlement] = await Promise.all([
      getOrgAiSettings({ supabase: auth.supabase, orgId: auth.profile.org_id }),
      getEntitlementStatus({ supabase: auth.supabase, orgId: auth.profile.org_id, refreshUsage: true })
    ]);

    if (!aiSettings.autoBriefEnabled) {
      return fail("Organization disabled auto brief in AI settings", 403);
    }

    const aiQuota = canRunAiByEntitlement(entitlement);
    if (!aiQuota.allowed) {
      return fail(`${aiQuota.reason ?? "AI quota reached"}. Morning brief can be retried after quota reset.`, 429);
    }

    const result = await generateMorningBrief({
      supabase: auth.supabase,
      profile: auth.profile,
      briefType,
      briefDate: parsed.data.briefDate
    });

    await trackSuggestionAdoption({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id,
      targetType: "morning_brief",
      targetId: result.brief.id,
      adoptionType: "viewed",
      adoptionContext: "after_review"
    }).catch(() => null);

    return ok(result);
  } catch (error) {
    return fail(error instanceof Error ? error.message : "generate_morning_brief_failed", 500);
  }
}
