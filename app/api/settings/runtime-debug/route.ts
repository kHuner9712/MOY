import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { assertOrgManagerAccess } from "@/services/org-membership-service";
import { getRuntimeExplainDebugPanelData } from "@/services/runtime-explain-debug-service";

export async function GET() {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  try {
    const membership = await assertOrgManagerAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    const panel = await getRuntimeExplainDebugPanelData({
      supabase: auth.supabase,
      orgId: auth.profile.org_id
    });

    return ok({
      role: membership.role,
      panel
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "get_runtime_debug_panel_failed";
    const status = message === "org_manager_access_required" ? 403 : 500;
    return fail(message, status);
  }
}

