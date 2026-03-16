import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { assertImportReadAccess, listImportRows } from "@/services/import-job-service";

interface RouteParams {
  params: { id: string };
}

export async function GET(request: Request, { params }: RouteParams) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const { searchParams } = new URL(request.url);
  const limitRaw = Number(searchParams.get("limit") ?? "120");
  const offsetRaw = Number(searchParams.get("offset") ?? "0");
  const limit = Number.isFinite(limitRaw) ? Math.max(1, Math.min(500, Math.round(limitRaw))) : 120;
  const offset = Number.isFinite(offsetRaw) ? Math.max(0, Math.round(offsetRaw)) : 0;

  try {
    await assertImportReadAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    const rows = await listImportRows({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      jobId: params.id,
      limit,
      offset
    });

    return ok({ rows });
  } catch (error) {
    const message = error instanceof Error ? error.message : "list_import_rows_failed";
    const status = message === "org_manager_access_required" ? 403 : 500;
    return fail(message, status);
  }
}
