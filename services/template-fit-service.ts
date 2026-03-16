import { INDUSTRY_TEMPLATE_KEYWORDS } from "@/data/industry-templates";
import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import { buildFallbackTemplateFitRecommendation } from "@/lib/productization-fallback";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { getActivePromptVersion } from "@/services/ai-prompt-service";
import { createAiRun, updateAiRunStatus } from "@/services/ai-run-service";
import { listIndustryTemplates } from "@/services/industry-template-service";
import { getOrgSettings } from "@/services/org-settings-service";
import { templateFitRecommendationResultSchema } from "@/types/ai";
import type { TemplateFitRecommendation } from "@/types/productization";

type DbClient = ServerSupabaseClient;

function nowIso(): string {
  return new Date().toISOString();
}

function pickTemplateByRule(params: {
  industryHint: string | null;
  availableTemplateKeys: string[];
}): string {
  const hint = (params.industryHint ?? "").toLowerCase().trim();
  if (!hint) return params.availableTemplateKeys.includes("generic") ? "generic" : params.availableTemplateKeys[0] ?? "generic";

  let bestKey = "generic";
  let bestScore = 0;

  for (const family of INDUSTRY_TEMPLATE_KEYWORDS) {
    let score = 0;
    for (const keyword of family.keywords) {
      if (hint.includes(keyword.toLowerCase())) score += 1;
    }
    if (score > bestScore) {
      bestScore = score;
      bestKey = family.family;
    }
  }

  if (bestScore === 0) {
    return params.availableTemplateKeys.includes("generic") ? "generic" : params.availableTemplateKeys[0] ?? "generic";
  }

  if (!params.availableTemplateKeys.includes(bestKey)) {
    return params.availableTemplateKeys.includes("generic") ? "generic" : params.availableTemplateKeys[0] ?? "generic";
  }

  return bestKey;
}

export async function recommendTemplateFit(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  industryHint?: string | null;
}): Promise<{
  recommendation: TemplateFitRecommendation;
  usedFallback: boolean;
  fallbackReason: string | null;
}> {
  const [settings, templates, membershipCountRes] = await Promise.all([
    getOrgSettings({ supabase: params.supabase, orgId: params.orgId }),
    listIndustryTemplates({ supabase: params.supabase }),
    params.supabase.from("org_memberships").select("id", { count: "exact", head: true }).eq("org_id", params.orgId).eq("seat_status", "active")
  ]);
  if (membershipCountRes.error) throw new Error(membershipCountRes.error.message);

  const availableTemplateKeys = templates.map((item) => item.templateKey);
  const teamSize = membershipCountRes.count ?? 0;
  const inputIndustryHint = params.industryHint ?? settings.industryHint ?? null;

  const scenario = "template_fit_recommendation" as const;
  const provider = getAiProvider();
  const model = provider.getDefaultModel({ reasoning: false });
  const prompt = await getActivePromptVersion({
    supabase: params.supabase,
    orgId: params.orgId,
    scenario,
    providerId: provider.id
  });

  const run = await createAiRun({
    supabase: params.supabase,
    orgId: params.orgId,
    triggerSource: "manual",
    scenario,
    provider: provider.id,
    model,
    promptVersion: prompt.version,
    triggeredByUserId: params.actorUserId,
    inputSnapshot: {
      org_id: params.orgId,
      industry_hint: inputIndustryHint,
      team_size: teamSize,
      available_template_keys: availableTemplateKeys
    }
  });

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "running",
    startedAt: nowIso()
  });

  try {
    if (!provider.isConfigured()) throw new Error("provider_not_configured");

    const response = await provider.chatCompletion({
      scenario,
      model,
      systemPrompt: prompt.systemPrompt,
      developerPrompt: `${prompt.developerPrompt}\n\nOutput schema:\n${JSON.stringify(prompt.outputSchema)}`,
      userPrompt: JSON.stringify({
        org_id: params.orgId,
        industry_hint: inputIndustryHint,
        team_size: teamSize,
        available_template_keys: availableTemplateKeys
      }),
      jsonMode: true,
      strictMode: true
    });

    const parsed = templateFitRecommendationResultSchema.safeParse(
      response.parsedJson ?? (response.rawText ? JSON.parse(response.rawText) : null)
    );
    if (!parsed.success) throw new Error("template_fit_schema_invalid");

    const recommendation: TemplateFitRecommendation = {
      recommendedTemplateKey: parsed.data.recommended_template_key,
      fitReasons: parsed.data.fit_reasons,
      risksOfMismatch: parsed.data.risks_of_mismatch,
      recommendedApplyMode: parsed.data.recommended_apply_mode,
      recommendedOverrides: parsed.data.recommended_overrides
    };

    await updateAiRunStatus({
      supabase: params.supabase,
      runId: run.id,
      status: "completed",
      provider: response.provider,
      model: response.model,
      outputSnapshot: response.rawResponse,
      parsedResult: parsed.data,
      latencyMs: response.latencyMs,
      resultSource: "provider",
      completedAt: nowIso()
    });

    return {
      recommendation,
      usedFallback: false,
      fallbackReason: null
    };
  } catch (error) {
    const fallbackReason = error instanceof Error ? error.message : "template_fit_failed";
    if (!isRuleFallbackEnabled()) {
      await updateAiRunStatus({
        supabase: params.supabase,
        runId: run.id,
        status: "failed",
        errorMessage: fallbackReason,
        completedAt: nowIso()
      });
      throw error;
    }

    const fallback = buildFallbackTemplateFitRecommendation({
      industryHint: inputIndustryHint,
      availableTemplateKeys,
      teamSize
    });
    fallback.recommendedTemplateKey = pickTemplateByRule({
      industryHint: inputIndustryHint,
      availableTemplateKeys
    });

    await updateAiRunStatus({
      supabase: params.supabase,
      runId: run.id,
      status: "completed",
      model: "rule-fallback",
      outputSnapshot: {
        fallback: true,
        reason: fallbackReason,
        payload: fallback
      },
      parsedResult: {
        recommended_template_key: fallback.recommendedTemplateKey,
        fit_reasons: fallback.fitReasons,
        risks_of_mismatch: fallback.risksOfMismatch,
        recommended_apply_mode: fallback.recommendedApplyMode,
        recommended_overrides: fallback.recommendedOverrides
      },
      resultSource: "fallback",
      fallbackReason,
      errorMessage: fallbackReason,
      completedAt: nowIso()
    });

    return {
      recommendation: fallback,
      usedFallback: true,
      fallbackReason
    };
  }
}

