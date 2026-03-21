import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { compileBehaviorQualitySnapshot } from "@/services/behavior-quality-service";
import { getActivePromptVersion } from "@/services/ai-prompt-service";
import { createAiRun, updateAiRunStatus } from "@/services/ai-run-service";
import {
  applyManagerActionPreference,
  buildManagerVisibilityRuntimeContext,
  buildPromptAugmentationContext,
  buildResolvedOrgRuntimeConfig,
  summarizeResolvedIndustryTemplateContext
} from "@/services/template-org-runtime-bridge-service";
import { managerQualityInsightResultSchema, type AiScenario, type ManagerQualityInsightResult } from "@/types/ai";
import type { QualityPeriodType } from "@/types/quality";

type DbClient = ServerSupabaseClient;

interface SalesProfileLite {
  id: string;
  display_name: string;
}

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

function fallbackInsight(params: {
  rows: Array<{
    userId: string;
    userName: string;
    activityQualityScore: number;
    shallowActivityRatio: number;
    highRiskUnhandledCount: number;
  }>;
}): ManagerQualityInsightResult {
  const sorted = [...params.rows].sort((a, b) => b.activityQualityScore - a.activityQualityScore);
  const top = sorted.slice(0, 3);
  const low = sorted.slice(-3).reverse();

  return {
    executive_summary: `团队经营质量已完成规则分析。当前高质量推进人数 ${top.length}，需重点辅导人数 ${low.length}。`,
    replicable_patterns: top.map((item) => `${item.userName}：活动质量得分 ${item.activityQualityScore.toFixed(1)}`),
    needs_coaching: low.map((item) => ({
      user_id: item.userId,
      user_name: item.userName,
      reason: `浅层忙碌占比 ${(item.shallowActivityRatio * 100).toFixed(0)}%，高风险未处理 ${item.highRiskUnhandledCount} 个`,
      priority: item.highRiskUnhandledCount >= 2 ? "high" : "medium"
    })),
    management_actions: [
      "优先对低分销售进行跟进记录质量辅导",
      "复盘高质量销售的可复制推进方法",
      "对高风险未处理客户安排经理协同"
    ],
    risk_warnings: low.map((item) => `${item.userName}存在推进浅层化风险`)
  };
}

