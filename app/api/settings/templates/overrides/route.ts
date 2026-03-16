import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { getCurrentOrgTemplateContext, upsertOrgTemplateOverride } from "@/services/industry-template-service";
import { assertOrgAdminAccess } from "@/services/org-membership-service";

const requestSchema = z.object({
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
  overridePayload: z.record(z.unknown())
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

    let templateId = parsed.data.templateId ?? "";
    if (!templateId) {
      const current = await getCurrentOrgTemplateContext({
        supabase: auth.supabase,
        orgId: auth.profile.org_id
      });
      if (!current.template) return fail("No active template for this org", 400);
      templateId = current.template.id;
    }

    const override = await upsertOrgTemplateOverride({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      templateId,
      overrideType: parsed.data.overrideType,
      overridePayload: parsed.data.overridePayload,
      actorUserId: auth.profile.id
    });

    return ok({ override });
  } catch (error) {
    const message = error instanceof Error ? error.message : "template_override_failed";
    const status = message === "org_admin_access_required" ? 403 : 500;
    return fail(message, status);
  }
}

