import { getDefaultAutomationRuleSeeds, matchAutomationRuleTargets, type RuleMatchTarget } from "@/lib/automation-ops";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { generateBusinessEventsFromSignals, listBusinessEvents } from "@/services/business-event-service";
import { createInterventionRequest } from "@/services/intervention-request-service";
import { createWorkItem } from "@/services/work-item-service";
import type { AutomationRule, AutomationRuleRun } from "@/types/automation";

type DbClient = ServerSupabaseClient;

interface AutomationRuleRow {
  id: string;
  org_id: string;
  rule_key: string;
  rule_name: string;
  rule_scope: AutomationRule["ruleScope"];
  trigger_type: AutomationRule["triggerType"];
  conditions_json: Record<string, unknown> | null;
  action_json: Record<string, unknown> | null;
  severity: AutomationRule["severity"];
  is_enabled: boolean;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

interface AutomationRuleRunRow {
  id: string;
  org_id: string;
  rule_id: string;
  run_status: AutomationRuleRun["runStatus"];
  matched_count: number;
  created_action_count: number;
  summary: string | null;
  detail_snapshot: Record<string, unknown> | null;
  started_at: string;
  completed_at: string | null;
  created_at: string;
}

function mapRuleRow(row: AutomationRuleRow): AutomationRule {
  return {
    id: row.id,
    orgId: row.org_id,
    ruleKey: row.rule_key,
    ruleName: row.rule_name,
    ruleScope: row.rule_scope,
    triggerType: row.trigger_type,
    conditionsJson: (row.conditions_json ?? {}) as Record<string, unknown>,
    actionJson: (row.action_json ?? {}) as Record<string, unknown>,
    severity: row.severity,
    isEnabled: row.is_enabled,
    createdBy: row.created_by,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

function mapRuleRunRow(row: AutomationRuleRunRow): AutomationRuleRun {
  return {
    id: row.id,
    orgId: row.org_id,
    ruleId: row.rule_id,
    runStatus: row.run_status,
    matchedCount: Number(row.matched_count ?? 0),
    createdActionCount: Number(row.created_action_count ?? 0),
    summary: row.summary,
    detailSnapshot: (row.detail_snapshot ?? {}) as Record<string, unknown>,
    startedAt: row.started_at,
    completedAt: row.completed_at,
    createdAt: row.created_at
  };
}

async function ensureDefaultAutomationRules(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
}): Promise<void> {
  const existingRes = await (params.supabase as any)
    .from("automation_rules")
    .select("rule_key")
    .eq("org_id", params.orgId);
  if (existingRes.error) throw new Error(existingRes.error.message);

  const existingKeys = new Set<string>(((existingRes.data ?? []) as Array<{ rule_key: string }>).map((item) => item.rule_key));
  const seeds = getDefaultAutomationRuleSeeds().filter((seed) => !existingKeys.has(seed.ruleKey));
  if (seeds.length === 0) return;

  const payload = seeds.map((seed) => ({
    org_id: params.orgId,
    rule_key: seed.ruleKey,
    rule_name: seed.ruleName,
    rule_scope: seed.ruleScope,
    trigger_type: seed.triggerType,
    conditions_json: seed.conditionsJson,
    action_json: seed.actionJson,
    severity: seed.severity,
    is_enabled: true,
    created_by: params.actorUserId
  }));

  const insertRes = await (params.supabase as any).from("automation_rules").insert(payload);
  if (insertRes.error) throw new Error(insertRes.error.message);
}

export async function listAutomationRules(params: {
  supabase: DbClient;
  orgId: string;
  includeDisabled?: boolean;
}): Promise<AutomationRule[]> {
  let query = (params.supabase as any)
    .from("automation_rules")
    .select("*")
    .eq("org_id", params.orgId)
    .order("created_at", { ascending: true });
  if (!params.includeDisabled) query = query.eq("is_enabled", true);

  const res = await query;
  if (res.error) throw new Error(res.error.message);
  return ((res.data ?? []) as AutomationRuleRow[]).map(mapRuleRow);
}

export async function upsertAutomationRule(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  ruleId?: string;
  ruleKey: string;
  ruleName: string;
  ruleScope: AutomationRule["ruleScope"];
  triggerType: AutomationRule["triggerType"];
  conditionsJson: Record<string, unknown>;
  actionJson: Record<string, unknown>;
  severity: AutomationRule["severity"];
  isEnabled: boolean;
}): Promise<AutomationRule> {
  if (params.ruleId) {
    const updateRes = await (params.supabase as any)
      .from("automation_rules")
      .update({
        rule_name: params.ruleName,
        rule_scope: params.ruleScope,
        trigger_type: params.triggerType,
        conditions_json: params.conditionsJson,
        action_json: params.actionJson,
        severity: params.severity,
        is_enabled: params.isEnabled
      })
      .eq("org_id", params.orgId)
      .eq("id", params.ruleId)
      .select("*")
      .single();
    if (updateRes.error) throw new Error(updateRes.error.message);
    return mapRuleRow(updateRes.data as AutomationRuleRow);
  }

  const insertRes = await (params.supabase as any)
    .from("automation_rules")
    .insert({
      org_id: params.orgId,
      rule_key: params.ruleKey,
      rule_name: params.ruleName,
      rule_scope: params.ruleScope,
      trigger_type: params.triggerType,
      conditions_json: params.conditionsJson,
      action_json: params.actionJson,
      severity: params.severity,
      is_enabled: params.isEnabled,
      created_by: params.actorUserId
    })
    .select("*")
    .single();
  if (insertRes.error) throw new Error(insertRes.error.message);
  return mapRuleRow(insertRes.data as AutomationRuleRow);
}

export async function setAutomationRuleEnabled(params: {
  supabase: DbClient;
  orgId: string;
  ruleId: string;
  isEnabled: boolean;
}): Promise<AutomationRule> {
  const res = await (params.supabase as any)
    .from("automation_rules")
    .update({ is_enabled: params.isEnabled })
    .eq("org_id", params.orgId)
    .eq("id", params.ruleId)
    .select("*")
    .single();
  if (res.error) throw new Error(res.error.message);
  return mapRuleRow(res.data as AutomationRuleRow);
}

export async function listAutomationRuleRuns(params: {
  supabase: DbClient;
  orgId: string;
  ruleId?: string;
  limit?: number;
}): Promise<AutomationRuleRun[]> {
  let query = (params.supabase as any)
    .from("automation_rule_runs")
    .select("*")
    .eq("org_id", params.orgId)
    .order("created_at", { ascending: false })
    .limit(params.limit ?? 50);
  if (params.ruleId) query = query.eq("rule_id", params.ruleId);

  const res = await query;
  if (res.error) throw new Error(res.error.message);
  return ((res.data ?? []) as AutomationRuleRunRow[]).map(mapRuleRunRow);
}

async function createRuleRun(params: {
  supabase: DbClient;
  orgId: string;
  ruleId: string;
}): Promise<AutomationRuleRunRow> {
  const res = await (params.supabase as any)
    .from("automation_rule_runs")
    .insert({
      org_id: params.orgId,
      rule_id: params.ruleId,
      run_status: "running",
      started_at: new Date().toISOString()
    })
    .select("*")
    .single();
  if (res.error) throw new Error(res.error.message);
  return res.data as AutomationRuleRunRow;
}

async function finishRuleRun(params: {
  supabase: DbClient;
  runId: string;
  runStatus: AutomationRuleRun["runStatus"];
  matchedCount: number;
  createdActionCount: number;
  summary: string;
  detailSnapshot: Record<string, unknown>;
}): Promise<void> {
  const res = await (params.supabase as any)
    .from("automation_rule_runs")
    .update({
      run_status: params.runStatus,
      matched_count: params.matchedCount,
      created_action_count: params.createdActionCount,
      summary: params.summary,
      detail_snapshot: params.detailSnapshot,
      completed_at: new Date().toISOString()
    })
    .eq("id", params.runId);
  if (res.error) throw new Error(res.error.message);
}

function matchRuleTargets(params: {
  rule: AutomationRule;
  eventTargets: RuleMatchTarget[];
}): RuleMatchTarget[] {
  return matchAutomationRuleTargets({
    ruleKey: params.rule.ruleKey,
    eventTargets: params.eventTargets
  });
}

async function maybeCreateActionByRuleMatch(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  rule: AutomationRule;
  target: RuleMatchTarget;
  eventId: string;
}): Promise<number> {
  let actionCount = 0;
  const actionJson = params.rule.actionJson ?? {};
  const ownerId = (params.target.ownerId ?? params.actorUserId) as string;

  if (actionJson.createWorkItem === true) {
    const existing = await (params.supabase as any)
      .from("work_items")
      .select("id")
      .eq("org_id", params.orgId)
      .eq("source_ref_type", "business_event")
      .eq("source_ref_id", params.eventId)
      .in("status", ["todo", "in_progress", "snoozed"])
      .limit(1)
      .maybeSingle();
    if (existing.error) throw new Error(existing.error.message);

    if (!existing.data) {
      await createWorkItem({
        supabase: params.supabase,
        orgId: params.orgId,
        ownerId,
        customerId: params.target.customerId ?? null,
        opportunityId: params.target.entityType === "opportunity" ? params.target.entityId : null,
        sourceType: actionJson.createManagerCheckin === true ? "manager_assigned" : "ai_suggested",
        workType: (actionJson.workType as any) ?? "review_customer",
        title: `[Ops] ${params.rule.ruleName}`,
        description: params.target.summary,
        rationale: params.target.recommendedAction,
        priorityScore: params.rule.severity === "critical" ? 92 : params.rule.severity === "warning" ? 76 : 58,
        priorityBand: params.rule.severity === "critical" ? "critical" : params.rule.severity === "warning" ? "high" : "medium",
        dueAt: new Date(Date.now() + (params.rule.severity === "critical" ? 24 : 48) * 60 * 60 * 1000).toISOString(),
        sourceRefType: "business_event",
        sourceRefId: params.eventId,
        aiGenerated: true,
        createdBy: params.actorUserId
      });
      actionCount += 1;
    }
  }

  if (actionJson.createInterventionRequest === true && params.target.dealRoomId) {
    await createInterventionRequest({
      supabase: params.supabase,
      orgId: params.orgId,
      dealRoomId: params.target.dealRoomId,
      requestedBy: params.actorUserId,
      targetUserId: ownerId,
      requestType: "executive_escalation",
      priorityBand: params.rule.severity === "critical" ? "critical" : "high",
      requestSummary: params.target.summary,
      contextSnapshot: {
        source: "automation_rule",
        rule_key: params.rule.ruleKey,
        business_event_id: params.eventId,
        recommended_action: params.target.recommendedAction
      },
      dueAt: new Date(Date.now() + 2 * 24 * 60 * 60 * 1000).toISOString()
    });
    actionCount += 1;
  }

  if (actionJson.createExecutiveBrief === true) {
    const insertRes = await (params.supabase as any).from("executive_briefs").insert({
      org_id: params.orgId,
      brief_type: "executive_daily",
      target_user_id: null,
      status: "completed",
      headline: `Automation Watch: ${params.rule.ruleName}`,
      summary: params.target.summary,
      brief_payload: {
        source: "automation_rule",
        rule_key: params.rule.ruleKey,
        business_event_id: params.eventId,
        suggested_action: params.target.recommendedAction
      },
      source_snapshot: {
        entity_type: params.target.entityType,
        entity_id: params.target.entityId
      }
    });
    if (insertRes.error) throw new Error(insertRes.error.message);
    actionCount += 1;
  }

  return actionCount;
}

