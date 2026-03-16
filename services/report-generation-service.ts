import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import { buildFallbackReport } from "@/lib/report-fallback";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { getActivePromptVersion } from "@/services/ai-prompt-service";
import { createAiRun, updateAiRunStatus } from "@/services/ai-run-service";
import { mapGeneratedReportRow } from "@/services/mappers";
import { reportGenerationResultSchema, type AiScenario, type ReportGenerationResult } from "@/types/ai";
import type { Database } from "@/types/database";
import type { GeneratedReport, GenerateReportInput, ReportType } from "@/types/report";

type DbClient = ServerSupabaseClient;
type ProfileRow = Database["public"]["Tables"]["profiles"]["Row"];
type GeneratedReportRow = Database["public"]["Tables"]["generated_reports"]["Row"];

function nowIso(): string {
  return new Date().toISOString();
}

function reportTypeToScenario(reportType: ReportType): AiScenario {
  switch (reportType) {
    case "sales_daily":
      return "sales_daily_report";
    case "sales_weekly":
      return "sales_weekly_report";
    case "manager_daily":
      return "manager_daily_report";
    case "manager_weekly":
      return "manager_weekly_report";
    default:
      return "manager_summary";
  }
}

function parseDateBoundary(params: { periodStart: string; periodEnd: string }): { fromIso: string; toIso: string } {
  const start = new Date(`${params.periodStart}T00:00:00.000Z`);
  const end = new Date(`${params.periodEnd}T23:59:59.999Z`);
  return {
    fromIso: start.toISOString(),
    toIso: end.toISOString()
  };
}

async function collectReportSnapshots(params: {
  supabase: DbClient;
  profile: ProfileRow;
  input: GenerateReportInput;
}): Promise<{
  metricsSnapshot: Record<string, unknown>;
  sourceSnapshot: Record<string, unknown>;
}> {
  const { fromIso, toIso } = parseDateBoundary({
    periodStart: params.input.periodStart,
    periodEnd: params.input.periodEnd
  });

  const targetUserId = params.input.targetUserId ?? params.profile.id;
  const isManager = params.profile.role === "manager";
  const teamScope = isManager && (params.input.reportType === "manager_daily" || params.input.reportType === "manager_weekly");

  const customerQuery = params.supabase
    .from("customers")
    .select("id, owner_id, current_stage, risk_level, created_at")
    .eq("org_id", params.profile.org_id)
    .gte("created_at", fromIso)
    .lte("created_at", toIso);

  const followupQuery = params.supabase
    .from("followups")
    .select("id, customer_id, owner_id, draft_status, created_at")
    .eq("org_id", params.profile.org_id)
    .gte("created_at", fromIso)
    .lte("created_at", toIso);

  const alertQuery = params.supabase
    .from("alerts")
    .select("id, owner_id, severity, status, created_at")
    .eq("org_id", params.profile.org_id)
    .gte("created_at", fromIso)
    .lte("created_at", toIso);

  const commQuery = params.supabase
    .from("communication_inputs")
    .select("id, owner_id, extraction_status, created_at")
    .eq("org_id", params.profile.org_id)
    .gte("created_at", fromIso)
    .lte("created_at", toIso);

  const opportunityQuery = params.supabase
    .from("opportunities")
    .select("id, owner_id, stage, risk_level, amount, updated_at")
    .eq("org_id", params.profile.org_id)
    .gte("updated_at", fromIso)
    .lte("updated_at", toIso);

  const aiRunQuery = params.supabase
    .from("ai_runs")
    .select("id, triggered_by_user_id, status, result_source, scenario, created_at")
    .eq("org_id", params.profile.org_id)
    .gte("created_at", fromIso)
    .lte("created_at", toIso);

  const [customersRes, followupsRes, alertsRes, commRes, opportunitiesRes, aiRunsRes] = await Promise.all([
    customerQuery,
    followupQuery,
    alertQuery,
    commQuery,
    opportunityQuery,
    aiRunQuery
  ]);

  if (customersRes.error) throw new Error(customersRes.error.message);
  if (followupsRes.error) throw new Error(followupsRes.error.message);
  if (alertsRes.error) throw new Error(alertsRes.error.message);
  if (commRes.error) throw new Error(commRes.error.message);
  if (opportunitiesRes.error) throw new Error(opportunitiesRes.error.message);
  if (aiRunsRes.error) throw new Error(aiRunsRes.error.message);

  const customers = (customersRes.data ?? []) as Array<any>;
  const followups = (followupsRes.data ?? []) as Array<any>;
  const alerts = (alertsRes.data ?? []) as Array<any>;
  const commInputs = (commRes.data ?? []) as Array<any>;
  const opportunities = (opportunitiesRes.data ?? []) as Array<any>;
  const aiRuns = (aiRunsRes.data ?? []) as Array<any>;

  const scopedCustomers = teamScope ? customers : customers.filter((item) => item.owner_id === targetUserId);
  const scopedFollowups = teamScope ? followups : followups.filter((item) => item.owner_id === targetUserId);
  const scopedAlerts = teamScope ? alerts : alerts.filter((item) => item.owner_id === targetUserId);
  const scopedComm = teamScope ? commInputs : commInputs.filter((item) => item.owner_id === targetUserId);
  const scopedOpps = teamScope ? opportunities : opportunities.filter((item) => item.owner_id === targetUserId);
  const scopedRuns = teamScope ? aiRuns : aiRuns.filter((item) => item.triggered_by_user_id === targetUserId);

  const metricsSnapshot: Record<string, unknown> = {
    new_customers: scopedCustomers.length,
    followups_count: scopedFollowups.length,
    communication_inputs_count: scopedComm.length,
    extracted_failed_inputs: scopedComm.filter((item) => item.extraction_status === "failed").length,
    pending_drafts: scopedFollowups.filter((item) => item.draft_status === "draft").length,
    open_alerts: scopedAlerts.filter((item) => item.status !== "resolved").length,
    high_risk_alerts: scopedAlerts.filter((item) => item.severity === "critical" && item.status !== "resolved").length,
    high_risk_customers: scopedCustomers.filter((item) => item.risk_level === "high" && item.current_stage !== "won" && item.current_stage !== "lost").length,
    active_opportunities: scopedOpps.filter((item) => item.stage !== "won" && item.stage !== "lost").length,
    total_opportunity_amount: scopedOpps.reduce((sum, item) => sum + Number(item.amount ?? 0), 0),
    ai_runs_count: scopedRuns.length,
    ai_fallback_count: scopedRuns.filter((item) => item.result_source === "fallback").length
  };

  const sourceSnapshot: Record<string, unknown> = {
    period: {
      start: params.input.periodStart,
      end: params.input.periodEnd
    },
    scope: {
      report_type: params.input.reportType,
      team_scope: teamScope,
      target_user_id: targetUserId
    },
    sample_lists: {
      top_risk_customer_ids: scopedCustomers
        .filter((item) => item.risk_level === "high")
        .slice(0, 10)
        .map((item) => item.id),
      pending_draft_followup_ids: scopedFollowups
        .filter((item) => item.draft_status === "draft")
        .slice(0, 10)
        .map((item) => item.id),
      open_alert_ids: scopedAlerts
        .filter((item) => item.status !== "resolved")
        .slice(0, 10)
        .map((item) => item.id)
    }
  };

  return {
    metricsSnapshot,
    sourceSnapshot
  };
}

