import type { ZodSchema } from "zod";

import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import {
  buildFallbackDealPlaybookMapping,
  buildFallbackDealRoomCommandSummary,
  buildFallbackDecisionSupport,
  buildFallbackInterventionRecommendation,
  buildFallbackThreadSummary
} from "@/lib/deal-command-fallback";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { addThreadMessage, addSystemEventMessage, listThreadMessages, updateThreadSummary } from "@/services/collaboration-thread-service";
import { getActivePromptVersion } from "@/services/ai-prompt-service";
import { createAiRun, updateAiRunStatus } from "@/services/ai-run-service";
import { listDecisionRecords } from "@/services/decision-record-service";
import { getDealRoomById, updateDealRoomCommand } from "@/services/deal-room-service";
import { listDealCheckpoints } from "@/services/deal-checkpoint-service";
import { listInterventionRequests } from "@/services/intervention-request-service";
import { mapAlertRow, mapContentDraftRow, mapPrepCardRow, mapWorkItemRow } from "@/services/mappers";
import { listPlaybooks } from "@/services/playbook-service";
import {
  dealPlaybookMappingResultSchema,
  decisionSupportResultSchema,
  interventionRecommendationResultSchema,
  threadSummaryResultSchema,
  type AiProviderId,
  type AiScenario,
  type AiTriggerSource,
  type DealPlaybookMappingResult,
  type DecisionSupportResult,
  type DealRoomCommandSummaryResult,
  type InterventionRecommendationResult,
  type ThreadSummaryResult,
  dealRoomCommandSummaryResultSchema
} from "@/types/ai";
import type { Database } from "@/types/database";
import type {
  CollaborationMessage,
  CollaborationThread,
  DealCheckpoint,
  DealRoomDetailView,
  DealParticipant,
  DealRoom,
  DecisionRecord,
  InterventionRequest
} from "@/types/deal";
import type { ActionOutcome } from "@/types/outcome";

type DbClient = ServerSupabaseClient;

interface ProfileLite {
  id: string;
  display_name: string;
}

function nowIso(): string {
  return new Date().toISOString();
}

function parseObjectFromRawText(text: string | null): Record<string, unknown> {
  if (!text) throw new Error("empty_response_text");
  const parsed = JSON.parse(text);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("model_output_not_object");
  }
  return parsed as Record<string, unknown>;
}

function logAiEvent(params: {
  orgId: string;
  userId: string;
  customerId: string;
  dealRoomId: string;
  scenario: AiScenario;
  provider: string;
  model: string;
  status: "started" | "completed" | "failed";
  durationMs?: number;
  fallbackReason?: string | null;
  error?: string;
}): void {
  console.info("[deal.command.ai]", {
    org_id: params.orgId,
    user_id: params.userId,
    customer_id: params.customerId,
    deal_room_id: params.dealRoomId,
    scenario: params.scenario,
    provider: params.provider,
    model: params.model,
    status: params.status,
    duration_ms: params.durationMs ?? null,
    fallback_reason: params.fallbackReason ?? null,
    error: params.error ?? null
  });
}

async function getPromptVersion(params: { supabase: DbClient; orgId: string; scenario: AiScenario; providerId: AiProviderId }): Promise<string> {
  const prompt = await getActivePromptVersion({
    supabase: params.supabase,
    orgId: params.orgId,
    scenario: params.scenario,
    providerId: params.providerId
  });
  return prompt.version;
}

