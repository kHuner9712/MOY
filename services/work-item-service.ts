import { resolveWorkItemTransition, type WorkItemOperation } from "@/lib/work-item-state";
import { buildWorkItemDraftFromAlert } from "@/lib/work-item-builder";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { mapAlertRow, mapTaskExecutionLogRow, mapWorkItemRow } from "@/services/mappers";
import type { AlertItem } from "@/types/alert";
import type { Database, Json } from "@/types/database";
import type { TaskExecutionLog, WorkItem, WorkItemStatus, WorkItemTraceContext, WorkItemTriggerOrigin } from "@/types/work";

type DbClient = ServerSupabaseClient;
type WorkItemRow = Database["public"]["Tables"]["work_items"]["Row"];
type TaskExecutionLogInsert = Database["public"]["Tables"]["task_execution_logs"]["Insert"];

interface ProfileLite {
  id: string;
  display_name: string;
}

interface CustomerLite {
  id: string;
  company_name: string;
}

interface BusinessEventTraceRow {
  id: string;
  entity_type: string;
  entity_id: string;
  event_payload: Record<string, unknown> | null;
}

interface AlertTraceRow {
  id: string;
  customer_id: string | null;
  opportunity_id: string | null;
  source: string;
  rule_type: string;
}

interface InterventionTraceRow {
  id: string;
  deal_room_id: string | null;
  request_type: string;
}

interface WorkItemStatusRow {
  id: string;
  status: WorkItemStatus;
}

function nowIso(): string {
  return new Date().toISOString();
}

