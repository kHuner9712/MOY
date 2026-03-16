import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import { buildFallbackOutcomeAssist } from "@/lib/outcome-fallback";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { getActivePromptVersion } from "@/services/ai-prompt-service";
import { createAiRun, updateAiRunStatus } from "@/services/ai-run-service";
import { mapActionOutcomeRow } from "@/services/mappers";
import { actionOutcomeCaptureAssistResultSchema, type ActionOutcomeCaptureAssistResult } from "@/types/ai";
import type { Database } from "@/types/database";
import type { ActionOutcome, ActionOutcomeType } from "@/types/outcome";

type DbClient = ServerSupabaseClient;
type ProfileRow = Database["public"]["Tables"]["profiles"]["Row"];
type CustomerStage = Database["public"]["Enums"]["customer_stage"];

const STAGES: CustomerStage[] = ["lead", "initial_contact", "needs_confirmed", "proposal", "negotiation", "won", "lost"];

function nowIso(): string {
  return new Date().toISOString();
}

function toStage(value: string | null | undefined): CustomerStage | null {
  if (!value) return null;
  return STAGES.includes(value as CustomerStage) ? (value as CustomerStage) : null;
}

function inferOutcomeTypeFromWork(workType: Database["public"]["Enums"]["work_item_type"] | null): ActionOutcomeType {
  if (workType === "send_quote" || workType === "prepare_proposal") return "quote_result";
  if (workType === "schedule_demo") return "meeting_result";
  if (workType === "manager_checkin") return "manager_intervention_result";
  if (workType === "followup_call" || workType === "confirm_decision_maker" || workType === "review_customer") return "followup_result";
  return "task_result";
}

function normalizeOutcomeDraft(value: ActionOutcomeCaptureAssistResult): ActionOutcomeCaptureAssistResult {
  return {
    ...value,
    old_stage: toStage(value.old_stage) ?? null,
    new_stage: toStage(value.new_stage) ?? null
  };
}

export async function listActionOutcomes(params: {
  supabase: DbClient;
  orgId: string;
  ownerId?: string;
  customerId?: string;
  outcomeType?: Database["public"]["Enums"]["action_outcome_type"];
  limit?: number;
}): Promise<ActionOutcome[]> {
  let query = params.supabase
    .from("action_outcomes")
    .select("*")
    .eq("org_id", params.orgId)
    .order("created_at", { ascending: false })
    .limit(params.limit ?? 50);

  if (params.ownerId) query = query.eq("owner_id", params.ownerId);
  if (params.customerId) query = query.eq("customer_id", params.customerId);
  if (params.outcomeType) query = query.eq("outcome_type", params.outcomeType);

  const { data, error } = await query;
  if (error) throw new Error(error.message);
  return (data ?? []).map((row: Database["public"]["Tables"]["action_outcomes"]["Row"]) => mapActionOutcomeRow(row));
}

export async function getActionOutcomeById(params: {
  supabase: DbClient;
  orgId: string;
  outcomeId: string;
}): Promise<ActionOutcome | null> {
  const { data, error } = await params.supabase
    .from("action_outcomes")
    .select("*")
    .eq("org_id", params.orgId)
    .eq("id", params.outcomeId)
    .maybeSingle();
  if (error) throw new Error(error.message);
  if (!data) return null;
  return mapActionOutcomeRow(data as Database["public"]["Tables"]["action_outcomes"]["Row"]);
}

