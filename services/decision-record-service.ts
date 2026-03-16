import { deriveDecisionApprovalLinkage } from "@/lib/deal-decision-linkage";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { addSystemEventMessage, createCollaborationThread } from "@/services/collaboration-thread-service";
import { mapDecisionRecordRow } from "@/services/mappers";
import { createWorkItem } from "@/services/work-item-service";
import type { Database } from "@/types/database";
import type { DecisionRecord } from "@/types/deal";

type DbClient = ServerSupabaseClient;
type DecisionRow = Database["public"]["Tables"]["decision_records"]["Row"];

function nowIso(): string {
  return new Date().toISOString();
}

async function ensureSystemThread(params: {
  supabase: DbClient;
  orgId: string;
  dealRoomId: string;
  createdBy: string;
  preferredType: Database["public"]["Enums"]["collaboration_thread_type"];
  preferredTitle: string;
}): Promise<string> {
  const { data, error } = await params.supabase
    .from("collaboration_threads")
    .select("id")
    .eq("org_id", params.orgId)
    .eq("deal_room_id", params.dealRoomId)
    .eq("thread_type", params.preferredType)
    .eq("status", "open")
    .limit(1)
    .maybeSingle();
  if (error) throw new Error(error.message);
  if (data?.id) return data.id;

  const thread = await createCollaborationThread({
    supabase: params.supabase,
    orgId: params.orgId,
    dealRoomId: params.dealRoomId,
    threadType: params.preferredType,
    title: params.preferredTitle,
    createdBy: params.createdBy,
    summary: "System-created thread for decision follow-up."
  });
  return thread.id;
}

async function applyApprovedDecisionLinkage(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  decision: DecisionRecord;
  ownerId: string;
}): Promise<void> {
  const linkage = deriveDecisionApprovalLinkage(params.decision.decisionType);
  await createWorkItem({
    supabase: params.supabase,
    orgId: params.orgId,
    ownerId: params.ownerId,
    customerId: params.decision.customerId,
    opportunityId: params.decision.opportunityId,
    sourceType: "manager_assigned",
    workType: linkage.workType,
    title: `[Decision] ${params.decision.title}`,
    description: params.decision.contextSummary || "Execute approved decision and move checkpoint.",
    rationale: params.decision.decisionReason || "Approved decision requires execution.",
    priorityScore: 82,
    priorityBand: "high",
    dueAt: params.decision.dueAt,
    sourceRefType: "decision_record",
    sourceRefId: params.decision.id,
    aiGenerated: false,
    createdBy: params.actorUserId
  });

  const checkpointType = linkage.checkpointType;
  const checkpointPayload: Database["public"]["Tables"]["deal_checkpoints"]["Insert"] = {
    org_id: params.orgId,
    deal_room_id: params.decision.dealRoomId,
    checkpoint_type: checkpointType,
    status: "pending",
    title: `Decision-linked checkpoint: ${checkpointType}`,
    description: `Created from decision ${params.decision.id}`,
    due_at: params.decision.dueAt,
    owner_id: params.ownerId,
    evidence_snapshot: {
      decision_id: params.decision.id,
      decision_type: params.decision.decisionType
    } as unknown as Database["public"]["Tables"]["deal_checkpoints"]["Insert"]["evidence_snapshot"]
  };

  await params.supabase.from("deal_checkpoints").upsert(checkpointPayload, { onConflict: "deal_room_id,checkpoint_type" });

  const threadId = await ensureSystemThread({
    supabase: params.supabase,
    orgId: params.orgId,
    dealRoomId: params.decision.dealRoomId,
    createdBy: params.actorUserId,
    preferredType: "next_step",
    preferredTitle: "Decision-linked next steps"
  });

  await addSystemEventMessage({
    supabase: params.supabase,
    orgId: params.orgId,
    threadId,
    actorUserId: params.actorUserId,
    eventText: `Decision approved: **${params.decision.title}**. Linked execution task and checkpoint have been created.`,
    sourceRefType: "decision_record",
    sourceRefId: params.decision.id
  });
}

export async function listDecisionRecords(params: {
  supabase: DbClient;
  orgId: string;
  dealRoomId: string;
  statuses?: Database["public"]["Enums"]["decision_status"][];
}): Promise<DecisionRecord[]> {
  let query = params.supabase
    .from("decision_records")
    .select("*")
    .eq("org_id", params.orgId)
    .eq("deal_room_id", params.dealRoomId)
    .order("created_at", { ascending: false });
  if (params.statuses?.length) query = query.in("status", params.statuses);

  const { data, error } = await query;
  if (error) throw new Error(error.message);
  return (data ?? []).map((row: DecisionRow) => mapDecisionRecordRow(row));
}

