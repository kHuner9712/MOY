import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { mapImportTemplateRow } from "@/services/mappers";
import type { Database } from "@/types/database";
import type { ImportTemplate } from "@/types/import";

type DbClient = ServerSupabaseClient;
type ImportTemplateRow = Database["public"]["Tables"]["import_templates"]["Row"];

export async function listImportTemplates(params: {
  supabase: DbClient;
  orgId: string;
  importType?: Database["public"]["Enums"]["import_type"];
}): Promise<ImportTemplate[]> {
  let query = params.supabase.from("import_templates").select("*").eq("org_id", params.orgId).order("updated_at", { ascending: false });
  if (params.importType) {
    query = query.eq("import_type", params.importType);
  }
  const res = await query;
  if (res.error) throw new Error(res.error.message);
  return ((res.data ?? []) as ImportTemplateRow[]).map((row: ImportTemplateRow) => mapImportTemplateRow(row));
}

export async function createImportTemplate(params: {
  supabase: DbClient;
  orgId: string;
  templateName: string;
  importType: Database["public"]["Enums"]["import_type"];
  columnMapping: Record<string, unknown>;
  normalizationConfig: Record<string, unknown>;
  isDefault?: boolean;
  createdBy: string;
}): Promise<ImportTemplate> {
  if (params.isDefault) {
    await params.supabase.from("import_templates").update({ is_default: false }).eq("org_id", params.orgId).eq("import_type", params.importType);
  }

  const res = await params.supabase
    .from("import_templates")
    .insert({
      org_id: params.orgId,
      template_name: params.templateName,
      import_type: params.importType,
      column_mapping: params.columnMapping,
      normalization_config: params.normalizationConfig,
      is_default: params.isDefault ?? false,
      created_by: params.createdBy
    })
    .select("*")
    .single();
  if (res.error) throw new Error(res.error.message);
  return mapImportTemplateRow(res.data as ImportTemplateRow);
}

