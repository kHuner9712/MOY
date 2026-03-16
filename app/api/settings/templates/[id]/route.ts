import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { getIndustryTemplateDetail } from "@/services/industry-template-service";
import { assertOrgManagerAccess } from "@/services/org-membership-service";

export async function GET(_request: Request, context: { params: { id: string } }) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  try {
    await assertOrgManagerAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    const detail = await getIndustryTemplateDetail({
      supabase: auth.supabase,
      templateIdOrKey: context.params.id
    });

    return ok(detail);
  } catch (error) {
    const message = error instanceof Error ? error.message : "get_template_detail_failed";
    const status = message === "org_manager_access_required" ? 403 : message === "industry_template_not_found" ? 404 : 500;
    return fail(message, status);
  }
}

