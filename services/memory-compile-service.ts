import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import { buildFallbackMemoryCompileResult } from "@/lib/memory-fallback";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { compileBehaviorQualitySnapshot } from "@/services/behavior-quality-service";
import { getActivePromptVersion } from "@/services/ai-prompt-service";
import { createAiRun, updateAiRunStatus } from "@/services/ai-run-service";
import { replaceUserMemoryItems, upsertUserMemoryProfile } from "@/services/user-memory-service";
import {
  salesMemoryCompileResultSchema,
  type AiScenario,
  type SalesMemoryCompileResult
} from "@/types/ai";
import type { Database } from "@/types/database";
import type { UserMemoryItem, UserMemoryProfile } from "@/types/memory";

type DbClient = ServerSupabaseClient;
type SourceType = Database["public"]["Enums"]["communication_source_type"];
type CommunicationType = Database["public"]["Enums"]["communication_type"];
type MemoryItemType = Database["public"]["Enums"]["memory_item_type"];

function nowIso(): string {
  return new Date().toISOString();
}

function daysAgoIso(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() - days);
  return date.toISOString();
}

function toTopEntries(input: Record<string, number>, limit = 6): string[] {
  return Object.entries(input)
    .sort((a, b) => b[1] - a[1])
    .slice(0, limit)
    .map(([key, count]) => `${key}（${count}）`);
}

function pickKeywords(text: string): string[] {
  return text
    .replace(/[，。；、,.!?！？:：/\\\n\r]/g, " ")
    .split(" ")
    .map((item) => item.trim())
    .filter((item) => item.length >= 2)
    .filter((item) => !["客户", "我们", "已经", "这个", "那个", "需要", "推进", "沟通", "跟进"].includes(item));
}

function normalizeCompileResult(raw: SalesMemoryCompileResult): SalesMemoryCompileResult {
  return {
    summary: raw.summary,
    preferred_customer_types: raw.preferred_customer_types ?? [],
    preferred_communication_styles: raw.preferred_communication_styles ?? [],
    common_objections: raw.common_objections ?? [],
    effective_tactics: raw.effective_tactics ?? [],
    common_followup_rhythm: raw.common_followup_rhythm ?? [],
    quoting_style_notes: raw.quoting_style_notes ?? [],
    risk_blind_spots: raw.risk_blind_spots ?? [],
    manager_coaching_focus: raw.manager_coaching_focus ?? [],
    memory_items: raw.memory_items ?? [],
    confidence_score: raw.confidence_score
  };
}

