import type { ZodSchema } from "zod";

import { evaluateAlertRules } from "@/lib/alert-rules";
import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import { getAiRuntimeEnv } from "@/lib/env";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { upsertLeakAlert } from "@/services/alert-workflow-service";
import { getActivePromptVersion } from "@/services/ai-prompt-service";
import { createAiRun, listCustomerAiRuns as listRuns, updateAiRunStatus } from "@/services/ai-run-service";
import { mapAlertRow, mapCustomerRow, mapFollowupRow, mapOpportunityRow } from "@/services/mappers";
import { createWorkItemFromAlert } from "@/services/work-item-service";
import {
  customerHealthResultSchema,
  followupAnalysisResultSchema,
  leakAlertInferenceResultSchema,
  type AiProviderId,
  type AiRun,
  type AiScenario,
  type AiTriggerSource,
  type CustomerHealthResult,
  type FollowupAnalysisResult,
  type LeakAlertInferenceResult
} from "@/types/ai";
import type { AlertItem, AlertRuleHit } from "@/types/alert";
import type { Customer } from "@/types/customer";
import type { FollowupRecord } from "@/types/followup";
import type { Opportunity } from "@/types/opportunity";

type DbClient = ServerSupabaseClient;

interface CustomerContext {
  customer: Customer;
  followups: FollowupRecord[];
  opportunities: Opportunity[];
  unresolvedAlerts: AlertItem[];
}

interface ScenarioExecution<T> {
  result: T;
  outputSnapshot: Record<string, unknown>;
  provider: AiProviderId;
  model: string;
  latencyMs: number | null;
  usedFallback: boolean;
  fallbackReason: string | null;
}

interface LeakAlertWorkItemLink {
  alertId: string;
  workItemId: string;
  created: boolean;
}

function nowIso(): string {
  return new Date().toISOString();
}

function toIsoOrNull(value: unknown): string | null {
  if (typeof value !== "string" || !value.trim()) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return date.toISOString();
}

function shouldEnableStrictMode(): boolean {
  return getAiRuntimeEnv("ai_analysis_strict_mode").deepseekStrictBetaEnabled;
}

function logAiEvent(params: {
  orgId: string;
  customerId: string;
  followupId?: string | null;
  scenario: AiScenario;
  provider: AiProviderId;
  model: string;
  status: "started" | "completed" | "failed";
  durationMs?: number;
  error?: string;
}): void {
  console.info("[ai.run]", {
    org_id: params.orgId,
    customer_id: params.customerId,
    followup_id: params.followupId ?? null,
    scenario: params.scenario,
    provider: params.provider,
    model: params.model,
    status: params.status,
    duration_ms: params.durationMs ?? null,
    error: params.error ?? null
  });
}

function parseObjectOrThrow(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("model_output_not_object");
  }
  return value as Record<string, unknown>;
}

function normalizeFollowupResult(raw: Partial<FollowupAnalysisResult>): FollowupAnalysisResult {
  return {
    customer_status_summary: raw.customer_status_summary ?? "暂无客户状态总结",
    key_needs: raw.key_needs ?? [],
    key_objections: raw.key_objections ?? [],
    buying_signals: raw.buying_signals ?? [],
    risk_level: raw.risk_level ?? "medium",
    leak_risk: raw.leak_risk ?? "medium",
    leak_reasons: raw.leak_reasons ?? [],
    next_best_actions: raw.next_best_actions ?? [],
    recommended_next_followup_at: raw.recommended_next_followup_at ?? null,
    manager_attention_needed: raw.manager_attention_needed ?? false,
    confidence_score: raw.confidence_score ?? 0.6,
    reasoning_brief: raw.reasoning_brief ?? "当前信息不足，建议补充更多沟通事实后再评估。"
  };
}

function normalizeCustomerHealthResult(raw: Partial<CustomerHealthResult>): CustomerHealthResult {
  return {
    stage_fit_assessment: raw.stage_fit_assessment ?? "暂无阶段匹配结论",
    momentum_score: raw.momentum_score ?? 50,
    relationship_score: raw.relationship_score ?? 50,
    decision_clarity_score: raw.decision_clarity_score ?? 40,
    budget_clarity_score: raw.budget_clarity_score ?? 40,
    timeline_clarity_score: raw.timeline_clarity_score ?? 40,
    overall_risk_level: raw.overall_risk_level ?? "medium",
    stall_signals: raw.stall_signals ?? [],
    suggested_strategy: raw.suggested_strategy ?? [],
    summary: raw.summary ?? "暂无健康度总结"
  };
}

