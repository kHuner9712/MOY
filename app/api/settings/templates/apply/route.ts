import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { assertOrgAdminAccess } from "@/services/org-membership-service";
import { applyTemplate } from "@/services/template-application-service";

const requestSchema = z.object({
  templateIdOrKey: z.string().min(1),
  applyMode: z.enum(["onboarding_default", "demo_seed", "manual_apply", "trial_bootstrap"]).default("manual_apply"),
  applyStrategy: z.enum(["additive_only", "merge_prefer_existing", "template_override_existing"]).default("merge_prefer_existing"),
  generateDemoSeed: z.boolean().default(false),
  overrides: z
    .array(
      z.object({
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
      })
    )
    .optional()
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

    const result = await applyTemplate({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      actorUserId: auth.profile.id,
      templateIdOrKey: parsed.data.templateIdOrKey,
      applyMode: parsed.data.applyMode,
      applyStrategy: parsed.data.applyStrategy,
      generateDemoSeed: parsed.data.generateDemoSeed,
      overrides: parsed.data.overrides
    });

    return ok(result);
  } catch (error) {
    const message = error instanceof Error ? error.message : "template_apply_failed";
    const status =
      message === "org_admin_access_required"
        ? 403
        : message.startsWith("template_override_payload_invalid")
          ? 400
        : message === "industry_template_not_found"
          ? 404
          : 500;
    return fail(message, status);
  }
}
