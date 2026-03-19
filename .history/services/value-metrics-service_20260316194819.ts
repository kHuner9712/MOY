import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { mapBusinessEventRow, type BusinessEventRow } from "./business-event-service";
import { mapActionOutcomeRow, type ActionOutcomeRow } from "./action-outcome-service";
import { mapWorkItemRow, type WorkItemRow } from "./work-item-service";
import type { ValueMetrics, ValueMetricsParams, ValueMetricsResult, ValueMetricsSummary, TrendResult } from "@/types/value-metrics";
import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import { getActivePromptVersion } from "./ai-prompt-service";
import { createAiRun, updateAiRunStatus } from "./ai-run-service";
import { valueMetricsSummarySchema, type ValueMetricsSummaryResult } from "@/types/ai";

type DbClient = ServerSupabaseClient;

function toISODate(date: Date): string {
  return date.toISOString().split('T')[0];
}

function getWeekStart(date: Date): Date {
  const d = new Date(date);
  const day = d.getDay();
  const diff = d.getDate() - day + (day === 0 ? -6 : 1);
  return new Date(d.setDate(diff));
}

function getPreviousWeekRange(currentWeekStart: Date): { start: Date; end: Date } {
  const start = new Date(currentWeekStart);
  start.setDate(start.getDate() - 7);
  const end = new Date(start);
  end.setDate(end.getDate() + 6);
  return { start, end };
}

export async function calculateValueMetrics(params: {
  supabase: DbClient;
  orgId: string;
  periodStart: string;
  periodEnd: string;
  ownerId?: string;
}): Promise<ValueMetrics> {
  const { supabase, orgId, periodStart, periodEnd, ownerId } = params;

  const [eventsRes, outcomesRes, workItemsRes, adoptionsRes, healthRes, interventionsRes] = await Promise.all([
    (supabase as any)
      .from("business_events")
      .select("id, event_type, severity, status, created_at, event_payload")
      .eq("org_id", orgId)
      .gte("created_at", periodStart)
      .lte("created_at", periodEnd),
    (supabase as any)
      .from("action_outcomes")
      .select("id, outcome_type, result_status, stage_changed, created_at, owner_id, customer_id, used_prep_card, used_draft")
      .eq("org_id", orgId)
      .gte("created_at", periodStart)
      .lte("created_at", periodEnd),
    (supabase as any)
      .from("work_items")
      .select("id, work_type, status, created_at, owner_id, source_type, ai_generated, completed_at")
      .eq("org_id", orgId)
      .gte("created_at", periodStart)
      .lte("created_at", periodEnd),
    (supabase as any)
      .from("suggestion_adoptions")
      .select("id, adoption_type, created_at, user_id")
      .eq("org_id", orgId)
      .gte("created_at", periodStart)
      .lte("created_at", periodEnd),
    (supabase as any)
      .from("customer_health_snapshots")
      .select("id, health_band, snapshot_date, risk_flags, created_at, customer_id")
      .eq("org_id", orgId)
      .gte("created_at", periodStart)
      .lte("created_at", periodEnd),
    (supabase as any)
      .from("intervention_requests")
      .select("id, request_type, status, created_at, target_user_id")
      .eq("org_id", orgId)
      .gte("created_at", periodStart)
      .lte("created_at", periodEnd)
  ]);

  if (eventsRes.error) throw new Error(eventsRes.error.message);
  if (outcomesRes.error) throw new Error(outcomesRes.error.message);
  if (workItemsRes.error) new Error(workItemsRes.error.message);
  if (adoptionsRes.error) throw new Error(adoptionsRes.error.message);
  if (healthRes.error) throw new Error(healthRes.error.message);
  if (interventionsRes.error) throw new Error(interventionsRes.error.message);

  const events = (eventsRes.data ?? []) as BusinessEventRow[];
  const outcomes = (outcomesRes.data ?? []) as ActionOutcomeRow[];
  const workItems = (workItemsRes.data ?? []) as WorkItemRow[];
  const adoptions = (adoptionsRes.data ?? []) as any[];
  const healthSnapshots = (healthRes.data ?? []) as any[];
  const interventions = (interventionsRes.data ?? []) as any[];

  const filteredEvents = ownerId
    ? events.filter((e) => {
        const payload = e.event_payload as Record<string, unknown>;
        return payload?.owner_id === ownerId;
      })
    : events;

  const filteredOutcomes = ownerId
    ? outcomes.filter((o) => o.owner_id === ownerId)
    : outcomes;

  const filteredWorkItems = ownerId
    ? workItems.filter((w) => w.owner_id === ownerId)
    : workItems;

  const filteredAdoptions = ownerId
    ? adoptions.filter((a) => a.user_id === ownerId)
    : adoptions;

  const filteredInterventions = ownerId
    ? interventions.filter((i) => i.target_user_id === ownerId)
    : interventions;

  const handledRiskEvents = filteredEvents.filter(
    (e) => e.status === "resolved" || e.status === "acknowledged"
  ).length;

  const recoveredProgressions = filteredOutcomes.filter(
    (o) => o.stage_changed === true && o.result_status === "positive_progress"
  ).length;

  const aiAdoptions = filteredAdoptions.filter(
    (a) => a.adoption_type === "adopted" || a.adoption_type === "copied"
  ).length;

  const managerInterventions = filteredInterventions.length;

  const newRiskCustomers = healthSnapshots.filter(
    (h) => h.health_band === "at_risk" || h.health_band === "critical"
  ).length;

  const completedTasks = filteredWorkItems.filter(
    (w) => w.status === "done" && w.ai_generated === true
  ).length;

  const timeSavedMinutes = (completedTasks * 15) + (aiAdoptions * 5);

  const followupInactiveEvents = filteredEvents.filter(
    (e) => e.event_type === "no_recent_touchpoint" || e.event_type === "health_declined"
  );

  const resolvedFollowupIssues = followupInactiveEvents.filter(
    (e) => e.status === "resolved"
  ).length;

  const lessFollowup = Math.max(0, resolvedFollowupIssues);

  return {
    periodStart,
    lessFollowup,
    newRiskCustomers,
    handledRiskEvents,
    managerInterventions,
    recoveredProgressions,
    aiAdoptions,
    timeSavedMinutes,
    weeklyTrend: {
      direction: 'stable',
      changePercent: 0,
      description: '首次统计，暂无趋势对比'
    }
  };
}