export async function runAutomationRules(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  ruleIds?: string[];
  ownerId?: string;
}): Promise<{
  totalRules: number;
  totalMatches: number;
  totalActions: number;
  failedRules: string[];
  runs: AutomationRuleRun[];
}> {
  await ensureDefaultAutomationRules({
    supabase: params.supabase,
    orgId: params.orgId,
    actorUserId: params.actorUserId
  });

  let rules = await listAutomationRules({
    supabase: params.supabase,
    orgId: params.orgId,
    includeDisabled: false
  });

  if (params.ruleIds?.length) {
    const allow = new Set(params.ruleIds);
    rules = rules.filter((item) => allow.has(item.id));
  }

  const signalResult = await generateBusinessEventsFromSignals({
    supabase: params.supabase,
    orgId: params.orgId,
    ownerId: params.ownerId,
    lookbackDays: 7
  });

  const signalTargets: RuleMatchTarget[] = signalResult.events.map((item) => {
    const payload = item.eventPayload;
    return {
      entityType: item.entityType,
      entityId: item.entityId,
      ownerId: typeof payload.owner_id === "string" ? payload.owner_id : null,
      customerId: typeof payload.customer_id === "string" ? payload.customer_id : null,
      dealRoomId: typeof payload.deal_room_id === "string" ? payload.deal_room_id : null,
      severity: item.severity,
      eventType: item.eventType,
      summary: item.eventSummary,
      evidence: Array.isArray(payload.evidence) ? (payload.evidence as string[]) : [],
      recommendedAction: typeof payload.recommended_action === "string" ? payload.recommended_action : "Assign owner and next step."
    };
  });

  const runs: AutomationRuleRun[] = [];
  const failedRules: string[] = [];
  let totalMatches = 0;
  let totalActions = 0;

  for (const rule of rules) {
    const run = await createRuleRun({
      supabase: params.supabase,
      orgId: params.orgId,
      ruleId: rule.id
    });

    try {
      const matched = matchRuleTargets({
        rule,
        eventTargets: signalTargets
      });

      let createdActionCount = 0;
      for (const target of matched) {
        const event = signalResult.events.find(
          (item) => item.entityType === target.entityType && item.entityId === target.entityId && item.eventType === target.eventType
        );
        if (!event) continue;

        createdActionCount += await maybeCreateActionByRuleMatch({
          supabase: params.supabase,
          orgId: params.orgId,
          actorUserId: params.actorUserId,
          rule,
          target,
          eventId: event.id
        });
      }

      totalMatches += matched.length;
      totalActions += createdActionCount;

      await finishRuleRun({
        supabase: params.supabase,
        runId: run.id,
        runStatus: "completed",
        matchedCount: matched.length,
        createdActionCount,
        summary: `${rule.ruleName}: ${matched.length} matched, ${createdActionCount} actions created.`,
        detailSnapshot: {
          rule_key: rule.ruleKey,
          matched_entities: matched.map((item) => ({
            entity_type: item.entityType,
            entity_id: item.entityId,
            event_type: item.eventType,
            severity: item.severity
          }))
        }
      });

      const runData = await listAutomationRuleRuns({
        supabase: params.supabase,
        orgId: params.orgId,
        ruleId: rule.id,
        limit: 1
      });
      if (runData[0]) runs.push(runData[0]);
    } catch (error) {
      failedRules.push(rule.ruleKey);
      await finishRuleRun({
        supabase: params.supabase,
        runId: run.id,
        runStatus: "failed",
        matchedCount: 0,
        createdActionCount: 0,
        summary: error instanceof Error ? error.message : "automation_rule_failed",
        detailSnapshot: {
          rule_key: rule.ruleKey,
          error: error instanceof Error ? error.message : "unknown"
        }
      });

      const runData = await listAutomationRuleRuns({
        supabase: params.supabase,
        orgId: params.orgId,
        ruleId: rule.id,
        limit: 1
      });
      if (runData[0]) runs.push(runData[0]);
    }
  }

  return {
    totalRules: rules.length,
    totalMatches,
    totalActions,
    failedRules,
    runs
  };
}

export async function getAutomationCenterSnapshot(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId?: string;
}): Promise<{
  rules: AutomationRule[];
  recentRuns: AutomationRuleRun[];
  openEvents: number;
}> {
  if (params.actorUserId) {
    await ensureDefaultAutomationRules({
      supabase: params.supabase,
      orgId: params.orgId,
      actorUserId: params.actorUserId
    });
  }

  const [rules, recentRuns, openEvents] = await Promise.all([
    listAutomationRules({
      supabase: params.supabase,
      orgId: params.orgId,
      includeDisabled: true
    }),
    listAutomationRuleRuns({
      supabase: params.supabase,
      orgId: params.orgId,
      limit: 20
    }),
    listBusinessEvents({
      supabase: params.supabase,
      orgId: params.orgId,
      statuses: ["open"],
      limit: 200
    })
  ]);

  return {
    rules,
    recentRuns,
    openEvents: openEvents.length
  };
}

