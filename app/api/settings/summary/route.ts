import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { getCurrentOrgTemplateContext } from "@/services/industry-template-service";
import { assertOrgManagerAccess } from "@/services/org-membership-service";
import { getOrgAiControlStatus } from "@/services/org-ai-settings-service";
import { getEntitlementStatus } from "@/services/plan-entitlement-service";
import { getOrgSettings } from "@/services/org-settings-service";

export async function GET() {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  try {
    const membership = await assertOrgManagerAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    const [settings, aiStatus, entitlement, templateContext] = await Promise.all([
      getOrgSettings({ supabase: auth.supabase, orgId: auth.profile.org_id }),
      getOrgAiControlStatus({ supabase: auth.supabase, orgId: auth.profile.org_id }),
      getEntitlementStatus({ supabase: auth.supabase, orgId: auth.profile.org_id, refreshUsage: false }),
      getCurrentOrgTemplateContext({ supabase: auth.supabase, orgId: auth.profile.org_id })
    ]);

    const stepState = settings.onboardingStepState ?? {};
    const stepValues = Object.values(stepState);
    const onboardingProgress = stepValues.length === 0 ? 0 : Math.round((stepValues.filter(Boolean).length / stepValues.length) * 100);

    return ok({
      role: membership.role,
      onboardingCompleted: settings.onboardingCompleted,
      onboardingProgress,
      aiProviderConfigured: aiStatus.providerConfigured,
      aiProviderReason: aiStatus.providerReason,
      planTier: entitlement.planTier,
      planStatus: entitlement.status,
      quotaNearLimit: entitlement.quotaNearLimit,
      quotaExceeded: entitlement.quotaExceeded,
      aiRunUsedMonthly: entitlement.aiRunUsedMonthly,
      aiRunLimitMonthly: entitlement.aiRunLimitMonthly,
      currentTemplateKey: templateContext.template?.templateKey ?? null,
      currentTemplateName: templateContext.template?.displayName ?? null,
      templateApplied: Boolean(templateContext.assignment)
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "get_settings_summary_failed";
    const status = message === "org_manager_access_required" ? 403 : 500;
    return fail(message, status);
  }
}
