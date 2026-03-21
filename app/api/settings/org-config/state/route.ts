import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { getOrgConfigEditorState } from "@/services/org-config-editor-service";
import { assertOrgManagerAccess } from "@/services/org-membership-service";

export async function GET() {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  try {
    const membership = await assertOrgManagerAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    const state = await getOrgConfigEditorState({
      supabase: auth.supabase,
      orgId: auth.profile.org_id
    });

    return ok({
      role: membership.role,
      canManage: membership.role === "owner" || membership.role === "admin",
      state
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "get_org_config_editor_state_failed";
    const status = message === "org_manager_access_required" ? 403 : 500;
    return fail(message, status);
  }
}
