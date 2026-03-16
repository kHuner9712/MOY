import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import {
  assertImportReadAccess,
  assertImportWriteAccess,
  getImportJob,
  listDedupeGroups,
  listImportColumns
} from "@/services/import-job-service";

interface RouteParams {
  params: { id: string };
}

export async function GET(_request: Request, { params }: RouteParams) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

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

    const [job, columns, dedupeGroups] = await Promise.all([
      getImportJob({
        supabase: auth.supabase,
        orgId: auth.profile.org_id,
        jobId: params.id
      }),
      listImportColumns({
        supabase: auth.supabase,
        orgId: auth.profile.org_id,
        jobId: params.id
      }),
      listDedupeGroups({
        supabase: auth.supabase,
        orgId: auth.profile.org_id,
        jobId: params.id
      })
    ]);

    return ok({ job, columns, dedupeGroups, canWrite });
  } catch (error) {
    const message = error instanceof Error ? error.message : "get_import_job_failed";
    const status = message === "org_manager_access_required" ? 403 : 500;
    return fail(message, status);
  }
}
