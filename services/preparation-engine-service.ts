import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import {
  buildFallbackFollowupPrepCard,
  buildFallbackManagerAttentionCard,
  buildFallbackMeetingPrepCard,
  buildFallbackQuotePrepCard,
  buildFallbackTaskBriefCard,
  mapPrepCardTypeToScenario
} from "@/lib/preparation-fallback";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { getActivePromptVersion } from "@/services/ai-prompt-service";
import { createAiRun, updateAiRunStatus } from "@/services/ai-run-service";
import { checkOrgFeatureAccess } from "@/services/feature-access-service";
import { canRunAiByEntitlement, getEntitlementStatus } from "@/services/plan-entitlement-service";
import { createPrepCard } from "@/services/prep-card-service";
import { getUserMemoryProfile } from "@/services/user-memory-service";
import {
  followupPrepCardResultSchema,
  managerAttentionCardResultSchema,
  meetingPrepCardResultSchema,
  quotePrepCardResultSchema,
  taskBriefCardResultSchema,
  type AiScenario,
  type AiTriggerSource
} from "@/types/ai";
import type { Database } from "@/types/database";
import type { PrepCard, PrepCardType } from "@/types/preparation";

type DbClient = ServerSupabaseClient;
type ProfileRow = Database["public"]["Tables"]["profiles"]["Row"];

function nowIso(): string {
  return new Date().toISOString();
}

function plusDays(days: number): string {
  const value = new Date();
  value.setDate(value.getDate() + days);
  return value.toISOString();
}

function getSummaryFromResult(cardType: PrepCardType, result: Record<string, unknown>): string {
  if (cardType === "followup_prep") return String(result.current_state_summary ?? "");
  if (cardType === "quote_prep") return String(result.quote_context_summary ?? "");
  if (cardType === "meeting_prep") return String(result.meeting_goal ?? "");
  if (cardType === "task_brief") return String(result.task_summary ?? "");
  return String(result.why_manager_should_intervene ?? "");
}

function getTitle(params: {
  cardType: PrepCardType;
  customerName: string | null;
  workItemTitle: string | null;
}): string {
  if (params.cardType === "followup_prep") return `Follow-up Prep | ${params.customerName ?? "Unknown Customer"}`;
  if (params.cardType === "quote_prep") return `Quote Prep | ${params.customerName ?? "Unknown Customer"}`;
  if (params.cardType === "meeting_prep") return `Meeting Prep | ${params.customerName ?? "Unknown Customer"}`;
  if (params.cardType === "task_brief") return `Task Brief | ${params.workItemTitle ?? params.customerName ?? "Task"}`;
  return `Manager Attention | ${params.customerName ?? params.workItemTitle ?? "Key Signal"}`;
}