export async function inferActionOutcomeDraft(params: {
  supabase: DbClient;
  profile: ProfileRow;
  outcomeType: ActionOutcomeType;
  customerId?: string | null;
  followupId?: string | null;
  workItemId?: string | null;
  prepCardId?: string | null;
  contentDraftId?: string | null;
  summaryHint?: string | null;
}): Promise<{
  draft: ActionOutcomeCaptureAssistResult;
  runId: string;
  usedFallback: boolean;
  fallbackReason: string | null;
}> {
  const scenario = "action_outcome_capture_assist" as const;
  const provider = getAiProvider();
  const model = provider.getDefaultModel({ reasoning: true });

  const [workRes, followupRes, customerRes, prepRes, draftRes] = await Promise.all([
    params.workItemId
      ? params.supabase
          .from("work_items")
          .select("id, title, work_type, status, rationale, customer_id, source_ref_type, source_ref_id")
          .eq("org_id", params.profile.org_id)
          .eq("id", params.workItemId)
          .maybeSingle()
      : Promise.resolve({ data: null, error: null }),
    params.followupId
      ? params.supabase
          .from("followups")
          .select("id, summary, customer_needs, objections, next_step, next_followup_at, customer_id, created_at")
          .eq("org_id", params.profile.org_id)
          .eq("id", params.followupId)
          .maybeSingle()
      : Promise.resolve({ data: null, error: null }),
    params.customerId
      ? params.supabase
          .from("customers")
          .select("id, company_name, current_stage, risk_level, next_followup_at, ai_summary, ai_suggestion")
          .eq("org_id", params.profile.org_id)
          .eq("id", params.customerId)
          .maybeSingle()
      : Promise.resolve({ data: null, error: null }),
    params.prepCardId
      ? params.supabase
          .from("prep_cards")
          .select("id, card_type, title, summary")
          .eq("org_id", params.profile.org_id)
          .eq("id", params.prepCardId)
          .maybeSingle()
      : Promise.resolve({ data: null, error: null }),
    params.contentDraftId
      ? params.supabase
          .from("content_drafts")
          .select("id, draft_type, title, status")
          .eq("org_id", params.profile.org_id)
          .eq("id", params.contentDraftId)
          .maybeSingle()
      : Promise.resolve({ data: null, error: null })
  ]);

  if (workRes.error) throw new Error(workRes.error.message);
  if (followupRes.error) throw new Error(followupRes.error.message);
  if (customerRes.error) throw new Error(customerRes.error.message);
  if (prepRes.error) throw new Error(prepRes.error.message);
  if (draftRes.error) throw new Error(draftRes.error.message);

  const prompt = await getActivePromptVersion({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    scenario,
    providerId: provider.id
  });

  const run = await createAiRun({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    customerId: params.customerId ?? workRes.data?.customer_id ?? followupRes.data?.customer_id ?? null,
    followupId: params.followupId ?? null,
    triggeredByUserId: params.profile.id,
    triggerSource: "manual",
    scenario,
    provider: provider.id,
    model,
    promptVersion: prompt.version,
    inputSnapshot: {
      outcome_type: params.outcomeType,
      summary_hint: params.summaryHint ?? null,
      work_item: workRes.data ?? null,
      followup: followupRes.data ?? null,
      customer: customerRes.data ?? null,
      prep_card: prepRes.data ?? null,
      content_draft: draftRes.data ?? null
    }
  });

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "running",
    startedAt: nowIso()
  });

  let usedFallback = false;
  let fallbackReason: string | null = null;
  let responseProvider = provider.id;
  let responseModel = model;
  let outputSnapshot: Record<string, unknown> = {};
  let draft: ActionOutcomeCaptureAssistResult;
  const startedAt = Date.now();

  try {
    if (!provider.isConfigured()) throw new Error("provider_not_configured");

    const response = await provider.chatCompletion({
      scenario,
      model,
      systemPrompt: prompt.systemPrompt,
      developerPrompt: `${prompt.developerPrompt}\n\nOutput schema:\n${JSON.stringify(prompt.outputSchema)}`,
      userPrompt: JSON.stringify({
        scenario,
        payload: {
          outcome_type: params.outcomeType,
          summary_hint: params.summaryHint ?? null,
          work_item: workRes.data ?? null,
          followup: followupRes.data ?? null,
          customer: customerRes.data ?? null,
          prep_card: prepRes.data ?? null,
          content_draft: draftRes.data ?? null
        }
      }),
      jsonMode: true,
      strictMode: true,
      useReasonerModel: true
    });

    responseProvider = response.provider;
    responseModel = response.model;
    outputSnapshot = response.rawResponse;
    if (response.error) throw new Error(response.error);

    const parsed = actionOutcomeCaptureAssistResultSchema.safeParse(
      response.parsedJson ?? (response.rawText ? JSON.parse(response.rawText) : null)
    );
    if (!parsed.success) throw new Error("action_outcome_capture_assist_schema_invalid");
    draft = normalizeOutcomeDraft(parsed.data);
  } catch (error) {
    if (!isRuleFallbackEnabled()) {
      const message = error instanceof Error ? error.message : "action_outcome_capture_assist_failed";
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
    fallbackReason = error instanceof Error ? error.message : "action_outcome_capture_assist_fallback";
    draft = buildFallbackOutcomeAssist({
      preferredOutcomeType: params.outcomeType,
      previousStage: customerRes.data?.current_stage ?? null,
      summaryHint: params.summaryHint ?? workRes.data?.title ?? followupRes.data?.summary ?? null,
      usedPrepCard: Boolean(params.prepCardId),
      usedDraft: Boolean(params.contentDraftId)
    });

    outputSnapshot = {
      fallback: true,
      reason: fallbackReason
    };
    responseModel = "rule-fallback";
  }

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "completed",
    provider: responseProvider,
    model: responseModel,
    outputSnapshot,
    parsedResult: draft,
    resultSource: usedFallback ? "fallback" : "provider",
    fallbackReason,
    latencyMs: Date.now() - startedAt,
    completedAt: nowIso(),
    errorMessage: usedFallback ? fallbackReason : null
  });

  console.info("[outcome.assist]", {
    org_id: params.profile.org_id,
    user_id: params.profile.id,
    customer_id: params.customerId ?? null,
    work_item_id: params.workItemId ?? null,
    scenario,
    provider: responseProvider,
    model: responseModel,
    status: "completed",
    duration_ms: Date.now() - startedAt,
    fallback_reason: fallbackReason
  });

  return {
    draft,
    runId: run.id,
    usedFallback,
    fallbackReason
  };
}