async function runStructuredScenario<T>(params: {
  supabase: DbClient;
  orgId: string;
  customerId: string;
  actorUserId: string;
  triggerSource: AiTriggerSource;
  scenario: AiScenario;
  inputSnapshot: Record<string, unknown>;
  schema: ZodSchema<T>;
  fallbackBuilder: () => T;
  reasoningModel?: boolean;
}): Promise<{
  runId: string;
  result: T;
  usedFallback: boolean;
  fallbackReason: string | null;
}> {
  const provider = getAiProvider();
  const model = provider.getDefaultModel({ reasoning: params.reasoningModel === true });
  const promptVersion = await getPromptVersion({
    supabase: params.supabase,
    orgId: params.orgId,
    scenario: params.scenario,
    providerId: provider.id
  });
  const prompt = await getActivePromptVersion({
    supabase: params.supabase,
    orgId: params.orgId,
    scenario: params.scenario,
    providerId: provider.id
  });

  const run = await createAiRun({
    supabase: params.supabase,
    orgId: params.orgId,
    customerId: params.customerId,
    followupId: null,
    triggeredByUserId: params.actorUserId,
    triggerSource: params.triggerSource,
    scenario: params.scenario,
    provider: provider.id,
    model,
    promptVersion,
    inputSnapshot: params.inputSnapshot
  });

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "running",
    startedAt: nowIso()
  });

  const startedAt = Date.now();
  try {
    if (!provider.isConfigured()) throw new Error("provider_not_configured");

    const response = await provider.chatCompletion({
      scenario: params.scenario,
      model,
      systemPrompt: prompt.systemPrompt,
      developerPrompt: `${prompt.developerPrompt}\n\nOutput schema:\n${JSON.stringify(prompt.outputSchema)}`,
      userPrompt: JSON.stringify({
        scenario: params.scenario,
        payload: params.inputSnapshot
      }),
      jsonMode: true,
      strictMode: true,
      useReasonerModel: params.reasoningModel === true
    });

    if (response.error) throw new Error(response.error);
    const candidate = response.parsedJson ?? parseObjectFromRawText(response.rawText);
    const parsed = params.schema.parse(candidate);

    await updateAiRunStatus({
      supabase: params.supabase,
      runId: run.id,
      status: "completed",
      provider: response.provider,
      model: response.model,
      outputSnapshot: response.rawResponse,
      parsedResult: parsed as Record<string, unknown>,
      latencyMs: response.latencyMs,
      resultSource: "provider",
      fallbackReason: response.strictFallbackUsed ? "strict_mode_auto_fallback" : null,
      completedAt: nowIso()
    });

    return {
      runId: run.id,
      result: parsed,
      usedFallback: false,
      fallbackReason: response.strictFallbackUsed ? "strict_mode_auto_fallback" : null
    };
  } catch (error) {
    if (!isRuleFallbackEnabled()) {
      const message = error instanceof Error ? error.message : "scenario_failed";
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

    const fallback = params.fallbackBuilder();
    const fallbackReason = error instanceof Error ? error.message : "scenario_fallback";
    await updateAiRunStatus({
      supabase: params.supabase,
      runId: run.id,
      status: "completed",
      provider: provider.id,
      model: "rule-fallback",
      outputSnapshot: {
        fallback: true,
        reason: fallbackReason
      },
      parsedResult: fallback as Record<string, unknown>,
      latencyMs: Date.now() - startedAt,
      resultSource: "fallback",
      fallbackReason,
      completedAt: nowIso(),
      errorMessage: fallbackReason
    });

    return {
      runId: run.id,
      result: fallback,
      usedFallback: true,
      fallbackReason
    };
  }
}

