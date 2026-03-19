import type { ServerSupabaseClient } from "@/lib/supabase/types";
import type {
  ValueMetrics,
  ValueMetricsResult,
  ValueMetricsSummary,
  TrendResult,
  KeyNumberItem
} from "@/types/value-metrics";
import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import { getActivePromptVersion } from "./ai-prompt-service";
import { createAiRun, updateAiRunStatus } from "./ai-run-service";
import { valueMetricsSummaryResultSchema } from "@/types/ai";

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

  const periodStartIso = `${periodStart}T00:00:00.000Z`;
  const periodEndIso = `${periodEnd}T23:59:59.999Z`;

  const [eventsRes, outcomesRes, workItemsRes, adoptionsRes, healthRes, interventionsRes] = await Promise.all([
    (supabase as any)
      .from("business_events")
      .select("id, event_type, severity, status, created_at, event_payload")
      .eq("org_id", orgId)
      .gte("created_at", periodStartIso)
      .lte("created_at", periodEndIso),
    (supabase as any)
      .from("action_outcomes")
      .select("id, outcome_type, result_status, stage_changed, created_at, owner_id, customer_id, used_prep_card, used_draft")
      .eq("org_id", orgId)
      .gte("created_at", periodStartIso)
      .lte("created_at", periodEndIso),
    (supabase as any)
      .from("work_items")
      .select("id, work_type, status, created_at, owner_id, source_type, ai_generated, completed_at")
      .eq("org_id", orgId)
      .gte("created_at", periodStartIso)
      .lte("created_at", periodEndIso),
    (supabase as any)
      .from("suggestion_adoptions")
      .select("id, adoption_type, created_at, user_id")
      .eq("org_id", orgId)
      .gte("created_at", periodStartIso)
      .lte("created_at", periodEndIso),
    (supabase as any)
      .from("customer_health_snapshots")
      .select("id, health_band, snapshot_date, risk_flags, created_at, customer_id")
      .eq("org_id", orgId)
      .gte("created_at", periodStartIso)
      .lte("created_at", periodEndIso),
    (supabase as any)
      .from("intervention_requests")
      .select("id, request_type, status, created_at, target_user_id")
      .eq("org_id", orgId)
      .gte("created_at", periodStartIso)
      .lte("created_at", periodEndIso)
  ]);

  if (eventsRes.error) throw new Error(eventsRes.error.message);
  if (outcomesRes.error) throw new Error(outcomesRes.error.message);
  if (workItemsRes.error) throw new Error(workItemsRes.error.message);
  if (adoptionsRes.error) throw new Error(adoptionsRes.error.message);
  if (healthRes.error) throw new Error(healthRes.error.message);
  if (interventionsRes.error) throw new Error(interventionsRes.error.message);

  const events = (eventsRes.data ?? []) as Array<{
    id: string;
    event_type: string;
    severity: string;
    status: string;
    created_at: string;
    event_payload: Record<string, unknown>;
  }>;
  const outcomes = (outcomesRes.data ?? []) as Array<{
    id: string;
    outcome_type: string;
    result_status: string;
    stage_changed: boolean;
    created_at: string;
    owner_id: string;
    customer_id: string | null;
    used_prep_card: boolean;
    used_draft: boolean;
  }>;
  const workItems = (workItemsRes.data ?? []) as Array<{
    id: string;
    work_type: string;
    status: string;
    created_at: string;
    owner_id: string;
    source_type: string;
    ai_generated: boolean;
    completed_at: string | null;
  }>;
  const adoptions = (adoptionsRes.data ?? []) as Array<{
    id: string;
    adoption_type: string;
    created_at: string;
    user_id: string;
  }>;
  const healthSnapshots = (healthRes.data ?? []) as Array<{
    id: string;
    health_band: string;
    snapshot_date: string;
    risk_flags: string[];
    created_at: string;
    customer_id: string;
  }>;
  const interventions = (interventionsRes.data ?? []) as Array<{
    id: string;
    request_type: string;
    status: string;
    created_at: string;
    target_user_id: string;
  }>;

  const filteredEvents = ownerId
    ? events.filter((e) => {
      const payloadOwnerId = e.event_payload?.owner_id;
      return typeof payloadOwnerId === 'string' && payloadOwnerId === ownerId;
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

  const newRiskCustomersSet = new Set<string>();
  for (const h of healthSnapshots) {
    if (h.health_band === "at_risk" || h.health_band === "critical") {
      newRiskCustomersSet.add(h.customer_id);
    }
  }
  const newRiskCustomers = newRiskCustomersSet.size;

  const completedAiTasks = filteredWorkItems.filter(
    (w) => w.status === "done" && w.ai_generated === true
  ).length;

  const timeSavedMinutes = (completedAiTasks * 15) + (aiAdoptions * 5);

  const followupInactiveEvents = filteredEvents.filter(
    (e) => e.event_type === "no_recent_touchpoint" || e.event_type === "health_declined"
  );

  const resolvedFollowupIssues = followupInactiveEvents.filter(
    (e) => e.status === "resolved"
  ).length;

  const lessFollowup = Math.max(0, resolvedFollowupIssues);

  return {
    periodStart,
    periodEnd,
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
    ownerId
  });

  const trend = calculateTrend(current, previous);
  current.weeklyTrend = trend;

  return { current, previous, trend };
}

