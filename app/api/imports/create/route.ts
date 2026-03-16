import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { appendImportAuditEvent, assertImportWriteAccess, createImportJob } from "@/services/import-job-service";

const requestSchema = z.object({
  importType: z.enum(["customers", "opportunities", "followups", "mixed"]),
  sourceType: z.enum(["csv", "xlsx", "manual_table", "demo_bootstrap"]),
  fileName: z.string().min(1).max(260),
  storagePath: z.string().max(400).optional()
});

export async function POST(request: Request) {
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

    const job = await createImportJob({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      initiatedBy: auth.profile.id,
      importType: parsed.data.importType,
      sourceType: parsed.data.sourceType,
      fileName: parsed.data.fileName,
      storagePath: parsed.data.storagePath ?? null
    });

    await appendImportAuditEvent({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      jobId: job.id,
      actorUserId: auth.profile.id,
      eventType: "uploaded",
      eventSummary: "Import job created",
      eventPayload: {
        import_type: parsed.data.importType,
        source_type: parsed.data.sourceType,
        file_name: parsed.data.fileName
      }
    });

    return ok({ job });
  } catch (error) {
    const message = error instanceof Error ? error.message : "create_import_job_failed";
    const status = message === "org_admin_access_required" ? 403 : 500;
    return fail(message, status);
  }
}

