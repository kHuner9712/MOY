import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import { computeOutcomeOverview, summarizeOutcomePatterns } from "@/lib/closed-loop";
import { buildFallbackOutcomeReview, buildFallbackPersonalEffectivenessUpdate } from "@/lib/outcome-review-fallback";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { getActivePromptVersion } from "@/services/ai-prompt-service";
import { createAiRun, updateAiRunStatus } from "@/services/ai-run-service";
import { mapActionOutcomeRow, mapOutcomeReviewRow, mapSuggestionAdoptionRow } from "@/services/mappers";
import { personalEffectivenessUpdateResultSchema, outcomeEffectivenessReviewResultSchema } from "@/types/ai";
import type { Database } from "@/types/database";
import type { ActionOutcome, OutcomeReview, SuggestionAdoption } from "@/types/outcome";

type DbClient = ServerSupabaseClient;
type ProfileRow = Database["public"]["Tables"]["profiles"]["Row"];

function nowIso(): string {
  return new Date().toISOString();
}

async function createOutcomeReviewRecord(params: {
  supabase: DbClient;
  orgId: string;
  reviewScope: Database["public"]["Enums"]["outcome_review_scope"];
  targetUserId?: string | null;
  periodStart: string;
  periodEnd: string;
  generatedBy: string;
}): Promise<Database["public"]["Tables"]["outcome_reviews"]["Row"]> {
  const { data, error } = await params.supabase
    .from("outcome_reviews")
    .insert({
      org_id: params.orgId,
      review_scope: params.reviewScope,
      target_user_id: params.targetUserId ?? null,
      period_start: params.periodStart,
      period_end: params.periodEnd,
      status: "generating",
      generated_by: params.generatedBy
    })
    .select("*")
    .single();

  if (error || !data) throw new Error(error?.message ?? "Failed to create outcome review record");
  return data as Database["public"]["Tables"]["outcome_reviews"]["Row"];
}