export async function generateReport(params: {
  supabase: DbClient;
  profile: ProfileRow;
  input: GenerateReportInput;
}): Promise<GeneratedReport> {
  const scenario = reportTypeToScenario(params.input.reportType);
  const provider = getAiProvider();

  const { data: reportRowRaw, error: reportInsertError } = await params.supabase
    .from("generated_reports")
    .insert({
      org_id: params.profile.org_id,
      report_type: params.input.reportType,
      target_user_id: params.input.targetUserId ?? null,
      scope_type: params.input.scopeType ?? "self",
      period_start: params.input.periodStart,
      period_end: params.input.periodEnd,
      status: "generating",
      generated_by: params.profile.id
    })
    .select("*")
    .single();

  if (reportInsertError || !reportRowRaw) {
    throw new Error(reportInsertError?.message ?? "Failed to create generated report row");
  }

  const reportRow = reportRowRaw as GeneratedReportRow;

  const snapshots = await collectReportSnapshots({
    supabase: params.supabase,
    profile: params.profile,
    input: params.input
  });

  const prompt = await getActivePromptVersion({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    scenario,
    providerId: provider.id
  });

  const run = await createAiRun({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    customerId: null,
    followupId: null,
    triggeredByUserId: params.profile.id,
    triggerSource: "manual",
    scenario,
    provider: provider.id,
    model: provider.getDefaultModel({ reasoning: true }),
    promptVersion: prompt.version,
    inputSnapshot: {
      report_type: params.input.reportType,
      period_start: params.input.periodStart,
      period_end: params.input.periodEnd,
      metrics_snapshot: snapshots.metricsSnapshot,
      source_snapshot: snapshots.sourceSnapshot
    }
  });

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "running",
    startedAt: nowIso()
  });

  const startedAt = Date.now();

  try {
    const response = await provider.chatCompletion({
      scenario,
      model: provider.getDefaultModel({ reasoning: true }),
      systemPrompt: prompt.systemPrompt,
      developerPrompt: `${prompt.developerPrompt}\n\nOutput schema:\n${JSON.stringify(prompt.outputSchema)}`,
      userPrompt: JSON.stringify({
        scenario,
        payload: {
          report_type: params.input.reportType,
          period_start: params.input.periodStart,
          period_end: params.input.periodEnd,
          metrics_snapshot: snapshots.metricsSnapshot,
          source_snapshot: snapshots.sourceSnapshot
        }
      }),
      jsonMode: true,
      strictMode: true,
      useReasonerModel: true
    });

    let parsedResult: ReportGenerationResult;
    let resultSource: "provider" | "fallback" = "provider";
    let fallbackReason: string | null = null;

    if (!response.error) {
      const candidate = response.parsedJson ?? (response.rawText ? (JSON.parse(response.rawText) as Record<string, unknown>) : null);
      const parsed = reportGenerationResultSchema.safeParse(candidate);
      if (parsed.success) {
        parsedResult = parsed.data;
      } else if (isRuleFallbackEnabled()) {
        parsedResult = buildFallbackReport({
          reportType: params.input.reportType,
          periodStart: params.input.periodStart,
          periodEnd: params.input.periodEnd,
          metricsSnapshot: snapshots.metricsSnapshot,
          sourceSnapshot: snapshots.sourceSnapshot
        });
        resultSource = "fallback";
        fallbackReason = "report_schema_invalid";
      } else {
        throw new Error("report_schema_invalid");
      }
    } else if (isRuleFallbackEnabled()) {
      parsedResult = buildFallbackReport({
        reportType: params.input.reportType,
        periodStart: params.input.periodStart,
        periodEnd: params.input.periodEnd,
        metricsSnapshot: snapshots.metricsSnapshot,
        sourceSnapshot: snapshots.sourceSnapshot
      });
      resultSource = "fallback";
      fallbackReason = response.error;
    } else {
      throw new Error(response.error);
    }

    const { data: updatedReport, error: reportUpdateError } = await params.supabase
      .from("generated_reports")
      .update({
        status: "completed",
        title: parsedResult.title,
        summary: parsedResult.summary,
        content_markdown: parsedResult.content_markdown,
        metrics_snapshot: snapshots.metricsSnapshot,
        source_snapshot: snapshots.sourceSnapshot
      })
      .eq("id", reportRow.id)
      .select("*")
      .single();

    if (reportUpdateError || !updatedReport) {
      throw new Error(reportUpdateError?.message ?? "Failed to update generated report row");
    }

    await updateAiRunStatus({
      supabase: params.supabase,
      runId: run.id,
      status: "completed",
      provider: response.provider,
      model: response.model,
      outputSnapshot: response.rawResponse,
      parsedResult: {
        report: parsedResult,
        metrics_snapshot: snapshots.metricsSnapshot,
        source_snapshot: snapshots.sourceSnapshot
      },
      latencyMs: response.latencyMs,
      resultSource,
      fallbackReason,
      completedAt: nowIso(),
      errorMessage: fallbackReason
    });

    console.info("[report.generate]", {
      org_id: params.profile.org_id,
      user_id: params.profile.id,
      scenario,
      provider: response.provider,
      model: response.model,
      status: "completed",
      duration_ms: Date.now() - startedAt,
      result_source: resultSource
    });

    return mapGeneratedReportRow(updatedReport as GeneratedReportRow);
  } catch (error) {
    const message = error instanceof Error ? error.message : "report_generation_failed";

    await updateAiRunStatus({
      supabase: params.supabase,
      runId: run.id,
      status: "failed",
      provider: provider.id,
      model: provider.getDefaultModel({ reasoning: true }),
      errorMessage: message,
      completedAt: nowIso()
    });

    await params.supabase
      .from("generated_reports")
      .update({
        status: "failed",
        summary: message,
        metrics_snapshot: snapshots.metricsSnapshot,
        source_snapshot: snapshots.sourceSnapshot
      })
      .eq("id", reportRow.id);

    console.error("[report.generate]", {
      org_id: params.profile.org_id,
      user_id: params.profile.id,
      scenario,
      provider: provider.id,
      model: provider.getDefaultModel({ reasoning: true }),
      status: "failed",
      duration_ms: Date.now() - startedAt,
      error: message
    });

    throw new Error("Report generation failed");
  }
}

