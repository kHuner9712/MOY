import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import { computeOutcomeOverview, inferPlaybookTypeFromOutcomeType, summarizeOutcomePatterns } from "@/lib/closed-loop";
import { buildFallbackPlaybook } from "@/lib/playbook-fallback";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { getActivePromptVersion } from "@/services/ai-prompt-service";
import { createAiRun, updateAiRunStatus } from "@/services/ai-run-service";
import { mapActionOutcomeRow, mapSuggestionAdoptionRow } from "@/services/mappers";
import { createPlaybookWithEntries } from "@/services/playbook-service";
import { playbookCompileResultSchema } from "@/types/ai";
import type { Database } from "@/types/database";
import type { ActionOutcome, SuggestionAdoption } from "@/types/outcome";
import type { PlaybookWithEntries } from "@/types/playbook";

type DbClient = ServerSupabaseClient;
type ProfileRow = Database["public"]["Tables"]["profiles"]["Row"];

function nowIso(): string {
  return new Date().toISOString();
}

export async function compilePlaybook(params: {
  supabase: DbClient;
  profile: ProfileRow;
  scopeType?: Database["public"]["Enums"]["playbook_scope_type"];
  ownerUserId?: string | null;
  periodStart?: string;
  periodEnd?: string;
  title?: string;
}): Promise<{ playbook: PlaybookWithEntries; runId: string; usedFallback: boolean; fallbackReason: string | null }> {
  const scopeType = params.scopeType ?? (params.profile.role === "manager" ? "team" : "user");
  const ownerUserId = scopeType === "user" ? (params.ownerUserId ?? params.profile.id) : params.ownerUserId ?? null;

  const end = params.periodEnd ?? new Date().toISOString().slice(0, 10);
  const startDate = params.periodStart
    ? new Date(params.periodStart)
    : (() => {
        const d = new Date();
        d.setDate(d.getDate() - 30);
        return d;
      })();
  const start = params.periodStart ?? startDate.toISOString().slice(0, 10);

  let outcomesQuery = params.supabase
    .from("action_outcomes")
    .select("*")
    .eq("org_id", params.profile.org_id)
    .gte("created_at", `${start}T00:00:00.000Z`)
    .lte("created_at", `${end}T23:59:59.999Z`)
    .order("created_at", { ascending: false })
    .limit(300);

  if (ownerUserId) outcomesQuery = outcomesQuery.eq("owner_id", ownerUserId);

  let adoptionsQuery = params.supabase
    .from("suggestion_adoptions")
    .select("*")
    .eq("org_id", params.profile.org_id)
    .gte("created_at", `${start}T00:00:00.000Z`)
    .lte("created_at", `${end}T23:59:59.999Z`)
    .order("created_at", { ascending: false })
    .limit(300);

  if (ownerUserId) adoptionsQuery = adoptionsQuery.eq("user_id", ownerUserId);

  const [outcomesRes, adoptionsRes] = await Promise.all([outcomesQuery, adoptionsQuery]);
  if (outcomesRes.error) throw new Error(outcomesRes.error.message);
  if (adoptionsRes.error) throw new Error(adoptionsRes.error.message);

  const outcomes: ActionOutcome[] = (outcomesRes.data ?? []).map((row: Database["public"]["Tables"]["action_outcomes"]["Row"]) => mapActionOutcomeRow(row));
  const adoptions: SuggestionAdoption[] = (adoptionsRes.data ?? []).map((row: Database["public"]["Tables"]["suggestion_adoptions"]["Row"]) =>
    mapSuggestionAdoptionRow(row)
  );

  if (outcomes.length === 0) {
    throw new Error("No action outcomes available for playbook compilation");
  }

  const summary = summarizeOutcomePatterns({
    outcomes: outcomes.map((item) => ({
      id: item.id,
      resultStatus: item.resultStatus,
      outcomeType: item.outcomeType,
      newRisks: item.newRisks,
      newObjections: item.newObjections,
      stageChanged: item.stageChanged,
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

  const scenario = "playbook_compile" as const;
  const provider = getAiProvider();
  const model = provider.getDefaultModel({ reasoning: true });
  const prompt = await getActivePromptVersion({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    scenario,
    providerId: provider.id
  });

  const dominantOutcomeType = outcomes[0]?.outcomeType ?? "followup_result";

  const run = await createAiRun({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    customerId: null,
    followupId: null,
    triggeredByUserId: params.profile.id,
    triggerSource: "manager_review",
    scenario,
    provider: provider.id,
    model,
    promptVersion: prompt.version,
    inputSnapshot: {
      scope_type: scopeType,
      owner_user_id: ownerUserId,
      period_start: start,
      period_end: end,
      metrics: overview,
      summary,
      sample_count: outcomes.length
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
  let compiled: Database["public"]["Tables"]["playbooks"]["Insert"] & {
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
  };

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
          scope_type: scopeType,
          owner_user_id: ownerUserId,
          period_start: start,
          period_end: end,
          metrics: overview,
          effective_patterns: summary.effectivePatterns,
          ineffective_patterns: summary.ineffectivePatterns,
          repeated_failures: summary.repeatedFailures,
          sample_outcomes: outcomes.slice(0, 30)
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

    const parsed = playbookCompileResultSchema.safeParse(
      response.parsedJson ?? (response.rawText ? JSON.parse(response.rawText) : null)
    );

    if (!parsed.success) throw new Error("playbook_compile_schema_invalid");

    compiled = {
      org_id: params.profile.org_id,
      scope_type: scopeType,
      owner_user_id: ownerUserId,
      playbook_type: parsed.data.playbook_type,
      title: params.title ?? parsed.data.title,
      summary: parsed.data.summary,
      status: "active",
      confidence_score: parsed.data.confidence_score,
      applicability_notes: parsed.data.applicability_notes,
      source_snapshot: {
        metrics: overview,
        summary
      },
      generated_by: params.profile.id,
      ai_run_id: run.id,
      entries: parsed.data.entries.map((item, index) => ({
        entryTitle: item.entry_title,
        entrySummary: item.entry_summary,
        conditions: item.conditions,
        recommendedActions: item.recommended_actions,
        cautionNotes: item.caution_notes,
        evidenceSnapshot: item.evidence_snapshot,
        successSignal: item.success_signal,
        failureModes: item.failure_modes,
        confidenceScore: item.confidence_score,
        sortOrder: item.sort_order ?? index + 1
      }))
    };
  } catch (error) {
    if (!isRuleFallbackEnabled()) {
      const message = error instanceof Error ? error.message : "playbook_compile_failed";
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
    fallbackReason = error instanceof Error ? error.message : "playbook_compile_fallback";

    const fallback = buildFallbackPlaybook({
      title: params.title ?? `Playbook (${scopeType})`,
      playbookType: inferPlaybookTypeFromOutcomeType(dominantOutcomeType),
      effectivePatternHints: summary.effectivePatterns,
      ineffectivePatternHints: summary.ineffectivePatterns
    });

    compiled = {
      org_id: params.profile.org_id,
      scope_type: scopeType,
      owner_user_id: ownerUserId,
      playbook_type: fallback.playbook_type,
      title: fallback.title,
      summary: fallback.summary,
      status: "active",
      confidence_score: fallback.confidence_score,
      applicability_notes: fallback.applicability_notes,
      source_snapshot: {
        metrics: overview,
        summary,
        fallback: true
      },
      generated_by: params.profile.id,
      ai_run_id: run.id,
      entries: fallback.entries.map((item, index) => ({
        entryTitle: item.entry_title,
        entrySummary: item.entry_summary,
        conditions: item.conditions,
        recommendedActions: item.recommended_actions,
        cautionNotes: item.caution_notes,
        evidenceSnapshot: item.evidence_snapshot,
        successSignal: item.success_signal,
        failureModes: item.failure_modes,
        confidenceScore: item.confidence_score,
        sortOrder: item.sort_order ?? index + 1
      }))
    };

    outputSnapshot = {
      fallback: true,
      reason: fallbackReason,
      summary
    };
    responseModel = "rule-fallback";
  }

  const saved = await createPlaybookWithEntries({
    supabase: params.supabase,
    orgId: params.profile.org_id,
    scopeType: compiled.scope_type!,
    ownerUserId: compiled.owner_user_id,
    playbookType: compiled.playbook_type!,
    title: compiled.title!,
    summary: compiled.summary!,
    status: compiled.status,
    confidenceScore: Number(compiled.confidence_score),
    applicabilityNotes: compiled.applicability_notes!,
    sourceSnapshot: compiled.source_snapshot as Record<string, unknown>,
    generatedBy: compiled.generated_by!,
    aiRunId: compiled.ai_run_id,
    entries: compiled.entries
  });

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "completed",
    provider: responseProvider,
    model: responseModel,
    outputSnapshot,
    parsedResult: {
      playbook_id: saved.playbook.id,
      title: saved.playbook.title,
      entry_count: saved.entries.length,
      confidence_score: saved.playbook.confidenceScore
    },
    resultSource: usedFallback ? "fallback" : "provider",
    fallbackReason,
    latencyMs: Date.now() - startedAt,
    completedAt: nowIso(),
    errorMessage: usedFallback ? fallbackReason : null
  });

  return {
    playbook: saved,
    runId: run.id,
    usedFallback,
    fallbackReason
  };
}
