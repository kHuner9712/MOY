import { getAlertDedupeDecision, severityRank } from "@/lib/alert-dedupe";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import type { AlertLevel, AlertRuleType, AlertSource } from "@/types/alert";

type DbClient = ServerSupabaseClient;

export const MANAGED_ALERT_RULE_TYPES: AlertRuleType[] = [
  "no_followup_timeout",
  "quoted_but_stalled",
  "positive_reply_but_no_progress",
  "no_decision_maker",
  "high_probability_stalled",
  "ai_detected",
  // backward-compatible legacy values
  "no_followup_overdue",
  "active_response_no_quote",
  "missing_decision_maker"
];

interface UpsertAlertInput {
  orgId: string;
  customerId?: string | null;
  opportunityId?: string | null;
  ownerId?: string | null;
  ruleType: AlertRuleType;
  source: AlertSource;
  level: AlertLevel;
  title: string;
  description: string;
  evidence: string[];
  suggestedOwnerAction: string[];
  dueAt: string | null;
  aiRunId?: string | null;
}

export interface UpsertAlertResult {
  action: "created" | "updated" | "deduped";
  alertId: string;
}

interface AlertRuleRow {
  id: string;
  rule_type: string | null;
}

export async function upsertLeakAlert(params: { supabase: DbClient; input: UpsertAlertInput }): Promise<UpsertAlertResult> {
  const { supabase, input } = params;

  let builder = supabase
    .from("alerts")
    .select("*")
    .eq("org_id", input.orgId)
    .eq("rule_type", input.ruleType)
    .neq("status", "resolved")
    .order("created_at", { ascending: false })
    .limit(1);

  builder = input.customerId ? builder.eq("customer_id", input.customerId) : builder.is("customer_id", null);
  builder = input.opportunityId ? builder.eq("opportunity_id", input.opportunityId) : builder.is("opportunity_id", null);

  const { data: existing, error: queryError } = await builder.maybeSingle();
  if (queryError) {
    throw new Error(queryError.message);
  }

  if (!existing) {
    const { data, error } = await supabase
      .from("alerts")
      .insert({
        org_id: input.orgId,
        customer_id: input.customerId ?? null,
        opportunity_id: input.opportunityId ?? null,
        owner_id: input.ownerId ?? null,
        rule_type: input.ruleType,
        source: input.source,
        severity: input.level,
        status: "open",
        title: input.title,
        description: input.description,
        evidence: input.evidence,
        suggested_owner_action: input.suggestedOwnerAction,
        ai_run_id: input.aiRunId ?? null,
        due_at: input.dueAt,
        last_triggered_at: new Date().toISOString()
      })
      .select("id")
      .single();

    if (error || !data) {
      throw new Error(error?.message ?? "Failed to create alert");
    }

    return {
      action: "created",
      alertId: data.id
    };
  }

  const decision = getAlertDedupeDecision({
    existing,
    incoming: {
      source: input.source,
      level: input.level,
      title: input.title,
      description: input.description,
      evidence: input.evidence,
      suggestedOwnerAction: input.suggestedOwnerAction
    }
  });

  if (!decision.shouldUpdate) {
    const { error: touchError } = await supabase
      .from("alerts")
      .update({
        last_triggered_at: new Date().toISOString(),
        due_at: input.dueAt ?? existing.due_at
      })
      .eq("id", existing.id);

    if (touchError) throw new Error(touchError.message);

    return {
      action: "deduped",
      alertId: existing.id
    };
  }

  const { error: updateError } = await supabase
    .from("alerts")
    .update({
      owner_id: input.ownerId ?? existing.owner_id,
      source: decision.nextSource,
      severity: decision.shouldUpgradeSeverity ? input.level : existing.severity,
      status: decision.shouldUpgradeSeverity ? "open" : existing.status,
      title: input.title,
      description: input.description,
      evidence: input.evidence,
      suggested_owner_action: input.suggestedOwnerAction,
      ai_run_id: input.aiRunId ?? existing.ai_run_id,
      due_at: input.dueAt ?? existing.due_at,
      last_triggered_at: new Date().toISOString()
    })
    .eq("id", existing.id);

  if (updateError) throw new Error(updateError.message);

  return {
    action: "updated",
    alertId: existing.id
  };
}

export async function resolveAlert(params: { supabase: DbClient; alertId: string }): Promise<void> {
  const { error } = await params.supabase
    .from("alerts")
    .update({
      status: "resolved",
      resolved_at: new Date().toISOString()
    })
    .eq("id", params.alertId);

  if (error) {
    throw new Error(error.message);
  }
}

export async function resolveObsoleteCustomerAlerts(params: {
  supabase: DbClient;
  orgId: string;
  customerId: string;
  activeRuleTypes: AlertRuleType[];
}): Promise<number> {
  const { data, error } = await params.supabase
    .from("alerts")
    .select("id, rule_type")
    .eq("org_id", params.orgId)
    .eq("customer_id", params.customerId)
    .neq("status", "resolved")
    .in("rule_type", MANAGED_ALERT_RULE_TYPES);

  if (error) {
    throw new Error(error.message);
  }

  const alertRows: AlertRuleRow[] = (data ?? []) as AlertRuleRow[];

  const toResolve = alertRows
    .filter((item) => !params.activeRuleTypes.includes(item.rule_type as AlertRuleType))
    .map((item) => item.id);

  if (toResolve.length === 0) return 0;

  const { error: updateError } = await params.supabase
    .from("alerts")
    .update({
      status: "resolved",
      resolved_at: new Date().toISOString()
    })
    .in("id", toResolve);

  if (updateError) {
    throw new Error(updateError.message);
  }

  return toResolve.length;
}