export async function generateOutcomeReview(params: {
  supabase: DbClient;
  profile: ProfileRow;
  reviewScope?: Database["public"]["Enums"]["outcome_review_scope"];
  targetUserId?: string | null;
  periodStart?: string;
  periodEnd?: string;
}): Promise<{
  review: OutcomeReview;
  personalEffectiveness: ReturnType<typeof personalEffectivenessUpdateResultSchema.parse>;
  runId: string;
  usedFallback: boolean;
  fallbackReason: string | null;
}> {
  const reviewScope = params.reviewScope ?? (params.profile.role === "manager" ? "team" : "user");
  const targetUserId = reviewScope === "user" ? (params.targetUserId ?? params.profile.id) : params.targetUserId ?? null;

  const end = params.periodEnd ?? new Date().toISOString().slice(0, 10);
  const start = params.periodStart ?? (() => {
    const d = new Date();
    d.setDate(d.getDate() - 30);
    return d.toISOString().slice(0, 10);
  })();

  let outcomesQuery = params.supabase
    .from("action_outcomes")
    .select("*")
    .eq("org_id", params.profile.org_id)
    .gte("created_at", `${start}T00:00:00.000Z`)
    .lte("created_at", `${end}T23:59:59.999Z`)
    .order("created_at", { ascending: false })
    .limit(400);

  let adoptionsQuery = params.supabase
    .from("suggestion_adoptions")
    .select("*")
    .eq("org_id", params.profile.org_id)
    .gte("created_at", `${start}T00:00:00.000Z`)
    .lte("created_at", `${end}T23:59:59.999Z`)
    .order("created_at", { ascending: false })
    .limit(400);

  if (targetUserId) {
    outcomesQuery = outcomesQuery.eq("owner_id", targetUserId);
    adoptionsQuery = adoptionsQuery.eq("user_id", targetUserId);
  }

  const [outcomesRes, adoptionsRes] = await Promise.all([outcomesQuery, adoptionsQuery]);
  if (outcomesRes.error) throw new Error(outcomesRes.error.message);
  if (adoptionsRes.error) throw new Error(adoptionsRes.error.message);

  const outcomes: ActionOutcome[] = (outcomesRes.data ?? []).map((item: Database["public"]["Tables"]["action_outcomes"]["Row"]) =>
    mapActionOutcomeRow(item)
  );
  const adoptions: SuggestionAdoption[] = (adoptionsRes.data ?? []).map((item: Database["public"]["Tables"]["suggestion_adoptions"]["Row"]) =>
    mapSuggestionAdoptionRow(item)
  );

  const summary = summarizeOutcomePatterns({
    outcomes: outcomes.map((item) => ({
      id: item.id,
      resultStatus: item.resultStatus,
      outcomeType: item.outcomeType,
      usedPrepCard: item.usedPrepCard,
      usedDraft: item.usedDraft
    }))
  });

  const overview = computeOutcomeOverview({
    outcomes: outcomes.map((item) => ({
      id: item.id,
      resultStatus: item.resultStatus,
      outcomeType: item.outcomeType
    })),
    adoptions: adoptions.map((item) => ({
      id: item.id,
      linkedOutcomeId: item.linkedOutcomeId,
      adoptionType: item.adoptionType
    }))
  });

  const baseReviewRow = await createOutcomeReviewRecord({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    reviewScope,
    targetUserId,
    periodStart: start,
    periodEnd: end,
    generatedBy: params.profile.id
  });

  const provider = getAiProvider();
  const model = provider.getDefaultModel({ reasoning: true });

  const reviewPrompt = await getActivePromptVersion({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    scenario: "outcome_effectiveness_review",
    providerId: provider.id
  });

  const run = await createAiRun({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    customerId: null,
    followupId: null,
    triggeredByUserId: params.profile.id,
    triggerSource: params.profile.role === "manager" ? "manager_review" : "manual",
    scenario: "outcome_effectiveness_review",
    provider: provider.id,
    model,
    promptVersion: reviewPrompt.version,
    inputSnapshot: {
      review_scope: reviewScope,
      target_user_id: targetUserId,
      period_start: start,
      period_end: end,
      overview,
      summary,
      outcomes_count: outcomes.length,
      adoptions_count: adoptions.length
    }
  });

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "running",
    startedAt: nowIso()
  });

  const startedAt = Date.now();
  let usedFallback = false;
  let fallbackReason: string | null = null;
  let responseProvider = provider.id;
  let responseModel = model;
  let outputSnapshot: Record<string, unknown> = {};

  let reviewResult: ReturnType<typeof outcomeEffectivenessReviewResultSchema.parse>;

  try {
    if (!provider.isConfigured()) throw new Error("provider_not_configured");

    const response = await provider.chatCompletion({
      scenario: "outcome_effectiveness_review",
      model,
      systemPrompt: reviewPrompt.systemPrompt,
      developerPrompt: `${reviewPrompt.developerPrompt}\n\nOutput schema:\n${JSON.stringify(reviewPrompt.outputSchema)}`,
      userPrompt: JSON.stringify({
        scenario: "outcome_effectiveness_review",
        payload: {
          review_scope: reviewScope,
          target_user_id: targetUserId,
          period_start: start,
          period_end: end,
          overview,
          effective_patterns: summary.effectivePatterns,
          ineffective_patterns: summary.ineffectivePatterns,
          repeated_failures: summary.repeatedFailures,
          sample_outcomes: outcomes.slice(0, 30),
          sample_adoptions: adoptions.slice(0, 30)
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

    reviewResult = outcomeEffectivenessReviewResultSchema.parse(
      response.parsedJson ?? (response.rawText ? JSON.parse(response.rawText) : null)
    );
  } catch (error) {
    if (!isRuleFallbackEnabled()) {
      const message = error instanceof Error ? error.message : "outcome_effectiveness_review_failed";
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
    fallbackReason = error instanceof Error ? error.message : "outcome_effectiveness_review_fallback";
    reviewResult = buildFallbackOutcomeReview({
      periodLabel: `${start}~${end}`,
      positiveRate: overview.positiveRate,
      adoptionRate: overview.adoptionRate,
      repeatedFailures: summary.repeatedFailures
    });

    outputSnapshot = {
      fallback: true,
      reason: fallbackReason,
      overview,
      summary
    };
    responseModel = "rule-fallback";
  }

  const personalPrompt = await getActivePromptVersion({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    scenario: "personal_effectiveness_update",
    providerId: provider.id
  });

  let personalEffectiveness: ReturnType<typeof personalEffectivenessUpdateResultSchema.parse>;
  try {
    if (!provider.isConfigured()) throw new Error("provider_not_configured");
    const response = await provider.chatCompletion({
      scenario: "personal_effectiveness_update",
      model,
      systemPrompt: personalPrompt.systemPrompt,
      developerPrompt: `${personalPrompt.developerPrompt}\n\nOutput schema:\n${JSON.stringify(personalPrompt.outputSchema)}`,
      userPrompt: JSON.stringify({
        scenario: "personal_effectiveness_update",
        payload: {
          target_user_id: targetUserId,
          period_start: start,
          period_end: end,
          overview,
          sample_outcomes: outcomes.slice(0, 20),
          sample_adoptions: adoptions.slice(0, 20)
        }
      }),
      jsonMode: true,
      strictMode: true,
      useReasonerModel: true
    });
    if (response.error) throw new Error(response.error);

    personalEffectiveness = personalEffectivenessUpdateResultSchema.parse(
      response.parsedJson ?? (response.rawText ? JSON.parse(response.rawText) : null)
    );
  } catch {
    personalEffectiveness = buildFallbackPersonalEffectivenessUpdate({
      positiveRateAfterAdoption: overview.adoptionPositiveRate,
      positiveRateWithoutAdoption: overview.positiveRate
    });
  }

  const { data: updatedReviewRaw, error: reviewUpdateError } = await params.supabase
    .from("outcome_reviews")
    .update({
      status: "completed",
      title: reviewResult.title,
      executive_summary: reviewResult.executive_summary,
      effective_patterns: reviewResult.effective_patterns,
      ineffective_patterns: reviewResult.ineffective_patterns,
      repeated_failures: reviewResult.repeated_failures,
      coaching_actions: reviewResult.coaching_actions,
      playbook_candidates: reviewResult.playbook_candidates,
      source_snapshot: {
        overview,
        summary,
        personal_effectiveness: personalEffectiveness
      },
      ai_run_id: run.id,
      updated_at: nowIso()
    })
    .eq("id", baseReviewRow.id)
    .select("*")
    .single();

  if (reviewUpdateError || !updatedReviewRaw) {
    throw new Error(reviewUpdateError?.message ?? "Failed to persist outcome review");
  }

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "completed",
    provider: responseProvider,
    model: responseModel,
    outputSnapshot,
    parsedResult: {
      review_id: updatedReviewRaw.id,
      title: reviewResult.title,
      effective_pattern_count: reviewResult.effective_patterns.length,
      ineffective_pattern_count: reviewResult.ineffective_patterns.length,
      personal_effectiveness: personalEffectiveness
    },
    resultSource: usedFallback ? "fallback" : "provider",
    fallbackReason,
    latencyMs: Date.now() - startedAt,
    completedAt: nowIso(),
    errorMessage: usedFallback ? fallbackReason : null
  });

  return {
    review: mapOutcomeReviewRow(updatedReviewRaw as Database["public"]["Tables"]["outcome_reviews"]["Row"]),
    personalEffectiveness,
    runId: run.id,
    usedFallback,
    fallbackReason
  };
}

export async function listOutcomeReviews(params: {
  supabase: DbClient;
  orgId: string;
  reviewScope?: Database["public"]["Enums"]["outcome_review_scope"];
  targetUserId?: string;
  limit?: number;
}): Promise<OutcomeReview[]> {
  let query = params.supabase
    .from("outcome_reviews")
    .select("*")
    .eq("org_id", params.orgId)
    .order("created_at", { ascending: false })
    .limit(params.limit ?? 50);
  if (params.reviewScope) query = query.eq("review_scope", params.reviewScope);
  if (params.targetUserId) query = query.eq("target_user_id", params.targetUserId);
  const { data, error } = await query;
  if (error) throw new Error(error.message);
  return (data ?? []).map((row: Database["public"]["Tables"]["outcome_reviews"]["Row"]) => mapOutcomeReviewRow(row));
}