export async function buildManagerQualityInsight(params: {
  supabase: DbClient;
  orgId: string;
  triggeredByUserId: string;
  periodType?: QualityPeriodType;
}): Promise<{
  periodType: QualityPeriodType;
  periodStart: string;
  periodEnd: string;
  userRows: Array<{
    userId: string;
    userName: string;
    assignedCustomerCount: number;
    activeCustomerCount: number;
    followupCount: number;
    onTimeFollowupRate: number;
    overdueFollowupRate: number;
    followupCompletenessScore: number;
    stageProgressionScore: number;
    riskResponseScore: number;
    highValueFocusScore: number;
    activityQualityScore: number;
    shallowActivityRatio: number;
    stalledCustomerCount: number;
    highRiskUnhandledCount: number;
  }>;
  aiInsight: ManagerQualityInsightResult;
  usedFallback: boolean;
}> {
  const periodType = params.periodType ?? "weekly";
  const { periodStart, periodEnd } = getPeriodRange(periodType);

  const { data: profilesRaw, error: profilesError } = await params.supabase
    .from("profiles")
    .select("id, display_name")
    .eq("org_id", params.orgId)
    .eq("role", "sales")
    .eq("is_active", true);

  if (profilesError) throw new Error(profilesError.message);
  const profiles = (profilesRaw ?? []) as SalesProfileLite[];

  const snapshots = await Promise.all(
    profiles.map((profile) =>
      compileBehaviorQualitySnapshot({
        supabase: params.supabase,
        orgId: params.orgId,
        userId: profile.id,
        periodType,
        periodStart,
        periodEnd
      })
    )
  );

  const rows = snapshots.map((item) => {
    const profile = profiles.find((p) => p.id === item.userId);
    return {
      userId: item.userId,
      userName: profile?.display_name ?? "Unknown",
      assignedCustomerCount: item.assignedCustomerCount,
      activeCustomerCount: item.activeCustomerCount,
      followupCount: item.followupCount,
      onTimeFollowupRate: item.onTimeFollowupRate,
      overdueFollowupRate: item.overdueFollowupRate,
      followupCompletenessScore: item.followupCompletenessScore,
      stageProgressionScore: item.stageProgressionScore,
      riskResponseScore: item.riskResponseScore,
      highValueFocusScore: item.highValueFocusScore,
      activityQualityScore: item.activityQualityScore,
      shallowActivityRatio: item.shallowActivityRatio,
      stalledCustomerCount: item.stalledCustomerCount,
      highRiskUnhandledCount: item.highRiskUnhandledCount
    };
  });
  const runtimeTemplateContext = await buildResolvedOrgRuntimeConfig({
    supabase: params.supabase,
    orgId: params.orgId
  });
  const runtimeVisibilityContext = buildManagerVisibilityRuntimeContext({
    context: runtimeTemplateContext
  });
  const runtimePromptAugmentation = buildPromptAugmentationContext({
    scenario: "manager_quality_insight",
    context: runtimeTemplateContext
  });

  const scenario: AiScenario = "manager_quality_insight";
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
    triggeredByUserId: params.triggeredByUserId,
    triggerSource: "manager_review",
    scenario,
    provider: provider.id,
    model,
    promptVersion: prompt.version,
    inputSnapshot: {
      period_type: periodType,
      period_start: periodStart,
      period_end: periodEnd,
      rows,
      runtime_template_context: summarizeResolvedIndustryTemplateContext(runtimeTemplateContext),
      runtime_preference_overlay: {
        manager_focus_metrics: runtimeVisibilityContext.managerFocusMetricPriority,
        recommended_action_priority: runtimeVisibilityContext.recommendedActionPriority
      }
    }
  });

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "running",
    startedAt: nowIso()
  });

  let insight: ManagerQualityInsightResult;
  let usedFallback = false;
  let fallbackReason: string | null = null;
  let outputSnapshot: Record<string, unknown> = {};
  let responseModel = model;
  let responseProvider = provider.id;
  let latencyMs: number | null = null;

  try {
    if (!provider.isConfigured()) {
      throw new Error("provider_not_configured");
    }

    const response = await provider.chatCompletion({
      scenario,
      model,
      systemPrompt: prompt.systemPrompt,
      developerPrompt: `${prompt.developerPrompt}${runtimePromptAugmentation ? `\n\n${runtimePromptAugmentation}` : ""}\n\nOutput schema:\n${JSON.stringify(prompt.outputSchema)}`,
      userPrompt: JSON.stringify({
        scenario,
        payload: {
          period_type: periodType,
          period_start: periodStart,
          period_end: periodEnd,
          rows,
          runtime_template_context: summarizeResolvedIndustryTemplateContext(runtimeTemplateContext),
          runtime_preference_overlay: {
            manager_focus_metrics: runtimeVisibilityContext.managerFocusMetricPriority,
            recommended_action_priority: runtimeVisibilityContext.recommendedActionPriority
          }
        }
      }),
      jsonMode: true,
      strictMode: true,
      useReasonerModel: true
    });

    responseModel = response.model;
    responseProvider = response.provider;
    latencyMs = response.latencyMs;
    outputSnapshot = response.rawResponse;

    if (response.error) {
      throw new Error(response.error);
    }

    const parsed = managerQualityInsightResultSchema.safeParse(
      response.parsedJson ?? (response.rawText ? JSON.parse(response.rawText) : null)
    );
    if (!parsed.success) throw new Error("manager_quality_insight_schema_invalid");
    insight = parsed.data;
  } catch (error) {
    if (!isRuleFallbackEnabled()) {
      const message = error instanceof Error ? error.message : "manager_quality_insight_failed";
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
    fallbackReason = error instanceof Error ? error.message : "manager_quality_insight_fallback";
    insight = fallbackInsight({ rows });
    responseModel = "rule-fallback";
    outputSnapshot = { fallback: true, reason: fallbackReason, rows };
  }
  const focusSuffix =
    runtimeVisibilityContext.managerFocusMetricPriority.length > 0
      ? ` Focus metrics: ${runtimeVisibilityContext.managerFocusMetricPriority.slice(0, 3).join(", ")}.`
      : "";
  insight = {
    ...insight,
    executive_summary: `${insight.executive_summary}${focusSuffix}`,
    management_actions: applyManagerActionPreference({
      actions: insight.management_actions,
      context: runtimeVisibilityContext,
      limit: 2
    })
  };

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "completed",
    provider: responseProvider,
    model: responseModel,
    outputSnapshot,
    parsedResult: {
      insight,
      user_rows_count: rows.length
    },
    latencyMs,
    resultSource: usedFallback ? "fallback" : "provider",
    fallbackReason,
    completedAt: nowIso(),
    errorMessage: usedFallback ? fallbackReason : null
  });

  return {
    periodType,
    periodStart,
    periodEnd,
    userRows: rows,
    aiInsight: insight,
    usedFallback
  };
}
