import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import { buildFallbackUserCoachingReport } from "@/lib/coaching-fallback";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { compileBehaviorQualitySnapshot } from "@/services/behavior-quality-service";
import { getActivePromptVersion } from "@/services/ai-prompt-service";
import { createAiRun, updateAiRunStatus } from "@/services/ai-run-service";
import { buildManagerQualityInsight } from "@/services/manager-insight-service";
import { mapCoachingReportRow } from "@/services/mappers";
import { getUserMemoryProfile, listUserMemoryItems } from "@/services/user-memory-service";
import { userCoachingReportResultSchema, type AiScenario, type UserCoachingReportResult } from "@/types/ai";
import type { Database } from "@/types/database";
import type { CoachingReport, CoachingReportScope, QualityPeriodType } from "@/types/quality";

type DbClient = ServerSupabaseClient;
type CoachingReportRow = Database["public"]["Tables"]["coaching_reports"]["Row"];

function nowIso(): string {
  return new Date().toISOString();
}

function getPeriodRange(periodType: QualityPeriodType): { periodStart: string; periodEnd: string } {
  const end = new Date();
  const start = new Date(end);
  if (periodType === "monthly") {
    start.setDate(end.getDate() - 29);
  } else if (periodType === "weekly") {
    start.setDate(end.getDate() - 6);
  }
  return {
    periodStart: start.toISOString().slice(0, 10),
    periodEnd: end.toISOString().slice(0, 10)
  };
}

