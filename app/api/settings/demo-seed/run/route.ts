import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { checkOrgFeatureAccess } from "@/services/feature-access-service";
import { assertOrgAdminAccess } from "@/services/org-membership-service";
import { runDemoSeed } from "@/services/demo-seed-service";

export async function POST() {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  try {
    await assertOrgAdminAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    const featureCheck = await checkOrgFeatureAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      featureKey: "demo_seed_tools"
    });

    if (!featureCheck.allowed) {
      return fail(featureCheck.reason ?? "Demo seed tool disabled", 403);
    }

    const result = await runDemoSeed({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      actorUserId: auth.profile.id,
      runType: "demo_seed"
    });

    return ok(result);
  } catch (error) {
    const message = error instanceof Error ? error.message : "run_demo_seed_failed";
    const status = message === "org_admin_access_required" ? 403 : 500;
    return fail(message, status);
  }
}
