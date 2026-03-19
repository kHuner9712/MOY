import type { ServerSupabaseClient } from "@/lib/supabase/types";
import type { BusinessEvent } from "@/types/automation";
import type { ActionOutcome } from "@/types/outcome";
import type { WorkItem } from "@/types/work";

type DbClient = ServerSupabaseClient;

export interface AttributionChain {
  event: BusinessEvent;
  workItems: WorkItem[];
  outcomes: ActionOutcome[];
  customerImpact: {
    stageChanged: boolean;
    oldStage: string | null;
    newStage: string | null;
    healthImproved: boolean;
    riskReduced: boolean;
  };
  timeline: {
    eventDetectedAt: string;
    firstActionAt: string | null;
    lastOutcomeAt: string | null;
    resolutionDurationHours: number | null;
  };
}

export interface AttributionSummary {
  totalEventsDetected: number;
  eventsHandled: number;
  eventsWithPositiveOutcome: number;
  eventsWithNegativeOutcome: number;
  eventsPending: number;
  averageResolutionHours: number | null;
  topEventTypes: Array<{
    eventType: string;
    count: number;
    handledCount: number;
    positiveOutcomeCount: number;
  }>;
  topHandlers: Array<{
    handlerId: string;
    handlerName: string;
    handledCount: number;
    positiveOutcomeCount: number;
  }>;
}

export interface OrgAttributionStats {
  periodStart: string;
  periodEnd: string;
  summary: AttributionSummary;
  chains: AttributionChain[];
}

function mapBusinessEventRow(row: Record<string, unknown>): BusinessEvent {
  return {
    id: row.id as string,
    orgId: row.org_id as string,
    entityType: row.entity_type as BusinessEvent["entityType"],
    entityId: row.entity_id as string,
    eventType: row.event_type as BusinessEvent["eventType"],
    severity: row.severity as BusinessEvent["severity"],
    eventSummary: row.event_summary as string,
    eventPayload: (row.event_payload ?? {}) as Record<string, unknown>,
    status: row.status as BusinessEvent["status"],
    createdAt: row.created_at as string,
    updatedAt: row.updated_at as string
  };
}

function mapWorkItemRow(row: Record<string, unknown>): WorkItem {
  return {
    id: row.id as string,
    orgId: row.org_id as string,
    ownerId: row.owner_id as string,
    ownerName: (row.owner_name as string) ?? "",
    customerId: row.customer_id as string | null,
    customerName: row.customer_name as string | null,
    opportunityId: row.opportunity_id as string | null,
    sourceType: row.source_type as WorkItem["sourceType"],
    workType: row.work_type as WorkItem["workType"],
    title: row.title as string,
    description: row.description as string,
    rationale: row.rationale as string,
    priorityScore: row.priority_score as number,
    priorityBand: row.priority_band as WorkItem["priorityBand"],
    status: row.status as WorkItem["status"],
    scheduledFor: row.scheduled_for as string | null,
    dueAt: row.due_at as string | null,
    completedAt: row.completed_at as string | null,
    snoozedUntil: row.snoozed_until as string | null,
    sourceRefType: row.source_ref_type as string | null,
    sourceRefId: row.source_ref_id as string | null,
    aiGenerated: row.ai_generated as boolean,
    aiRunId: row.ai_run_id as string | null,
    createdBy: row.created_by as string,
    createdAt: row.created_at as string,
    updatedAt: row.updated_at as string
  };
}