export async function getDealRoomDetail(params: {
  supabase: DbClient;
  orgId: string;
  dealRoomId: string;
}): Promise<DealRoomDetailView | null> {
  const room = await getDealRoomById({
    supabase: params.supabase,
    orgId: params.orgId,
    dealRoomId: params.dealRoomId
  });
  if (!room) return null;

  const [participantsRes, threadsRes, decisions, interventions, checkpoints, workItemsRes, prepRes, draftsRes, outcomesRes, playbooks, alertsRes] =
    await Promise.all([
      params.supabase
        .from("deal_participants")
        .select("*, profile:profiles!deal_participants_user_id_fkey(id, display_name)")
        .eq("org_id", params.orgId)
        .eq("deal_room_id", params.dealRoomId)
        .order("created_at", { ascending: true }),
      params.supabase
        .from("collaboration_threads")
        .select("*")
        .eq("org_id", params.orgId)
        .eq("deal_room_id", params.dealRoomId)
        .order("updated_at", { ascending: false }),
      listDecisionRecords({
        supabase: params.supabase,
        orgId: params.orgId,
        dealRoomId: params.dealRoomId
      }),
      listInterventionRequests({
        supabase: params.supabase,
        orgId: params.orgId,
        dealRoomId: params.dealRoomId
      }),
      listDealCheckpoints({
        supabase: params.supabase,
        orgId: params.orgId,
        dealRoomId: params.dealRoomId
      }),
      params.supabase
        .from("work_items")
        .select("*, owner:profiles!work_items_owner_id_fkey(id, display_name), customer:customers!work_items_customer_id_fkey(id, company_name)")
        .eq("org_id", params.orgId)
        .eq("customer_id", room.customerId)
        .order("updated_at", { ascending: false })
        .limit(60),
      params.supabase
        .from("prep_cards")
        .select("*")
        .eq("org_id", params.orgId)
        .eq("customer_id", room.customerId)
        .order("updated_at", { ascending: false })
        .limit(40),
      params.supabase
        .from("content_drafts")
        .select("*")
        .eq("org_id", params.orgId)
        .eq("customer_id", room.customerId)
        .order("updated_at", { ascending: false })
        .limit(40),
      params.supabase
        .from("action_outcomes")
        .select("*")
        .eq("org_id", params.orgId)
        .eq("customer_id", room.customerId)
        .order("created_at", { ascending: false })
        .limit(40),
      listPlaybooks({
        supabase: params.supabase,
        orgId: params.orgId,
        statuses: ["active"],
        includeEntries: true,
        limit: 20
      }),
      params.supabase
        .from("alerts")
        .select("*, owner:profiles!alerts_owner_id_fkey(id, display_name), customer:customers!alerts_customer_id_fkey(id, company_name)")
        .eq("org_id", params.orgId)
        .eq("customer_id", room.customerId)
        .neq("status", "resolved")
        .order("created_at", { ascending: false })
    ]);

  if (participantsRes.error) throw new Error(participantsRes.error.message);
  if (threadsRes.error) throw new Error(threadsRes.error.message);
  if (workItemsRes.error) throw new Error(workItemsRes.error.message);
  if (prepRes.error) throw new Error(prepRes.error.message);
  if (draftsRes.error) throw new Error(draftsRes.error.message);
  if (outcomesRes.error) throw new Error(outcomesRes.error.message);
  if (alertsRes.error) throw new Error(alertsRes.error.message);

  const threads: CollaborationThread[] = (threadsRes.data ?? []).map((row: Database["public"]["Tables"]["collaboration_threads"]["Row"]) =>
    mapThread(row)
  );
  const threadIds = threads.map((item: CollaborationThread) => item.id);
  const messages = threadIds.length === 0 ? [] : await listMessagesForThreads(params.supabase, params.orgId, threadIds);

  return {
    room,
    participants: (participantsRes.data ?? []).map((row: Database["public"]["Tables"]["deal_participants"]["Row"] & { profile?: ProfileLite | null }) =>
      mapDealParticipantRowShim(row)
    ),
    checkpoints,
    threads,
    messages,
    decisions,
    interventions,
    related: {
      workItems: (workItemsRes.data ?? []).map((row: Database["public"]["Tables"]["work_items"]["Row"] & { owner?: ProfileLite | null; customer?: { id: string; company_name: string } | null }) =>
        mapWorkItemRow(row)
      ),
      prepCards: (prepRes.data ?? []).map((row: Database["public"]["Tables"]["prep_cards"]["Row"]) => mapPrepCardRow(row)),
      contentDrafts: (draftsRes.data ?? []).map((row: Database["public"]["Tables"]["content_drafts"]["Row"]) => mapContentDraftRow(row)),
      outcomes: (outcomesRes.data ?? []).map((row: Database["public"]["Tables"]["action_outcomes"]["Row"]) => mapOutcomeRowShim(row)),
      playbooks,
      alerts: (alertsRes.data ?? []).map((row: Database["public"]["Tables"]["alerts"]["Row"] & {
          owner?: ProfileLite | null;
          customer?: { id: string; company_name: string } | null;
        }) =>
        mapAlertRow(row)
      )
    }
  };
}

