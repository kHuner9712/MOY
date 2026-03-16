import { z } from "zod";

import { fail, ok } from "@/lib/api-response";
import { getServerAuthContext } from "@/lib/server-auth";
import { assertImportReadAccess, assertImportWriteAccess } from "@/services/import-job-service";
import { createImportTemplate, listImportTemplates } from "@/services/import-template-service";

const createSchema = z.object({
  templateName: z.string().min(1).max(120),
  importType: z.enum(["customers", "opportunities", "followups", "mixed"]),
  columnMapping: z.record(z.string(), z.unknown()),
  normalizationConfig: z.record(z.string(), z.unknown()).optional(),
  isDefault: z.boolean().optional()
});

export async function GET(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const { searchParams } = new URL(request.url);
  const importTypeRaw = searchParams.get("importType");
  const importType = importTypeRaw && ["customers", "opportunities", "followups", "mixed"].includes(importTypeRaw) ? importTypeRaw : undefined;

  try {
    await assertImportReadAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    const templates = await listImportTemplates({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      importType: importType as "customers" | "opportunities" | "followups" | "mixed" | undefined
    });

    return ok({ templates });
  } catch (error) {
    const message = error instanceof Error ? error.message : "list_import_templates_failed";
    const status = message === "org_manager_access_required" ? 403 : 500;
    return fail(message, status);
  }
}

export async function POST(request: Request) {
  const auth = await getServerAuthContext();
  if (!auth) return fail("Please login first", 401);

  const body = await request.json().catch(() => null);
  const parsed = createSchema.safeParse(body ?? {});
  if (!parsed.success) return fail("Invalid request payload", 400);

  try {
    await assertImportWriteAccess({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      userId: auth.profile.id
    });

    const template = await createImportTemplate({
      supabase: auth.supabase,
      orgId: auth.profile.org_id,
      templateName: parsed.data.templateName,
      importType: parsed.data.importType,
      columnMapping: parsed.data.columnMapping,
      normalizationConfig: parsed.data.normalizationConfig ?? {},
      isDefault: parsed.data.isDefault ?? false,
      createdBy: auth.profile.id
    });

    return ok({ template });
  } catch (error) {
    const message = error instanceof Error ? error.message : "create_import_template_failed";
    const status = message === "org_admin_access_required" ? 403 : 500;
    return fail(message, status);
  }
}