function asRecord(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function readString(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

function readPayloadRef(payload: Record<string, unknown>, key: string): string | null {
  return readString(payload[key]);
}

export function isWorkItemActiveStatus(status: WorkItemStatus): boolean {
  return status === "todo" || status === "in_progress" || status === "snoozed";
}

export function findReusableWorkItemIdByStatus(rows: WorkItemStatusRow[]): string | null {
  for (const row of rows) {
    if (isWorkItemActiveStatus(row.status)) return row.id;
  }
  return null;
}

export function deriveWorkItemTriggerOrigin(params: {
  sourceType: WorkItem["sourceType"];
  sourceRefType: string | null;
  aiGenerated: boolean;
}): WorkItemTriggerOrigin {
  if (params.sourceRefType === "business_event") return "rule";
  if (params.sourceType === "manager_assigned") return "manager";
  if (params.sourceType === "manual") return "manual";
  if (params.aiGenerated) return "ai";
  return "system";
}

export function buildWorkItemTraceContext(params: {
  item: WorkItem;
  businessEvent?: BusinessEventTraceRow | null;
  alert?: AlertTraceRow | null;
  intervention?: InterventionTraceRow | null;
}): WorkItemTraceContext {
  const payload = asRecord(params.businessEvent?.event_payload);
  const linkedBusinessEventId = params.item.sourceRefType === "business_event" ? params.item.sourceRefId : null;
  const linkedAlertId = params.item.sourceRefType === "alert" ? params.item.sourceRefId : null;
  const linkedInterventionRequestId =
    params.item.sourceRefType === "intervention_request" ? params.item.sourceRefId : null;
  const linkedDealRoomId =
    params.intervention?.deal_room_id ??
    readPayloadRef(payload, "deal_room_id") ??
    null;
  const linkedCustomerId =
    params.item.customerId ??
    params.alert?.customer_id ??
    readPayloadRef(payload, "customer_id") ??
    null;
  const linkedOpportunityId =
    params.item.opportunityId ??
    params.alert?.opportunity_id ??
    readPayloadRef(payload, "opportunity_id") ??
    null;

  let triggerEntityType: string | null = null;
  let triggerEntityId: string | null = null;
  if (params.businessEvent) {
    triggerEntityType = params.businessEvent.entity_type;
    triggerEntityId = params.businessEvent.entity_id;
  } else if (params.intervention?.deal_room_id) {
    triggerEntityType = "deal_room";
    triggerEntityId = params.intervention.deal_room_id;
  } else if (params.alert?.customer_id) {
    triggerEntityType = "customer";
    triggerEntityId = params.alert.customer_id;
  } else if (params.alert?.opportunity_id) {
    triggerEntityType = "opportunity";
    triggerEntityId = params.alert.opportunity_id;
  } else if (params.item.sourceRefType && params.item.sourceRefId) {
    triggerEntityType = params.item.sourceRefType;
    triggerEntityId = params.item.sourceRefId;
  } else if (params.item.customerId) {
    triggerEntityType = "customer";
    triggerEntityId = params.item.customerId;
  } else if (params.item.opportunityId) {
    triggerEntityType = "opportunity";
    triggerEntityId = params.item.opportunityId;
  }

  return {
    sourceType: params.item.sourceType,
    sourceRefType: params.item.sourceRefType,
    sourceRefId: params.item.sourceRefId,
    triggerOrigin: deriveWorkItemTriggerOrigin({
      sourceType: params.item.sourceType,
      sourceRefType: params.item.sourceRefType,
      aiGenerated: params.item.aiGenerated
    }),
    triggerEntityType,
    triggerEntityId,
    linkedCustomerId,
    linkedOpportunityId,
    linkedDealRoomId,
    linkedBusinessEventId,
    linkedAlertId,
    linkedInterventionRequestId
  };
}

async function attachWorkItemTraceContext(params: {
  supabase: DbClient;
  orgId: string;
  items: WorkItem[];
}): Promise<WorkItem[]> {
  if (params.items.length === 0) return [];

  const eventIds = Array.from(
    new Set(
      params.items
        .filter((item) => item.sourceRefType === "business_event" && item.sourceRefId)
        .map((item) => item.sourceRefId as string)
    )
  );
  const alertIds = Array.from(
    new Set(
      params.items
        .filter((item) => item.sourceRefType === "alert" && item.sourceRefId)
        .map((item) => item.sourceRefId as string)
    )
  );
  const interventionIds = Array.from(
    new Set(
      params.items
        .filter((item) => item.sourceRefType === "intervention_request" && item.sourceRefId)
        .map((item) => item.sourceRefId as string)
    )
  );

  const [eventRes, alertRes, interventionRes] = await Promise.all([
    eventIds.length
      ? (params.supabase as any)
          .from("business_events")
          .select("id, entity_type, entity_id, event_payload")
          .eq("org_id", params.orgId)
          .in("id", eventIds)
      : Promise.resolve({ data: [], error: null }),
    alertIds.length
      ? (params.supabase as any)
          .from("alerts")
          .select("id, customer_id, opportunity_id, source, rule_type")
          .eq("org_id", params.orgId)
          .in("id", alertIds)
      : Promise.resolve({ data: [], error: null }),
    interventionIds.length
      ? (params.supabase as any)
          .from("intervention_requests")
          .select("id, deal_room_id, request_type")
          .eq("org_id", params.orgId)
          .in("id", interventionIds)
      : Promise.resolve({ data: [], error: null })
  ]);

  if (eventRes.error) throw new Error(eventRes.error.message);
  if (alertRes.error) throw new Error(alertRes.error.message);
  if (interventionRes.error) throw new Error(interventionRes.error.message);

  const eventMap = new Map<string, BusinessEventTraceRow>(
    ((eventRes.data ?? []) as BusinessEventTraceRow[]).map((row) => [row.id, row])
  );
  const alertMap = new Map<string, AlertTraceRow>(
    ((alertRes.data ?? []) as AlertTraceRow[]).map((row) => [row.id, row])
  );
  const interventionMap = new Map<string, InterventionTraceRow>(
    ((interventionRes.data ?? []) as InterventionTraceRow[]).map((row) => [row.id, row])
  );

  return params.items.map((item) => {
    const traceContext = buildWorkItemTraceContext({
      item,
      businessEvent: item.sourceRefType === "business_event" && item.sourceRefId ? eventMap.get(item.sourceRefId) ?? null : null,
      alert: item.sourceRefType === "alert" && item.sourceRefId ? alertMap.get(item.sourceRefId) ?? null : null,
      intervention:
        item.sourceRefType === "intervention_request" && item.sourceRefId ? interventionMap.get(item.sourceRefId) ?? null : null
    });
    return {
      ...item,
      traceContext
    };
  });
}

async function getExistingActiveWorkItemBySourceRef(params: {
  supabase: DbClient;
  orgId: string;
  sourceRefType: string;
  sourceRefId: string;
}): Promise<WorkItem | null> {
  const { data, error } = await params.supabase
    .from("work_items")
    .select("id, status")
    .eq("org_id", params.orgId)
    .eq("source_ref_type", params.sourceRefType)
    .eq("source_ref_id", params.sourceRefId)
    .order("updated_at", { ascending: false });
  if (error) throw new Error(error.message);

  const reusableId = findReusableWorkItemIdByStatus((data ?? []) as WorkItemStatusRow[]);
  if (!reusableId) return null;

  return getWorkItemById({
    supabase: params.supabase,
    orgId: params.orgId,
    workItemId: reusableId
  });
}

async function writeExecutionLog(params: {
  supabase: DbClient;
  orgId: string;
  workItemId: string;
  userId: string;
  actionType: Database["public"]["Enums"]["task_action_type"];
  actionNote?: string | null;
  beforeSnapshot?: unknown;
  afterSnapshot?: unknown;
}): Promise<void> {
  const payload: TaskExecutionLogInsert = {
    org_id: params.orgId,
    work_item_id: params.workItemId,
    user_id: params.userId,
    action_type: params.actionType,
    action_note: params.actionNote ?? null,
    before_snapshot: (params.beforeSnapshot ?? {}) as unknown as Json,
    after_snapshot: (params.afterSnapshot ?? {}) as unknown as Json
  };

  const { error } = await params.supabase.from("task_execution_logs").insert(payload);
  if (error) {
    throw new Error(error.message);
  }
}

export async function listWorkItems(params: {
  supabase: DbClient;
  orgId: string;
  ownerId?: string;
  customerId?: string;
  statuses?: WorkItemStatus[];
  limit?: number;
}): Promise<WorkItem[]> {
  let query = params.supabase
    .from("work_items")
    .select("*, owner:profiles!work_items_owner_id_fkey(id, display_name), customer:customers!work_items_customer_id_fkey(id, company_name)")
    .eq("org_id", params.orgId)
    .order("priority_score", { ascending: false })
    .order("due_at", { ascending: true, nullsFirst: false })
    .limit(params.limit ?? 200);

  if (params.ownerId) query = query.eq("owner_id", params.ownerId);
  if (params.customerId) query = query.eq("customer_id", params.customerId);
  if (params.statuses?.length) query = query.in("status", params.statuses);

  const { data, error } = await query;
  if (error) throw new Error(error.message);

  const rows = (data ?? []) as Array<WorkItemRow & { owner?: ProfileLite | null; customer?: CustomerLite | null }>;
  const items = rows.map((item) => mapWorkItemRow(item));
  return attachWorkItemTraceContext({
    supabase: params.supabase,
    orgId: params.orgId,
    items
  });
}

export async function getWorkItemById(params: {
  supabase: DbClient;
  orgId: string;
  workItemId: string;
}): Promise<WorkItem | null> {
  const { data, error } = await params.supabase
    .from("work_items")
    .select("*, owner:profiles!work_items_owner_id_fkey(id, display_name), customer:customers!work_items_customer_id_fkey(id, company_name)")
    .eq("org_id", params.orgId)
    .eq("id", params.workItemId)
    .maybeSingle();

  if (error) throw new Error(error.message);
  if (!data) return null;

  const item = mapWorkItemRow(data as WorkItemRow & { owner?: ProfileLite | null; customer?: CustomerLite | null });
  const [enriched] = await attachWorkItemTraceContext({
    supabase: params.supabase,
    orgId: params.orgId,
    items: [item]
  });
  return enriched ?? item;
}

export async function createWorkItem(params: {
  supabase: DbClient;
  orgId: string;
  ownerId: string;
  customerId?: string | null;
  opportunityId?: string | null;
  sourceType: Database["public"]["Enums"]["work_item_source_type"];
  workType: Database["public"]["Enums"]["work_item_type"];
  title: string;
  description: string;
  rationale: string;
  priorityScore: number;
  priorityBand: Database["public"]["Enums"]["work_priority_band"];
  scheduledFor?: string | null;
  dueAt?: string | null;
  sourceRefType?: string | null;
  sourceRefId?: string | null;
  aiGenerated?: boolean;
  aiRunId?: string | null;
  createdBy: string;
}): Promise<WorkItem> {
  const insertPayload: Database["public"]["Tables"]["work_items"]["Insert"] = {
    org_id: params.orgId,
    owner_id: params.ownerId,
    customer_id: params.customerId ?? null,
    opportunity_id: params.opportunityId ?? null,
    source_type: params.sourceType,
    work_type: params.workType,
    title: params.title,
    description: params.description,
    rationale: params.rationale,
    priority_score: params.priorityScore,
    priority_band: params.priorityBand,
    status: "todo",
    scheduled_for: params.scheduledFor ?? null,
    due_at: params.dueAt ?? null,
    source_ref_type: params.sourceRefType ?? null,
    source_ref_id: params.sourceRefId ?? null,
    ai_generated: params.aiGenerated ?? false,
    ai_run_id: params.aiRunId ?? null,
    created_by: params.createdBy
  };

  const { data, error } = await params.supabase
    .from("work_items")
    .insert(insertPayload)
    .select("*, owner:profiles!work_items_owner_id_fkey(id, display_name), customer:customers!work_items_customer_id_fkey(id, company_name)")
    .single();

  if (error || !data) throw new Error(error?.message ?? "Failed to create work item");

  const mapped = mapWorkItemRow(data as WorkItemRow & { owner?: ProfileLite | null; customer?: CustomerLite | null });
  await writeExecutionLog({
    supabase: params.supabase,
    orgId: params.orgId,
    workItemId: mapped.id,
    userId: params.createdBy,
    actionType: "created",
    afterSnapshot: mapped
  });

  const [enriched] = await attachWorkItemTraceContext({
    supabase: params.supabase,
    orgId: params.orgId,
    items: [mapped]
  });
  return enriched ?? mapped;
}

export async function createOrReuseWorkItemBySourceRef(params: {
  supabase: DbClient;
  orgId: string;
  ownerId: string;
  customerId?: string | null;
  opportunityId?: string | null;
  sourceType: Database["public"]["Enums"]["work_item_source_type"];
  workType: Database["public"]["Enums"]["work_item_type"];
  title: string;
  description: string;
  rationale: string;
  priorityScore: number;
  priorityBand: Database["public"]["Enums"]["work_priority_band"];
  scheduledFor?: string | null;
  dueAt?: string | null;
  sourceRefType: string;
  sourceRefId: string;
  aiGenerated?: boolean;
  aiRunId?: string | null;
  createdBy: string;
}): Promise<{ workItem: WorkItem; created: boolean }> {
  const existing = await getExistingActiveWorkItemBySourceRef({
    supabase: params.supabase,
    orgId: params.orgId,
    sourceRefType: params.sourceRefType,
    sourceRefId: params.sourceRefId
  });
  if (existing) {
    return {
      workItem: existing,
      created: false
    };
  }

  const workItem = await createWorkItem({
    supabase: params.supabase,
    orgId: params.orgId,
    ownerId: params.ownerId,
    customerId: params.customerId ?? null,
    opportunityId: params.opportunityId ?? null,
    sourceType: params.sourceType,
    workType: params.workType,
    title: params.title,
    description: params.description,
    rationale: params.rationale,
    priorityScore: params.priorityScore,
    priorityBand: params.priorityBand,
    scheduledFor: params.scheduledFor ?? null,
    dueAt: params.dueAt ?? null,
    sourceRefType: params.sourceRefType,
    sourceRefId: params.sourceRefId,
    aiGenerated: params.aiGenerated ?? false,
    aiRunId: params.aiRunId ?? null,
    createdBy: params.createdBy
  });

  return {
    workItem,
    created: true
  };
}

export async function createWorkItemFromAlert(params: {
  supabase: DbClient;
  orgId: string;
  alertId: string;
  actorUserId: string;
}): Promise<{ workItem: WorkItem; created: boolean; alert: AlertItem }> {
  const { data: alertRaw, error: alertError } = await params.supabase
    .from("alerts")
    .select("*, owner:profiles!alerts_owner_id_fkey(id, display_name), customer:customers!alerts_customer_id_fkey(id, company_name)")
    .eq("org_id", params.orgId)
    .eq("id", params.alertId)
    .single();

  if (alertError || !alertRaw) throw new Error(alertError?.message ?? "Alert not found");
  const alert = mapAlertRow(alertRaw as never);
  const draft = buildWorkItemDraftFromAlert(alert);

  const ownerId = alert.ownerId || params.actorUserId;
  const priorityBand = alert.level === "critical" ? "critical" : alert.level === "warning" ? "high" : "medium";
  const priorityScore = alert.level === "critical" ? 92 : alert.level === "warning" ? 74 : 52;

  const created = await createOrReuseWorkItemBySourceRef({
    supabase: params.supabase,
    orgId: params.orgId,
    ownerId,
    customerId: alert.customerId || null,
    opportunityId: alert.opportunityId ?? null,
    sourceType: draft.sourceType,
    workType: draft.workType,
    title: draft.title,
    description: draft.description,
    rationale: draft.rationale,
    priorityScore,
    priorityBand,
    dueAt: alert.dueAt,
    sourceRefType: draft.sourceRefType,
    sourceRefId: draft.sourceRefId,
    aiGenerated: alert.source !== "rule",
    createdBy: params.actorUserId
  });

  return {
    workItem: created.workItem,
    created: created.created,
    alert
  };
}

export async function updateWorkItemStatus(params: {
  supabase: DbClient;
  orgId: string;
  workItemId: string;
  actorUserId: string;
  operation: WorkItemOperation;
  note?: string | null;
  snoozedUntil?: string | null;
}): Promise<WorkItem> {
  const before = await getWorkItemById({
    supabase: params.supabase,
    orgId: params.orgId,
    workItemId: params.workItemId
  });
  if (!before) throw new Error("Work item not found");

  const transition = resolveWorkItemTransition(before.status, params.operation);
  if (!transition.valid) {
    throw new Error(`Invalid transition from ${before.status} by ${params.operation}`);
  }

  const patch: Database["public"]["Tables"]["work_items"]["Update"] = {
    status: transition.nextStatus,
    updated_at: nowIso()
  };

  if (transition.nextStatus === "done") {
    patch.completed_at = nowIso();
    patch.snoozed_until = null;
  } else if (transition.nextStatus === "snoozed") {
    patch.snoozed_until = params.snoozedUntil ?? new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString();
    patch.completed_at = null;
  } else {
    patch.completed_at = null;
    if (transition.nextStatus === "todo" || transition.nextStatus === "in_progress") {
      patch.snoozed_until = null;
    }
  }

  const { data, error } = await params.supabase
    .from("work_items")
    .update(patch)
    .eq("org_id", params.orgId)
    .eq("id", params.workItemId)
    .select("*, owner:profiles!work_items_owner_id_fkey(id, display_name), customer:customers!work_items_customer_id_fkey(id, company_name)")
    .single();

  if (error || !data) throw new Error(error?.message ?? "Failed to update work item");

  const after = mapWorkItemRow(data as WorkItemRow & { owner?: ProfileLite | null; customer?: CustomerLite | null });

  await writeExecutionLog({
    supabase: params.supabase,
    orgId: params.orgId,
    workItemId: after.id,
    userId: params.actorUserId,
    actionType: transition.actionType,
    actionNote: params.note,
    beforeSnapshot: before,
    afterSnapshot: after
  });

  const [enriched] = await attachWorkItemTraceContext({
    supabase: params.supabase,
    orgId: params.orgId,
    items: [after]
  });
  return enriched ?? after;
}

export async function completeWorkItemsBySourceRef(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  sourceRefType: string;
  sourceRefId: string;
  note: string;
}): Promise<number> {
  const { data, error } = await params.supabase
    .from("work_items")
    .select("*")
    .eq("org_id", params.orgId)
    .eq("source_ref_type", params.sourceRefType)
    .eq("source_ref_id", params.sourceRefId)
    .in("status", ["todo", "in_progress", "snoozed"]);
  if (error) throw new Error(error.message);

  const rows = (data ?? []) as WorkItemRow[];
  if (rows.length === 0) return 0;

  for (const row of rows) {
    await updateWorkItemStatus({
      supabase: params.supabase,
      orgId: params.orgId,
      workItemId: row.id,
      actorUserId: params.actorUserId,
      operation: "complete",
      note: params.note
    });
  }
  return rows.length;
}

export async function listTaskExecutionLogs(params: {
  supabase: DbClient;
  orgId: string;
  workItemId: string;
  limit?: number;
}): Promise<TaskExecutionLog[]> {
  const { data, error } = await params.supabase
    .from("task_execution_logs")
    .select("*")
    .eq("org_id", params.orgId)
    .eq("work_item_id", params.workItemId)
    .order("created_at", { ascending: false })
    .limit(params.limit ?? 20);

  if (error) throw new Error(error.message);
  return (data ?? []).map((item: Database["public"]["Tables"]["task_execution_logs"]["Row"]) => mapTaskExecutionLogRow(item as never));
}

export async function convertWorkItemToFollowup(params: {
  supabase: DbClient;
  orgId: string;
  workItemId: string;
  actorUserId: string;
}): Promise<{ workItem: WorkItem; followupId: string }> {
  const item = await getWorkItemById({
    supabase: params.supabase,
    orgId: params.orgId,
    workItemId: params.workItemId
  });
  if (!item) throw new Error("Work item not found");
  if (!item.customerId) throw new Error("Work item has no linked customer");

  const { data: customerRaw, error: customerError } = await params.supabase
    .from("customers")
    .select("id, owner_id")
    .eq("org_id", params.orgId)
    .eq("id", item.customerId)
    .single();
  if (customerError || !customerRaw) throw new Error(customerError?.message ?? "Customer not found");

  const now = nowIso();
  const nextFollowup = new Date(Date.now() + 3 * 24 * 60 * 60 * 1000).toISOString();

  const { data: insertedFollowup, error: insertFollowupError } = await params.supabase
    .from("followups")
    .insert({
      org_id: params.orgId,
      customer_id: item.customerId,
      owner_id: customerRaw.owner_id,
      communication_type: "other",
      summary: `[Task Converted] ${item.title}`,
      customer_needs: item.description || "Continue pushing customer progression",
      objections: null,
      next_step: item.rationale || "Execute next action and confirm timeline",
      next_followup_at: nextFollowup,
      needs_ai_analysis: true,
      source_input_id: null,
      draft_status: "confirmed",
      created_by: params.actorUserId
    })
    .select("id")
    .single();

  if (insertFollowupError || !insertedFollowup) {
    throw new Error(insertFollowupError?.message ?? "Failed to create followup from work item");
  }

  await params.supabase
    .from("customers")
    .update({
      last_followup_at: now,
      next_followup_at: nextFollowup,
      updated_at: now
    })
    .eq("id", item.customerId);

  const updated = await updateWorkItemStatus({
    supabase: params.supabase,
    orgId: params.orgId,
    workItemId: item.id,
    actorUserId: params.actorUserId,
    operation: "complete",
    note: `Converted to followup ${insertedFollowup.id}`
  });

  await writeExecutionLog({
    supabase: params.supabase,
    orgId: params.orgId,
    workItemId: item.id,
    userId: params.actorUserId,
    actionType: "converted_to_followup",
    actionNote: `Converted to followup ${insertedFollowup.id}`,
    beforeSnapshot: item,
    afterSnapshot: {
      status: updated.status,
      followup_id: insertedFollowup.id
    }
  });

  return {
    workItem: updated,
    followupId: insertedFollowup.id
  };
}