function mapThread(row: Database["public"]["Tables"]["collaboration_threads"]["Row"]): CollaborationThread {
  return {
    id: row.id,
    orgId: row.org_id,
    dealRoomId: row.deal_room_id,
    threadType: row.thread_type,
    title: row.title,
    status: row.status,
    summary: row.summary,
    createdBy: row.created_by,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

function mapDealParticipantRowShim(
  row: Database["public"]["Tables"]["deal_participants"]["Row"] & { profile?: ProfileLite | null }
): DealParticipant {
  return {
    id: row.id,
    orgId: row.org_id,
    dealRoomId: row.deal_room_id,
    userId: row.user_id,
    userName: row.profile?.display_name ?? "Unknown",
    roleInRoom: row.role_in_room,
    isActive: row.is_active,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

function mapOutcomeRowShim(row: Database["public"]["Tables"]["action_outcomes"]["Row"]): ActionOutcome {
  return {
    id: row.id,
    orgId: row.org_id,
    ownerId: row.owner_id,
    customerId: row.customer_id,
    opportunityId: row.opportunity_id,
    workItemId: row.work_item_id,
    followupId: row.followup_id,
    communicationInputId: row.communication_input_id,
    prepCardId: row.prep_card_id,
    contentDraftId: row.content_draft_id,
    outcomeType: row.outcome_type,
    resultStatus: row.result_status,
    stageChanged: row.stage_changed,
    oldStage: row.old_stage,
    newStage: row.new_stage,
    customerSentimentShift: row.customer_sentiment_shift,
    keyOutcomeSummary: row.key_outcome_summary,
    newObjections: Array.isArray(row.new_objections) ? row.new_objections.filter((item): item is string => typeof item === "string") : [],
    newRisks: Array.isArray(row.new_risks) ? row.new_risks.filter((item): item is string => typeof item === "string") : [],
    nextStepDefined: row.next_step_defined,
    nextStepText: row.next_step_text,
    followupDueAt: row.followup_due_at,
    usedPrepCard: row.used_prep_card,
    usedDraft: row.used_draft,
    usefulnessRating: row.usefulness_rating,
    notes: row.notes,
    createdBy: row.created_by,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

async function listMessagesForThreads(supabase: DbClient, orgId: string, threadIds: string[]): Promise<CollaborationMessage[]> {
  const { data, error } = await supabase
    .from("collaboration_messages")
    .select("*, author:profiles!collaboration_messages_author_user_id_fkey(id, display_name)")
    .eq("org_id", orgId)
    .in("thread_id", threadIds)
    .order("created_at", { ascending: true });
  if (error) throw new Error(error.message);
  return (data ?? []).map((row: Database["public"]["Tables"]["collaboration_messages"]["Row"] & { author?: ProfileLite | null }) => ({
    id: row.id,
    orgId: row.org_id,
    threadId: row.thread_id,
    authorUserId: row.author_user_id,
    authorName: (row.author as ProfileLite | null)?.display_name ?? "Unknown",
    messageType: row.message_type,
    bodyMarkdown: row.body_markdown,
    mentions: Array.isArray(row.mentions) ? row.mentions.filter((item): item is string => typeof item === "string") : [],
    sourceRefType: row.source_ref_type,
    sourceRefId: row.source_ref_id,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  }));
}

export async function refreshDealRoomCommandSummary(params: {
  supabase: DbClient;
  orgId: string;
  dealRoomId: string;
  actorUserId: string;
}): Promise<{
  room: DealRoom;
  result: DealRoomCommandSummaryResult;
  runId: string;
  usedFallback: boolean;
  fallbackReason: string | null;
  playbookMapping: DealPlaybookMappingResult;
}> {
  const detail = await getDealRoomDetail({
    supabase: params.supabase,
    orgId: params.orgId,
    dealRoomId: params.dealRoomId
  });
  if (!detail) throw new Error("deal_room_not_found");

  const openTasks = detail.related.workItems.filter((item) => item.status !== "done" && item.status !== "cancelled");
  const overdueTasks = openTasks.filter((item) => item.dueAt && new Date(item.dueAt).getTime() < Date.now());
  const openInterventions = detail.interventions.filter((item) => item.status === "open" || item.status === "accepted");

  const inputSnapshot = {
    room: detail.room,
    checkpoints: detail.checkpoints,
    open_tasks: openTasks.slice(0, 20),
    overdue_task_count: overdueTasks.length,
    open_interventions: openInterventions.slice(0, 10),
    open_decisions: detail.decisions.filter((item) => item.status === "proposed" || item.status === "approved").slice(0, 10),
    recent_messages: detail.messages.slice(-20).map((item) => ({
      thread_id: item.threadId,
      type: item.messageType,
      body: item.bodyMarkdown,
      created_at: item.createdAt
    })),
    alerts: detail.related.alerts.slice(0, 10).map((item) => ({
      id: item.id,
      level: item.level,
      title: item.title,
      rule_type: item.ruleType
    }))
  } as Record<string, unknown>;

  const started = Date.now();
  logAiEvent({
    orgId: params.orgId,
    userId: params.actorUserId,
    customerId: detail.room.customerId,
    dealRoomId: params.dealRoomId,
    scenario: "deal_room_command_summary",
    provider: getAiProvider().id,
    model: getAiProvider().getDefaultModel({ reasoning: true }),
    status: "started"
  });

  const summaryRun = await runStructuredScenario({
    supabase: params.supabase,
    orgId: params.orgId,
    customerId: detail.room.customerId,
    actorUserId: params.actorUserId,
    triggerSource: "manager_review",
    scenario: "deal_room_command_summary",
    inputSnapshot,
    schema: dealRoomCommandSummaryResultSchema,
    fallbackBuilder: () =>
      buildFallbackDealRoomCommandSummary({
        room: detail.room,
        blockers: detail.room.currentBlockers,
        openTasks: openTasks.length,
        overdueTasks: overdueTasks.length,
        openInterventions: openInterventions.length
      }),
    reasoningModel: true
  });

  const playbookRun = await runStructuredScenario({
    supabase: params.supabase,
    orgId: params.orgId,
    customerId: detail.room.customerId,
    actorUserId: params.actorUserId,
    triggerSource: "manager_review",
    scenario: "deal_playbook_mapping",
    inputSnapshot: {
      room: detail.room,
      signals: {
        blockers: detail.room.currentBlockers,
        checkpoints: detail.checkpoints,
        alerts: detail.related.alerts.map((item) => ({ rule_type: item.ruleType, level: item.level })),
        outcomes: detail.related.outcomes.slice(0, 8).map((item) => ({ result_status: item.resultStatus, summary: item.keyOutcomeSummary }))
      },
      playbooks: detail.related.playbooks.map((item) => ({
        id: item.playbook.id,
        type: item.playbook.playbookType,
        title: item.playbook.title,
        summary: item.playbook.summary,
        top_entries: item.entries.slice(0, 3).map((entry) => entry.entryTitle)
      }))
    },
    schema: dealPlaybookMappingResultSchema,
    fallbackBuilder: () =>
      buildFallbackDealPlaybookMapping({
        room: detail.room,
        playbooks: detail.related.playbooks
      }),
    reasoningModel: true
  });

  const summary: DealRoomCommandSummaryResult = {
    command_summary: summaryRun.result.command_summary,
    current_goal_refinement: summaryRun.result.current_goal_refinement,
    key_blockers: summaryRun.result.key_blockers ?? [],
    recommended_next_moves: summaryRun.result.recommended_next_moves ?? [],
    manager_attention_reason: summaryRun.result.manager_attention_reason,
    missing_information: summaryRun.result.missing_information ?? []
  };
  const playbookMapping: DealPlaybookMappingResult = {
    relevant_playbooks: (playbookRun.result.relevant_playbooks ?? []).map((item) => ({
      playbook_id: item.playbook_id,
      title: item.title,
      applicability_score: item.applicability_score,
      applicability_reason: item.applicability_reason,
      suggested_application: item.suggested_application ?? []
    })),
    applicability_reason: playbookRun.result.applicability_reason,
    suggested_application: playbookRun.result.suggested_application ?? []
  };
  const updatedRoom = await updateDealRoomCommand({
    supabase: params.supabase,
    orgId: params.orgId,
    dealRoomId: params.dealRoomId,
    commandSummary: summary.command_summary,
    currentGoal: summary.current_goal_refinement,
    currentBlockers: summary.key_blockers,
    managerAttentionNeeded: summary.manager_attention_reason.trim().length > 0 && summary.manager_attention_reason !== "No urgent manager intervention required now.",
    sourceSnapshot: {
      ...inputSnapshot,
      playbook_mapping: playbookMapping
    }
  });

  const strategyThread = detail.threads.find((item) => item.threadType === "strategy") ?? detail.threads[0];
  if (strategyThread) {
    await addThreadMessage({
      supabase: params.supabase,
      orgId: params.orgId,
      threadId: strategyThread.id,
      authorUserId: params.actorUserId,
      messageType: "ai_summary",
      bodyMarkdown: `AI command summary refreshed.\n\n${summary.command_summary}`,
      sourceRefType: "deal_room",
      sourceRefId: updatedRoom.id
    });
  }

  const usedFallback = summaryRun.usedFallback || playbookRun.usedFallback;
  const fallbackReason = summaryRun.fallbackReason ?? playbookRun.fallbackReason;

  logAiEvent({
    orgId: params.orgId,
    userId: params.actorUserId,
    customerId: detail.room.customerId,
    dealRoomId: params.dealRoomId,
    scenario: "deal_room_command_summary",
    provider: getAiProvider().id,
    model: getAiProvider().getDefaultModel({ reasoning: true }),
    status: "completed",
    durationMs: Date.now() - started,
    fallbackReason
  });

  return {
    room: updatedRoom,
    result: summary,
    runId: summaryRun.runId,
    usedFallback,
    fallbackReason,
    playbookMapping
  };
}

export async function summarizeDealThread(params: {
  supabase: DbClient;
  orgId: string;
  dealRoomId: string;
  threadId: string;
  actorUserId: string;
}): Promise<{
  summary: ThreadSummaryResult;
  runId: string;
  usedFallback: boolean;
  fallbackReason: string | null;
}> {
  const detail = await getDealRoomDetail({
    supabase: params.supabase,
    orgId: params.orgId,
    dealRoomId: params.dealRoomId
  });
  if (!detail) throw new Error("deal_room_not_found");

  const thread = detail.threads.find((item) => item.id === params.threadId);
  if (!thread) throw new Error("thread_not_found");

  const messages = await listThreadMessages({
    supabase: params.supabase,
    orgId: params.orgId,
    threadId: params.threadId,
    limit: 80
  });

  const run = await runStructuredScenario({
    supabase: params.supabase,
    orgId: params.orgId,
    customerId: detail.room.customerId,
    actorUserId: params.actorUserId,
    triggerSource: "manual",
    scenario: "thread_summary",
    inputSnapshot: {
      deal_room: {
        id: detail.room.id,
        title: detail.room.title,
        goal: detail.room.currentGoal
      },
      thread: {
        id: thread.id,
        type: thread.threadType,
        title: thread.title
      },
      messages: messages.map((item) => ({
        type: item.messageType,
        author: item.authorName,
        body: item.bodyMarkdown,
        created_at: item.createdAt
      }))
    },
    schema: threadSummaryResultSchema,
    fallbackBuilder: () =>
      buildFallbackThreadSummary({
        threadTitle: thread.title,
        recentMessages: messages.slice(-6).map((item) => ({ body: item.bodyMarkdown, type: item.messageType }))
      })
  });

  const summaryResult: ThreadSummaryResult = {
    summary: run.result.summary,
    open_questions: run.result.open_questions ?? [],
    recommended_next_action: run.result.recommended_next_action,
    decision_needed: run.result.decision_needed
  };

  await updateThreadSummary({
    supabase: params.supabase,
    orgId: params.orgId,
    threadId: params.threadId,
    summary: summaryResult.summary
  });

  await addThreadMessage({
    supabase: params.supabase,
    orgId: params.orgId,
      threadId: params.threadId,
      authorUserId: params.actorUserId,
      messageType: "ai_summary",
      bodyMarkdown: `Thread summary:\n\n${summaryResult.summary}\n\nRecommended next action: ${summaryResult.recommended_next_action}`,
      sourceRefType: "collaboration_thread",
      sourceRefId: params.threadId
  });

  return {
    summary: summaryResult,
    runId: run.runId,
    usedFallback: run.usedFallback,
    fallbackReason: run.fallbackReason
  };
}

export async function getDecisionSupport(params: {
  supabase: DbClient;
  orgId: string;
  dealRoomId: string;
  actorUserId: string;
  decisionType: Database["public"]["Enums"]["decision_type"];
  options: string[];
  knownRisks?: string[];
  contextSummary?: string;
}): Promise<{
  support: DecisionSupportResult;
  runId: string;
  usedFallback: boolean;
  fallbackReason: string | null;
}> {
  const room = await getDealRoomById({
    supabase: params.supabase,
    orgId: params.orgId,
    dealRoomId: params.dealRoomId
  });
  if (!room) throw new Error("deal_room_not_found");

  const run = await runStructuredScenario({
    supabase: params.supabase,
    orgId: params.orgId,
    customerId: room.customerId,
    actorUserId: params.actorUserId,
    triggerSource: "manual",
    scenario: "decision_support",
    inputSnapshot: {
      room: {
        id: room.id,
        title: room.title,
        current_goal: room.currentGoal,
        blockers: room.currentBlockers
      },
      decision_type: params.decisionType,
      context_summary: params.contextSummary ?? "",
      options: params.options,
      known_risks: params.knownRisks ?? []
    },
    schema: decisionSupportResultSchema,
    fallbackBuilder: () =>
      buildFallbackDecisionSupport({
        decisionType: params.decisionType,
        options: params.options,
        knownRisks: params.knownRisks ?? []
      }),
    reasoningModel: true
  });

  const support: DecisionSupportResult = {
    options_assessment: (run.result.options_assessment ?? []).map((item) => ({
      option: item.option,
      pros: item.pros ?? [],
      cons: item.cons ?? [],
      risk_level: item.risk_level
    })),
    recommended_option: run.result.recommended_option,
    pros_cons: {
      pros: run.result.pros_cons?.pros ?? [],
      cons: run.result.pros_cons?.cons ?? []
    },
    caution_notes: run.result.caution_notes ?? [],
    followup_actions: run.result.followup_actions ?? []
  };

  return {
    support,
    runId: run.runId,
    usedFallback: run.usedFallback,
    fallbackReason: run.fallbackReason
  };
}

export async function getInterventionRecommendation(params: {
  supabase: DbClient;
  orgId: string;
  dealRoomId: string;
  actorUserId: string;
}): Promise<{
  recommendation: InterventionRecommendationResult;
  runId: string;
  usedFallback: boolean;
  fallbackReason: string | null;
}> {
  const detail = await getDealRoomDetail({
    supabase: params.supabase,
    orgId: params.orgId,
    dealRoomId: params.dealRoomId
  });
  if (!detail) throw new Error("deal_room_not_found");

  const openTasks = detail.related.workItems.filter((item) => item.status === "todo" || item.status === "in_progress" || item.status === "snoozed");
  const overdueTaskCount = openTasks.filter((item) => item.dueAt && new Date(item.dueAt).getTime() < Date.now()).length;
  const blockerCount = detail.checkpoints.filter((item) => item.status === "blocked").length + detail.room.currentBlockers.length;

  const run = await runStructuredScenario({
    supabase: params.supabase,
    orgId: params.orgId,
    customerId: detail.room.customerId,
    actorUserId: params.actorUserId,
    triggerSource: "manager_review",
    scenario: "intervention_recommendation",
    inputSnapshot: {
      room: detail.room,
      blocker_count: blockerCount,
      overdue_task_count: overdueTaskCount,
      open_interventions: detail.interventions.filter((item) => item.status === "open" || item.status === "accepted"),
      alerts: detail.related.alerts.map((item) => ({ level: item.level, title: item.title })),
      decisions: detail.decisions.filter((item) => item.status === "proposed")
    },
    schema: interventionRecommendationResultSchema,
    fallbackBuilder: () =>
      buildFallbackInterventionRecommendation({
        managerAttentionNeeded: detail.room.managerAttentionNeeded,
        blockerCount,
        overdueTaskCount
      }),
    reasoningModel: true
  });

  const interventionThread = detail.threads.find((item) => item.threadType === "manager_intervention");
  if (interventionThread) {
    await addSystemEventMessage({
      supabase: params.supabase,
      orgId: params.orgId,
      threadId: interventionThread.id,
      actorUserId: params.actorUserId,
      eventText: `Intervention recommendation: ${run.result.why_now}`,
      sourceRefType: "deal_room",
      sourceRefId: detail.room.id
    });
  }

  const recommendation: InterventionRecommendationResult = {
    whether_to_intervene: run.result.whether_to_intervene,
    why_now: run.result.why_now,
    intervention_goal: run.result.intervention_goal,
    suggested_manager_action: run.result.suggested_manager_action ?? [],
    expected_shift: run.result.expected_shift ?? []
  };

  return {
    recommendation,
    runId: run.runId,
    usedFallback: run.usedFallback,
    fallbackReason: run.fallbackReason
  };
}
