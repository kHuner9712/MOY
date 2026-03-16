import { read, utils } from "xlsx";

import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import { buildRuleBasedMappingSuggestions, type ImportFieldSuggestion } from "@/lib/import-mapping";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { getActivePromptVersion } from "@/services/ai-prompt-service";
import { createAiRun, updateAiRunStatus } from "@/services/ai-run-service";
import { appendImportAuditEvent, getImportJob, replaceImportColumns, replaceImportRows } from "@/services/import-job-service";
import { importColumnMappingAssistResultSchema } from "@/types/ai";
import type { Database } from "@/types/database";
import type { ImportJobColumn } from "@/types/import";

type DbClient = ServerSupabaseClient;

interface ParsedUploadResult {
  columns: string[];
  rows: Array<Record<string, unknown>>;
}

type ImportRowInsert = Database["public"]["Tables"]["import_job_rows"]["Insert"];

function asJson(value: unknown): Database["public"]["Tables"]["import_job_rows"]["Insert"]["raw_payload"] {
  return value as Database["public"]["Tables"]["import_job_rows"]["Insert"]["raw_payload"];
}

function nowIso(): string {
  return new Date().toISOString();
}

function parseCsvLine(line: string): string[] {
  const result: string[] = [];
  let current = "";
  let inQuotes = false;

  for (let i = 0; i < line.length; i += 1) {
    const char = line[i];
    if (char === '"') {
      if (inQuotes && line[i + 1] === '"') {
        current += '"';
        i += 1;
      } else {
        inQuotes = !inQuotes;
      }
    } else if (char === "," && !inQuotes) {
      result.push(current);
      current = "";
    } else {
      current += char;
    }
  }
  result.push(current);
  return result.map((item) => item.trim());
}

function parseCsvText(text: string): ParsedUploadResult {
  const lines = text
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter((item) => item.length > 0);

  if (lines.length === 0) return { columns: [], rows: [] };
  const columns = parseCsvLine(lines[0]).map((item, index) => item || `column_${index + 1}`);
  const rows: Array<Record<string, unknown>> = [];

  for (let i = 1; i < lines.length; i += 1) {
    const cells = parseCsvLine(lines[i]);
    const payload: Record<string, unknown> = {};
    for (let c = 0; c < columns.length; c += 1) {
      payload[columns[c]] = cells[c] ?? "";
    }
    rows.push(payload);
  }

  return { columns, rows };
}

function parseXlsxBase64(base64: string): ParsedUploadResult {
  const buffer = Buffer.from(base64, "base64");
  const workbook = read(buffer, { type: "buffer", cellDates: false });
  const firstSheet = workbook.SheetNames[0];
  if (!firstSheet) return { columns: [], rows: [] };

  const sheet = workbook.Sheets[firstSheet];
  const matrix = utils.sheet_to_json(sheet, { header: 1, defval: "" }) as unknown[][];
  if (matrix.length === 0) return { columns: [], rows: [] };
  const columns = (matrix[0] ?? []).map((item, index) => String(item || `column_${index + 1}`));
  const rows: Array<Record<string, unknown>> = [];

  for (let i = 1; i < matrix.length; i += 1) {
    const line = matrix[i] ?? [];
    const payload: Record<string, unknown> = {};
    for (let c = 0; c < columns.length; c += 1) {
      payload[columns[c]] = line[c] ?? "";
    }
    rows.push(payload);
  }

  return { columns, rows };
}

export function parseImportUpload(params: {
  sourceType: Database["public"]["Enums"]["import_source_type"];
  fileText?: string;
  fileBase64?: string;
}): ParsedUploadResult {
  if (params.sourceType === "csv" || params.sourceType === "manual_table") {
    return parseCsvText(params.fileText ?? "");
  }
  if (params.sourceType === "xlsx") {
    if (!params.fileBase64) return { columns: [], rows: [] };
    return parseXlsxBase64(params.fileBase64);
  }
  return parseCsvText(params.fileText ?? "");
}

