import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { assertImportWriteAccess } from "@/services/import-job-service";
import { validateImportJob } from "@/services/import-validation-service";

interface RouteParams {
  params: { id: string };
}

export async function POST(_request: Request, { params }: RouteParams) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  try {
    await assertImportWriteAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    const result = await validateImportJob({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      actorUserId: auth.profile.id,
      jobId: params.id
    });

    return ok(result);
  } catch (error) {
    const message = error instanceof Error ? error.message : "validate_import_job_failed";
    const status = message === "org_admin_access_required" ? 403 : 500;
    return fail(message, status);
  }
}