function normalizeLeakInferenceResult(raw: Partial<LeakAlertInferenceResult>): LeakAlertInferenceResult {
  return {
    should_create_alert: raw.should_create_alert ?? false,
    severity: raw.severity ?? "warning",
    primary_rule_type: raw.primary_rule_type ?? "ai_detected",
    title: raw.title ?? "潜在漏单风险",
    description: raw.description ?? "建议尽快确认推进节点。",
    evidence: raw.evidence ?? [],
    suggested_owner_action: raw.suggested_owner_action ?? [],
    due_at: raw.due_at ?? null
  };
}

function fallbackFollowup(snapshot: Record<string, unknown>): FollowupAnalysisResult {
  const customer = (snapshot.customer ?? {}) as Record<string, unknown>;
  const latest = (snapshot.latest_followup ?? {}) as Record<string, unknown>;
  const stalledDays = typeof customer.stalled_days === "number" ? customer.stalled_days : 0;
  const winProbability = typeof customer.win_probability === "number" ? customer.win_probability : 40;

  return normalizeFollowupResult({
    customer_status_summary: typeof latest.summary === "string" ? latest.summary : "客户近期保持沟通，需要继续推进关键节点。",
    key_needs: typeof latest.customer_needs === "string" && latest.customer_needs ? [latest.customer_needs] : [],
    key_objections: typeof latest.objections === "string" && latest.objections ? [latest.objections] : [],
    buying_signals: winProbability >= 60 ? ["客户存在较强推进意愿"] : [],
    risk_level: stalledDays >= 8 || winProbability >= 80 ? "high" : stalledDays >= 4 ? "medium" : "low",
    leak_risk: stalledDays >= 6 && winProbability >= 65 ? "high" : stalledDays >= 4 ? "medium" : "low",
    leak_reasons: stalledDays >= 4 ? [`停滞 ${stalledDays} 天`, `成交概率 ${winProbability}%`] : [],
    next_best_actions: ["确认下次会议目标", "推进决策人参与", "48 小时内同步推进清单"],
    recommended_next_followup_at: typeof latest.next_followup_at === "string" ? latest.next_followup_at : null,
    manager_attention_needed: stalledDays >= 6,
    confidence_score: 0.62,
    reasoning_brief: "规则引擎回退结果，建议结合最新沟通记录复核。"
  });
}

function fallbackCustomerHealth(snapshot: Record<string, unknown>): CustomerHealthResult {
  const customer = (snapshot.customer ?? {}) as Record<string, unknown>;
  const stalledDays = typeof customer.stalled_days === "number" ? customer.stalled_days : 0;
  const hasDecisionMaker = customer.has_decision_maker === true;
  const momentum = Math.max(20, 75 - stalledDays * 4);

  return normalizeCustomerHealthResult({
    stage_fit_assessment: "客户可继续推进，建议补强决策链和预算确认。",
    momentum_score: momentum,
    relationship_score: Math.min(100, momentum + 5),
    decision_clarity_score: hasDecisionMaker ? 75 : 35,
    budget_clarity_score: momentum,
    timeline_clarity_score: Math.max(20, momentum - 8),
    overall_risk_level: stalledDays >= 8 || !hasDecisionMaker ? "high" : stalledDays >= 4 ? "medium" : "low",
    stall_signals: stalledDays >= 4 ? [`停滞 ${stalledDays} 天`] : [],
    suggested_strategy: ["明确决策链和预算节点", "设定一周内推进里程碑"],
    summary: "系统已回退到规则化分析。"
  });
}