export async function getWeeklyValueMetrics(params: {
  supabase: DbClient;
  orgId: string;
  ownerId?: string;
}): Promise<ValueMetricsResult> {
  const { supabase, orgId, ownerId } = params;

  const now = new Date();
  const currentWeekStart = getWeekStart(now);
  const currentWeekEnd = new Date(currentWeekStart);
  currentWeekEnd.setDate(currentWeekEnd.getDate() + 6);

  const previousRange = getPreviousWeekRange(currentWeekStart);

  const current = await calculateValueMetrics({
    supabase,
    orgId,
    periodStart: toISODate(currentWeekStart),
    periodEnd: toISODate(currentWeekEnd),
    ownerId
  });

  const previous = await calculateValueMetrics({
    supabase,
    orgId,
    periodStart: toISODate(previousRange.start),
    periodEnd: toISODate(previousRange.end),
    owner
  });

  const trend = calculateTrend(current, previous);

  return { current, previous, trend };
}

function calculateTrend(current: ValueMetrics, previous: ValueMetrics | null): TrendResult {
  if (!previous) {
    return {
      direction: 'stable',
      changePercent: 0,
      `description`: '首次统计，暂无趋势对比'
    };
  }

  const metrics = [
    { current: current.handledRiskEvents, previous: previous.handledRiskEvents },
    { current: current.recoveredProgressions, previous: previous.recoveredProgressions },
    { current: current.aiAdoptions, previous: previous.aiAdoptions },
    { current: current.timeSavedMinutes, previous: previous.timeSavedMinutes }
  ];

  let totalImprovement = 0;
  let totalBase = 0;

  for (const m of metrics) {
    const base = m.previous || 1;
    const change = m.current - m.previous;
    const percentChange = (change / base) * 100;
    totalImprovement += percentChange;
    totalBase += 1;
  }

  const avgChange = totalImprovement / totalBase;

  let direction: 'up' | 'down' | 'stable';
  let description: string;

  if (avgChange > 10) {
    direction = 'up';
    description = `相比上周提升 ${Math.round(avgChange)}%，表现优秀`;
  } else if (avgChange < -10) {
    direction = 'down';
    description = `相比上周下降 ${Math.abs(Math.round(avgChange))}%，需要关注`;
  } else {
    direction = 'stable';
    description = '与上周持平，保持稳定';
  }

  return {
    direction,
    changePercent: Math.round(avgChange),
    description
  };
}

