import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { assertImportWriteAccess } from "@/services/import-job-service";
import { saveImportMapping } from "@/services/import-mapping-service";

interface RouteParams {
  params: { id: string };
}

const saveSchema = z.object({
  mapping: z.array(
    z.object({
      columnId: z.string().uuid(),
      mappedTargetEntity: z.enum(["customer", "opportunity", "followup", "mixed"]).nullable(),
      mappedTargetField: z.string().nullable(),
      normalizationRule: z.record(z.string(), z.unknown()).optional()
    })
  )
});

export async function POST(request: Request, { params }: RouteParams) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const body = await request.json().catch(() => null);
  const parsed = saveSchema.safeParse(body ?? {});
  if (!parsed.success) return fail("Invalid request payload", 400);

  try {
    await assertImportWriteAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    await saveImportMapping({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      actorUserId: auth.profile.id,
      jobId: params.id,
      mapping: parsed.data.mapping
    });

    return ok({ ok: true });
  } catch (error) {
    const message = error instanceof Error ? error.message : "save_import_mapping_failed";
    const status = message === "org_admin_access_required" ? 403 : 500;
    return fail(message, status);
  }
}