export async function uploadImportData(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  jobId: string;
  sourceType: Database["public"]["Enums"]["import_source_type"];
  fileText?: string;
  fileBase64?: string;
}): Promise<{
  columns: ImportJobColumn[];
  totalRows: number;
}> {
  await getImportJob({ supabase: params.supabase, orgId: params.orgId, jobId: params.jobId });

  await params.supabase.from("import_jobs").update({ job_status: "parsing" }).eq("id", params.jobId);

  const parsed = parseImportUpload({
    sourceType: params.sourceType,
    fileText: params.fileText,
    fileBase64: params.fileBase64
  });

  const suggestions = buildRuleBasedMappingSuggestions(parsed.columns);

  const columns = await replaceImportColumns({
    supabase: params.supabase,
    orgId: params.orgId,
    jobId: params.jobId,
    columns: suggestions.map((item, index) => ({
      org_id: params.orgId,
      import_job_id: params.jobId,
      source_column_name: item.sourceColumnName,
      source_column_index: index,
      detected_type: item.detectedType,
      mapped_target_entity: item.mappedTargetEntity,
      mapped_target_field: item.mappedTargetField,
      mapping_confidence: item.confidence,
      normalization_rule: {}
    }))
  });

  await replaceImportRows({
    supabase: params.supabase,
    orgId: params.orgId,
    jobId: params.jobId,
    rows: parsed.rows.map((payload, index): ImportRowInsert => ({
      org_id: params.orgId,
      import_job_id: params.jobId,
      source_row_no: index + 1,
      raw_payload: asJson(payload),
      normalized_payload: asJson({}),
      row_status: "pending",
      validation_errors: asJson([]),
      duplicate_candidates: asJson([])
    }))
  });

  await params.supabase
    .from("import_jobs")
    .update({
      job_status: "mapping",
      total_rows: parsed.rows.length,
      detail_snapshot: {
        parsed_columns: parsed.columns
      }
    })
    .eq("id", params.jobId);

  await appendImportAuditEvent({
    supabase: params.supabase,
    orgId: params.orgId,
    jobId: params.jobId,
    actorUserId: params.actorUserId,
    eventType: "parsed",
    eventSummary: `Parsed ${parsed.rows.length} rows from uploaded file.`,
    eventPayload: { columns: parsed.columns }
  });

  return {
    columns,
    totalRows: parsed.rows.length
  };
}

async function runAiMappingAssist(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  jobId: string;
  sourceColumns: string[];
  sampleRows: Array<Record<string, unknown>>;
  fallbackSuggestions: ImportFieldSuggestion[];
}): Promise<{ suggestions: ImportFieldSuggestion[]; usedFallback: boolean; fallbackReason: string | null }> {
  const scenario = "import_column_mapping_assist" as const;
  const provider = getAiProvider();
  const model = provider.getDefaultModel({ reasoning: false });
  const prompt = await getActivePromptVersion({
    supabase: params.supabase,
    orgId: params.orgId,
    scenario,
    providerId: provider.id
  });

  const aiRun = await createAiRun({
    supabase: params.supabase,
    orgId: params.orgId,
    triggerSource: "manual",
    scenario,
    provider: provider.id,
    model,
    promptVersion: prompt.version,
    triggeredByUserId: params.actorUserId,
    inputSnapshot: {
      source_columns: params.sourceColumns,
      sample_rows: params.sampleRows
    }
  });

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: aiRun.id,
    status: "running",
    startedAt: nowIso()
  });

  try {
    if (!provider.isConfigured()) throw new Error("provider_not_configured");

    const response = await provider.chatCompletion({
      scenario,
      model,
      systemPrompt: prompt.systemPrompt,
      developerPrompt: `${prompt.developerPrompt}\n\nOutput schema:\n${JSON.stringify(prompt.outputSchema)}`,
      userPrompt: JSON.stringify({
        source_columns: params.sourceColumns,
        sample_rows: params.sampleRows
      }),
      jsonMode: true,
      strictMode: true
    });

    const parsed = importColumnMappingAssistResultSchema.safeParse(
      response.parsedJson ?? (response.rawText ? JSON.parse(response.rawText) : null)
    );
    if (!parsed.success) throw new Error("import_mapping_schema_invalid");

    const mergedSuggestions = params.fallbackSuggestions.map((item) => {
      const ai = parsed.data.mapping_suggestions.find((row) => row.source_column_name === item.sourceColumnName);
      if (!ai) return item;
      return {
        sourceColumnName: item.sourceColumnName,
        mappedTargetEntity: ai.mapped_target_entity,
        mappedTargetField: ai.mapped_target_field,
        confidence: ai.confidence,
        detectedType: item.detectedType,
        warning: ai.warning
      } as ImportFieldSuggestion;
    });

    await updateAiRunStatus({
      supabase: params.supabase,
      runId: aiRun.id,
      status: "completed",
      provider: response.provider,
      model: response.model,
      outputSnapshot: response.rawResponse,
      parsedResult: parsed.data,
      latencyMs: response.latencyMs,
      resultSource: "provider",
      completedAt: nowIso()
    });

    return {
      suggestions: mergedSuggestions,
      usedFallback: false,
      fallbackReason: null
    };
  } catch (error) {
    const reason = error instanceof Error ? error.message : "import_mapping_assist_failed";
    if (!isRuleFallbackEnabled()) {
      await updateAiRunStatus({
        supabase: params.supabase,
        runId: aiRun.id,
        status: "failed",
        errorMessage: reason,
        completedAt: nowIso()
      });
      throw error;
    }

    await updateAiRunStatus({
      supabase: params.supabase,
      runId: aiRun.id,
      status: "completed",
      model: "rule-fallback",
      outputSnapshot: {
        fallback: true,
        reason
      },
      parsedResult: {
        mapping_suggestions: params.fallbackSuggestions.map((item) => ({
          source_column_name: item.sourceColumnName,
          mapped_target_entity: item.mappedTargetEntity,
          mapped_target_field: item.mappedTargetField,
          confidence: item.confidence,
          warning: item.warning
        })),
        warnings: params.fallbackSuggestions.flatMap((item) => (item.warning ? [item.warning] : []))
      },
      resultSource: "fallback",
      fallbackReason: reason,
      errorMessage: reason,
      completedAt: nowIso()
    });

    return {
      suggestions: params.fallbackSuggestions,
      usedFallback: true,
      fallbackReason: reason
    };
  }
}