function calculateTrend(current: ValueMetrics, previous: ValueMetrics | null): TrendResult {
  if (!previous) {
    return {
      direction: 'stable',
      changePercent: 0,
      description: '首次统计，暂无趋势对比'
    };
  }

  const metrics = [
    { current: current.handledRiskEvents, previous: previous.handledRiskEvents, weight: 1 },
    { current: current.recoveredProgressions, previous: previous.recoveredProgressions, weight: 1 },
    { current: current.aiAdoptions, previous: previous.aiAdoptions, weight: 0.8 },
    { current: current.timeSavedMinutes, previous: previous.timeSavedMinutes, weight: 0.5 }
  ];

  let totalImprovement = 0;
  let totalWeight = 0;

  for (const m of metrics) {
    const base = m.previous || 1;
    const change = m.current - m.previous;
    const percentChange = (change / base) * 100;
    totalImprovement += percentChange * m.weight;
    totalWeight += m.weight;
  }

  const avgChange = totalImprovement / totalWeight;

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
    ownerId
  });

  const { current, trend } = metricsResult;

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

  if (!provider.isConfigured() || !isRuleFallbackEnabled()) {
    return {
      summary: fallbackSummary,
      usedFallback: true,
      fallbackReason: provider.isConfigured() ? null : 'provider_not_configured'
    };
  }

  const model = provider.getDefaultModel({ reasoning: false });

  try {
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
      inputSnapshot: { current, trend }
    });

    await updateAiRunStatus({
      supabase,
      runId: run.id,
      status: "running",
      startedAt: new Date().toISOString()
    });

    const response = await provider.chatCompletion({
      scenario: "value_metrics_summary",
      model,
      systemPrompt: prompt.systemPrompt,
      developerPrompt: `${prompt.developerPrompt}\n\nOutput schema:\n${JSON.stringify(prompt.outputSchema)}`,
      userPrompt: JSON.stringify({ current, trend }),
      jsonMode: true,
      strictMode: true
    });

    if (response.error) throw new Error(response.error);

    const candidate = response.parsedJson ?? (response.rawText ? JSON.parse(response.rawText) : null);
    const parsed = valueMetricsSummaryResultSchema.safeParse(candidate);

    if (!parsed.success) throw new Error("value_metrics_summary_schema_invalid");

    const summary: ValueMetricsSummary = {
      headline: parsed.data.headline,
      highlights: parsed.data.highlights,
      keyNumbers: fallbackSummary.keyNumbers,
      recommendation: parsed.data.recommendation
    };

    await updateAiRunStatus({
      supabase,
      runId: run.id,
      status: "completed",
      provider: provider.id,
      model: response.model,
      outputSnapshot: response.rawResponse,
      parsedResult: summary as unknown as Record<string, unknown>,
      resultSource: "provider",
      completedAt: new Date().toISOString()
    });

    return {
      summary,
      usedFallback: false,
      fallbackReason: null
    };
  } catch (error) {
    return {
      summary: fallbackSummary,
      usedFallback: true,
      fallbackReason: error instanceof Error ? error.message : "value_metrics_summary_failed"
    };
  }
}

export function buildValueOverviewBlock(
  metrics: ValueMetrics,
  summary: ValueMetricsSummary
): {
  title: string;
  summary: string;
  metrics: KeyNumberItem[];
  trend: TrendResult;
  recommendation: string;
} {
  return {
    title: '本周价值总览',
    summary: summary.headline,
    metrics: summary.keyNumbers,
    trend: metrics.weeklyTrend,
    recommendation: summary.recommendation
  };
}
