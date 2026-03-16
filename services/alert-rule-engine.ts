import { evaluateAlertRules } from "@/lib/alert-rules";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { resolveObsoleteCustomerAlerts, upsertLeakAlert } from "@/services/alert-workflow-service";
import { runLeakRiskInference } from "@/services/ai-analysis-service";
import { mapAlertRow, mapCustomerRow, mapFollowupRow, mapOpportunityRow } from "@/services/mappers";
import type { AlertRuleHit } from "@/types/alert";
import type { Customer } from "@/types/customer";
import type { Database } from "@/types/database";
import type { FollowupRecord } from "@/types/followup";
import type { Opportunity } from "@/types/opportunity";

type DbClient = ServerSupabaseClient;

type CustomerRow = Database["public"]["Tables"]["customers"]["Row"];
type FollowupRow = Database["public"]["Tables"]["followups"]["Row"];
type OpportunityRow = Database["public"]["Tables"]["opportunities"]["Row"];
type AlertRow = Database["public"]["Tables"]["alerts"]["Row"];

interface ScanCustomerContext {
  customer: Customer;
  followups: FollowupRecord[];
  opportunities: Opportunity[];
  unresolvedAlerts: ReturnType<typeof mapAlertRow>[];
}

export interface AlertScanResult {
  runId: string;
  scannedCount: number;
  createdAlertCount: number;
  dedupedAlertCount: number;
  resolvedAlertCount: number;
}

function isActiveCustomer(customer: Customer): boolean {
  return customer.stage !== "won" && customer.stage !== "lost";
}

function groupByCustomer<T extends { customerId: string }>(items: T[]): Record<string, T[]> {
  return items.reduce<Record<string, T[]>>((acc, item) => {
    if (!acc[item.customerId]) acc[item.customerId] = [];
    acc[item.customerId].push(item);
    return acc;
  }, {});
}

function groupAlertsByCustomer(items: ReturnType<typeof mapAlertRow>[]): Record<string, ReturnType<typeof mapAlertRow>[]> {
  return items.reduce<Record<string, ReturnType<typeof mapAlertRow>[]>>((acc, item) => {
    if (!item.customerId) return acc;
    if (!acc[item.customerId]) acc[item.customerId] = [];
    acc[item.customerId].push(item);
    return acc;
  }, {});
}

