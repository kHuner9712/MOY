import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import { buildFallbackImportReviewSummary } from "@/lib/import-review-fallback";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { getActivePromptVersion } from "@/services/ai-prompt-service";
import { createAiRun, updateAiRunStatus } from "@/services/ai-run-service";
import { appendImportAuditEvent, getImportJob, updateImportJob } from "@/services/import-job-service";
import { importReviewSummaryResultSchema } from "@/types/ai";

import type { Database } from "@/types/database";

type DbClient = ServerSupabaseClient;

type ImportReviewSummary = {
  summary: string;
  issues: string[];
  recommended_cleanup: string[];
  recommended_next_steps: string[];
};

type ImportDetailSnapshot = Database["public"]["Tables"]["import_jobs"]["Update"]["detail_snapshot"];

function nowIso(): string {
  return new Date().toISOString();
}

function mergeDetailSnapshot(existing: Record<string, unknown>, patch: Record<string, unknown>): Record<string, unknown> {
  return {
    ...existing,
    ...patch
  };
}

function asImportDetailSnapshot(value: Record<string, unknown>): ImportDetailSnapshot {
  return value as unknown as ImportDetailSnapshot;
}

async function buildRuleSummaryInput(params: {
  supabase: DbClient;
  orgId: string;
  jobId: string;
}) {
  const [job, rowsRes] = await Promise.all([
    getImportJob({ supabase: params.supabase, orgId: params.orgId, jobId: params.jobId }),
    params.supabase
      .from("import_job_rows")
      .select("row_status, validation_errors")
      .eq("org_id", params.orgId)
      .eq("import_job_id", params.jobId)
  ]);

  if (rowsRes.error) throw new Error(rowsRes.error.message);

  const rows =
    (rowsRes.data ?? []) as Array<{
      row_status: Database["public"]["Enums"]["import_row_status"];
      validation_errors: unknown;
    }>;

  const errorCounter = new Map<string, number>();
  for (const row of rows) {
    if (!Array.isArray(row.validation_errors)) continue;
    for (const issue of row.validation_errors) {
      if (typeof issue !== "string") continue;
      errorCounter.set(issue, (errorCounter.get(issue) ?? 0) + 1);
    }
  }

  const commonErrors = Array.from(errorCounter.entries())
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8)
    .map(([name, count]) => `${name} x${count}`);

  return {
    job,
    rows,
    commonErrors
  };
}

export async function generateImportReviewSummary(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  jobId: string;
  forceFallback?: boolean;
}): Promise<{
  review: ImportReviewSummary;
  runId: string;
  usedFallback: boolean;
  fallbackReason: string | null;
}> {
  const { job, rows, commonErrors } = await buildRuleSummaryInput({
    supabase: params.supabase,
    orgId: params.orgId,
    jobId: params.jobId
  });

  const fallback = buildFallbackImportReviewSummary({
    importType: job.importType,
    totalRows: job.totalRows,
    importedRows: job.importedRows,
    invalidRows: job.invalidRows,
    duplicateRows: job.duplicateRows,
    mergedRows: job.mergedRows,
    skippedRows: job.skippedRows,
    commonErrors
  });

  const provider = getAiProvider();
  const model = provider.getDefaultModel({ reasoning: false });
  const prompt = await getActivePromptVersion({
    supabase: params.supabase,
    orgId: params.orgId,
    scenario: "import_review_summary",
    providerId: provider.id
  });

  const run = await createAiRun({
    supabase: params.supabase,
    orgId: params.orgId,
    triggerSource: "manual",
    scenario: "import_review_summary",
    provider: provider.id,
    model,
    promptVersion: prompt.version,
    triggeredByUserId: params.actorUserId,
    inputSnapshot: {
      import_job: job,
      common_errors: commonErrors,
      row_status_distribution: rows.reduce<Record<string, number>>((acc, row) => {
        acc[row.row_status] = (acc[row.row_status] ?? 0) + 1;
        return acc;
      }, {})
    }
  });

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "running",
    startedAt: nowIso()
  });

  let review: ImportReviewSummary = fallback;
  let usedFallback = false;
  let fallbackReason: string | null = null;
  let outputSnapshot: Record<string, unknown> = {
    fallback: true,
    reason: "not_started"
  };
  let responseModel = model;

  try {
    if (params.forceFallback) throw new Error("import_review_forced_fallback");
    if (!provider.isConfigured()) throw new Error("provider_not_configured");

    const response = await provider.chatCompletion({
      scenario: "import_review_summary",
      model,
      systemPrompt: prompt.systemPrompt,
      developerPrompt: `${prompt.developerPrompt}\n\nOutput schema:\n${JSON.stringify(prompt.outputSchema)}`,
      userPrompt: JSON.stringify({
        import_job: job,
        common_errors: commonErrors,
        fallback_reference: fallback
      }),
      jsonMode: true,
      strictMode: true
    });

    if (response.error) throw new Error(response.error);
    const candidate = response.parsedJson ?? (response.rawText ? JSON.parse(response.rawText) : null);
    const parsed = importReviewSummaryResultSchema.safeParse(candidate);
    if (!parsed.success) throw new Error("import_review_summary_schema_invalid");

    review = parsed.data;
    outputSnapshot = response.rawResponse;
    responseModel = response.model;
  } catch (error) {
    if (!isRuleFallbackEnabled()) {
      const message = error instanceof Error ? error.message : "import_review_summary_failed";
      await updateAiRunStatus({
        supabase: params.supabase,
        runId: run.id,
        status: "failed",
        provider: provider.id,
        model,
        errorMessage: message,
        completedAt: nowIso()
      });
      throw error;
    }

    usedFallback = true;
    fallbackReason = error instanceof Error ? error.message : "import_review_summary_fallback";
    outputSnapshot = {
      fallback: true,
      reason: fallbackReason,
      payload: review
    };
    responseModel = "rule-fallback";
  }

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "completed",
    provider: provider.id,
    model: responseModel,
    outputSnapshot,
    parsedResult: review as unknown as Record<string, unknown>,
    resultSource: usedFallback ? "fallback" : "provider",
    fallbackReason,
    errorMessage: usedFallback ? fallbackReason : null,
    completedAt: nowIso()
  });

  const nextDetail = mergeDetailSnapshot(job.detailSnapshot ?? {}, {
    import_review: review,
    import_review_fallback: usedFallback,
    import_review_fallback_reason: fallbackReason,
    import_review_run_id: run.id
  });

  await updateImportJob({
    supabase: params.supabase,
    jobId: params.jobId,
    patch: {
      summary: review.summary,
      detail_snapshot: asImportDetailSnapshot(nextDetail)
    }
  });

  await appendImportAuditEvent({
    supabase: params.supabase,
    orgId: params.orgId,
    jobId: params.jobId,
    actorUserId: params.actorUserId,
    eventType: "completed",
    eventSummary: usedFallback ? "Import review generated via fallback" : "Import review generated by AI",
    eventPayload: {
      run_id: run.id,
      used_fallback: usedFallback,
      fallback_reason: fallbackReason
    }
  });

  return {
    review,
    runId: run.id,
    usedFallback,
    fallbackReason
  };
}