export async function generateCoachingReport(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  actorRole: "sales" | "manager";
  scope: CoachingReportScope;
  periodType?: QualityPeriodType;
  targetUserId?: string | null;
}): Promise<CoachingReport> {
  const periodType = params.periodType ?? "weekly";
  const { periodStart, periodEnd } = getPeriodRange(periodType);
  const reportScope = params.scope;
  const targetUserId = reportScope === "user" ? params.targetUserId ?? params.actorUserId : null;

  if (params.actorRole !== "manager" && reportScope !== "user") {
    throw new Error("sales_can_only_generate_personal_coaching_report");
  }
  if (params.actorRole !== "manager" && targetUserId !== params.actorUserId) {
    throw new Error("sales_can_only_generate_self_coaching_report");
  }

  const { data: reportRaw, error: reportCreateError } = await params.supabase
    .from("coaching_reports")
    .insert({
      org_id: params.orgId,
      report_scope: reportScope,
      target_user_id: targetUserId,
      period_start: periodStart,
      period_end: periodEnd,
      status: "generating",
      generated_by: params.actorUserId
    })
    .select("*")
    .single();

  if (reportCreateError || !reportRaw) {
    throw new Error(reportCreateError?.message ?? "Failed to create coaching report");
  }
  const reportRow = reportRaw as CoachingReportRow;

  if (reportScope === "team") {
    try {
      const teamInsight = await buildManagerQualityInsight({
        supabase: params.supabase,
        orgId: params.orgId,
        triggeredByUserId: params.actorUserId,
        periodType
      });

      const { data: updatedRaw, error: updateError } = await params.supabase
        .from("coaching_reports")
        .update({
          status: "completed",
          title: `团队经营辅导报告（${periodType}）`,
          executive_summary: teamInsight.aiInsight.executive_summary,
          strengths: teamInsight.aiInsight.replicable_patterns,
          weaknesses: teamInsight.aiInsight.needs_coaching.map((item) => `${item.user_name}：${item.reason}`),
          coaching_actions: teamInsight.aiInsight.management_actions,
          replicable_patterns: teamInsight.aiInsight.replicable_patterns,
          risk_warnings: teamInsight.aiInsight.risk_warnings,
          content_markdown: [
            `# 团队经营辅导报告（${periodType}）`,
            "",
            `- 周期：${teamInsight.periodStart} ~ ${teamInsight.periodEnd}`,
            "",
            `## 执行摘要`,
            teamInsight.aiInsight.executive_summary,
            "",
            "## 可复制打法",
            ...teamInsight.aiInsight.replicable_patterns.map((item) => `- ${item}`),
            "",
            "## 需要辅导",
            ...teamInsight.aiInsight.needs_coaching.map((item) => `- ${item.user_name}（${item.priority}）：${item.reason}`),
            "",
            "## 管理动作",
            ...teamInsight.aiInsight.management_actions.map((item) => `- ${item}`)
          ].join("\n"),
          source_snapshot: {
            period_type: teamInsight.periodType,
            period_start: teamInsight.periodStart,
            period_end: teamInsight.periodEnd,
            rows: teamInsight.userRows,
            used_fallback: teamInsight.usedFallback
          }
        })
        .eq("id", reportRow.id)
        .select("*")
        .single();

      if (updateError || !updatedRaw) {
        throw new Error(updateError?.message ?? "Failed to update team coaching report");
      }
      return mapCoachingReportRow(updatedRaw as CoachingReportRow);
    } catch (error) {
      await params.supabase.from("coaching_reports").update({ status: "failed" }).eq("id", reportRow.id);
      throw error;
    }
  }

  const targetProfileQuery = await params.supabase
    .from("profiles")
    .select("id, display_name")
    .eq("id", targetUserId!)
    .eq("org_id", params.orgId)
    .maybeSingle();
  if (targetProfileQuery.error || !targetProfileQuery.data) {
    throw new Error(targetProfileQuery.error?.message ?? "target_user_not_found");
  }
  const targetName = (targetProfileQuery.data as { display_name: string }).display_name;

  const quality = await compileBehaviorQualitySnapshot({
    supabase: params.supabase,
    orgId: params.orgId,
    userId: targetUserId!,
    periodType,
    periodStart,
    periodEnd
  });
  const memoryProfile = await getUserMemoryProfile({
    supabase: params.supabase,
    orgId: params.orgId,
    userId: targetUserId!
  });
  const memoryItems = await listUserMemoryItems({
    supabase: params.supabase,
    orgId: params.orgId,
    userId: targetUserId!,
    includeHidden: false,
    limit: 20
  });

  const scenario: AiScenario = "user_coaching_report";
  const provider = getAiProvider();
  const model = provider.getDefaultModel({ reasoning: true });
  const prompt = await getActivePromptVersion({
    supabase: params.supabase,
    orgId: params.orgId,
    scenario,
    providerId: provider.id
  });

  const run = await createAiRun({
    supabase: params.supabase,
    orgId: params.orgId,
    customerId: null,
    followupId: null,
    triggeredByUserId: params.actorUserId,
    triggerSource: params.actorRole === "manager" ? "manager_review" : "manual",
    scenario,
    provider: provider.id,
    model,
    promptVersion: prompt.version,
    inputSnapshot: {
      target_user_id: targetUserId,
      period_type: periodType,
      period_start: periodStart,
      period_end: periodEnd,
      quality_snapshot: quality.metricsSnapshot,
      memory_profile: memoryProfile,
      memory_items: memoryItems.slice(0, 10)
    }
  });

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "running",
    startedAt: nowIso()
  });

  let result: UserCoachingReportResult;
  let usedFallback = false;
  let fallbackReason: string | null = null;
  let outputSnapshot: Record<string, unknown> = {};
  let responseProvider = provider.id;
  let responseModel = model;
  let latencyMs: number | null = null;

  try {
    if (!provider.isConfigured()) {
      throw new Error("provider_not_configured");
    }

    const response = await provider.chatCompletion({
      scenario,
      model,
      systemPrompt: prompt.systemPrompt,
      developerPrompt: `${prompt.developerPrompt}\n\nOutput schema:\n${JSON.stringify(prompt.outputSchema)}`,
      userPrompt: JSON.stringify({
        scenario,
        payload: {
          target_user_name: targetName,
          period_type: periodType,
          period_start: periodStart,
          period_end: periodEnd,
          quality_snapshot: quality.metricsSnapshot,
          memory_profile: memoryProfile,
          memory_items: memoryItems.slice(0, 10)
        }
      }),
      jsonMode: true,
      strictMode: true,
      useReasonerModel: true
    });

    responseProvider = response.provider;
    responseModel = response.model;
    latencyMs = response.latencyMs;
    outputSnapshot = response.rawResponse;

    if (response.error) throw new Error(response.error);
    const parsed = userCoachingReportResultSchema.safeParse(
      response.parsedJson ?? (response.rawText ? JSON.parse(response.rawText) : null)
    );
    if (!parsed.success) throw new Error("user_coaching_report_schema_invalid");
    result = parsed.data;
  } catch (error) {
    if (!isRuleFallbackEnabled()) {
      const message = error instanceof Error ? error.message : "user_coaching_report_failed";
      await updateAiRunStatus({
        supabase: params.supabase,
        runId: run.id,
        status: "failed",
        provider: provider.id,
        model,
        errorMessage: message,
        completedAt: nowIso()
      });
      await params.supabase.from("coaching_reports").update({ status: "failed" }).eq("id", reportRow.id);
      throw error;
    }

    usedFallback = true;
    fallbackReason = error instanceof Error ? error.message : "user_coaching_report_fallback";
    result = buildFallbackUserCoachingReport({
      userName: targetName,
      periodStart,
      periodEnd,
      quality: {
        activityQualityScore: quality.activityQualityScore,
        shallowActivityRatio: quality.shallowActivityRatio,
        riskResponseScore: quality.riskResponseScore,
        highRiskUnhandledCount: quality.highRiskUnhandledCount,
        followupCompletenessScore: quality.followupCompletenessScore
      }
    });
    responseModel = "rule-fallback";
    outputSnapshot = {
      fallback: true,
      reason: fallbackReason,
      quality: quality.metricsSnapshot
    };
  }

  const { data: updatedRaw, error: updateError } = await params.supabase
    .from("coaching_reports")
    .update({
      status: "completed",
      title: result.title,
      executive_summary: result.executive_summary,
      strengths: result.strengths,
      weaknesses: result.weaknesses,
      coaching_actions: result.coaching_actions,
      replicable_patterns: result.replicable_patterns,
      risk_warnings: result.risk_warnings,
      content_markdown: result.content_markdown,
      source_snapshot: {
        period_type: periodType,
        period_start: periodStart,
        period_end: periodEnd,
        quality_snapshot: quality.metricsSnapshot,
        memory_profile: memoryProfile,
        memory_items: memoryItems.slice(0, 10),
        used_fallback: usedFallback
      }
    })
    .eq("id", reportRow.id)
    .select("*")
    .single();

  if (updateError || !updatedRaw) {
    await params.supabase.from("coaching_reports").update({ status: "failed" }).eq("id", reportRow.id);
    throw new Error(updateError?.message ?? "Failed to update coaching report");
  }

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "completed",
    provider: responseProvider,
    model: responseModel,
    outputSnapshot,
    parsedResult: {
      coaching_report_id: reportRow.id,
      target_user_id: targetUserId,
      used_fallback: usedFallback
    },
    latencyMs,
    resultSource: usedFallback ? "fallback" : "provider",
    fallbackReason,
    completedAt: nowIso(),
    errorMessage: usedFallback ? fallbackReason : null
  });

  return mapCoachingReportRow(updatedRaw as CoachingReportRow);
}

export async function listCoachingReports(params: {
  supabase: DbClient;
  orgId: string;
  targetUserId?: string;
  scope?: CoachingReportScope;
  limit?: number;
}): Promise<CoachingReport[]> {
  let query = params.supabase
    .from("coaching_reports")
    .select("*")
    .eq("org_id", params.orgId)
    .order("created_at", { ascending: false })
    .limit(params.limit ?? 40);

  if (params.targetUserId) query = query.eq("target_user_id", params.targetUserId);
  if (params.scope) query = query.eq("report_scope", params.scope);

  const { data, error } = await query;
  if (error) throw new Error(error.message);
  const rows = (data ?? []) as CoachingReportRow[];
  return rows.map((item) => mapCoachingReportRow(item));
}
