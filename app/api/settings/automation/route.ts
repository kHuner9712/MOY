import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { getAutomationCenterSnapshot, setAutomationRuleEnabled, upsertAutomationRule } from "@/services/automation-rule-service";
import { assertOrgAdminAccess, assertOrgManagerAccess } from "@/services/org-membership-service";

const upsertSchema = z.object({
  ruleId: z.string().uuid().optional(),
  ruleKey: z.string().min(1),
  ruleName: z.string().min(1),
  ruleScope: z.enum([
    "customer_health",
    "deal_progress",
    "trial_conversion",
    "onboarding",
    "retention",
    "external_touchpoint",
    "manager_attention"
  ]),
  triggerType: z.enum(["threshold", "inactivity", "missing_step", "health_score", "event_sequence"]),
  conditionsJson: z.record(z.unknown()).default({}),
  actionJson: z.record(z.unknown()).default({}),
  severity: z.enum(["info", "warning", "critical"]),
  isEnabled: z.boolean().default(true)
});

const toggleSchema = z.object({
  ruleId: z.string().uuid(),
  isEnabled: z.boolean()
});

export async function GET() {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  try {
    await assertOrgManagerAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    const data = await getAutomationCenterSnapshot({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      actorUserId: auth.profile.id
    });
    return ok(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "get_automation_settings_failed";
    const status = message === "org_manager_access_required" ? 403 : 500;
    return fail(message, status);
  }
}

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const payload = await request.json().catch(() => ({}));
  const toggleParsed = toggleSchema.safeParse(payload);
  const upsertParsed = upsertSchema.safeParse(payload);

  if (!toggleParsed.success && !upsertParsed.success) {
    return fail("Invalid request payload", 400);
  }

  try {
    await assertOrgAdminAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    if (toggleParsed.success && Object.keys(payload).length <= 2) {
      const data = await setAutomationRuleEnabled({
        supabase: auth.supabase,
        orgId: auth.profile.org_id,
        ruleId: toggleParsed.data.ruleId,
        isEnabled: toggleParsed.data.isEnabled
      });
      return ok(data);
    }

    const parsed = upsertParsed.success ? upsertParsed.data : null;
    if (!parsed) return fail("Invalid request payload", 400);

    const data = await upsertAutomationRule({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      actorUserId: auth.profile.id,
      ruleId: parsed.ruleId,
      ruleKey: parsed.ruleKey,
      ruleName: parsed.ruleName,
      ruleScope: parsed.ruleScope,
      triggerType: parsed.triggerType,
      conditionsJson: parsed.conditionsJson,
      actionJson: parsed.actionJson,
      severity: parsed.severity,
      isEnabled: parsed.isEnabled
    });
    return ok(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "save_automation_rule_failed";
    const status = message === "org_admin_access_required" ? 403 : 500;
    return fail(message, status);
  }
}
