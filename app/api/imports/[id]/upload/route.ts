import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { assertImportWriteAccess } from "@/services/import-job-service";
import { uploadImportData } from "@/services/import-mapping-service";

interface RouteParams {
  params: { id: string };
}

const uploadSchema = z.object({
  sourceType: z.enum(["csv", "xlsx", "manual_table", "demo_bootstrap"]),
  fileText: z.string().optional(),
  fileBase64: z.string().optional()
});

export async function POST(request: Request, { params }: RouteParams) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const body = await request.json().catch(() => null);
  const parsed = uploadSchema.safeParse(body ?? {});
  if (!parsed.success) return fail("Invalid request payload", 400);

  if ((parsed.data.sourceType === "csv" || parsed.data.sourceType === "manual_table") && !parsed.data.fileText) {
    return fail("fileText is required for csv/manual_table", 400);
  }
  if (parsed.data.sourceType === "xlsx" && !parsed.data.fileBase64) {
    return fail("fileBase64 is required for xlsx", 400);
  }

  try {
    await assertImportWriteAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    const result = await uploadImportData({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      actorUserId: auth.profile.id,
      jobId: params.id,
      sourceType: parsed.data.sourceType,
      fileText: parsed.data.fileText,
      fileBase64: parsed.data.fileBase64
    });

    return ok(result);
  } catch (error) {
    const message = error instanceof Error ? error.message : "upload_import_data_failed";
    const status = message === "org_admin_access_required" ? 403 : 500;
    return fail(message, status);
  }
}
