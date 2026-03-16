import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { assertOrgAdminAccess } from "@/services/org-membership-service";
import { runOnboardingFlow } from "@/services/onboarding-service";

const requestSchema = z.object({
  runType: z.enum(["first_time_setup", "trial_bootstrap", "demo_seed", "reinitialize_demo"])
});

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

    const result = await runOnboardingFlow({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      actorUserId: auth.profile.id,
      runType: parsed.data.runType
    });

    return ok(result);
  } catch (error) {
    const message = error instanceof Error ? error.message : "run_onboarding_failed";
    const status = message === "org_admin_access_required" ? 403 : 500;
    return fail(message, status);
  }
}