function fallbackLeakInference(snapshot: Record<string, unknown>): LeakAlertInferenceResult {
  const customer = (snapshot.customer ?? {}) as Record<string, unknown>;
  const ruleHits = Array.isArray(snapshot.rule_hits) ? snapshot.rule_hits : [];
  const stalledDays = typeof customer.stalled_days === "number" ? customer.stalled_days : 0;
  const winProbability = typeof customer.win_probability === "number" ? customer.win_probability : 40;
  const shouldCreate = ruleHits.length > 0 || (stalledDays >= 5 && winProbability >= 65);

  return normalizeLeakInferenceResult({
    should_create_alert: shouldCreate,
    severity: shouldCreate ? (stalledDays >= 8 ? "critical" : "warning") : "info",
    primary_rule_type: shouldCreate ? "ai_detected" : "ai_detected",
    title: shouldCreate ? "潜在漏单风险需要关注" : "当前漏单风险可控",
    description: shouldCreate ? `客户停滞 ${stalledDays} 天，建议立即推进下一节点。` : "暂无新增高风险信号。",
    evidence: shouldCreate ? [`停滞 ${stalledDays} 天`, `成交概率 ${winProbability}%`] : [],
    suggested_owner_action: shouldCreate ? ["24 小时内安排跟进", "同步决策人与预算时间点"] : ["保持当前推进节奏"],
    due_at: shouldCreate ? new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString() : null
  });
}