export async function generateValueMetricsSummary(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  ownerId?: string;
}): Promise<{
  summary: ValueMetricsSummary;
  usedFallback: boolean;
  fallbackReason: string | null;
}> {
  const { supabase, orgId, actorUserId, ownerId } = params;

  const metricsResult = await getWeeklyValueMetrics({
    supabase,
    orgId,
    owner
  });

  const { current, previous, trend } = metricsResult;

  const fallbackSummary: ValueMetricsSummary = {
    headline: `本周系统为您发现并处理了 ${current.handledRiskEvents} 个风险事件`,
    highlights: [
      `识别并处理 ${current.handledRiskEvents} 个风险事件`,
      `帮助恢复 ${current.recoveredProgressions} 个客户推进`,
      `AI 建议被采纳 ${current.aiAdoptions} 次`,
      `预估节省 ${current.timeSavedMinutes} 分钟`
    ],
    keyNumbers: [
      { label: '漏跟进减少', value: current.lessFollowup, trend: 'up', change: current.lessFollowup },
      { label: '风险事件处理', value: current.handledRiskEvents, trend: 'up', change: current.handledRiskEvents },
      { label: '客户推进恢复', value: current.recoveredProgressions, trend: 'up', change: current.recoveredProgressions },
      { label: 'AI 建议采纳', value: current.aiAdoptions, trend: 'up', change: current.aiAdoptions },
      { label: '经理介入', value: current.managerInterventions, trend: 'stable', change: 0 },
      { label: '节省时间(分钟)', value: current.timeSavedMinutes, trend: 'up', change: current.timeSavedMinutes }
    ],
    recommendation: trend.direction === 'up' 
      ? '继续保持当前节奏，关注高风险客户的及时跟进'
      : '建议增加对风险客户的主动跟进，提高 AI 建议的采纳率'
  };

  const provider = getAiProvider();
  const model = provider.getDefaultModel({ reasoning: false });
  const prompt = await getActivePromptVersion({
    supabase,
    orgId,
    scenario: "value_metrics_summary",
    providerId: provider.id
  });

  const run = await createAiRun({
    supabase,
    orgId,
    triggeredByUserId: actorUserId,
    triggerSource: "manager_review",
    scenario: "value_metrics_summary",
    provider: provider.id,
    model,
    promptVersion: prompt.version,
    inputSnapshot: { current, previous, trend }
  });

  await updateAiRunStatus({
    supabase,
    runId: run.id,
    status: "running",
    startedAt: new Date().toISOString()
  });

  let summary = fallbackSummary;
  let usedFallback = false;
  let fallbackReason: string | null = null;
  let outputSnapshot: Record<string, unknown> = { fallback: true, reason: "not_started" };
  let responseModel = model;

  try {
    if (!provider.isConfigured()) throw new Error("provider_not_configured");

    const response = await provider.chatCompletion({
      scenario: "value_metrics_summary",
      model,
      systemPrompt: prompt.systemPrompt,
      developerPrompt: `${prompt.developerPrompt}\n\nOutput schema:\n${JSON.stringify(prompt.outputSchema)}`,
      userPrompt: JSON.stringify({ current, previous, trend }),
      jsonMode: true,
      strictMode: true
    });

    if (response.error) throw new Error(response.error);
    const candidate = response.parsedJson ?? (response.rawText ? JSON.parse(response.rawText) : null);
    const parsed = valueMetricsSummarySchema.safeParse(candidate);
    if (!parsed.success) throw new Error("value_metrics_summary_schema_invalid");

    summary = {
      headline: parsed.data.headline,
      highlights: parsed.data.highlights,
      keyNumbers: summary.keyNumbers,
      recommendation: parsed.data.recommendation
    };
    outputSnapshot = response.rawResponse;
    responseModel = response.model;
  }  catch (error) {
    if (!isRuleFallbackEnabled()) {
      const message = error instanceof Error ? error.message : "value_metrics_summary_failed";
      await updateAiRunStatus({
        supabase,
        runId: run.id,
        status: "failed",
        provider: provider.id,
        model,
        errorMessage: message,
        completedAt: new Date().toISOString()
      });
      throw error;
    }
    usedFallback = true;
    fallbackReason = error instanceof Error ? error.message : "value_metrics_summary_fallback";
    outputSnapshot = {
      fallback: true,
      reason: fallbackReason,
      payload: fallbackSummary
    };
    responseModel = "rule-fallback";
  }

  await updateAiRunStatus({
    supabase,
    runId: run.id,
    status: "completed",
    provider: provider.id,
    model: responseModel,
    outputSnapshot,
    parsedResult: summary,
    resultSource: usedFallback ? "fallback" : "provider",
    fallbackReason,
    errorMessage: usedFallback ? fallbackReason : null,
    completedAt: new Date().toISOString()
  });

  return {
    summary,
    usedFallback,
    fallbackReason
  };
}
