import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import { buildFallbackDailyPlan } from "@/lib/daily-plan-fallback";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { getActivePromptVersion } from "@/services/ai-prompt-service";
import { autoGenerateTaskPrepCards } from "@/services/preparation-engine-service";
import { prioritizeWorkCandidates, type WorkCandidate } from "@/services/task-priority-service";
import { createWorkItem, listWorkItems } from "@/services/work-item-service";
import { getUserMemoryProfile } from "@/services/user-memory-service";
import { getTodayPlanView, saveDailyPlan } from "@/services/work-plan-service";
import { mapWorkAgentRunRow } from "@/services/mappers";
import { dailyWorkPlanGenerationResultSchema, type AiScenario, type DailyWorkPlanGenerationResult } from "@/types/ai";
import type { Database, Json } from "@/types/database";
import type { WorkAgentRun, WorkItem } from "@/types/work";

type DbClient = ServerSupabaseClient;

function todayDateText(): string {
  return new Date().toISOString().slice(0, 10);
}

function nowIso(): string {
  return new Date().toISOString();
}

function plusDaysIso(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString();
}

function toPriorityScoreFromAlert(level: Database["public"]["Enums"]["alert_severity"]): number {
  if (level === "critical") return 92;
  if (level === "warning") return 76;
  return 52;
}

async function createWorkAgentRun(params: {
  supabase: DbClient;
  orgId: string;
  userId: string | null;
  runScope: Database["public"]["Enums"]["work_agent_run_scope"];
  inputSnapshot: Json;
}): Promise<WorkAgentRun> {
  const { data, error } = await params.supabase
    .from("work_agent_runs")
    .insert({
      org_id: params.orgId,
      user_id: params.userId,
      run_scope: params.runScope,
      status: "queued",
      input_snapshot: params.inputSnapshot
    })
    .select("*")
    .single();

  if (error || !data) throw new Error(error?.message ?? "Failed to create work agent run");
  return mapWorkAgentRunRow(data as never);
}

async function updateWorkAgentRun(params: {
  supabase: DbClient;
  runId: string;
  status: Database["public"]["Enums"]["work_agent_run_status"];
  provider?: Database["public"]["Enums"]["ai_provider"] | null;
  model?: string | null;
  resultSource?: Database["public"]["Enums"]["ai_result_source"];
  fallbackReason?: string | null;
  errorMessage?: string | null;
  outputSnapshot?: Json;
  parsedResult?: Json;
  startedAt?: string | null;
  completedAt?: string | null;
}): Promise<void> {
  const payload: Database["public"]["Tables"]["work_agent_runs"]["Update"] = {
    status: params.status
  };
  if (params.provider !== undefined) payload.provider = params.provider;
  if (params.model !== undefined) payload.model = params.model;
  if (params.resultSource !== undefined) payload.result_source = params.resultSource;
  if (params.fallbackReason !== undefined) payload.fallback_reason = params.fallbackReason;
  if (params.errorMessage !== undefined) payload.error_message = params.errorMessage;
  if (params.outputSnapshot !== undefined) payload.output_snapshot = params.outputSnapshot;
  if (params.parsedResult !== undefined) payload.parsed_result = params.parsedResult;
  if (params.startedAt !== undefined) payload.started_at = params.startedAt;
  if (params.completedAt !== undefined) payload.completed_at = params.completedAt;

  const { error } = await params.supabase.from("work_agent_runs").update(payload).eq("id", params.runId);
  if (error) throw new Error(error.message);
}

function uniqueBySourceRef(items: WorkItem[]): Map<string, WorkItem> {
  const map = new Map<string, WorkItem>();
  for (const item of items) {
    if (!item.sourceRefType || !item.sourceRefId) continue;
    map.set(`${item.sourceRefType}:${item.sourceRefId}`, item);
  }
  return map;
}