export async function captureActionOutcome(params: {
  supabase: DbClient;
  profile: ProfileRow;
  ownerId?: string;
  customerId?: string | null;
  opportunityId?: string | null;
  workItemId?: string | null;
  followupId?: string | null;
  communicationInputId?: string | null;
  prepCardId?: string | null;
  contentDraftId?: string | null;
  outcomeType?: ActionOutcomeType;
  resultStatus?: Database["public"]["Enums"]["action_outcome_status"];
  stageChanged?: boolean;
  oldStage?: string | null;
  newStage?: string | null;
  customerSentimentShift?: Database["public"]["Enums"]["action_outcome_sentiment_shift"];
  keyOutcomeSummary?: string;
  newObjections?: string[];
  newRisks?: string[];
  nextStepDefined?: boolean;
  nextStepText?: string | null;
  followupDueAt?: string | null;
  usedPrepCard?: boolean;
  usedDraft?: boolean;
  usefulnessRating?: Database["public"]["Enums"]["action_outcome_usefulness_rating"];
  notes?: string | null;
  autoInfer?: boolean;
  summaryHint?: string | null;
  linkAdoptionIds?: string[];
}): Promise<{
  outcome: ActionOutcome;
  runId: string | null;
  usedFallback: boolean;
  fallbackReason: string | null;
}> {
  const inferredType = params.outcomeType ?? (() => {
    if (!params.workItemId) return "task_result" as ActionOutcomeType;
    return "task_result" as ActionOutcomeType;
  })();

  let assisted: ActionOutcomeCaptureAssistResult | null = null;
  let runId: string | null = null;
  let usedFallback = false;
  let fallbackReason: string | null = null;

  if (params.autoInfer) {
    const workTypeRes = params.workItemId
      ? await params.supabase
          .from("work_items")
          .select("work_type")
          .eq("org_id", params.profile.org_id)
          .eq("id", params.workItemId)
          .maybeSingle()
      : { data: null, error: null };
    if (workTypeRes.error) throw new Error(workTypeRes.error.message);

    const outcomeType = params.outcomeType ?? inferOutcomeTypeFromWork((workTypeRes.data?.work_type as Database["public"]["Enums"]["work_item_type"] | null) ?? null);

    const draft = await inferActionOutcomeDraft({
      supabase: params.supabase,
      profile: params.profile,
      outcomeType,
      customerId: params.customerId ?? null,
      followupId: params.followupId ?? null,
      workItemId: params.workItemId ?? null,
      prepCardId: params.prepCardId ?? null,
      contentDraftId: params.contentDraftId ?? null,
      summaryHint: params.summaryHint ?? params.keyOutcomeSummary ?? null
    });
    assisted = draft.draft;
    runId = draft.runId;
    usedFallback = draft.usedFallback;
    fallbackReason = draft.fallbackReason;
  }

  const payload: Database["public"]["Tables"]["action_outcomes"]["Insert"] = {
    org_id: params.profile.org_id,
    owner_id: params.ownerId ?? params.profile.id,
    customer_id: params.customerId ?? null,
    opportunity_id: params.opportunityId ?? null,
    work_item_id: params.workItemId ?? null,
    followup_id: params.followupId ?? null,
    communication_input_id: params.communicationInputId ?? null,
    prep_card_id: params.prepCardId ?? null,
    content_draft_id: params.contentDraftId ?? null,
    outcome_type: (params.outcomeType ?? assisted?.outcome_type ?? inferredType) as Database["public"]["Enums"]["action_outcome_type"],
    result_status: (params.resultStatus ?? assisted?.result_status ?? "neutral") as Database["public"]["Enums"]["action_outcome_status"],
    stage_changed: params.stageChanged ?? assisted?.stage_changed ?? false,
    old_stage: toStage(params.oldStage ?? assisted?.old_stage ?? null),
    new_stage: toStage(params.newStage ?? assisted?.new_stage ?? null),
    customer_sentiment_shift: (params.customerSentimentShift ?? assisted?.customer_sentiment_shift ?? "unknown") as Database["public"]["Enums"]["action_outcome_sentiment_shift"],
    key_outcome_summary: params.keyOutcomeSummary ?? assisted?.key_outcome_summary ?? "Action outcome captured.",
    new_objections: (params.newObjections ?? assisted?.new_objections ?? []) as unknown as Database["public"]["Tables"]["action_outcomes"]["Insert"]["new_objections"],
    new_risks: (params.newRisks ?? assisted?.new_risks ?? []) as unknown as Database["public"]["Tables"]["action_outcomes"]["Insert"]["new_risks"],
    next_step_defined: params.nextStepDefined ?? assisted?.next_step_defined ?? false,
    next_step_text: params.nextStepText ?? assisted?.next_step_text ?? null,
    followup_due_at: params.followupDueAt ?? assisted?.followup_due_at ?? null,
    used_prep_card: params.usedPrepCard ?? assisted?.used_prep_card ?? false,
    used_draft: params.usedDraft ?? assisted?.used_draft ?? false,
    usefulness_rating: (params.usefulnessRating ?? assisted?.usefulness_rating ?? "unknown") as Database["public"]["Enums"]["action_outcome_usefulness_rating"],
    notes: params.notes ?? assisted?.notes ?? null,
    created_by: params.profile.id
  };

  const { data, error } = await params.supabase.from("action_outcomes").insert(payload).select("*").single();
  if (error || !data) throw new Error(error?.message ?? "Failed to capture action outcome");
  const outcome = mapActionOutcomeRow(data as Database["public"]["Tables"]["action_outcomes"]["Row"]);

  if (outcome.customerId) {
    const customerPatch: Database["public"]["Tables"]["customers"]["Update"] = {
      updated_at: nowIso()
    };

    if (outcome.stageChanged && outcome.newStage) {
      customerPatch.current_stage = outcome.newStage as Database["public"]["Enums"]["customer_stage"];
    }
    if (outcome.followupDueAt) {
      customerPatch.next_followup_at = outcome.followupDueAt;
    }
    if (outcome.resultStatus === "risk_increased") {
      customerPatch.risk_level = "high";
    }

    await params.supabase.from("customers").update(customerPatch).eq("org_id", params.profile.org_id).eq("id", outcome.customerId);
  }

  if (params.linkAdoptionIds?.length) {
    await params.supabase
      .from("suggestion_adoptions")
      .update({ linked_outcome_id: outcome.id })
      .eq("org_id", params.profile.org_id)
      .eq("user_id", params.profile.id)
      .in("id", params.linkAdoptionIds);
  }

  return {
    outcome,
    runId,
    usedFallback,
    fallbackReason
  };
}
