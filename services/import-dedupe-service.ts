import type { ServerSupabaseClient } from "@/lib/supabase/types";
import {
  appendImportAuditEvent,
  listImportRows,
  replaceDedupeGroups,
  updateImportJob,
  updateImportRow
} from "@/services/import-job-service";
import { mapDedupeMatchGroupRow } from "@/services/mappers";
import type { Database } from "@/types/database";
import type { DedupeMatchGroup, ImportJobRow } from "@/types/import";

type DbClient = ServerSupabaseClient;
type DedupeInsert = Database["public"]["Tables"]["dedupe_match_groups"]["Insert"];

type CandidateEntity = "customer" | "opportunity";

interface DuplicateCandidate {
  entity_type: CandidateEntity;
  id: string;
  score?: number;
  reason?: string;
}

interface GroupAccumulator {
  entityType: Database["public"]["Enums"]["import_entity_type"];
  sourceRowIds: Set<string>;
  existingEntityIds: Set<string>;
  reasons: Set<string>;
  scores: number[];
}

function parseDuplicateCandidates(input: ImportJobRow): DuplicateCandidate[] {
  if (!Array.isArray(input.duplicateCandidates)) return [];
  const output: DuplicateCandidate[] = [];

  for (const item of input.duplicateCandidates) {
    if (!item || typeof item !== "object") continue;
    const entityType = item.entity_type;
    const id = item.id;
    if ((entityType !== "customer" && entityType !== "opportunity") || typeof id !== "string") continue;

    output.push({
      entity_type: entityType,
      id,
      score: typeof item.score === "number" ? item.score : undefined,
      reason: typeof item.reason === "string" ? item.reason : undefined
    });
  }

  return output;
}

function rowStatusAfterResolution(action: Database["public"]["Enums"]["dedupe_resolution_action"]): Database["public"]["Enums"]["import_row_status"] {
  if (action === "skip") return "skipped";
  if (action === "create_new") return "valid";
  return "merge_candidate";
}

export async function runImportDedupe(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  jobId: string;
}): Promise<{
  groups: DedupeMatchGroup[];
  candidateRows: number;
}> {
  const rows = await listImportRows({
    supabase: params.supabase,
    orgId: params.orgId,
    jobId: params.jobId
  });

  const candidateRows = rows.filter((row) => row.rowStatus === "duplicate_candidate" || row.rowStatus === "merge_candidate");

  const groupsMap = new Map<string, GroupAccumulator>();

  for (const row of candidateRows) {
    const candidates = parseDuplicateCandidates(row);
    if (candidates.length === 0) continue;

    const top = candidates[0];
    const key = `${top.entity_type}:${top.id}`;
    const current =
      groupsMap.get(key) ??
      ({
        entityType: top.entity_type,
        sourceRowIds: new Set<string>(),
        existingEntityIds: new Set<string>(),
        reasons: new Set<string>(),
        scores: []
      } as GroupAccumulator);

    current.sourceRowIds.add(row.id);
    for (const candidate of candidates) {
      current.existingEntityIds.add(candidate.id);
      if (candidate.reason) current.reasons.add(candidate.reason);
      if (typeof candidate.score === "number") current.scores.push(candidate.score);
    }

    groupsMap.set(key, current);

    // promote duplicate rows into merge candidate pool for explicit user resolution.
    if (row.rowStatus === "duplicate_candidate") {
      await updateImportRow({
        supabase: params.supabase,
        rowId: row.id,
        patch: {
          row_status: "merge_candidate"
        }
      });
    }
  }

  const groupsInsert: DedupeInsert[] = Array.from(groupsMap.values()).map((group) => {
    const avgScore = group.scores.length > 0 ? group.scores.reduce((sum, value) => sum + value, 0) / group.scores.length : 0.65;
    return {
      org_id: params.orgId,
      import_job_id: params.jobId,
      entity_type: group.entityType,
      source_row_ids: Array.from(group.sourceRowIds),
      existing_entity_ids: Array.from(group.existingEntityIds),
      match_reason: Array.from(group.reasons).slice(0, 5).join(" | ") || "rule_candidate",
      confidence_score: Number(avgScore.toFixed(4)),
      resolution_status: "pending",
      resolution_action: null
    };
  });

  const groups = await replaceDedupeGroups({
    supabase: params.supabase,
    orgId: params.orgId,
    jobId: params.jobId,
    groups: groupsInsert
  });

  await updateImportJob({
    supabase: params.supabase,
    jobId: params.jobId,
    patch: {
      duplicate_rows: candidateRows.length,
      job_status: "preview_ready"
    }
  });

  await appendImportAuditEvent({
    supabase: params.supabase,
    orgId: params.orgId,
    jobId: params.jobId,
    actorUserId: params.actorUserId,
    eventType: "dedupe_reviewed",
    eventSummary: `Dedupe candidate groups generated: ${groups.length}`,
    eventPayload: {
      candidate_rows: candidateRows.length,
      group_count: groups.length
    }
  });

  return {
    groups,
    candidateRows: candidateRows.length
  };
}

export async function applyDedupeResolutions(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  jobId: string;
  resolutions: Array<{
    groupId: string;
    action: Database["public"]["Enums"]["dedupe_resolution_action"];
  }>;
}): Promise<DedupeMatchGroup[]> {
  const updatedGroups: DedupeMatchGroup[] = [];

  for (const resolution of params.resolutions) {
    const updateRes = await params.supabase
      .from("dedupe_match_groups")
      .update({
        resolution_status: "confirmed",
        resolution_action: resolution.action
      })
      .eq("org_id", params.orgId)
      .eq("import_job_id", params.jobId)
      .eq("id", resolution.groupId)
      .select("*")
      .single();

    if (updateRes.error) throw new Error(updateRes.error.message);

    const group = mapDedupeMatchGroupRow(updateRes.data as Database["public"]["Tables"]["dedupe_match_groups"]["Row"]);
    updatedGroups.push(group);

    const targetRowStatus = rowStatusAfterResolution(resolution.action);
    for (const rowId of group.sourceRowIds) {
      await updateImportRow({
        supabase: params.supabase,
        rowId,
        patch: {
          merge_resolution: resolution.action === "merge" ? "merge_existing" : resolution.action,
          row_status: targetRowStatus
        }
      });
    }
  }

  await appendImportAuditEvent({
    supabase: params.supabase,
    orgId: params.orgId,
    jobId: params.jobId,
    actorUserId: params.actorUserId,
    eventType: "dedupe_reviewed",
    eventSummary: `Dedupe resolutions saved: ${params.resolutions.length}`,
    eventPayload: {
      resolutions: params.resolutions
    }
  });

  return updatedGroups;
}

