import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { assertImportReadAccess, assertImportWriteAccess, listImportJobs } from "@/services/import-job-service";

export async function GET(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const { searchParams } = new URL(request.url);
  const limitRaw = Number(searchParams.get("limit") ?? "30");
  const limit = Number.isFinite(limitRaw) ? Math.max(1, Math.min(200, Math.round(limitRaw))) : 30;

  try {
    await assertImportReadAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    let canWrite = true;
    try {
      await assertImportWriteAccess({
        supabase: auth.supabase,
        orgId: auth.profile.org_id,
        userId: auth.profile.id
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : "org_admin_access_required";
      if (message === "org_admin_access_required") {
        canWrite = false;
      } else {
        throw error;
      }
    }

    const jobs = await listImportJobs({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      limit
    });

    return ok({ jobs, canWrite });
  } catch (error) {
    const message = error instanceof Error ? error.message : "list_import_jobs_failed";
    const status = message === "org_manager_access_required" ? 403 : 500;
    return fail(message, status);
  }
}