export async function runOrganizationAlertScan(params: {
  supabase: DbClient;
  orgId: string;
  triggeredByUserId: string;
  withAiInference?: boolean;
}): Promise<AlertScanResult> {
  const startedAt = new Date().toISOString();

  const { data: runRow, error: runCreateError } = await params.supabase
    .from("alert_rule_runs")
    .insert({
      org_id: params.orgId,
      rule_name: "default_leak_scan",
      status: "running",
      started_at: startedAt
    })
    .select("id")
    .single();

  if (runCreateError || !runRow) {
    throw new Error(runCreateError?.message ?? "Failed to create alert_rule_runs record");
  }

  const runId = runRow.id;

  try {
    const [customerResult, followupResult, opportunityResult, alertResult] = await Promise.all([
      params.supabase
        .from("customers")
        .select("*, owner:profiles!customers_owner_id_fkey(id, display_name)")
        .eq("org_id", params.orgId)
        .order("updated_at", { ascending: false }),
      params.supabase
        .from("followups")
        .select("*, owner:profiles!followups_owner_id_fkey(id, display_name)")
        .eq("org_id", params.orgId)
        .eq("draft_status", "confirmed")
        .order("created_at", { ascending: false }),
      params.supabase
        .from("opportunities")
        .select("*, owner:profiles!opportunities_owner_id_fkey(id, display_name), customer:customers!opportunities_customer_id_fkey(id, company_name)")
        .eq("org_id", params.orgId)
        .order("updated_at", { ascending: false }),
      params.supabase
        .from("alerts")
        .select("*, owner:profiles!alerts_owner_id_fkey(id, display_name), customer:customers!alerts_customer_id_fkey(id, company_name)")
        .eq("org_id", params.orgId)
        .neq("status", "resolved")
    ]);

    if (customerResult.error) throw new Error(customerResult.error.message);
    if (followupResult.error) throw new Error(followupResult.error.message);
    if (opportunityResult.error) throw new Error(opportunityResult.error.message);
    if (alertResult.error) throw new Error(alertResult.error.message);

    const customers = (customerResult.data ?? []).map((row: any) =>
      mapCustomerRow(row as CustomerRow & { owner?: { id: string; display_name: string } | null })
    );
    const followups = (followupResult.data ?? []).map((row: any) =>
      mapFollowupRow(row as FollowupRow & { owner?: { id: string; display_name: string } | null })
    );
    const opportunities = (opportunityResult.data ?? []).map((row: any) =>
      mapOpportunityRow(
        row as OpportunityRow & {
          owner?: { id: string; display_name: string } | null;
          customer?: { id: string; company_name: string } | null;
        }
      )
    );
    const unresolvedAlerts = (alertResult.data ?? []).map((row: any) =>
      mapAlertRow(
        row as AlertRow & {
          owner?: { id: string; display_name: string } | null;
          customer?: { id: string; company_name: string } | null;
        }
      )
    );

    const followupsByCustomer = groupByCustomer<FollowupRecord>(followups);
    const opportunitiesByCustomer = groupByCustomer<Opportunity>(opportunities);
    const alertsByCustomer = groupAlertsByCustomer(unresolvedAlerts);

    let scannedCount = 0;
    let createdAlertCount = 0;
    let dedupedAlertCount = 0;
    let resolvedAlertCount = 0;

    for (const customer of customers) {
      if (!isActiveCustomer(customer)) continue;
      scannedCount += 1;

      const context: ScanCustomerContext = {
        customer,
        followups: followupsByCustomer[customer.id] ?? [],
        opportunities: opportunitiesByCustomer[customer.id] ?? [],
        unresolvedAlerts: alertsByCustomer[customer.id] ?? []
      };

      const ruleHits = evaluateAlertRules({
        now: new Date(),
        customer: context.customer,
        followups: context.followups,
        opportunities: context.opportunities
      });

      const activeRuleTypes: AlertRuleHit["ruleType"][] = [];

      for (const hit of ruleHits) {
        const upserted = await upsertLeakAlert({
          supabase: params.supabase,
          input: {
            orgId: params.orgId,
            customerId: customer.id,
            ownerId: customer.ownerId,
            ruleType: hit.ruleType,
            source: "rule",
            level: hit.level,
            title: hit.title,
            description: hit.description,
            evidence: hit.evidence,
            suggestedOwnerAction: hit.suggestedOwnerAction,
            dueAt: hit.dueAt
          }
        });

        activeRuleTypes.push(hit.ruleType);
        if (upserted.action === "created") createdAlertCount += 1;
        else dedupedAlertCount += 1;
      }

      if (params.withAiInference !== false && (ruleHits.length > 0 || customer.riskLevel === "high")) {
        const aiInference = await runLeakRiskInference({
          supabase: params.supabase,
          orgId: params.orgId,
          customerId: customer.id,
          triggeredByUserId: params.triggeredByUserId,
          triggerSource: "manager_review",
          contextOverride: {
            customer: context.customer,
            followups: context.followups,
            opportunities: context.opportunities,
            unresolvedAlerts: context.unresolvedAlerts
          },
          ruleHitsOverride: ruleHits
        });

        if (aiInference.result.should_create_alert) {
          activeRuleTypes.push(aiInference.result.primary_rule_type);
          if (aiInference.alertAction === "created") createdAlertCount += 1;
          else if (aiInference.alertAction) dedupedAlertCount += 1;
        }
      }

      const resolved = await resolveObsoleteCustomerAlerts({
        supabase: params.supabase,
        orgId: params.orgId,
        customerId: customer.id,
        activeRuleTypes
      });
      resolvedAlertCount += resolved;
    }

    await params.supabase
      .from("alert_rule_runs")
      .update({
        status: "completed",
        scanned_count: scannedCount,
        created_alert_count: createdAlertCount,
        deduped_alert_count: dedupedAlertCount,
        resolved_alert_count: resolvedAlertCount,
        completed_at: new Date().toISOString()
      })
      .eq("id", runId);

    return {
      runId,
      scannedCount,
      createdAlertCount,
      dedupedAlertCount,
      resolvedAlertCount
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "Alert scan failed";
    await params.supabase
      .from("alert_rule_runs")
      .update({
        status: "failed",
        error_message: message,
        completed_at: new Date().toISOString()
      })
      .eq("id", runId);
    throw error;
  }
}
