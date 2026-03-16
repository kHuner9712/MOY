import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { mapPlaybookEntryRow, mapPlaybookFeedbackRow, mapPlaybookRow } from "@/services/mappers";
import type { Database } from "@/types/database";
import type { Playbook, PlaybookEntry, PlaybookFeedback, PlaybookFeedbackType, PlaybookStatus, PlaybookType, PlaybookWithEntries } from "@/types/playbook";

type DbClient = ServerSupabaseClient;

export async function listPlaybooks(params: {
  supabase: DbClient;
  orgId: string;
  ownerUserId?: string;
  scopeType?: Database["public"]["Enums"]["playbook_scope_type"];
  playbookType?: PlaybookType;
  statuses?: PlaybookStatus[];
  limit?: number;
  includeEntries?: boolean;
}): Promise<PlaybookWithEntries[]> {
  let query = params.supabase
    .from("playbooks")
    .select("*")
    .eq("org_id", params.orgId)
    .order("updated_at", { ascending: false })
    .limit(params.limit ?? 50);

  if (params.ownerUserId) query = query.eq("owner_user_id", params.ownerUserId);
  if (params.scopeType) query = query.eq("scope_type", params.scopeType);
  if (params.playbookType) query = query.eq("playbook_type", params.playbookType);
  if (params.statuses?.length) query = query.in("status", params.statuses);

  const { data, error } = await query;
  if (error) throw new Error(error.message);

  const playbooks: Playbook[] = (data ?? []).map((row: Database["public"]["Tables"]["playbooks"]["Row"]) => mapPlaybookRow(row));
  if (!params.includeEntries || playbooks.length === 0) {
    return playbooks.map((item) => ({ playbook: item, entries: [] }));
  }

  const ids = playbooks.map((item) => item.id);
  const { data: entryRows, error: entryError } = await params.supabase
    .from("playbook_entries")
    .select("*")
    .eq("org_id", params.orgId)
    .in("playbook_id", ids)
    .order("sort_order", { ascending: true })
    .order("created_at", { ascending: true });
  if (entryError) throw new Error(entryError.message);

  const grouped = new Map<string, PlaybookEntry[]>();
  for (const row of entryRows ?? []) {
    const mapped = mapPlaybookEntryRow(row as Database["public"]["Tables"]["playbook_entries"]["Row"]);
    const list = grouped.get(mapped.playbookId) ?? [];
    list.push(mapped);
    grouped.set(mapped.playbookId, list);
  }

  return playbooks.map((playbook) => ({
    playbook,
    entries: grouped.get(playbook.id) ?? []
  }));
}

export async function createPlaybookWithEntries(params: {
  supabase: DbClient;
  orgId: string;
  scopeType: Database["public"]["Enums"]["playbook_scope_type"];
  ownerUserId?: string | null;
  playbookType: Database["public"]["Enums"]["playbook_type"];
  title: string;
  summary: string;
  status?: Database["public"]["Enums"]["playbook_status"];
  confidenceScore: number;
  applicabilityNotes: string;
  sourceSnapshot: Record<string, unknown>;
  generatedBy: string;
  aiRunId?: string | null;
  entries: Array<{
    entryTitle: string;
    entrySummary: string;
    conditions?: Record<string, unknown>;
    recommendedActions?: string[];
    cautionNotes?: string[];
    evidenceSnapshot?: Record<string, unknown>;
    successSignal?: Record<string, unknown>;
    failureModes?: string[];
    confidenceScore: number;
    sortOrder?: number;
  }>;
}): Promise<PlaybookWithEntries> {
  const playbookPayload: Database["public"]["Tables"]["playbooks"]["Insert"] = {
    org_id: params.orgId,
    scope_type: params.scopeType,
    owner_user_id: params.ownerUserId ?? null,
    playbook_type: params.playbookType,
    title: params.title,
    summary: params.summary,
    status: params.status ?? "active",
    confidence_score: params.confidenceScore,
    applicability_notes: params.applicabilityNotes,
    source_snapshot: params.sourceSnapshot as unknown as Database["public"]["Tables"]["playbooks"]["Insert"]["source_snapshot"],
    generated_by: params.generatedBy,
    ai_run_id: params.aiRunId ?? null
  };

  const { data: playbookRaw, error: playbookError } = await params.supabase.from("playbooks").insert(playbookPayload).select("*").single();
  if (playbookError || !playbookRaw) throw new Error(playbookError?.message ?? "Failed to create playbook");

  const playbook = mapPlaybookRow(playbookRaw as Database["public"]["Tables"]["playbooks"]["Row"]);

  if (params.entries.length === 0) {
    return {
      playbook,
      entries: []
    };
  }

  const entryPayloads: Database["public"]["Tables"]["playbook_entries"]["Insert"][] = params.entries.map((item, index) => ({
    org_id: params.orgId,
    playbook_id: playbook.id,
    entry_title: item.entryTitle,
    entry_summary: item.entrySummary,
    conditions: (item.conditions ?? {}) as unknown as Database["public"]["Tables"]["playbook_entries"]["Insert"]["conditions"],
    recommended_actions: (item.recommendedActions ?? []) as unknown as Database["public"]["Tables"]["playbook_entries"]["Insert"]["recommended_actions"],
    caution_notes: (item.cautionNotes ?? []) as unknown as Database["public"]["Tables"]["playbook_entries"]["Insert"]["caution_notes"],
    evidence_snapshot: (item.evidenceSnapshot ?? {}) as unknown as Database["public"]["Tables"]["playbook_entries"]["Insert"]["evidence_snapshot"],
    success_signal: (item.successSignal ?? {}) as unknown as Database["public"]["Tables"]["playbook_entries"]["Insert"]["success_signal"],
    failure_modes: (item.failureModes ?? []) as unknown as Database["public"]["Tables"]["playbook_entries"]["Insert"]["failure_modes"],
    confidence_score: item.confidenceScore,
    sort_order: item.sortOrder ?? index + 1
  }));

  const { data: entryRows, error: entryError } = await params.supabase.from("playbook_entries").insert(entryPayloads).select("*");
  if (entryError) throw new Error(entryError.message);

  return {
    playbook,
    entries: (entryRows ?? []).map((row: Database["public"]["Tables"]["playbook_entries"]["Row"]) => mapPlaybookEntryRow(row))
  };
}

export async function addPlaybookFeedback(params: {
  supabase: DbClient;
  orgId: string;
  userId: string;
  playbookId: string;
  playbookEntryId?: string | null;
  feedbackType: PlaybookFeedbackType;
  feedbackText?: string | null;
}): Promise<PlaybookFeedback> {
  const payload: Database["public"]["Tables"]["playbook_feedback"]["Insert"] = {
    org_id: params.orgId,
    user_id: params.userId,
    playbook_id: params.playbookId,
    playbook_entry_id: params.playbookEntryId ?? null,
    feedback_type: params.feedbackType,
    feedback_text: params.feedbackText ?? null
  };

  const { data, error } = await params.supabase.from("playbook_feedback").insert(payload).select("*").single();
  if (error || !data) throw new Error(error?.message ?? "Failed to save playbook feedback");
  return mapPlaybookFeedbackRow(data as Database["public"]["Tables"]["playbook_feedback"]["Row"]);
}
