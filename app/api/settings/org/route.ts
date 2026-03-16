import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { assertOrgAdminAccess, assertOrgManagerAccess } from "@/services/org-membership-service";
import { getOrgSettings, updateOrgSettings } from "@/services/org-settings-service";

const updateSchema = z.object({
  orgDisplayName: z.string().min(1).max(120).optional(),
  brandName: z.string().min(1).max(80).optional(),
  industryHint: z.string().max(120).nullable().optional(),
  timezone: z.string().min(2).max(64).optional(),
  locale: z.string().min(2).max(24).optional(),
  defaultCustomerStages: z.array(z.string().min(1)).min(3).optional(),
  defaultOpportunityStages: z.array(z.string().min(1)).min(3).optional(),
  defaultAlertRules: z.record(z.string(), z.number().int().min(1).max(90)).optional(),
  defaultFollowupSlaDays: z.number().int().min(1).max(30).optional(),
  onboardingCompleted: z.boolean().optional()
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

    const settings = await getOrgSettings({
      supabase: auth.supabase,
      orgId: auth.profile.org_id
    });

    return ok({ settings });
  } catch (error) {
    const message = error instanceof Error ? error.message : "get_org_settings_failed";
    const status = message === "org_manager_access_required" ? 403 : 500;
    return fail(message, status);
  }
}

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const payload = await request.json().catch(() => null);
  const parsed = updateSchema.safeParse(payload ?? {});
  if (!parsed.success) return fail("Invalid request payload", 400);

  try {
    await assertOrgAdminAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    const settings = await updateOrgSettings({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      patch: parsed.data
    });

    return ok({ settings });
  } catch (error) {
    const message = error instanceof Error ? error.message : "update_org_settings_failed";
    const status = message === "org_admin_access_required" ? 403 : 500;
    return fail(message, status);
  }
}
