import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { runAutomationRules } from "@/services/automation-rule-service";
import { assertOrgAdminAccess } from "@/services/org-membership-service";

const requestSchema = z.object({
  ruleIds: z.array(z.string().uuid()).optional(),
  ownerId: z.string().uuid().optional()
});

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

    const data = await runAutomationRules({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      actorUserId: auth.profile.id,
      ruleIds: parsed.data.ruleIds,
      ownerId: parsed.data.ownerId
    });
    return ok(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "run_automation_rules_failed";
    const status = message === "org_admin_access_required" ? 403 : 500;
    return fail(message, status);
  }
}

