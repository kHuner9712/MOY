import { getBusinessEventDedupeKey, isBusinessEventStatusTransitionAllowed, type RuleMatchTarget } from "@/lib/automation-ops";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import type { CommunicationExtractionResult } from "@/types/ai";
import type {
  BusinessEvent,
  BusinessEventEntityType,
  BusinessEventSeverity,
  BusinessEventStatus,
  BusinessEventType
} from "@/types/automation";

type DbClient = ServerSupabaseClient;

export type CaptureBusinessEventLifecycle = "capture_auto_confirmed" | "capture_manual_confirmed";

export interface CaptureBusinessEventSignal {
  customerId: string;
  ownerId: string | null;
  communicationInputId: string;
  followupId: string;
  extraction: Pick<
    CommunicationExtractionResult,
    "summary" | "buying_signals" | "key_objections" | "uncertainty_notes" | "should_trigger_alert_review"
  >;
  lifecycle: CaptureBusinessEventLifecycle;
}

interface BusinessEventRow {
  id: string;
  org_id: string;
  entity_type: BusinessEventEntityType;
  entity_id: string;
  event_type: BusinessEventType;
  severity: BusinessEventSeverity;
  event_summary: string;
  event_payload: Record<string, unknown> | null;
  status: BusinessEventStatus;
  created_at: string;
  updated_at: string;
}