export async function generateTodayPlan(params: {
  supabase: DbClient;
  orgId: string;
  userId: string;
  userName: string;
  triggeredBy: string;
  force?: boolean;
}): Promise<{
  planDate: string;
  focusTheme: string;
  usedFallback: boolean;
  runId: string;
}> {
  const planDate = todayDateText();
  if (!params.force) {
    const existing = await getTodayPlanView({
      supabase: params.supabase,
      orgId: params.orgId,
      userId: params.userId,
      date: planDate
    });
    if (existing?.plan && existing.plan.status !== "archived") {
      return {
        planDate,
        focusTheme: existing.plan.focusTheme ?? "Today's Focus",
        usedFallback: existing.usedFallback,
        runId: existing.latestRun?.id ?? "existing-plan"
      };
    }
  }

  const [customersRes, alertsRes, draftsRes, opportunitiesRes, openWorkItems, memoryProfile] = await Promise.all([
    params.supabase
      .from("customers")
      .select("*")
      .eq("org_id", params.orgId)
      .eq("owner_id", params.userId)
      .in("current_stage", ["lead", "initial_contact", "needs_confirmed", "proposal", "negotiation"]),
    params.supabase
      .from("alerts")
      .select("*")
      .eq("org_id", params.orgId)
      .eq("owner_id", params.userId)
      .in("status", ["open", "watching"]),
    params.supabase
      .from("followups")
      .select("*")
      .eq("org_id", params.orgId)
      .eq("owner_id", params.userId)
      .eq("draft_status", "draft")
      .order("created_at", { ascending: false })
      .limit(30),
    params.supabase
      .from("opportunities")
      .select("*")
      .eq("org_id", params.orgId)
      .eq("owner_id", params.userId)
      .in("stage", ["proposal", "negotiation", "business_review"]),
    listWorkItems({
      supabase: params.supabase,
      orgId: params.orgId,
      ownerId: params.userId,
      statuses: ["todo", "in_progress", "snoozed"],
      limit: 400
    }),
    getUserMemoryProfile({
      supabase: params.supabase,
      orgId: params.orgId,
      userId: params.userId
    }).catch(() => null)
  ]);

  if (customersRes.error) throw new Error(customersRes.error.message);
  if (alertsRes.error) throw new Error(alertsRes.error.message);
  if (draftsRes.error) throw new Error(draftsRes.error.message);
  if (opportunitiesRes.error) throw new Error(opportunitiesRes.error.message);

  const customers = (customersRes.data ?? []) as Database["public"]["Tables"]["customers"]["Row"][];
  const alerts = (alertsRes.data ?? []) as Database["public"]["Tables"]["alerts"]["Row"][];
  const drafts = (draftsRes.data ?? []) as Database["public"]["Tables"]["followups"]["Row"][];
  const opportunities = (opportunitiesRes.data ?? []) as Database["public"]["Tables"]["opportunities"]["Row"][];

  const backlogSize = openWorkItems.length;
  const bySource = uniqueBySourceRef(openWorkItems);
  const candidates: WorkCandidate[] = [];

  for (const customer of customers) {
    const customerMapped = {
      id: customer.id,
      customerName: customer.name,
      companyName: customer.company_name,
      contactName: customer.contact_name,
      phone: customer.phone ?? "-",
      email: customer.email ?? "-",
      sourceChannel: customer.source_channel ?? "-",
      stage: customer.current_stage,
      ownerId: customer.owner_id,
      ownerName: params.userName,
      lastFollowupAt: customer.last_followup_at ?? customer.created_at,
      nextFollowupAt: customer.next_followup_at ?? customer.created_at,
      winProbability: Number(customer.win_probability ?? 0),
      riskLevel: customer.risk_level,
      tags: customer.tags ?? [],
      aiSummary: customer.ai_summary ?? "",
      aiSuggestion: customer.ai_suggestion ?? "",
      aiRiskJudgement: customer.ai_risk_judgement ?? "",
      stalledDays: 0,
      hasDecisionMaker: customer.has_decision_maker,
      createdAt: customer.created_at,
      updatedAt: customer.updated_at
    };

    const dueDiff = customer.next_followup_at ? new Date(customer.next_followup_at).getTime() - Date.now() : Number.POSITIVE_INFINITY;
    if (dueDiff <= 24 * 60 * 60 * 1000) {
      candidates.push({
        sourceType: "followup_due",
        workType: "followup_call",
        title: `Follow up today: ${customer.company_name}`,
        description: "Due follow-up task. Prioritize direct contact to keep deal momentum.",
        customer: customerMapped,
        dueAt: customer.next_followup_at,
        scheduledFor: planDate,
        managerFlagged: false,
        highProbabilityOpportunity: Number(customer.win_probability ?? 0) >= 70,
        rhythmFit: "neutral",
        backlogSize,
        extraRationale: "Customer is at or near the planned follow-up time."
      });
    }

    const lastFollowupDays = Math.floor((Date.now() - new Date(customer.last_followup_at ?? customer.created_at).getTime()) / (24 * 60 * 60 * 1000));
    if (Number(customer.win_probability ?? 0) >= 70 && lastFollowupDays >= 7) {
      candidates.push({
        sourceType: "ai_suggested",
        workType: "revive_stalled_deal",
        title: `Revive stalled high-probability customer: ${customer.company_name}`,
        description: "High-probability customer has stalled. Re-engage with a concrete next step.",
        customer: customerMapped,
        dueAt: plusDaysIso(1),
        scheduledFor: planDate,
        managerFlagged: false,
        highProbabilityOpportunity: true,
        rhythmFit: "neutral",
        backlogSize,
        extraRationale: "High-probability customer has been inactive for an extended period."
      });
    }
  }

  for (const alert of alerts) {
    const customer = customers.find((item) => item.id === alert.customer_id) ?? null;
    const customerMapped = customer
      ? {
          id: customer.id,
          customerName: customer.name,
          companyName: customer.company_name,
          contactName: customer.contact_name,
          phone: customer.phone ?? "-",
          email: customer.email ?? "-",
          sourceChannel: customer.source_channel ?? "-",
          stage: customer.current_stage,
          ownerId: customer.owner_id,
          ownerName: params.userName,
          lastFollowupAt: customer.last_followup_at ?? customer.created_at,
          nextFollowupAt: customer.next_followup_at ?? customer.created_at,
          winProbability: Number(customer.win_probability ?? 0),
          riskLevel: customer.risk_level,
          tags: customer.tags ?? [],
          aiSummary: customer.ai_summary ?? "",
          aiSuggestion: customer.ai_suggestion ?? "",
          aiRiskJudgement: customer.ai_risk_judgement ?? "",
          stalledDays: 0,
          hasDecisionMaker: customer.has_decision_maker,
          createdAt: customer.created_at,
          updatedAt: customer.updated_at
        }
      : null;

    candidates.push({
      sourceType: "alert",
      workType: "resolve_alert",
      title: `Resolve alert: ${alert.title}`,
      description: alert.description ?? alert.title,
      customer: customerMapped,
      dueAt: alert.due_at ?? plusDaysIso(1),
      scheduledFor: planDate,
      managerFlagged: alert.severity === "critical",
      highProbabilityOpportunity: customer ? Number(customer.win_probability ?? 0) >= 70 : false,
      rhythmFit: "neutral",
      backlogSize,
      extraRationale: `Alert severity: ${alert.severity}`
    });
  }

  for (const draft of drafts) {
    const customer = customers.find((item) => item.id === draft.customer_id) ?? null;
    if (!customer) continue;
    const customerMapped = {
      id: customer.id,
      customerName: customer.name,
      companyName: customer.company_name,
      contactName: customer.contact_name,
      phone: customer.phone ?? "-",
      email: customer.email ?? "-",
      sourceChannel: customer.source_channel ?? "-",
      stage: customer.current_stage,
      ownerId: customer.owner_id,
      ownerName: params.userName,
      lastFollowupAt: customer.last_followup_at ?? customer.created_at,
      nextFollowupAt: customer.next_followup_at ?? customer.created_at,
      winProbability: Number(customer.win_probability ?? 0),
      riskLevel: customer.risk_level,
      tags: customer.tags ?? [],
      aiSummary: customer.ai_summary ?? "",
      aiSuggestion: customer.ai_suggestion ?? "",
      aiRiskJudgement: customer.ai_risk_judgement ?? "",
      stalledDays: 0,
      hasDecisionMaker: customer.has_decision_maker,
      createdAt: customer.created_at,
      updatedAt: customer.updated_at
    };

    candidates.push({
      sourceType: "draft_confirmation",
      workType: "confirm_capture_draft",
      title: `Confirm capture draft: ${customer.company_name}`,
      description: draft.summary,
      customer: customerMapped,
      dueAt: plusDaysIso(1),
      scheduledFor: planDate,
      managerFlagged: false,
      highProbabilityOpportunity: Number(customer.win_probability ?? 0) >= 70,
      rhythmFit: "neutral",
      backlogSize,
      extraRationale: "Unconfirmed capture draft can delay follow-up execution."
    });
  }

  for (const opp of opportunities) {
    const customer = customers.find((item) => item.id === opp.customer_id);
    if (!customer) continue;
    const lastActivity = opp.last_activity_at ? new Date(opp.last_activity_at).getTime() : 0;
    const staleDays = Math.floor((Date.now() - lastActivity) / (24 * 60 * 60 * 1000));
    if (staleDays < 7) continue;

    const customerMapped = {
      id: customer.id,
      customerName: customer.name,
      companyName: customer.company_name,
      contactName: customer.contact_name,
      phone: customer.phone ?? "-",
      email: customer.email ?? "-",
      sourceChannel: customer.source_channel ?? "-",
      stage: customer.current_stage,
      ownerId: customer.owner_id,
      ownerName: params.userName,
      lastFollowupAt: customer.last_followup_at ?? customer.created_at,
      nextFollowupAt: customer.next_followup_at ?? customer.created_at,
      winProbability: Number(customer.win_probability ?? 0),
      riskLevel: customer.risk_level,
      tags: customer.tags ?? [],
      aiSummary: customer.ai_summary ?? "",
      aiSuggestion: customer.ai_suggestion ?? "",
      aiRiskJudgement: customer.ai_risk_judgement ?? "",
      stalledDays: 0,
      hasDecisionMaker: customer.has_decision_maker,
      createdAt: customer.created_at,
      updatedAt: customer.updated_at
    };

    candidates.push({
      sourceType: "ai_suggested",
      workType: "prepare_proposal",
      title: `Advance opportunity: ${opp.title}`,
      description: "Opportunity stage has stalled. Push quote and decision progression.",
      customer: customerMapped,
      dueAt: plusDaysIso(2),
      scheduledFor: planDate,
      managerFlagged: false,
      highProbabilityOpportunity: Number(customer.win_probability ?? 0) >= 70,
      rhythmFit: "neutral",
      backlogSize,
      extraRationale: "Stalled opportunity stage impacts close momentum."
    });
  }

  const prioritized = prioritizeWorkCandidates({
    candidates,
    memoryRhythmHints: memoryProfile?.commonFollowupRhythm ?? []
  });

  const persistedItems: WorkItem[] = [];
  for (const item of prioritized) {
    const sourceRefId = item.sourceType === "alert"
      ? alerts.find((alert) => `Resolve alert: ${alert.title}` === item.title)?.id ?? null
      : item.sourceType === "draft_confirmation"
        ? drafts.find((draft) => draft.summary === item.description)?.id ?? null
        : item.customer?.id ?? null;
    const sourceRefType =
      item.sourceType === "alert"
        ? "alert"
        : item.sourceType === "draft_confirmation"
          ? "followup_draft"
          : "customer";

    const dedupeKey = sourceRefId ? `${sourceRefType}:${sourceRefId}` : null;
    const existing = dedupeKey ? bySource.get(dedupeKey) : null;

    if (existing) {
      const { data, error } = await params.supabase
        .from("work_items")
        .update({
          title: item.title,
          description: item.description,
          rationale: item.rationale,
          priority_score: item.priorityScore,
          priority_band: item.priorityBand,
          due_at: item.dueAt,
          scheduled_for: item.scheduledFor,
          updated_at: nowIso()
        })
        .eq("id", existing.id)
        .select("*, owner:profiles!work_items_owner_id_fkey(id, display_name), customer:customers!work_items_customer_id_fkey(id, company_name)")
        .single();
      if (error || !data) throw new Error(error?.message ?? "Failed to update existing work item");
      persistedItems.push({
        ...existing,
        ...{
          title: item.title,
          description: item.description,
          rationale: item.rationale,
          priorityScore: item.priorityScore,
          priorityBand: item.priorityBand,
          dueAt: item.dueAt,
          scheduledFor: item.scheduledFor,
          updatedAt: (data as any).updated_at
        }
      });
      continue;
    }

    const created = await createWorkItem({
      supabase: params.supabase,
      orgId: params.orgId,
      ownerId: params.userId,
      customerId: item.customer?.id ?? null,
      sourceType: item.sourceType,
      workType: item.workType,
      title: item.title,
      description: item.description,
      rationale: item.rationale,
      priorityScore: item.sourceType === "alert" && item.customer?.riskLevel === "high" ? Math.max(item.priorityScore, toPriorityScoreFromAlert("critical")) : item.priorityScore,
      priorityBand: item.priorityBand,
      scheduledFor: item.scheduledFor,
      dueAt: item.dueAt,
      sourceRefType,
      sourceRefId,
      aiGenerated: item.sourceType === "ai_suggested",
      createdBy: params.triggeredBy
    });
    persistedItems.push(created);
    if (dedupeKey) bySource.set(dedupeKey, created);
  }

  const sortedItems = [...persistedItems].sort((a, b) => b.priorityScore - a.priorityScore).slice(0, 20);

  const scenario: AiScenario = "daily_work_plan_generation";
  const provider = getAiProvider();
  const model = provider.getDefaultModel({ reasoning: true });
  const run = await createWorkAgentRun({
    supabase: params.supabase,
    orgId: params.orgId,
    userId: params.userId,
    runScope: "user_daily_plan",
    inputSnapshot: {
      scenario,
      plan_date: planDate,
      item_count: sortedItems.length
    }
  });

  await updateWorkAgentRun({
    supabase: params.supabase,
    runId: run.id,
    status: "running",
    startedAt: nowIso(),
    provider: provider.id,
    model
  });

  let result: DailyWorkPlanGenerationResult;
  let usedFallback = false;
  let fallbackReason: string | null = null;
  let outputSnapshot: Json = {};
  let responseModel = model;
  let responseProvider = provider.id;

  const prompt = await getActivePromptVersion({
    supabase: params.supabase,
    orgId: params.orgId,
    scenario,
    providerId: provider.id
  });

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
        payload: {
          plan_date: planDate,
          items: sortedItems.map((item) => ({
            id: item.id,
            title: item.title,
            priority_score: item.priorityScore,
            priority_band: item.priorityBand,
            rationale: item.rationale,
            due_at: item.dueAt,
            customer_name: item.customerName,
            work_type: item.workType
          })),
          memory_summary: memoryProfile?.summary ?? "",
          memory_rhythm: memoryProfile?.commonFollowupRhythm ?? []
        }
      }),
      jsonMode: true,
      strictMode: true,
      useReasonerModel: true
    });

    responseModel = response.model;
    responseProvider = response.provider;
    outputSnapshot = response.rawResponse as Json;
    if (response.error) throw new Error(response.error);

    const parsed = dailyWorkPlanGenerationResultSchema.safeParse(
      response.parsedJson ?? (response.rawText ? JSON.parse(response.rawText) : null)
    );
    if (!parsed.success) throw new Error("daily_work_plan_generation_schema_invalid");
    result = parsed.data;
  } catch (error) {
    if (!isRuleFallbackEnabled()) {
      const message = error instanceof Error ? error.message : "daily_work_plan_generation_failed";
      await updateWorkAgentRun({
        supabase: params.supabase,
        runId: run.id,
        status: "failed",
        errorMessage: message,
        completedAt: nowIso()
      });
      throw error;
    }
    usedFallback = true;
    fallbackReason = error instanceof Error ? error.message : "daily_work_plan_generation_fallback";
    result = buildFallbackDailyPlan({
      sortedItems,
      userName: params.userName,
      planDate
    });
    responseModel = "rule-fallback";
    outputSnapshot = {
      fallback: true,
      reason: fallbackReason,
      sorted_items: sortedItems.map((item) => ({ id: item.id, priority: item.priorityScore }))
    };
  }

  const validIds = new Set(sortedItems.map((item) => item.id));
  const normalizedPrioritized = result.prioritized_items.filter((item) => validIds.has(item.work_item_id));
  if (normalizedPrioritized.length === 0) {
    normalizedPrioritized.push(
      ...sortedItems.slice(0, 8).map((item, index) => ({
        work_item_id: item.id,
        sequence_no: index + 1,
        recommendation_reason: item.rationale,
        planned_time_block: (["early_morning", "morning", "noon", "afternoon", "evening"][index % 5] as Database["public"]["Enums"]["plan_time_block"]),
        suggested_action: item.description || item.title
      }))
    );
  }

  const normalizedResult: DailyWorkPlanGenerationResult = {
    ...result,
    prioritized_items: normalizedPrioritized,
    must_do_item_ids: result.must_do_item_ids.filter((id) => validIds.has(id))
  };

  const saved = await saveDailyPlan({
    supabase: params.supabase,
    orgId: params.orgId,
    userId: params.userId,
    generatedBy: params.triggeredBy,
    planDate,
    status: "active",
    result: normalizedResult,
    sourceSnapshot: {
      candidate_count: candidates.length,
      persisted_count: sortedItems.length,
      used_fallback: usedFallback
    }
  });

  try {
    await autoGenerateTaskPrepCards({
      supabase: params.supabase,
      profile: {
        id: params.userId,
        org_id: params.orgId,
        display_name: params.userName,
        role: "sales",
        is_active: true,
        title: null,
        team_name: null,
        created_at: nowIso(),
        updated_at: nowIso()
      },
      workItemIds: normalizedResult.must_do_item_ids
    });
  } catch {
    // keep plan generation non-blocking
  }

  await updateWorkAgentRun({
    supabase: params.supabase,
    runId: run.id,
    status: "completed",
    provider: responseProvider,
    model: responseModel,
    resultSource: usedFallback ? "fallback" : "provider",
    fallbackReason,
    outputSnapshot,
    parsedResult: {
      focus_theme: normalizedResult.focus_theme,
      must_do_count: normalizedResult.must_do_item_ids.length,
      plan_id: saved.plan.id
    } as Json,
    errorMessage: usedFallback ? fallbackReason : null,
    completedAt: nowIso()
  });

  console.info("[work-agent.daily-plan]", {
    org_id: params.orgId,
    user_id: params.userId,
    run_scope: "user_daily_plan",
    provider: responseProvider,
    model: responseModel,
    status: "completed",
    used_fallback: usedFallback
  });

  return {
    planDate,
    focusTheme: normalizedResult.focus_theme,
    usedFallback,
    runId: run.id
  };
}
