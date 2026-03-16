import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { assertImportWriteAccess } from "@/services/import-job-service";
import { executeImportJob } from "@/services/import-execution-service";

interface RouteParams {
  params: { id: string };
}

const requestSchema = z.object({
  runReviewSummary: z.boolean().optional()
});

export async function POST(request: Request, { params }: RouteParams) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const body = await request.json().catch(() => null);
  const parsed = requestSchema.safeParse(body ?? {});
  if (!parsed.success) return fail("Invalid request payload", 400);

  try {
    await assertImportWriteAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    const result = await executeImportJob({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      actorUserId: auth.profile.id,
      jobId: params.id,
      runReviewSummary: parsed.data.runReviewSummary
    });

    return ok(result);
  } catch (error) {
    const message = error instanceof Error ? error.message : "execute_import_job_failed";
    const status = message === "org_admin_access_required" ? 403 : 500;
    return fail(message, status);
  }
}
