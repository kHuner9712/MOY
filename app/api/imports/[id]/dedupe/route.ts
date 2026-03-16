import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { assertImportWriteAccess } from "@/services/import-job-service";
import { applyDedupeResolutions, runImportDedupe } from "@/services/import-dedupe-service";

interface RouteParams {
  params: { id: string };
}

const requestSchema = z.object({
  resolutions: z
    .array(
      z.object({
        groupId: z.string().uuid(),
        action: z.enum(["create_new", "merge", "skip"])
      })
    )
    .optional()
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

    if (parsed.data.resolutions && parsed.data.resolutions.length > 0) {
      const groups = await applyDedupeResolutions({
        supabase: auth.supabase,
        orgId: auth.profile.org_id,
        actorUserId: auth.profile.id,
        jobId: params.id,
        resolutions: parsed.data.resolutions
      });
      return ok({ groups });
    }

    const result = await runImportDedupe({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      actorUserId: auth.profile.id,
      jobId: params.id
    });

    return ok(result);
  } catch (error) {
    const message = error instanceof Error ? error.message : "run_import_dedupe_failed";
    const status = message === "org_admin_access_required" ? 403 : 500;
    return fail(message, status);
  }
}