function mapActionOutcomeRow(row: Record<string, unknown>): ActionOutcome {
  return {
    id: row.id as string,
    orgId: row.org_id as string,
    ownerId: row.owner_id as string,
    customerId: row.customer_id as string | null,
    opportunityId: row.opportunity_id as string | null,
    workItemId: row.work_item_id as string | null,
    followupId: row.followup_id as string | null,
    communicationInputId: row.communication_input_id as string | null,
    prepCardId: row.prep_card_id as string | null,
    contentDraftId: row.content_draft_id as string | null,
    outcomeType: row.outcome_type as ActionOutcome["outcomeType"],
    resultStatus: row.result_status as ActionOutcome["resultStatus"],
    stageChanged: row.stage_changed as boolean,
    oldStage: row.old_stage as ActionOutcome["oldStage"],
    newStage: row.new_stage as ActionOutcome["newStage"],
    customerSentimentShift: row.customer_sentiment_shift as ActionOutcome["customerSentimentShift"],
    keyOutcomeSummary: row.key_outcome_summary as string,
    newObjections: (row.new_objections as string[]) ?? [],
    newRisks: (row.new_risks as string[]) ?? [],
    nextStepDefined: row.next_step_defined as boolean,
    nextStepText: row.next_step_text as string | null,
    followupDueAt: row.followup_due_at as string | null,
    usedPrepCard: row.used_prep_card as boolean,
    usedDraft: row.used_draft as boolean,
    usefulnessRating: row.usefulness_rating as ActionOutcome["usefulnessRating"],
    notes: row.notes as string | null,
    createdBy: row.created_by as string,
    createdAt: row.created_at as string,
    updatedAt: row.updated_at as string
  };
}

export async function traceEventToOutcome(params: {
  supabase: DbClient;
  orgId: string;
  eventId: string;
}): Promise<AttributionChain | null> {
  const { supabase, orgId, eventId } = params;

  const eventRes = await (supabase as any)
    .from("business_events")
    .select("*")
    .eq("id", eventId)
    .eq("org_id", orgId)
    .maybeSingle();

  if (!eventRes.data) return null;

  const event = mapBusinessEventRow(eventRes.data);

  const workItemsRes = await (supabase as any)
    .from("work_items")
    .select("*")
    .eq("org_id", orgId)
    .eq("source_ref_type", "business_event")
    .eq("source_ref_id", eventId);

  const workItems: WorkItem[] = (workItemsRes.data ?? []).map(mapWorkItemRow);

  if (workItems.length === 0) {
    return {
      event,
      workItems: [],
      outcomes: [],
      customerImpact: {
        stageChanged: false,
        oldStage: null,
        newStage: null,
        healthImproved: false,
        riskReduced: false
      },
      timeline: {
        eventDetectedAt: event.createdAt,
        firstActionAt: null,
        lastOutcomeAt: null,
        resolutionDurationHours: null
      }
    };
  }

  const workItemIds = workItems.map((w) => w.id);

  const outcomesRes = await (supabase as any)
    .from("action_outcomes")
    .select("*")
    .eq("org_id", orgId)
    .in("work_item_id", workItemIds);

  const outcomes: ActionOutcome[] = (outcomesRes.data ?? []).map(mapActionOutcomeRow);

  const stageChangedOutcomes = outcomes.filter((o) => o.stageChanged === true);
  const positiveOutcomes = outcomes.filter((o) => o.resultStatus === "positive_progress");

  const firstActionAt = workItems.length > 0
    ? workItems.reduce((earliest, w) =>
      w.createdAt < earliest ? w.createdAt : earliest
      , workItems[0].createdAt)
    : null;

  const lastOutcomeAt = outcomes.length > 0
    ? outcomes.reduce((latest, o) =>
      o.createdAt > latest ? o.createdAt : latest
      , outcomes[0].createdAt)
    : null;

  let resolutionDurationHours: number | null = null;
  if (event.status === "resolved" && lastOutcomeAt) {
    const eventTime = new Date(event.createdAt).getTime();
    const outcomeTime = new Date(lastOutcomeAt).getTime();
    resolutionDurationHours = (outcomeTime - eventTime) / (1000 * 60 * 60);
  }

  return {
    event,
    workItems,
    outcomes,
    customerImpact: {
      stageChanged: stageChangedOutcomes.length > 0,
      oldStage: stageChangedOutcomes[0]?.oldStage ?? null,
      newStage: stageChangedOutcomes[0]?.newStage ?? null,
      healthImproved: positiveOutcomes.length > 0,
      riskReduced: event.status === "resolved" && positiveOutcomes.length > 0
    },
    timeline: {
      eventDetectedAt: event.createdAt,
      firstActionAt,
      lastOutcomeAt,
      resolutionDurationHours
    }
  };
}

