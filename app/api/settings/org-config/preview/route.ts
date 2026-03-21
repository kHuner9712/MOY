import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { previewOrgConfigWrite } from "@/services/org-config-editor-service";
import { assertOrgAdminAccess } from "@/services/org-membership-service";

const requestSchema = z.object({
  targetType: z.enum(["org_settings", "org_ai_settings", "org_feature_flags"]),
  patch: z.record(z.unknown())
});

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const payload = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(payload ?? {});
  if (!parsed.success) return fail("Invalid request payload", 400);

  try {
    const membership = await assertOrgAdminAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    const preview = previewOrgConfigWrite({
      targetType: parsed.data.targetType,
      patch: parsed.data.patch
    });

    return ok({
      role: membership.role,
      preview
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "preview_org_config_write_failed";
    const status = message === "org_admin_access_required" ? 403 : 500;
    return fail(message, status);
  }
}