async function executeScenario<T>(params: {
  supabase: DbClient;
  orgId: string;
  scenario: AiScenario;
  inputSnapshot: Record<string, unknown>;
  schema: ZodSchema<T>;
  fallbackBuilder: (snapshot: Record<string, unknown>) => T;
  reasoningModel?: boolean;
}): Promise<ScenarioExecution<T>> {
  const provider = getAiProvider();
  const prompt = await getActivePromptVersion({
    supabase: params.supabase,
    orgId: params.orgId,
    scenario: params.scenario,
    providerId: provider.id
  });
  const fallbackEnabled = isRuleFallbackEnabled();

  if (!provider.isConfigured()) {
    if (!fallbackEnabled) {
      throw new Error("AI provider is not configured and fallback is disabled");
    }
    const fallback = params.fallbackBuilder(params.inputSnapshot);
    return {
      result: fallback,
      outputSnapshot: fallback as Record<string, unknown>,
      provider: provider.id,
      model: "rule-fallback",
      latencyMs: null,
      usedFallback: true,
      fallbackReason: "provider_not_configured"
    };
  }

  try {
    const response = await provider.chatCompletion({
      scenario: params.scenario,
      model: provider.getDefaultModel({ reasoning: params.reasoningModel === true }),
      systemPrompt: prompt.systemPrompt,
      developerPrompt: `${prompt.developerPrompt}\n\nOutput schema:\n${JSON.stringify(prompt.outputSchema)}`,
      userPrompt: JSON.stringify({
        scenario: params.scenario,
        payload: params.inputSnapshot
      }),
      jsonMode: true,
      strictMode: shouldEnableStrictMode(),
      useReasonerModel: params.reasoningModel === true
    });

    if (response.error) {
      throw new Error(`provider_error:${response.error}`);
    }

    const parsedCandidate = response.parsedJson ?? parseObjectOrThrow(response.rawText ? JSON.parse(response.rawText) : null);
    const parsed = params.schema.parse(parsedCandidate);

    return {
      result: parsed,
      outputSnapshot: response.rawResponse,
      provider: response.provider,
      model: response.model,
      latencyMs: response.latencyMs,
      usedFallback: false,
      fallbackReason: response.strictFallbackUsed ? "strict_mode_auto_fallback" : null
    };
  } catch (error) {
    if (!fallbackEnabled) {
      throw error;
    }
    const fallback = params.fallbackBuilder(params.inputSnapshot);
    return {
      result: fallback,
      outputSnapshot: fallback as Record<string, unknown>,
      provider: provider.id,
      model: "rule-fallback",
      latencyMs: null,
      usedFallback: true,
      fallbackReason: error instanceof Error ? error.message : "provider_or_schema_failure"
    };
  }
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

async function loadCustomerContext(params: { supabase: DbClient; customerId: string }): Promise<CustomerContext> {
  const [customerRes, followupRes, opportunityRes, alertRes] = await Promise.all([
    params.supabase
      .from("customers")
      .select("*, owner:profiles!customers_owner_id_fkey(id, display_name)")
      .eq("id", params.customerId)
      .maybeSingle(),
    params.supabase
      .from("followups")
      .select("*, owner:profiles!followups_owner_id_fkey(id, display_name)")
      .eq("customer_id", params.customerId)
      .eq("draft_status", "confirmed")
      .order("created_at", { ascending: false })
      .limit(12),
    params.supabase
      .from("opportunities")
      .select("*, owner:profiles!opportunities_owner_id_fkey(id, display_name), customer:customers!opportunities_customer_id_fkey(id, company_name)")
      .eq("customer_id", params.customerId)
      .order("updated_at", { ascending: false }),
    params.supabase
      .from("alerts")
      .select("*, owner:profiles!alerts_owner_id_fkey(id, display_name), customer:customers!alerts_customer_id_fkey(id, company_name)")
      .eq("customer_id", params.customerId)
      .neq("status", "resolved")
      .order("created_at", { ascending: false })
  ]);

  if (customerRes.error) throw new Error(customerRes.error.message);
  if (!customerRes.data) throw new Error("customer_not_found_or_forbidden");
  if (followupRes.error) throw new Error(followupRes.error.message);
  if (opportunityRes.error) throw new Error(opportunityRes.error.message);
  if (alertRes.error) throw new Error(alertRes.error.message);

  return {
    customer: mapCustomerRow(customerRes.data as any),
    followups: (followupRes.data ?? []).map((row: any) => mapFollowupRow(row)),
    opportunities: (opportunityRes.data ?? []).map((row: any) => mapOpportunityRow(row)),
    unresolvedAlerts: (alertRes.data ?? []).map((row: any) => mapAlertRow(row))
  };
}

function buildFollowupSnapshot(context: CustomerContext, latest: FollowupRecord): Record<string, unknown> {
  return {
    customer: {
      id: context.customer.id,
      stage: context.customer.stage,
      owner_id: context.customer.ownerId,
      win_probability: context.customer.winProbability,
      stalled_days: context.customer.stalledDays,
      has_decision_maker: context.customer.hasDecisionMaker
    },
    latest_followup: {
      id: latest.id,
      method: latest.method,
      summary: latest.summary,
      customer_needs: latest.customerNeeds,
      objections: latest.objections,
      next_step: latest.nextPlan,
      next_followup_at: latest.nextFollowupAt
    },
    recent_followups: context.followups.slice(0, 6).map((item) => ({
      created_at: item.createdAt,
      summary: item.summary,
      customer_needs: item.customerNeeds,
      objections: item.objections,
      next_step: item.nextPlan
    })),
    opportunities: context.opportunities.slice(0, 6).map((item) => ({
      id: item.id,
      stage: item.stage,
      amount: item.expectedAmount,
      risk_level: item.riskLevel,
      last_progress_at: item.lastProgressAt
    }))
  };
}

function buildCustomerHealthSnapshot(context: CustomerContext): Record<string, unknown> {
  return {
    customer: {
      id: context.customer.id,
      stage: context.customer.stage,
      win_probability: context.customer.winProbability,
      stalled_days: context.customer.stalledDays,
      has_decision_maker: context.customer.hasDecisionMaker,
      tags: context.customer.tags
    },
    recent_followups: context.followups.slice(0, 8).map((item) => ({
      created_at: item.createdAt,
      summary: item.summary,
      customer_needs: item.customerNeeds,
      objections: item.objections,
      next_step: item.nextPlan
    })),
    opportunities: context.opportunities.slice(0, 8).map((item) => ({
      id: item.id,
      stage: item.stage,
      amount: item.expectedAmount,
      risk_level: item.riskLevel,
      last_progress_at: item.lastProgressAt
    })),
    unresolved_alerts: context.unresolvedAlerts.map((item) => ({
      id: item.id,
      rule_type: item.ruleType,
      severity: item.level,
      title: item.title
    }))
  };
}

function buildLeakSnapshot(context: CustomerContext, ruleHits: AlertRuleHit[]): Record<string, unknown> {
  return {
    customer: {
      id: context.customer.id,
      stage: context.customer.stage,
      owner_id: context.customer.ownerId,
      win_probability: context.customer.winProbability,
      stalled_days: context.customer.stalledDays,
      has_decision_maker: context.customer.hasDecisionMaker
    },
    recent_followups: context.followups.slice(0, 6).map((item) => ({
      created_at: item.createdAt,
      summary: item.summary,
      next_step: item.nextPlan
    })),
    opportunities: context.opportunities.slice(0, 6).map((item) => ({
      id: item.id,
      stage: item.stage,
      risk_level: item.riskLevel,
      last_progress_at: item.lastProgressAt
    })),
    existing_alerts: context.unresolvedAlerts.map((item) => ({
      id: item.id,
      rule_type: item.ruleType,
      severity: item.level,
      status: item.status
    })),
    rule_hits: ruleHits.map((item) => ({
      rule_type: item.ruleType,
      severity: item.level,
      title: item.title,
      evidence: item.evidence
    }))
  };
}

export async function runFollowupAnalysis(params: {
  supabase: DbClient;
  orgId: string;
  customerId: string;
  followupId: string;
  triggeredByUserId: string;
  triggerSource: AiTriggerSource;
}): Promise<{
  run: AiRun;
  result: FollowupAnalysisResult;
  leakInference: LeakAlertInferenceResult | null;
  leakAlertAction: "created" | "updated" | "deduped" | null;
  alertWorkItem: LeakAlertWorkItemLink | null;
  usedFallback: boolean;
}> {
  const start = Date.now();
  const provider = getAiProvider();
  const context = await loadCustomerContext({ supabase: params.supabase, customerId: params.customerId });
  const latest = context.followups.find((item) => item.id === params.followupId) ?? context.followups[0];
  if (!latest) throw new Error("没有可分析的跟进记录");

  const snapshot = buildFollowupSnapshot(context, latest);
  const promptVersion = await getPromptVersion({
    supabase: params.supabase,
    orgId: params.orgId,
    scenario: "followup_analysis",
    providerId: provider.id
  });

  const run = await createAiRun({
    supabase: params.supabase,
    orgId: params.orgId,
    customerId: params.customerId,
    followupId: params.followupId,
    triggeredByUserId: params.triggeredByUserId,
    triggerSource: params.triggerSource,
    scenario: "followup_analysis",
    provider: provider.id,
    model: provider.getDefaultModel(),
    promptVersion,
    inputSnapshot: snapshot
  });

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "running",
    startedAt: nowIso()
  });

  logAiEvent({
    orgId: params.orgId,
    customerId: params.customerId,
    followupId: params.followupId,
    scenario: "followup_analysis",
    provider: provider.id,
    model: provider.getDefaultModel(),
    status: "started"
  });

  try {
    const execution = await executeScenario({
      supabase: params.supabase,
      orgId: params.orgId,
      scenario: "followup_analysis",
      inputSnapshot: snapshot,
      schema: followupAnalysisResultSchema,
      fallbackBuilder: fallbackFollowup
    });

    const result = normalizeFollowupResult(execution.result as Partial<FollowupAnalysisResult>);
    const aiSummary = result.customer_status_summary;
    const aiSuggestion = result.next_best_actions.join("；");
    const aiRiskJudgement = result.leak_reasons.length > 0 ? `${result.reasoning_brief}。证据：${result.leak_reasons.join("；")}` : result.reasoning_brief;

    const followupUpdate = await params.supabase
      .from("followups")
      .update({
        ai_summary: aiSummary,
        ai_suggestion: aiSuggestion,
        ai_risk_level: result.risk_level,
        ai_leak_risk: result.leak_risk !== "low"
      })
      .eq("id", params.followupId);
    if (followupUpdate.error) throw new Error(`db_write_followup_failed:${followupUpdate.error.message}`);

    const customerUpdate = await params.supabase
      .from("customers")
      .update({
        ai_summary: aiSummary,
        ai_suggestion: aiSuggestion,
        ai_risk_judgement: aiRiskJudgement,
        risk_level: result.risk_level,
        next_followup_at: toIsoOrNull(result.recommended_next_followup_at) ?? context.customer.nextFollowupAt
      })
      .eq("id", params.customerId);
    if (customerUpdate.error) throw new Error(`db_write_customer_failed:${customerUpdate.error.message}`);

    let leakInference: LeakAlertInferenceResult | null = null;
    let leakAlertAction: "created" | "updated" | "deduped" | null = null;
    let alertWorkItem: LeakAlertWorkItemLink | null = null;
    try {
      const leak = await runLeakRiskInference({
        supabase: params.supabase,
        orgId: params.orgId,
        customerId: params.customerId,
        triggeredByUserId: params.triggeredByUserId,
        triggerSource: "alert_regen",
        createWorkItemForAlert: true
      });
      leakInference = leak.result;
      leakAlertAction = leak.alertAction;
      alertWorkItem = leak.alertWorkItem;
    } catch (leakError) {
      console.error("[ai.followup] leak_inference_failed", {
        customer_id: params.customerId,
        error: leakError instanceof Error ? leakError.message : "unknown"
      });
    }

    await updateAiRunStatus({
      supabase: params.supabase,
      runId: run.id,
      status: "completed",
      provider: execution.provider,
      model: execution.model,
      outputSnapshot: execution.outputSnapshot,
      parsedResult: {
        followup_analysis: result,
        leak_inference: leakInference,
        leak_alert_action: leakAlertAction,
        alert_work_item: alertWorkItem
      },
      latencyMs: execution.latencyMs,
      resultSource: execution.usedFallback ? "fallback" : "provider",
      fallbackReason: execution.fallbackReason,
      completedAt: nowIso(),
      errorMessage: execution.usedFallback ? execution.fallbackReason : null
    });

    logAiEvent({
      orgId: params.orgId,
      customerId: params.customerId,
      followupId: params.followupId,
      scenario: "followup_analysis",
      provider: execution.provider,
      model: execution.model,
      status: "completed",
      durationMs: Date.now() - start
    });

    return {
      run: {
        ...run,
        status: "completed",
        provider: execution.provider,
        model: execution.model,
        output_snapshot: execution.outputSnapshot,
        parsed_result: { followup_analysis: result },
        latency_ms: execution.latencyMs,
        result_source: execution.usedFallback ? "fallback" : "provider",
        fallback_reason: execution.fallbackReason,
        completed_at: nowIso()
      },
      result,
      leakInference,
      leakAlertAction,
      alertWorkItem,
      usedFallback: execution.usedFallback
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "followup_analysis_failed";
    await updateAiRunStatus({
      supabase: params.supabase,
      runId: run.id,
      status: "failed",
      provider: provider.id,
      model: provider.getDefaultModel(),
      errorMessage: message,
      completedAt: nowIso()
    });

    logAiEvent({
      orgId: params.orgId,
      customerId: params.customerId,
      followupId: params.followupId,
      scenario: "followup_analysis",
      provider: provider.id,
      model: provider.getDefaultModel(),
      status: "failed",
      durationMs: Date.now() - start,
      error: message
    });

    throw new Error("本次 AI 分析失败，请稍后重试。");
  }
}

