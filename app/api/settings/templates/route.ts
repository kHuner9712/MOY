import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { getCurrentOrgTemplateContext, listIndustryTemplates } from "@/services/industry-template-service";
import { assertOrgManagerAccess } from "@/services/org-membership-service";
import { listTemplateApplicationRuns } from "@/services/template-application-service";

export async function GET() {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  try {
    const membership = await assertOrgManagerAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    const [templates, current, runs] = await Promise.all([
      listIndustryTemplates({
        supabase: auth.supabase
      }),
      getCurrentOrgTemplateContext({
        supabase: auth.supabase,
        orgId: auth.profile.org_id
      }),
      listTemplateApplicationRuns({
        supabase: auth.supabase,
        orgId: auth.profile.org_id,
        limit: 10
      })
    ]);

    return ok({
      role: membership.role,
      templates,
      currentTemplate: current.template,
      currentAssignment: current.assignment,
      recentRuns: runs
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "list_templates_failed";
    const status = message === "org_manager_access_required" ? 403 : 500;
    return fail(message, status);
  }
}