export async function generatePrepCard(params: {
  supabase: DbClient;
  profile: ProfileRow;
  cardType: PrepCardType;
  customerId?: string | null;
  opportunityId?: string | null;
  workItemId?: string | null;
  meetingPurpose?: string | null;
  triggerSource?: AiTriggerSource;
}): Promise<{ prepCard: PrepCard; runId: string; usedFallback: boolean }> {
  const featureAccess = await checkOrgFeatureAccess({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    featureKey: "prep_cards"
  });
  if (!featureAccess.allowed) {
    throw new Error(featureAccess.reason ?? "prep_cards_feature_disabled");
  }

  const entitlement = await getEntitlementStatus({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    refreshUsage: true
  });
  const aiQuota = canRunAiByEntitlement(entitlement);
  if (!aiQuota.allowed) {
    throw new Error(aiQuota.reason ?? "ai_quota_reached");
  }

  const scenario: AiScenario = mapPrepCardTypeToScenario(params.cardType);
  const provider = getAiProvider();
  const model = provider.getDefaultModel({ reasoning: true });

  const workItemRes = params.workItemId
    ? await params.supabase
        .from("work_items")
        .select("id, owner_id, customer_id, opportunity_id, title, rationale, priority_band, status")
        .eq("org_id", params.profile.org_id)
        .eq("id", params.workItemId)
        .maybeSingle()
    : { data: null, error: null };
  if (workItemRes.error) throw new Error(workItemRes.error.message);

  const resolvedCustomerId = params.customerId ?? workItemRes.data?.customer_id ?? null;
  const resolvedOpportunityId = params.opportunityId ?? workItemRes.data?.opportunity_id ?? null;
  const ownerId = workItemRes.data?.owner_id ?? params.profile.id;

  const customerRes = resolvedCustomerId
    ? await params.supabase
        .from("customers")
        .select("id, name, company_name, current_stage, risk_level, win_probability, next_followup_at, owner_id, ai_summary, ai_suggestion")
        .eq("org_id", params.profile.org_id)
        .eq("id", resolvedCustomerId)
        .maybeSingle()
    : { data: null, error: null };
  if (customerRes.error) throw new Error(customerRes.error.message);

  const followupRes = resolvedCustomerId
    ? await params.supabase
        .from("followups")
        .select("id, communication_type, summary, customer_needs, objections, next_step, created_at")
        .eq("org_id", params.profile.org_id)
        .eq("customer_id", resolvedCustomerId)
        .order("created_at", { ascending: false })
        .limit(6)
    : { data: [], error: null };
  if (followupRes.error) throw new Error(followupRes.error.message);

  const alertRes = resolvedCustomerId
    ? await params.supabase
        .from("alerts")
        .select("id, title, severity, status, rule_type, description")
        .eq("org_id", params.profile.org_id)
        .eq("customer_id", resolvedCustomerId)
        .in("status", ["open", "watching"])
        .order("created_at", { ascending: false })
        .limit(8)
    : { data: [], error: null };
  if (alertRes.error) throw new Error(alertRes.error.message);

  const opportunityRes = resolvedOpportunityId
    ? await params.supabase
        .from("opportunities")
        .select("id, title, stage, amount, risk_level, expected_close_date, last_activity_at")
        .eq("org_id", params.profile.org_id)
        .eq("id", resolvedOpportunityId)
        .maybeSingle()
    : { data: null, error: null };
  if (opportunityRes.error) throw new Error(opportunityRes.error.message);

  const inputRes = resolvedCustomerId
    ? await params.supabase
        .from("communication_inputs")
        .select("id, source_type, title, raw_content, extraction_status, created_at")
        .eq("org_id", params.profile.org_id)
        .eq("customer_id", resolvedCustomerId)
        .order("created_at", { ascending: false })
        .limit(4)
    : { data: [], error: null };
  if (inputRes.error) throw new Error(inputRes.error.message);

  const memory = await getUserMemoryProfile({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    userId: ownerId
  }).catch(() => null);

  const customerName = customerRes.data?.company_name ?? null;
  const workItemTitle = workItemRes.data?.title ?? null;
  const title = getTitle({
    cardType: params.cardType,
    customerName,
    workItemTitle
  });

  const sourceSnapshot: Record<string, unknown> = {
    customer: customerRes.data ?? null,
    work_item: workItemRes.data ?? null,
    opportunity: opportunityRes.data ?? null,
    followups: followupRes.data ?? [],
    alerts: alertRes.data ?? [],
    communication_inputs: inputRes.data ?? [],
    memory_summary: memory?.summary ?? "",
    memory_tactics: memory?.effectiveTactics ?? [],
    meeting_purpose: params.meetingPurpose ?? null
  };

  const prompt = await getActivePromptVersion({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    scenario,
    providerId: provider.id
  });

  const run = await createAiRun({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    customerId: resolvedCustomerId,
    followupId: null,
    triggeredByUserId: params.profile.id,
    triggerSource: params.triggerSource ?? "manual",
    scenario,
    provider: provider.id,
    model,
    promptVersion: prompt.version,
    inputSnapshot: sourceSnapshot
  });

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "running",
    startedAt: nowIso()
  });

  let usedFallback = false;
  let fallbackReason: string | null = null;
  let parsedResult: Record<string, unknown>;
  let outputSnapshot: Record<string, unknown> = {};
  let responseProvider = provider.id;
  let responseModel = model;
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
        payload: sourceSnapshot
      }),
      jsonMode: true,
      strictMode: true,
      useReasonerModel: true
    });
    responseProvider = response.provider;
    responseModel = response.model;
    outputSnapshot = response.rawResponse;
    if (response.error) throw new Error(response.error);

    const candidate = response.parsedJson ?? (response.rawText ? JSON.parse(response.rawText) : null);
    if (params.cardType === "followup_prep") {
      const parsed = followupPrepCardResultSchema.safeParse(candidate);
      if (!parsed.success) throw new Error("followup_prep_card_schema_invalid");
      parsedResult = parsed.data;
    } else if (params.cardType === "quote_prep") {
      const parsed = quotePrepCardResultSchema.safeParse(candidate);
      if (!parsed.success) throw new Error("quote_prep_card_schema_invalid");
      parsedResult = parsed.data;
    } else if (params.cardType === "meeting_prep") {
      const parsed = meetingPrepCardResultSchema.safeParse(candidate);
      if (!parsed.success) throw new Error("meeting_prep_card_schema_invalid");
      parsedResult = parsed.data;
    } else if (params.cardType === "task_brief") {
      const parsed = taskBriefCardResultSchema.safeParse(candidate);
      if (!parsed.success) throw new Error("task_brief_card_schema_invalid");
      parsedResult = parsed.data;
    } else {
      const parsed = managerAttentionCardResultSchema.safeParse(candidate);
      if (!parsed.success) throw new Error("manager_attention_card_schema_invalid");
      parsedResult = parsed.data;
    }
  } catch (error) {
    if (!isRuleFallbackEnabled()) {
      const message = error instanceof Error ? error.message : "prep_card_generation_failed";
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
    fallbackReason = error instanceof Error ? error.message : "prep_card_fallback";

    if (params.cardType === "followup_prep") {
      parsedResult = buildFallbackFollowupPrepCard({
        customerName: customerName ?? "Customer",
        stage: customerRes.data?.current_stage ?? "lead",
        riskLevel: customerRes.data?.risk_level ?? "medium",
        nextFollowupAt: customerRes.data?.next_followup_at ?? null
      });
    } else if (params.cardType === "quote_prep") {
      parsedResult = buildFallbackQuotePrepCard({
        customerName: customerName ?? "Customer",
        opportunityTitle: opportunityRes.data?.title ?? null
      });
    } else if (params.cardType === "meeting_prep") {
      parsedResult = buildFallbackMeetingPrepCard({
        customerName: customerName ?? "Customer",
        meetingPurpose: params.meetingPurpose ?? "business progress review"
      });
    } else if (params.cardType === "task_brief") {
      parsedResult = buildFallbackTaskBriefCard({
        taskTitle: workItemTitle ?? "Task",
        rationale: workItemRes.data?.rationale ?? ""
      });
    } else {
      parsedResult = buildFallbackManagerAttentionCard({
        customerName: customerName ?? "Key customer",
        reason: "task and risk signals indicate potential stall"
      });
    }

    outputSnapshot = {
      fallback: true,
      reason: fallbackReason
    };
    responseModel = "rule-fallback";
  }

  const prepCard = await createPrepCard({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    ownerId,
    customerId: resolvedCustomerId,
    opportunityId: resolvedOpportunityId,
    workItemId: params.workItemId ?? null,
    cardType: params.cardType,
    status: "ready",
    title,
    summary: getSummaryFromResult(params.cardType, parsedResult),
    cardPayload: parsedResult,
    sourceSnapshot,
    generatedBy: params.profile.id,
    aiRunId: run.id,
    validUntil: plusDays(2)
  });

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "completed",
    provider: responseProvider,
    model: responseModel,
    outputSnapshot,
    parsedResult,
    resultSource: usedFallback ? "fallback" : "provider",
    fallbackReason,
    errorMessage: usedFallback ? fallbackReason : null,
    latencyMs: Date.now() - startedAt,
    completedAt: nowIso()
  });

  console.info("[prep-card.generate]", {
    org_id: params.profile.org_id,
    user_id: params.profile.id,
    customer_id: resolvedCustomerId,
    work_item_id: params.workItemId ?? null,
    scenario,
    provider: responseProvider,
    model: responseModel,
    status: "completed",
    duration_ms: Date.now() - startedAt,
    fallback_reason: fallbackReason
  });

  return {
    prepCard,
    runId: run.id,
    usedFallback
  };
}

export async function autoGenerateTaskPrepCards(params: {
  supabase: DbClient;
  profile: ProfileRow;
  workItemIds: string[];
}): Promise<number> {
  if (params.workItemIds.length === 0) return 0;

  let created = 0;
  for (const workItemId of params.workItemIds.slice(0, 3)) {
    const existing = await params.supabase
      .from("prep_cards")
      .select("id")
      .eq("org_id", params.profile.org_id)
      .eq("work_item_id", workItemId)
      .eq("card_type", "task_brief")
      .in("status", ["draft", "ready"])
      .limit(1)
      .maybeSingle();

    if (existing.error) continue;
    if (existing.data) continue;

    try {
      await generatePrepCard({
        supabase: params.supabase,
        profile: params.profile,
        cardType: "task_brief",
        workItemId,
        triggerSource: "manual"
      });
      created += 1;
    } catch {
      continue;
    }
  }
  return created;
}
