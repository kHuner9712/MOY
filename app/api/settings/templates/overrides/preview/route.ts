import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { buildOrgOverrideWriteDiagnosticsSummary, prepareOrgTemplateOverrideWrite } from "@/lib/org-override-write-governance";
import { getServerAuthContext } from "@/lib/server-auth";
import { getCurrentOrgTemplateContext } from "@/services/industry-template-service";
import { assertOrgManagerAccess } from "@/services/org-membership-service";

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
      if (!current.assignment?.templateId) return fail("No active template for this org", 400);
      templateId = current.assignment.templateId;
    }

    const prepared = prepareOrgTemplateOverrideWrite({
      overrideType: parsed.data.overrideType,
      overridePayload: parsed.data.overridePayload
    });

    return ok({
      role: membership.role,
      preview: {
        templateId,
        overrideType: parsed.data.overrideType,
        writeDiagnostics: prepared.writeDiagnostics,
        diagnosticsSummary: buildOrgOverrideWriteDiagnosticsSummary([prepared.writeDiagnostics])
      }
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "preview_template_override_write_failed";
    const status = message === "org_manager_access_required" ? 403 : 500;
    return fail(message, status);
  }
}