export async function getOrgAttributionStats(params: {
  supabase: DbClient;
  orgId: string;
  periodStart: string;
  periodEnd: string;
  ownerId?: string;
}): Promise<OrgAttributionStats> {
  const { supabase, orgId, periodStart, periodEnd, ownerId } = params;

  const periodStartIso = `${periodStart}T00:00:00.000Z`;
  const periodEndIso = `${periodEnd}T23:59:59.999Z`;

  const eventsRes = await (supabase as any)
    .from("business_events")
    .select("id, event_type, severity, status, created_at, event_payload")
    .eq("org_id", orgId)
    .gte("created_at", periodStartIso)
    .lte("created_at", periodEndIso);

  const events = (eventsRes.data ?? []) as Array<{
    id: string;
    event_type: string;
    severity: string;
    status: string;
    created_at: string;
    event_payload: Record<string, unknown>;
  }>;

  const filteredEvents = ownerId
    ? events.filter((e) => {
      const payloadOwnerId = e.event_payload?.owner_id;
      return typeof payloadOwnerId === 'string' && payloadOwnerId === ownerId;
    })
    : events;

  const totalEventsDetected = filteredEvents.length;
  const eventsHandled = filteredEvents.filter(
    (e) => e.status === "resolved" || e.status === "acknowledged"
  ).length;
  const eventsPending = filteredEvents.filter((e) => e.status === "open").length;

  const chains: AttributionChain[] = [];
  const positiveOutcomeEventIds: string[] = [];
  const negativeOutcomeEventIds: string[] = [];
  const resolutionDurations: number[] = [];

  for (const event of filteredEvents.slice(0, 50)) {
    const chain = await traceEventToOutcome({
      supabase,
      orgId,
      eventId: event.id
    });

    if (chain) {
      chains.push(chain);

      if (chain.outcomes.length > 0) {
        const hasPositive = chain.outcomes.some((o) => o.resultStatus === "positive_progress");
        const hasNegative = chain.outcomes.some((o) => o.resultStatus === "risk_increased");

        if (hasPositive) positiveOutcomeEventIds.push(event.id);
        if (hasNegative) negativeOutcomeEventIds.push(event.id);
      }

      if (chain.timeline.resolutionDurationHours !== null) {
        resolutionDurations.push(chain.timeline.resolutionDurationHours);
      }
    }
  }

  const averageResolutionHours = resolutionDurations.length > 0
    ? resolutionDurations.reduce((sum, h) => sum + h, 0) / resolutionDurations.length
    : null;

  const eventTypeStats = new Map<string, { count: number; handled: number; positive: number }>();

  for (const event of filteredEvents) {
    const existing = eventTypeStats.get(event.event_type) ?? { count: 0, handled: 0, positive: 0 };
    existing.count++;
    if (event.status === "resolved" || event.status === "acknowledged") {
      existing.handled++;
    }
    if (positiveOutcomeEventIds.includes(event.id)) {
      existing.positive++;
    }
    eventTypeStats.set(event.event_type, existing);
  }

  const topEventTypes = Array.from(eventTypeStats.entries())
    .map(([eventType, stats]) => ({
      eventType,
      count: stats.count,
      handledCount: stats.handled,
      positiveOutcomeCount: stats.positive
    }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 10);

  const handlerStats = new Map<string, { name: string; handled: number; positive: number }>();

  for (const chain of chains) {
    for (const outcome of chain.outcomes) {
      const handlerId = outcome.ownerId;
      const existing = handlerStats.get(handlerId) ?? { name: "未知", handled: 0, positive: 0 };
      existing.handled++;
      if (outcome.resultStatus === "positive_progress") {
        existing.positive++;
      }
      handlerStats.set(handlerId, existing);
    }
  }

  const topHandlers = Array.from(handlerStats.entries())
    .map(([handlerId, stats]) => ({
      handlerId,
      handlerName: stats.name,
      handledCount: stats.handled,
      positiveOutcomeCount: stats.positive
    }))
    .sort((a, b) => b.handledCount - a.handledCount)
    .slice(0, 10);

  return {
    periodStart,
    periodEnd,
    summary: {
      totalEventsDetected,
      eventsHandled,
      eventsWithPositiveOutcome: positiveOutcomeEventIds.length,
      eventsWithNegativeOutcome: negativeOutcomeEventIds.length,
      eventsPending,
      averageResolutionHours,
      topEventTypes,
      topHandlers
    },
    chains
  };
}

export async function linkOutcomeToEvent(params: {
  supabase: DbClient;
  outcomeId: string;
  eventId: string;
}): Promise<void> {
  const { supabase, outcomeId, eventId } = params;

  const outcomeRes = await (supabase as any)
    .from("action_outcomes")
    .select("linked_business_event_ids")
    .eq("id", outcomeId)
    .maybeSingle();

  if (!outcomeRes.data) {
    throw new Error("Outcome not found");
  }

  const existingIds: string[] = outcomeRes.data.linked_business_event_ids ?? [];

  if (!existingIds.includes(eventId)) {
    const updatedIds = [...existingIds, eventId];

    await (supabase as any)
      .from("action_outcomes")
      .update({ linked_business_event_ids: updatedIds })
      .eq("id", outcomeId);
  }
}

export async function getAttributionSummaryForBriefing(params: {
  supabase: DbClient;
  orgId: string;
  periodStart: string;
  periodEnd: string;
}): Promise<{
  headline: string;
  highlights: string[];
  keyNumbers: Array<{
    label: string;
    value: number;
    trend: "up" | "down" | "stable";
    change: number;
  }>;
  recommendation: string;
  topEventTypes: string[];
  topHandlers: string[];
}> {
  const stats = await getOrgAttributionStats(params);

  const { summary } = stats;

  const headline = summary.totalEventsDetected > 0
    ? `本周识别 ${summary.totalEventsDetected} 个风险事件，处理 ${summary.eventsHandled} 个，${summary.eventsWithPositiveOutcome} 个产生正向结果`
    : "本周未检测到风险事件";

  const highlights: string[] = [];

  if (summary.eventsHandled > 0) {
    highlights.push(`处理了 ${summary.eventsHandled} 个风险事件`);
  }
  if (summary.eventsWithPositiveOutcome > 0) {
    highlights.push(`${summary.eventsWithPositiveOutcome} 个事件产生正向结果`);
  }
  if (summary.averageResolutionHours !== null) {
    highlights.push(`平均处理时长 ${summary.averageResolutionHours.toFixed(1)} 小时`);
  }
  if (summary.eventsPending > 0) {
    highlights.push(`${summary.eventsPending} 个事件待处理`);
  }

  const keyNumbers = [
    {
      label: "识别风险",
      value: summary.totalEventsDetected,
      trend: "stable" as const,
      change: 0
    },
    {
      label: "已处理",
      value: summary.eventsHandled,
      trend: summary.eventsHandled > 0 ? "up" as const : "stable" as const,
      change: summary.eventsHandled
    },
    {
      label: "正向结果",
      value: summary.eventsWithPositiveOutcome,
      trend: summary.eventsWithPositiveOutcome > 0 ? "up" as const : "stable" as const,
      change: summary.eventsWithPositiveOutcome
    },
    {
      label: "待处理",
      value: summary.eventsPending,
      trend: summary.eventsPending > 0 ? "down" as const : "stable" as const,
      change: -summary.eventsPending
    }
  ];

  let recommendation = "继续保持风险监控和处理节奏";
  if (summary.eventsPending > summary.eventsHandled) {
    recommendation = "建议加快风险事件处理节奏，关注待处理事件";
  } else if (summary.eventsWithPositiveOutcome > summary.eventsHandled * 0.5) {
    recommendation = "处理效果良好，继续保持当前节奏";
  } else if (summary.eventsWithNegativeOutcome > 0) {
    recommendation = "部分事件处理后产生负面结果，建议复盘处理策略";
  }

  const topEventTypes = summary.topEventTypes
    .slice(0, 3)
    .map((e) => `${e.eventType}(${e.count})`);

  const topHandlers = summary.topHandlers
    .slice(0, 3)
    .map((h) => `${h.handlerName}(${h.handledCount})`);

  return {
    headline,
    highlights,
    keyNumbers,
    recommendation,
    topEventTypes,
    topHandlers
  };
}