export async function runCustomerHealthAnalysis(params: {
  supabase: DbClient;
  orgId: string;
  customerId: string;
  triggeredByUserId: string;
  triggerSource: AiTriggerSource;
}): Promise<{
  run: AiRun;
  result: CustomerHealthResult;
  usedFallback: boolean;
}> {
  const start = Date.now();
  const provider = getAiProvider();
  const context = await loadCustomerContext({ supabase: params.supabase, customerId: params.customerId });
  const snapshot = buildCustomerHealthSnapshot(context);
  const promptVersion = await getPromptVersion({
    supabase: params.supabase,
    orgId: params.orgId,
    scenario: "customer_health",
    providerId: provider.id
  });

  const run = await createAiRun({
    supabase: params.supabase,
    orgId: params.orgId,
    customerId: params.customerId,
    followupId: null,
    triggeredByUserId: params.triggeredByUserId,
    triggerSource: params.triggerSource,
    scenario: "customer_health",
    provider: provider.id,
    model: provider.getDefaultModel({ reasoning: true }),
    promptVersion,
    inputSnapshot: snapshot
  });

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "running",
    startedAt: nowIso()
  });

  logAiEvent({
    orgId: params.orgId,
    customerId: params.customerId,
    scenario: "customer_health",
    provider: provider.id,
    model: provider.getDefaultModel({ reasoning: true }),
    status: "started"
  });

  try {
    const execution = await executeScenario({
      supabase: params.supabase,
      orgId: params.orgId,
      scenario: "customer_health",
      inputSnapshot: snapshot,
      schema: customerHealthResultSchema,
      fallbackBuilder: fallbackCustomerHealth,
      reasoningModel: true
    });

    const result = normalizeCustomerHealthResult(execution.result as Partial<CustomerHealthResult>);

    const customerUpdate = await params.supabase
      .from("customers")
      .update({
        ai_summary: result.summary,
        ai_suggestion: result.suggested_strategy.join("；"),
        ai_risk_judgement: result.stage_fit_assessment,
        risk_level: result.overall_risk_level
      })
      .eq("id", params.customerId);
    if (customerUpdate.error) throw new Error(`db_write_customer_failed:${customerUpdate.error.message}`);

    await updateAiRunStatus({
      supabase: params.supabase,
      runId: run.id,
      status: "completed",
      provider: execution.provider,
      model: execution.model,
      outputSnapshot: execution.outputSnapshot,
      parsedResult: { customer_health: result },
      latencyMs: execution.latencyMs,
      resultSource: execution.usedFallback ? "fallback" : "provider",
      fallbackReason: execution.fallbackReason,
      completedAt: nowIso(),
      errorMessage: execution.usedFallback ? execution.fallbackReason : null
    });

    logAiEvent({
      orgId: params.orgId,
      customerId: params.customerId,
      scenario: "customer_health",
      provider: execution.provider,
      model: execution.model,
      status: "completed",
      durationMs: Date.now() - start
    });

    return {
      run: {
        ...run,
        status: "completed",
        provider: execution.provider,
        model: execution.model,
        output_snapshot: execution.outputSnapshot,
        parsed_result: { customer_health: result },
        latency_ms: execution.latencyMs,
        result_source: execution.usedFallback ? "fallback" : "provider",
        fallback_reason: execution.fallbackReason,
        completed_at: nowIso()
      },
      result,
      usedFallback: execution.usedFallback
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "customer_health_failed";
    await updateAiRunStatus({
      supabase: params.supabase,
      runId: run.id,
      status: "failed",
      provider: provider.id,
      model: provider.getDefaultModel({ reasoning: true }),
      errorMessage: message,
      completedAt: nowIso()
    });

    logAiEvent({
      orgId: params.orgId,
      customerId: params.customerId,
      scenario: "customer_health",
      provider: provider.id,
      model: provider.getDefaultModel({ reasoning: true }),
      status: "failed",
      durationMs: Date.now() - start,
      error: message
    });

    throw new Error("客户健康分析失败，请稍后重试。");
  }
}