export async function createDecisionRecord(params: {
  supabase: DbClient;
  orgId: string;
  dealRoomId: string;
  customerId: string;
  opportunityId?: string | null;
  decisionType: Database["public"]["Enums"]["decision_type"];
  title: string;
  contextSummary?: string;
  optionsConsidered?: string[];
  recommendedOption?: string | null;
  decisionReason?: string | null;
  status?: Database["public"]["Enums"]["decision_status"];
  decidedBy?: string | null;
  requestedBy: string;
  ownerIdForLinkedTask: string;
  dueAt?: string | null;
}): Promise<DecisionRecord> {
  const payload: Database["public"]["Tables"]["decision_records"]["Insert"] = {
    org_id: params.orgId,
    deal_room_id: params.dealRoomId,
    customer_id: params.customerId,
    opportunity_id: params.opportunityId ?? null,
    decision_type: params.decisionType,
    status: params.status ?? "proposed",
    title: params.title,
    context_summary: params.contextSummary ?? "",
    options_considered: (params.optionsConsidered ?? []) as unknown as Database["public"]["Tables"]["decision_records"]["Insert"]["options_considered"],
    recommended_option: params.recommendedOption ?? null,
    decision_reason: params.decisionReason ?? null,
    decided_by: params.decidedBy ?? null,
    requested_by: params.requestedBy,
    due_at: params.dueAt ?? null,
    completed_at: params.status === "completed" ? nowIso() : null
  };

  const { data, error } = await params.supabase.from("decision_records").insert(payload).select("*").single();
  if (error || !data) throw new Error(error?.message ?? "create_decision_record_failed");
  const decision = mapDecisionRecordRow(data as DecisionRow);

  const threadId = await ensureSystemThread({
    supabase: params.supabase,
    orgId: params.orgId,
    dealRoomId: params.dealRoomId,
    createdBy: params.requestedBy,
    preferredType: "quote_review",
    preferredTitle: "Decision discussion"
  });
  await addSystemEventMessage({
    supabase: params.supabase,
    orgId: params.orgId,
    threadId,
    actorUserId: params.requestedBy,
    eventText: `Decision created: **${decision.title}** (status: ${decision.status}).`,
    sourceRefType: "decision_record",
    sourceRefId: decision.id
  });

  if (decision.status === "approved") {
    await applyApprovedDecisionLinkage({
      supabase: params.supabase,
      orgId: params.orgId,
      actorUserId: params.requestedBy,
      decision,
      ownerId: params.ownerIdForLinkedTask
    });
  }

  return decision;
}

export async function updateDecisionStatus(params: {
  supabase: DbClient;
  orgId: string;
  decisionId: string;
  status: Database["public"]["Enums"]["decision_status"];
  actorUserId: string;
  ownerIdForLinkedTask: string;
  decisionReason?: string | null;
}): Promise<DecisionRecord> {
  const patch: Database["public"]["Tables"]["decision_records"]["Update"] = {
    status: params.status,
    decision_reason: params.decisionReason ?? null,
    decided_by: params.actorUserId,
    updated_at: nowIso()
  };
  if (params.status === "completed") patch.completed_at = nowIso();
  if (params.status !== "completed") patch.completed_at = null;

  const { data, error } = await params.supabase
    .from("decision_records")
    .update(patch)
    .eq("org_id", params.orgId)
    .eq("id", params.decisionId)
    .select("*")
    .single();
  if (error || !data) throw new Error(error?.message ?? "update_decision_status_failed");
  const decision = mapDecisionRecordRow(data as DecisionRow);

  const threadId = await ensureSystemThread({
    supabase: params.supabase,
    orgId: params.orgId,
    dealRoomId: decision.dealRoomId,
    createdBy: params.actorUserId,
    preferredType: "quote_review",
    preferredTitle: "Decision discussion"
  });
  await addSystemEventMessage({
    supabase: params.supabase,
    orgId: params.orgId,
    threadId,
    actorUserId: params.actorUserId,
    eventText: `Decision status changed: **${decision.title}** -> ${decision.status}.`,
    sourceRefType: "decision_record",
    sourceRefId: decision.id
  });

  if (decision.status === "approved") {
    await applyApprovedDecisionLinkage({
      supabase: params.supabase,
      orgId: params.orgId,
      actorUserId: params.actorUserId,
      decision,
      ownerId: params.ownerIdForLinkedTask
    });
  }

  return decision;
}