export async function detectImportMapping(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  jobId: string;
  enableAiAssist?: boolean;
}): Promise<{
  suggestions: ImportFieldSuggestion[];
  usedFallback: boolean;
  fallbackReason: string | null;
}> {
  const columnsRes = await params.supabase
    .from("import_job_columns")
    .select("*")
    .eq("org_id", params.orgId)
    .eq("import_job_id", params.jobId)
    .order("source_column_index", { ascending: true });
  if (columnsRes.error) throw new Error(columnsRes.error.message);

  const rowsRes = await params.supabase
    .from("import_job_rows")
    .select("raw_payload")
    .eq("org_id", params.orgId)
    .eq("import_job_id", params.jobId)
    .order("source_row_no", { ascending: true })
    .limit(10);
  if (rowsRes.error) throw new Error(rowsRes.error.message);

  const sourceColumns = ((columnsRes.data ?? []) as Database["public"]["Tables"]["import_job_columns"]["Row"][]).map(
    (row: Database["public"]["Tables"]["import_job_columns"]["Row"]) => row.source_column_name
  );
  const fallbackSuggestions = buildRuleBasedMappingSuggestions(sourceColumns);
  const sampleRows = ((rowsRes.data ?? []) as Array<{ raw_payload: unknown }>).map(
    (row: { raw_payload: unknown }) => (row.raw_payload as Record<string, unknown>) ?? {}
  );

  let result = {
    suggestions: fallbackSuggestions,
    usedFallback: false,
    fallbackReason: null as string | null
  };

  if (params.enableAiAssist) {
    result = await runAiMappingAssist({
      supabase: params.supabase,
      orgId: params.orgId,
      actorUserId: params.actorUserId,
      jobId: params.jobId,
      sourceColumns,
      sampleRows,
      fallbackSuggestions
    });
  }

  await replaceImportColumns({
    supabase: params.supabase,
    orgId: params.orgId,
    jobId: params.jobId,
    columns: result.suggestions.map((item, index) => ({
      org_id: params.orgId,
      import_job_id: params.jobId,
      source_column_name: item.sourceColumnName,
      source_column_index: index,
      detected_type: item.detectedType,
      mapped_target_entity: item.mappedTargetEntity,
      mapped_target_field: item.mappedTargetField,
      mapping_confidence: item.confidence,
      normalization_rule: {}
    }))
  });

  await appendImportAuditEvent({
    supabase: params.supabase,
    orgId: params.orgId,
    jobId: params.jobId,
    actorUserId: params.actorUserId,
    eventType: "mapping_saved",
    eventSummary: "Mapping suggestions generated",
    eventPayload: {
      used_fallback: result.usedFallback,
      fallback_reason: result.fallbackReason
    }
  });

  return result;
}

export async function saveImportMapping(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  jobId: string;
  mapping: Array<{
    columnId: string;
    mappedTargetEntity: Database["public"]["Enums"]["import_entity_type"] | null;
    mappedTargetField: string | null;
    normalizationRule?: Record<string, unknown>;
  }>;
}): Promise<void> {
  for (const item of params.mapping) {
    const res = await params.supabase
      .from("import_job_columns")
      .update({
        mapped_target_entity: item.mappedTargetEntity,
        mapped_target_field: item.mappedTargetField,
        normalization_rule: item.normalizationRule ?? {}
      })
      .eq("org_id", params.orgId)
      .eq("import_job_id", params.jobId)
      .eq("id", item.columnId);
    if (res.error) throw new Error(res.error.message);
  }

  await params.supabase
    .from("import_jobs")
    .update({
      job_status: "mapping"
    })
    .eq("id", params.jobId);

  await appendImportAuditEvent({
    supabase: params.supabase,
    orgId: params.orgId,
    jobId: params.jobId,
    actorUserId: params.actorUserId,
    eventType: "mapping_saved",
    eventSummary: "Manual mapping saved"
  });
}

