import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { getCurrentOrgTemplateContext } from "@/services/industry-template-service";
import { assertOrgManagerAccess } from "@/services/org-membership-service";
import { previewOrgTemplateOverrideRollback } from "@/services/org-template-override-rollback-service";

const requestSchema = z
  .object({
    templateId: z.string().uuid().optional(),
    overrideType: z.enum([
      "customer_stages",
      "opportunity_stages",
      "alert_rules",
      "checkpoints",
      "playbook_seed",
      "prep_preferences",
      "brief_preferences",
      "demo_seed_profile"
    ]),
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

    let templateId = parsed.data.templateId ?? "";
    if (!templateId) {
      const current = await getCurrentOrgTemplateContext({
        supabase: auth.supabase,
        orgId: auth.profile.org_id
      });
      if (!current.template) return fail("No active template for this org", 400);
      templateId = current.template.id;
    }

    const preview = await previewOrgTemplateOverrideRollback({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      templateId,
      overrideType: parsed.data.overrideType,
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
    const message = error instanceof Error ? error.message : "preview_template_override_rollback_failed";
    const status = message === "org_manager_access_required" ? 403 : 500;
    return fail(message, status);
  }
}
