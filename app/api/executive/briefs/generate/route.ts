import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { checkOrgAiScenarioAccess } from "@/services/feature-access-service";
import { generateExecutiveBrief } from "@/services/executive-brief-service";
import { assertOrgManagerAccess } from "@/services/org-membership-service";

const requestSchema = z.object({
  briefType: z.enum(["executive_daily", "executive_weekly", "retention_watch", "trial_watch", "deal_watch"]),
  targetUserId: z.string().uuid().nullable().optional(),
  ownerId: z.string().uuid().optional()
});

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const payload = await request.json().catch(() => ({}));
  const parsed = requestSchema.safeParse(payload);
  if (!parsed.success) return fail("Invalid request payload", 400);

  try {
    await assertOrgManagerAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    const access = await checkOrgAiScenarioAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      featureKey: "ai_morning_brief",
      settingKey: "autoBriefEnabled",
      refreshUsage: true
    });
    if (!access.allowed) {
      const status = access.reason?.toLowerCase().includes("quota") ? 429 : 403;
      return fail(access.reason ?? "Executive brief generation disabled", status);
    }

    const data = await generateExecutiveBrief({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      actorUserId: auth.profile.id,
      briefType: parsed.data.briefType,
      targetUserId: parsed.data.targetUserId ?? null,
      ownerId: parsed.data.ownerId
    });

    return ok(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "generate_executive_brief_failed";
    const status = message === "org_manager_access_required" ? 403 : 500;
    return fail(message, status);
  }
}