function asRecord(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function mapBusinessEventRow(row: BusinessEventRow): BusinessEvent {
  return {
    id: row.id,
    orgId: row.org_id,
    entityType: row.entity_type,
    entityId: row.entity_id,
    eventType: row.event_type,
    severity: row.severity,
    eventSummary: row.event_summary,
    eventPayload: asRecord(row.event_payload),
    status: row.status,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

function severityRank(level: BusinessEventSeverity): number {
  if (level === "critical") return 3;
  if (level === "warning") return 2;
  return 1;
}

function trimStringArray(values: string[] | null | undefined): string[] {
  if (!Array.isArray(values)) return [];
  const normalized = values.map((item) => item.trim()).filter((item) => item.length > 0);
  return Array.from(new Set(normalized));
}

function shortenText(value: string, maxLength: number): string {
  if (value.length <= maxLength) return value;
  return `${value.slice(0, Math.max(0, maxLength - 3))}...`;
}

function buildCaptureBaseEvidence(params: {
  communicationInputId: string;
  followupId: string;
  lifecycle: CaptureBusinessEventLifecycle;
}): string[] {
  return [
    "source=communication_capture",
    `communication_input_id=${params.communicationInputId}`,
    `followup_id=${params.followupId}`,
    `lifecycle=${params.lifecycle}`
  ];
}

export function buildCaptureBusinessEventTargets(input: CaptureBusinessEventSignal): RuleMatchTarget[] {
  const buyingSignals = trimStringArray(input.extraction.buying_signals);
  const objections = trimStringArray(input.extraction.key_objections);
  const uncertaintyNotes = trimStringArray(input.extraction.uncertainty_notes);
  const baseEvidence = buildCaptureBaseEvidence({
    communicationInputId: input.communicationInputId,
    followupId: input.followupId,
    lifecycle: input.lifecycle
  });
  const summarySnippet = shortenText(input.extraction.summary.trim(), 120);
  const targets: RuleMatchTarget[] = [];

  if (buyingSignals.length > 0) {
    targets.push({
      entityType: "customer",
      entityId: input.customerId,
      ownerId: input.ownerId,
      customerId: input.customerId,
      severity: "info",
      eventType: "conversion_signal",
      summary: `Communication capture suggests conversion momentum: ${summarySnippet}`,
      evidence: [
        ...baseEvidence,
        ...buyingSignals.slice(0, 3).map((item) => `buying_signal=${item}`)
      ],
      recommendedAction: "Convert buying signal into an explicit owner next step and confirm timeline."
    });
  }

  const requiresRiskAttention =
    input.extraction.should_trigger_alert_review || objections.length > 0 || uncertaintyNotes.length > 0;
  if (requiresRiskAttention) {
    const severity: BusinessEventSeverity =
      input.extraction.should_trigger_alert_review || objections.length >= 2 ? "critical" : "warning";
    targets.push({
      entityType: "customer",
      entityId: input.customerId,
      ownerId: input.ownerId,
      customerId: input.customerId,
      severity,
      eventType: "manager_attention_escalated",
      summary: `Communication capture indicates execution risk that needs manager attention: ${summarySnippet}`,
      evidence: [
        ...baseEvidence,
        ...objections.slice(0, 3).map((item) => `objection=${item}`),
        ...uncertaintyNotes.slice(0, 2).map((item) => `uncertainty=${item}`),
        `should_trigger_alert_review=${input.extraction.should_trigger_alert_review}`
      ],
      recommendedAction: "Review objection risks, confirm owner recovery plan, and set next checkpoint."
    });
  }

  return targets;
}

export function buildCaptureBusinessEventPayload(params: {
  input: CaptureBusinessEventSignal;
  target: RuleMatchTarget;
}): Record<string, unknown> {
  return {
    source: "communication_capture",
    source_lifecycle: params.input.lifecycle,
    source_ref_type: "communication_input",
    source_ref_id: params.input.communicationInputId,
    communication_input_id: params.input.communicationInputId,
    followup_id: params.input.followupId,
    owner_id: params.input.ownerId,
    customer_id: params.input.customerId,
    evidence: params.target.evidence,
    recommended_action: params.target.recommendedAction,
    extraction_snapshot: {
      buying_signal_count: trimStringArray(params.input.extraction.buying_signals).length,
      objection_count: trimStringArray(params.input.extraction.key_objections).length,
      uncertainty_note_count: trimStringArray(params.input.extraction.uncertainty_notes).length,
      should_trigger_alert_review: params.input.extraction.should_trigger_alert_review
    }
  };
}

export async function listBusinessEvents(params: {
  supabase: DbClient;
  orgId: string;
  statuses?: BusinessEventStatus[];
  eventTypes?: BusinessEventType[];
  ownerId?: string;
  limit?: number;
}): Promise<BusinessEvent[]> {
  let query = (params.supabase as any)
    .from("business_events")
    .select("*")
    .eq("org_id", params.orgId)
    .order("created_at", { ascending: false })
    .limit(params.limit ?? 120);

  if (params.statuses?.length) query = query.in("status", params.statuses);
  if (params.eventTypes?.length) query = query.in("event_type", params.eventTypes);

  const res = await query;
  if (res.error) throw new Error(res.error.message);

  const rows = ((res.data ?? []) as BusinessEventRow[]).map(mapBusinessEventRow);
  if (!params.ownerId) return rows;

  return rows.filter((item) => {
    const payload = item.eventPayload;
    const ownerId = typeof payload.owner_id === "string" ? payload.owner_id : null;
    return ownerId === params.ownerId;
  });
}

export async function getBusinessEventById(params: {
  supabase: DbClient;
  orgId: string;
  eventId: string;
}): Promise<BusinessEvent | null> {
  const res = await (params.supabase as any)
    .from("business_events")
    .select("*")
    .eq("org_id", params.orgId)
    .eq("id", params.eventId)
    .maybeSingle();
  if (res.error) throw new Error(res.error.message);
  if (!res.data) return null;
  return mapBusinessEventRow(res.data as BusinessEventRow);
}

export async function upsertBusinessEvent(params: {
  supabase: DbClient;
  orgId: string;
  entityType: BusinessEventEntityType;
  entityId: string;
  eventType: BusinessEventType;
  severity: BusinessEventSeverity;
  eventSummary: string;
  eventPayload?: Record<string, unknown>;
}): Promise<{ event: BusinessEvent; created: boolean }> {
  const existing = await (params.supabase as any)
    .from("business_events")
    .select("*")
    .eq("org_id", params.orgId)
    .eq("entity_type", params.entityType)
    .eq("entity_id", params.entityId)
    .eq("event_type", params.eventType)
    .in("status", ["open", "acknowledged"])
    .order("created_at", { ascending: false })
    .limit(1)
    .maybeSingle();

  if (existing.error) throw new Error(existing.error.message);

  if (existing.data) {
    const row = existing.data as BusinessEventRow;
    const nextSeverity = severityRank(params.severity) > severityRank(row.severity) ? params.severity : row.severity;
    const mergedPayload = {
      ...(asRecord(row.event_payload) ?? {}),
      ...(params.eventPayload ?? {}),
      dedupe_key: getBusinessEventDedupeKey({
        entityType: params.entityType,
        entityId: params.entityId,
        eventType: params.eventType
      })
    };
    const updateRes = await (params.supabase as any)
      .from("business_events")
      .update({
        severity: nextSeverity,
        event_summary: params.eventSummary,
        event_payload: mergedPayload,
        status: "open"
      })
      .eq("id", row.id)
      .select("*")
      .single();

    if (updateRes.error) throw new Error(updateRes.error.message);
    return {
      event: mapBusinessEventRow(updateRes.data as BusinessEventRow),
      created: false
    };
  }

  const insertRes = await (params.supabase as any)
    .from("business_events")
    .insert({
      org_id: params.orgId,
      entity_type: params.entityType,
      entity_id: params.entityId,
      event_type: params.eventType,
      severity: params.severity,
      event_summary: params.eventSummary,
      event_payload: {
        ...(params.eventPayload ?? {}),
        dedupe_key: getBusinessEventDedupeKey({
          entityType: params.entityType,
          entityId: params.entityId,
          eventType: params.eventType
        })
      },
      status: "open"
    })
    .select("*")
    .single();
  if (insertRes.error) throw new Error(insertRes.error.message);
  return {
    event: mapBusinessEventRow(insertRes.data as BusinessEventRow),
    created: true
  };
}

export async function updateBusinessEventStatus(params: {
  supabase: DbClient;
  orgId: string;
  eventId: string;
  status: BusinessEventStatus;
}): Promise<BusinessEvent> {
  const current = await getBusinessEventById({
    supabase: params.supabase,
    orgId: params.orgId,
    eventId: params.eventId
  });
  if (!current) throw new Error("business_event_not_found");
  if (!isBusinessEventStatusTransitionAllowed(current.status, params.status)) {
    throw new Error(`invalid_business_event_status_transition: ${current.status} -> ${params.status}`);
  }

  const res = await (params.supabase as any)
    .from("business_events")
    .update({ status: params.status })
    .eq("org_id", params.orgId)
    .eq("id", params.eventId)
    .select("*")
    .single();
  if (res.error) throw new Error(res.error.message);
  return mapBusinessEventRow(res.data as BusinessEventRow);
}

export async function upsertCaptureBusinessEvents(params: {
  supabase: DbClient;
  orgId: string;
  input: CaptureBusinessEventSignal;
}): Promise<{
  matchedCount: number;
  createdCount: number;
  updatedCount: number;
  events: BusinessEvent[];
}> {
  const targets = buildCaptureBusinessEventTargets(params.input);
  const events: BusinessEvent[] = [];
  let createdCount = 0;
  let updatedCount = 0;

  for (const target of targets) {
    const result = await upsertBusinessEvent({
      supabase: params.supabase,
      orgId: params.orgId,
      entityType: target.entityType,
      entityId: target.entityId,
      eventType: target.eventType,
      severity: target.severity,
      eventSummary: target.summary,
      eventPayload: buildCaptureBusinessEventPayload({
        input: params.input,
        target
      })
    });
    if (result.created) createdCount += 1;
    else updatedCount += 1;
    events.push(result.event);
  }

  return {
    matchedCount: targets.length,
    createdCount,
    updatedCount,
    events
  };
}

export async function generateBusinessEventsFromSignals(params: {
  supabase: DbClient;
  orgId: string;
  ownerId?: string;
  lookbackDays?: number;
}): Promise<{
  matchedCount: number;
  createdCount: number;
  updatedCount: number;
  events: BusinessEvent[];
}> {
  const lookbackDays = params.lookbackDays ?? 7;
  const cutoffIso = new Date(Date.now() - lookbackDays * 24 * 60 * 60 * 1000).toISOString();

  const [tracksRes, dealsRes, blockedCheckpointsRes, healthRes, renewalRes, conversionsRes, onboardingRes] = await Promise.all([
    (params.supabase as any)
      .from("trial_conversion_tracks")
      .select("id,target_org_id,owner_id,current_stage,updated_at,conversion_readiness_score")
      .eq("org_id", params.orgId),
    (params.supabase as any)
      .from("deal_rooms")
      .select("id,owner_id,room_status,priority_band,manager_attention_needed,updated_at")
      .eq("org_id", params.orgId),
    (params.supabase as any)
      .from("deal_checkpoints")
      .select("id,deal_room_id,owner_id,status,due_at,updated_at")
      .eq("org_id", params.orgId)
      .eq("status", "blocked"),
    (params.supabase as any)
      .from("customer_health_snapshots")
      .select("id,customer_id,health_band,risk_flags,overall_health_score,updated_at")
      .eq("org_id", params.orgId)
      .in("health_band", ["at_risk", "critical"])
      .gte("updated_at", cutoffIso),
    (params.supabase as any)
      .from("renewal_watch_items")
      .select("id,customer_id,owner_id,renewal_status,renewal_due_at")
      .eq("org_id", params.orgId)
      .in("renewal_status", ["due_soon", "at_risk", "expansion_candidate"]),
    (params.supabase as any)
      .from("conversion_events")
      .select("id,target_org_id,event_type,event_summary,created_at")
      .eq("org_id", params.orgId)
      .in("event_type", ["conversion_signal", "trial_activated"])
      .gte("created_at", cutoffIso),
    (params.supabase as any)
      .from("onboarding_runs")
      .select("id,org_id,status,updated_at")
      .eq("org_id", params.orgId)
      .in("status", ["running", "failed"])
  ]);

  for (const res of [tracksRes, dealsRes, blockedCheckpointsRes, healthRes, renewalRes, conversionsRes, onboardingRes]) {
    if (res.error) throw new Error(res.error.message);
  }

  const matches: RuleMatchTarget[] = [];

  for (const track of (tracksRes.data ?? []) as Array<Record<string, unknown>>) {
    const updatedAt = typeof track.updated_at === "string" ? track.updated_at : null;
    const stale = !updatedAt || new Date(updatedAt).getTime() < new Date(cutoffIso).getTime();
    if (stale && ["activated", "onboarding_started"].includes(String(track.current_stage ?? ""))) {
      matches.push({
        entityType: "trial_org",
        entityId: String(track.target_org_id),
        ownerId: typeof track.owner_id === "string" ? track.owner_id : null,
        severity: "warning",
        eventType: "trial_stalled",
        summary: "Trial org progress is stalled with no key movement in recent days.",
        evidence: [`stage=${String(track.current_stage)}`],
        recommendedAction: "Run manager check-in and unblock onboarding-first-value actions."
      });
    }
  }

  for (const run of (onboardingRes.data ?? []) as Array<Record<string, unknown>>) {
    const updatedAt = typeof run.updated_at === "string" ? run.updated_at : null;
    const stale = !updatedAt || new Date(updatedAt).getTime() < new Date(cutoffIso).getTime();
    if (stale) {
      matches.push({
        entityType: "onboarding_run",
        entityId: String(run.id),
        severity: "warning",
        eventType: "onboarding_stuck",
        summary: "Onboarding run is stale and requires manual intervention.",
        evidence: [`status=${String(run.status)}`],
        recommendedAction: "Assign owner to complete onboarding checklist in 48 hours."
      });
    }
  }

  for (const checkpoint of (blockedCheckpointsRes.data ?? []) as Array<Record<string, unknown>>) {
    const dueAt = typeof checkpoint.due_at === "string" ? checkpoint.due_at : null;
    const blockedLong = dueAt ? new Date(dueAt).getTime() < Date.now() - 24 * 60 * 60 * 1000 : true;
    if (blockedLong) {
      matches.push({
        entityType: "deal_room",
        entityId: String(checkpoint.deal_room_id),
        ownerId: typeof checkpoint.owner_id === "string" ? checkpoint.owner_id : null,
        dealRoomId: String(checkpoint.deal_room_id),
        severity: "critical",
        eventType: "deal_blocked",
        summary: "Deal has blocked checkpoint and is likely to stall pipeline progression.",
        evidence: ["blocked_checkpoint"],
        recommendedAction: "Create unblock task and manager intervention request."
      });
    }
  }

  for (const deal of (dealsRes.data ?? []) as Array<Record<string, unknown>>) {
    const needsAttention = Boolean(deal.manager_attention_needed);
    if (needsAttention) {
      matches.push({
        entityType: "deal_room",
        entityId: String(deal.id),
        ownerId: typeof deal.owner_id === "string" ? deal.owner_id : null,
        dealRoomId: String(deal.id),
        severity: "warning",
        eventType: "manager_attention_escalated",
        summary: "Manager attention flag is active for this deal.",
        evidence: [`room_status=${String(deal.room_status)}`],
        recommendedAction: "Confirm owner next step and review blockers today."
      });
    }

    const isHighPriority = ["strategic", "critical"].includes(String(deal.priority_band ?? ""));
    const stale = typeof deal.updated_at === "string" && new Date(deal.updated_at).getTime() < new Date(cutoffIso).getTime();
    if (isHighPriority && stale) {
      matches.push({
        entityType: "deal_room",
        entityId: String(deal.id),
        ownerId: typeof deal.owner_id === "string" ? deal.owner_id : null,
        dealRoomId: String(deal.id),
        severity: "warning",
        eventType: "no_recent_touchpoint",
        summary: "High-priority deal has no recent external push signal.",
        evidence: [`priority=${String(deal.priority_band)}`],
        recommendedAction: "Trigger outbound touchpoint and update deal milestone."
      });
    }
  }

  for (const health of (healthRes.data ?? []) as Array<Record<string, unknown>>) {
    matches.push({
      entityType: "customer",
      entityId: String(health.customer_id),
      customerId: String(health.customer_id),
      severity: String(health.health_band) === "critical" ? "critical" : "warning",
      eventType: "health_declined",
      summary: "Customer health has declined to at-risk band.",
      evidence: [
        `band=${String(health.health_band)}`,
        `score=${String(health.overall_health_score ?? "n/a")}`
      ],
      recommendedAction: "Run retention conversation and update next owner action."
    });
  }

  for (const item of (renewalRes.data ?? []) as Array<Record<string, unknown>>) {
    const status = String(item.renewal_status ?? "watch");
    if (status === "expansion_candidate") {
      matches.push({
        entityType: "customer",
        entityId: String(item.customer_id),
        ownerId: typeof item.owner_id === "string" ? item.owner_id : null,
        customerId: String(item.customer_id),
        severity: "info",
        eventType: "expansion_signal",
        summary: "Customer shows expansion opportunity signal.",
        evidence: ["renewal_watch=expansion_candidate"],
        recommendedAction: "Prepare expansion proposal and schedule value review."
      });
      continue;
    }

    matches.push({
      entityType: "customer",
      entityId: String(item.customer_id),
      ownerId: typeof item.owner_id === "string" ? item.owner_id : null,
      customerId: String(item.customer_id),
      severity: status === "at_risk" ? "critical" : "warning",
      eventType: status === "due_soon" ? "renewal_due_soon" : "renewal_risk_detected",
      summary: status === "due_soon" ? "Renewal due soon and needs owner plan." : "Renewal risk detected from health and activity signals.",
      evidence: [`renewal_status=${status}`],
      recommendedAction: "Create retention plan and manager check-in if unresolved."
    });
  }

  for (const event of (conversionsRes.data ?? []) as Array<Record<string, unknown>>) {
    matches.push({
      entityType: "trial_org",
      entityId: String(event.target_org_id ?? event.id),
      severity: "info",
      eventType: "conversion_signal",
      summary: String(event.event_summary ?? "Conversion signal detected."),
      evidence: [`conversion_event=${String(event.event_type)}`],
      recommendedAction: "Convert signal into explicit next conversion action."
    });
  }

  const filtered = params.ownerId
    ? matches.filter((item) => !item.ownerId || item.ownerId === params.ownerId)
    : matches;

  const events: BusinessEvent[] = [];
  let createdCount = 0;
  let updatedCount = 0;
  for (const match of filtered) {
    const result = await upsertBusinessEvent({
      supabase: params.supabase,
      orgId: params.orgId,
      entityType: match.entityType,
      entityId: match.entityId,
      eventType: match.eventType,
      severity: match.severity,
      eventSummary: match.summary,
      eventPayload: {
        owner_id: match.ownerId ?? null,
        customer_id: match.customerId ?? null,
        deal_room_id: match.dealRoomId ?? null,
        evidence: match.evidence,
        recommended_action: match.recommendedAction
      }
    });
    if (result.created) createdCount += 1;
    else updatedCount += 1;
    events.push(result.event);
  }

  return {
    matchedCount: filtered.length,
    createdCount,
    updatedCount,
    events
  };
}
