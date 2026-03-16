import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { assertImportReadAccess, listImportAuditEvents } from "@/services/import-job-service";

interface RouteParams {
  params: { id: string };
}

export async function GET(request: Request, { params }: RouteParams) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const { searchParams } = new URL(request.url);
  const limitRaw = Number(searchParams.get("limit") ?? "100");
  const limit = Number.isFinite(limitRaw) ? Math.max(1, Math.min(500, Math.round(limitRaw))) : 100;

  try {
    await assertImportReadAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    const events = await listImportAuditEvents({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      jobId: params.id,
      limit
    });

    return ok({ events });
  } catch (error) {
    const message = error instanceof Error ? error.message : "list_import_audit_failed";
    const status = message === "org_manager_access_required" ? 403 : 500;
    return fail(message, status);
  }
}