export async function runLeakRiskInference(params: {
  supabase: DbClient;
  orgId: string;
  customerId: string;
  triggeredByUserId: string;
  triggerSource: AiTriggerSource;
  contextOverride?: CustomerContext;
  ruleHitsOverride?: AlertRuleHit[];
  createWorkItemForAlert?: boolean;
}): Promise<{
  run: AiRun;
  result: LeakAlertInferenceResult;
  alertAction: "created" | "updated" | "deduped" | null;
  alertWorkItem: LeakAlertWorkItemLink | null;
  usedFallback: boolean;
}> {
  const start = Date.now();
  const provider = getAiProvider();
  const context = params.contextOverride ?? (await loadCustomerContext({ supabase: params.supabase, customerId: params.customerId }));
  const ruleHits =
    params.ruleHitsOverride ??
    evaluateAlertRules({
      now: new Date(),
      customer: context.customer,
      followups: context.followups,
      opportunities: context.opportunities
    });

  const snapshot = buildLeakSnapshot(context, ruleHits);
  const promptVersion = await getPromptVersion({
    supabase: params.supabase,
    orgId: params.orgId,
    scenario: "leak_risk_inference",
    providerId: provider.id
  });

  const run = await createAiRun({
    supabase: params.supabase,
    orgId: params.orgId,
    customerId: params.customerId,
    followupId: null,
    triggeredByUserId: params.triggeredByUserId,
    triggerSource: params.triggerSource,
    scenario: "leak_risk_inference",
    provider: provider.id,
    model: provider.getDefaultModel({ reasoning: true }),
    promptVersion,
    inputSnapshot: snapshot
  });

  await updateAiRunStatus({
    supabase: params.supabase,
    runId: run.id,
    status: "running",
    startedAt: nowIso()
  });

  logAiEvent({
    orgId: params.orgId,
    customerId: params.customerId,
    scenario: "leak_risk_inference",
    provider: provider.id,
    model: provider.getDefaultModel({ reasoning: true }),
    status: "started"
  });

  try {
    const execution = await executeScenario({
      supabase: params.supabase,
      orgId: params.orgId,
      scenario: "leak_risk_inference",
      inputSnapshot: snapshot,
      schema: leakAlertInferenceResultSchema,
      fallbackBuilder: fallbackLeakInference,
      reasoningModel: true
    });

    const result = normalizeLeakInferenceResult(execution.result as Partial<LeakAlertInferenceResult>);
    let alertAction: "created" | "updated" | "deduped" | null = null;
    let alertWorkItem: LeakAlertWorkItemLink | null = null;

    if (result.should_create_alert) {
      const upserted = await upsertLeakAlert({
        supabase: params.supabase,
        input: {
          orgId: params.orgId,
          customerId: params.customerId,
          ownerId: context.customer.ownerId,
          ruleType: result.primary_rule_type,
          source: execution.usedFallback ? "fallback" : ruleHits.length > 0 ? "hybrid" : "ai",
          level: result.severity,
          title: result.title,
          description: result.description,
          evidence: result.evidence,
          suggestedOwnerAction: result.suggested_owner_action,
          dueAt: toIsoOrNull(result.due_at),
          aiRunId: run.id
        }
      });
      alertAction = upserted.action;

      if (params.createWorkItemForAlert === true) {
        const linkedWorkItem = await createWorkItemFromAlert({
          supabase: params.supabase,
          orgId: params.orgId,
          alertId: upserted.alertId,
          actorUserId: params.triggeredByUserId
        });
        alertWorkItem = {
          alertId: upserted.alertId,
          workItemId: linkedWorkItem.workItem.id,
          created: linkedWorkItem.created
        };
      }
    }

    await updateAiRunStatus({
      supabase: params.supabase,
      runId: run.id,
      status: "completed",
      provider: execution.provider,
      model: execution.model,
      outputSnapshot: execution.outputSnapshot,
      parsedResult: {
        leak_inference: result,
        rule_hits: ruleHits,
        alert_action: alertAction,
        alert_work_item: alertWorkItem
      },
      latencyMs: execution.latencyMs,
      resultSource: execution.usedFallback ? "fallback" : "provider",
      fallbackReason: execution.fallbackReason,
      completedAt: nowIso(),
      errorMessage: execution.usedFallback ? execution.fallbackReason : null
    });

    logAiEvent({
      orgId: params.orgId,
      customerId: params.customerId,
      scenario: "leak_risk_inference",
      provider: execution.provider,
      model: execution.model,
      status: "completed",
      durationMs: Date.now() - start
    });

    return {
      run: {
        ...run,
        status: "completed",
        provider: execution.provider,
        model: execution.model,
        output_snapshot: execution.outputSnapshot,
        parsed_result: { leak_inference: result },
        latency_ms: execution.latencyMs,
        result_source: execution.usedFallback ? "fallback" : "provider",
        fallback_reason: execution.fallbackReason,
        completed_at: nowIso()
      },
      result,
      alertAction,
      alertWorkItem,
      usedFallback: execution.usedFallback
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "leak_risk_failed";
    await updateAiRunStatus({
      supabase: params.supabase,
      runId: run.id,
      status: "failed",
      provider: provider.id,
      model: provider.getDefaultModel({ reasoning: true }),
      errorMessage: message,
      completedAt: nowIso()
    });

    logAiEvent({
      orgId: params.orgId,
      customerId: params.customerId,
      scenario: "leak_risk_inference",
      provider: provider.id,
      model: provider.getDefaultModel({ reasoning: true }),
      status: "failed",
      durationMs: Date.now() - start,
      error: message
    });

    throw new Error("漏单推断失败，请稍后重试。");
  }
}

export async function listCustomerAiRuns(params: { supabase: DbClient; customerId: string; limit?: number }): Promise<AiRun[]> {
  return listRuns({
    supabase: params.supabase,
    customerId: params.customerId,
    limit: params.limit
  });
}