export async function listReports(params: {
  supabase: DbClient;
  profile: ProfileRow;
  reportType?: ReportType;
  from?: string;
  to?: string;
  limit?: number;
}): Promise<GeneratedReport[]> {
  let query = params.supabase
    .from("generated_reports")
    .select("*")
    .eq("org_id", params.profile.org_id)
    .order("created_at", { ascending: false })
    .limit(params.limit ?? 40);

  if (params.reportType) {
    query = query.eq("report_type", params.reportType);
  }

  if (params.from) {
    query = query.gte("period_start", params.from);
  }

  if (params.to) {
    query = query.lte("period_end", params.to);
  }

  const { data, error } = await query;
  if (error) throw new Error(error.message);

  const rows = (data ?? []) as GeneratedReportRow[];
  return rows.map((item) => mapGeneratedReportRow(item));
}

export async function getReportById(params: {
  supabase: DbClient;
  profile: ProfileRow;
  reportId: string;
}): Promise<GeneratedReport | null> {
  const { data, error } = await params.supabase
    .from("generated_reports")
    .select("*")
    .eq("org_id", params.profile.org_id)
    .eq("id", params.reportId)
    .maybeSingle();

  if (error) throw new Error(error.message);
  if (!data) return null;
  return mapGeneratedReportRow(data as GeneratedReportRow);
}