export async function compileUserMemory(params: {
  supabase: DbClient;
  orgId: string;
  userId: string;
  triggeredByUserId: string;
  triggerSource: "manual" | "manager_review";
  sourceWindowDays?: number;
}): Promise<{
  profile: UserMemoryProfile;
  items: UserMemoryItem[];
  usedFallback: boolean;
  fallbackReason: string | null;
}> {
  const windowDays = params.sourceWindowDays ?? 60;
  const fromIso = daysAgoIso(windowDays);

  const scenario: AiScenario = "sales_memory_compile";
  const provider = getAiProvider();
  const model = provider.getDefaultModel({ reasoning: true });
  const prompt = await getActivePromptVersion({
    supabase: params.supabase,
    orgId: params.orgId,
    scenario,
    providerId: provider.id
  });

  const run = await createAiRun({
    supabase: params.supabase,
    orgId: params.orgId,
    customerId: null,
    followupId: null,
    triggeredByUserId: params.triggeredByUserId,
    triggerSource: params.triggerSource,
    scenario,
    provider: provider.id,
    model,
    promptVersion: prompt.version,
    inputSnapshot: {
      user_id: params.userId,
      source_window_days: windowDays
    }
  });

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "running",
    startedAt: nowIso()
  });

  const [customersRes, followupsRes, commInputsRes, opportunitiesRes, alertsRes, reportsRes, aiRunsRes] = await Promise.all([
    params.supabase
      .from("customers")
      .select("id, current_stage, source_channel, win_probability, last_followup_at, next_followup_at, created_at")
      .eq("org_id", params.orgId)
      .eq("owner_id", params.userId)
      .gte("created_at", fromIso),
    params.supabase
      .from("followups")
      .select("id, customer_id, communication_type, summary, customer_needs, objections, next_step, next_followup_at, created_at, draft_status")
      .eq("org_id", params.orgId)
      .eq("owner_id", params.userId)
      .gte("created_at", fromIso),
    params.supabase
      .from("communication_inputs")
      .select("id, source_type, title, raw_content, extracted_data, extraction_status, created_at")
      .eq("org_id", params.orgId)
      .eq("owner_id", params.userId)
      .gte("created_at", fromIso),
    params.supabase
      .from("opportunities")
      .select("id, stage, risk_level, title, amount, updated_at")
      .eq("org_id", params.orgId)
      .eq("owner_id", params.userId)
      .gte("updated_at", fromIso),
    params.supabase
      .from("alerts")
      .select("id, rule_type, severity, status, title, created_at")
      .eq("org_id", params.orgId)
      .eq("owner_id", params.userId)
      .gte("created_at", fromIso),
    params.supabase
      .from("generated_reports")
      .select("id, report_type, summary, created_at")
      .eq("org_id", params.orgId)
      .eq("generated_by", params.userId)
      .gte("created_at", fromIso),
    params.supabase
      .from("ai_runs")
      .select("id, scenario, status, result_source, created_at")
      .eq("org_id", params.orgId)
      .eq("triggered_by_user_id", params.userId)
      .gte("created_at", fromIso)
  ]);

  if (customersRes.error) throw new Error(customersRes.error.message);
  if (followupsRes.error) throw new Error(followupsRes.error.message);
  if (commInputsRes.error) throw new Error(commInputsRes.error.message);
  if (opportunitiesRes.error) throw new Error(opportunitiesRes.error.message);
  if (alertsRes.error) throw new Error(alertsRes.error.message);
  if (reportsRes.error) throw new Error(reportsRes.error.message);
  if (aiRunsRes.error) throw new Error(aiRunsRes.error.message);

  const stageCounter: Record<string, number> = {};
  const sourceTypeCounter: Record<string, number> = {};
  const communicationCounter: Record<string, number> = {};
  const objectionCounter: Record<string, number> = {};
  const tacticsCounter: Record<string, number> = {};
  const riskCounter: Record<string, number> = {};

  const customers = customersRes.data ?? [];
  const followups = followupsRes.data ?? [];
  const commInputs = commInputsRes.data ?? [];
  const opportunities = opportunitiesRes.data ?? [];
  const alerts = alertsRes.data ?? [];
  const reports = reportsRes.data ?? [];
  const aiRuns = aiRunsRes.data ?? [];

  for (const item of customers as any[]) {
    const stage = item.current_stage ?? "unknown";
    stageCounter[stage] = (stageCounter[stage] ?? 0) + 1;
    const channel = item.source_channel ?? "unknown";
    sourceTypeCounter[channel] = (sourceTypeCounter[channel] ?? 0) + 1;
  }

  const rhythmDays: number[] = [];
  for (const item of followups as any[]) {
    const method = item.communication_type as CommunicationType;
    communicationCounter[method] = (communicationCounter[method] ?? 0) + 1;

    const objections = pickKeywords(item.objections ?? "");
    for (const keyword of objections) {
      objectionCounter[keyword] = (objectionCounter[keyword] ?? 0) + 1;
    }

    const tactics = pickKeywords(`${item.next_step ?? ""} ${item.summary ?? ""}`);
    for (const keyword of tactics) {
      tacticsCounter[keyword] = (tacticsCounter[keyword] ?? 0) + 1;
    }

    if (item.next_followup_at) {
      const next = new Date(item.next_followup_at);
      const created = new Date(item.created_at);
      const diff = Math.round((next.getTime() - created.getTime()) / (24 * 60 * 60 * 1000));
      if (diff >= 0 && diff <= 30) rhythmDays.push(diff);
    }
  }

  for (const item of commInputs as any[]) {
    const sourceType = item.source_type as SourceType;
    sourceTypeCounter[sourceType] = (sourceTypeCounter[sourceType] ?? 0) + 1;
  }

  for (const item of alerts as any[]) {
    const key = `${item.rule_type}:${item.severity}`;
    riskCounter[key] = (riskCounter[key] ?? 0) + 1;
  }

  for (const item of opportunities as any[]) {
    if (item.stage === "proposal" || item.stage === "negotiation") {
      tacticsCounter["推进报价与谈判"] = (tacticsCounter["推进报价与谈判"] ?? 0) + 1;
    }
  }

  const avgRhythm = rhythmDays.length === 0 ? 4 : Number((rhythmDays.reduce((a, b) => a + b, 0) / rhythmDays.length).toFixed(1));
  const rhythmTop = [`平均 ${avgRhythm} 天进行二次跟进`, avgRhythm > 5 ? "建议将关键客户二次跟进压缩至 3 天内" : "当前跟进节奏较积极"];

  const stageTop = toTopEntries(stageCounter, 5);
  const sourceTypeTop = toTopEntries(sourceTypeCounter, 5);
  const communicationTop = toTopEntries(communicationCounter, 5);
  const objectionTop = toTopEntries(objectionCounter, 6);
  const tacticsTop = toTopEntries(tacticsCounter, 6);
  const riskTop = toTopEntries(riskCounter, 6);

  const qualitySnapshot = await compileBehaviorQualitySnapshot({
    supabase: params.supabase,
    orgId: params.orgId,
    userId: params.userId,
    periodType: "weekly",
    periodStart: new Date(Date.now() - 6 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10),
    periodEnd: new Date().toISOString().slice(0, 10)
  });

  const coachingTop = [
    qualitySnapshot.shallowActivityRatio > 0.45 ? "减少浅层忙碌记录，补充可执行下一步" : "保持记录完整度与节奏稳定",
    qualitySnapshot.riskResponseScore < 60 ? "提升高风险提醒响应速度，建议 48 小时内闭环" : "保持风险处理响应",
    qualitySnapshot.highValueFocusScore < 50 ? "提升高价值客户聚焦度" : "继续维持高价值客户投入"
  ];

  const rulesSummary = {
    stage_top: stageTop,
    source_type_top: sourceTypeTop,
    communication_top: communicationTop,
    objection_top: objectionTop,
    tactics_top: tacticsTop,
    rhythm_top: rhythmTop,
    risk_top: riskTop,
    coaching_top: coachingTop
  };

  const inputSnapshot = {
    org_id: params.orgId,
    user_id: params.userId,
    source_window_days: windowDays,
    rules_summary: rulesSummary,
    data_volume: {
      customers: customers.length,
      followups: followups.length,
      communication_inputs: commInputs.length,
      opportunities: opportunities.length,
      alerts: alerts.length,
      generated_reports: reports.length,
      ai_runs: aiRuns.length
    },
    quality_snapshot: qualitySnapshot.metricsSnapshot
  };

  let compiledResult: SalesMemoryCompileResult;
  let usedFallback = false;
  let fallbackReason: string | null = null;
  let outputSnapshot: Record<string, unknown> = {};
  let responseProvider = provider.id;
  let responseModel = model;
  let latencyMs: number | null = null;

  try {
    if (!provider.isConfigured()) {
      throw new Error("provider_not_configured");
    }

    const response = await provider.chatCompletion({
      scenario,
      model,
      systemPrompt: prompt.systemPrompt,
      developerPrompt: `${prompt.developerPrompt}\n\nOutput schema:\n${JSON.stringify(prompt.outputSchema)}`,
      userPrompt: JSON.stringify({
        scenario,
        payload: inputSnapshot
      }),
      jsonMode: true,
      strictMode: true,
      useReasonerModel: true
    });

    responseProvider = response.provider;
    responseModel = response.model;
    latencyMs = response.latencyMs;
    outputSnapshot = response.rawResponse;

    if (response.error) {
      throw new Error(response.error);
    }

    const parsed = salesMemoryCompileResultSchema.safeParse(response.parsedJson ?? (response.rawText ? JSON.parse(response.rawText) : null));
    if (!parsed.success) {
      throw new Error("sales_memory_compile_schema_invalid");
    }
    compiledResult = normalizeCompileResult(parsed.data);
  } catch (error) {
    if (!isRuleFallbackEnabled()) {
      const message = error instanceof Error ? error.message : "sales_memory_compile_failed";
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
    fallbackReason = error instanceof Error ? error.message : "sales_memory_compile_fallback";
    compiledResult = normalizeCompileResult(
      buildFallbackMemoryCompileResult({
      stageTop,
      sourceTypeTop,
      communicationTop,
      objectionTop,
      tacticsTop,
      rhythmTop,
      riskTop,
      coachingTop,
      confidence: 0.62
      })
    );
    outputSnapshot = {
      fallback: true,
      reason: fallbackReason,
      rules_summary: rulesSummary
    };
    responseModel = "rule-fallback";
  }

  const profile = await upsertUserMemoryProfile({
    supabase: params.supabase,
    orgId: params.orgId,
    userId: params.userId,
    profile: {
      memoryVersion: prompt.version,
      summary: compiledResult.summary,
      preferredCustomerTypes: compiledResult.preferred_customer_types,
      preferredCommunicationStyles: compiledResult.preferred_communication_styles,
      commonObjections: compiledResult.common_objections,
      effectiveTactics: compiledResult.effective_tactics,
      commonFollowupRhythm: compiledResult.common_followup_rhythm,
      quotingStyleNotes: compiledResult.quoting_style_notes,
      riskBlindSpots: compiledResult.risk_blind_spots,
      managerCoachingFocus: compiledResult.manager_coaching_focus,
      confidenceScore: compiledResult.confidence_score,
      sourceWindowDays: windowDays,
      lastCompiledAt: nowIso()
    }
  });

  const items = await replaceUserMemoryItems({
    supabase: params.supabase,
    orgId: params.orgId,
    userId: params.userId,
    items: compiledResult.memory_items.map((item) => ({
      memoryType: item.memory_type as MemoryItemType,
      title: item.title,
      description: item.description,
      evidenceSnapshot: {
        evidence: item.evidence
      },
      confidenceScore: item.confidence_score,
      sourceCount: item.source_count
    }))
  });

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "completed",
    provider: responseProvider,
    model: responseModel,
    outputSnapshot,
    parsedResult: {
      profile,
      memory_item_count: items.length
    },
    latencyMs,
    resultSource: usedFallback ? "fallback" : "provider",
    fallbackReason,
    completedAt: nowIso(),
    errorMessage: usedFallback ? fallbackReason : null
  });

  console.info("[memory.compile]", {
    org_id: params.orgId,
    user_id: params.userId,
    scenario,
    provider: responseProvider,
    model: responseModel,
    status: "completed",
    used_fallback: usedFallback
  });

  return {
    profile,
    items,
    usedFallback,
    fallbackReason
  };
}
