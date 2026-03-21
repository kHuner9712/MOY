import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { assertOrgManagerAccess } from "@/services/org-membership-service";
import { previewOrgConfigRollback } from "@/services/org-config-rollback-service";

const requestSchema = z
  .object({
    targetType: z.enum(["org_settings", "org_ai_settings", "org_feature_flags"]),
    targetAuditId: z.string().uuid().optional(),
    targetVersionLabel: z.string().min(1).optional(),
    targetVersionNumber: z.number().int().positive().optional()
  })
  .refine(
    (value) =>
      Boolean(value.targetAuditId) ||
      Boolean(value.targetVersionLabel) ||
      typeof value.targetVersionNumber === "number",
    {
      message: "rollback_selector_required",
      path: ["targetAuditId"]
    }
  );

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const payload = await request.json().catch(() => ({}));
  const parsed = requestSchema.safeParse(payload);
  if (!parsed.success) return fail("Invalid request payload", 400);

  try {
    const membership = await assertOrgManagerAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    const preview = await previewOrgConfigRollback({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      targetType: parsed.data.targetType,
      selector: {
        targetAuditId: parsed.data.targetAuditId ?? null,
        targetVersionLabel: parsed.data.targetVersionLabel ?? null,
        targetVersionNumber: parsed.data.targetVersionNumber ?? null
      }
    });

    return ok({
      role: membership.role,
      preview
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "preview_org_config_rollback_failed";
    const status = message === "org_manager_access_required" ? 403 : 500;
    return fail(message, status);
  }
}

