import { inferRenewalStatus } from "@/lib/renewal-watch";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { listLatestCustomerHealthSnapshots } from "@/services/customer-health-service";
import type { RenewalWatchItem } from "@/types/automation";

type DbClient = ServerSupabaseClient;

interface RenewalWatchRow {
  id: string;
  org_id: string;
  customer_id: string;
  owner_id: string | null;
  renewal_status: RenewalWatchItem["renewalStatus"];
  renewal_due_at: string | null;
  product_scope: string | null;
  health_snapshot_id: string | null;
  recommendation_summary: string | null;
  created_at: string;
  updated_at: string;
}

function mapRow(row: RenewalWatchRow, customerName?: string, ownerName?: string): RenewalWatchItem {
  return {
    id: row.id,
    orgId: row.org_id,
    customerId: row.customer_id,
    customerName,
    ownerId: row.owner_id,
    ownerName,
    renewalStatus: row.renewal_status,
    renewalDueAt: row.renewal_due_at,
    productScope: row.product_scope,
    healthSnapshotId: row.health_snapshot_id,
    recommendationSummary: row.recommendation_summary,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export async function refreshRenewalWatchItems(params: {
  supabase: DbClient;
  orgId: string;
  ownerId?: string;
}): Promise<{
  updated: number;
  items: RenewalWatchItem[];
}> {
  const snapshots = await listLatestCustomerHealthSnapshots({
    supabase: params.supabase,
    orgId: params.orgId,
    ownerId: params.ownerId,
    limit: 500
  });

  const items: RenewalWatchItem[] = [];
  for (const snapshot of snapshots) {
    const customerRes = await (params.supabase as any)
      .from("customers")
      .select("id,owner_id,current_stage,company_name")
      .eq("org_id", params.orgId)
      .eq("id", snapshot.customerId)
      .maybeSingle();
    if (customerRes.error) throw new Error(customerRes.error.message);
    const customer = customerRes.data as { id: string; owner_id: string; current_stage: string; company_name: string } | null;
    if (!customer) continue;

    const renewalDueAt =
      customer.current_stage === "won"
        ? new Date(Date.now() + 45 * 24 * 60 * 60 * 1000).toISOString()
        : null;

    const renewalStatus = inferRenewalStatus({
      healthBand: snapshot.healthBand,
      overallHealthScore: snapshot.overallHealthScore,
      renewalDueAt
    });

    const recommendationSummary =
      renewalStatus === "at_risk"
        ? "Run retention check-in and confirm next milestone immediately."
        : renewalStatus === "expansion_candidate"
          ? "Prepare expansion proposal based on positive adoption signals."
          : renewalStatus === "due_soon"
            ? "Prepare renewal plan and owner timeline before due date."
            : "Keep regular success cadence and monitor health band changes.";

    const upsertRes = await (params.supabase as any)
      .from("renewal_watch_items")
      .upsert(
        {
          org_id: params.orgId,
          customer_id: snapshot.customerId,
          owner_id: customer.owner_id,
          renewal_status: renewalStatus,
          renewal_due_at: renewalDueAt,
          product_scope: "MOY workspace",
          health_snapshot_id: snapshot.id,
          recommendation_summary: recommendationSummary
        },
        { onConflict: "org_id,customer_id" }
      )
      .select("*")
      .single();
    if (upsertRes.error) throw new Error(upsertRes.error.message);

    items.push(mapRow(upsertRes.data as RenewalWatchRow, customer.company_name));
  }

  return {
    updated: items.length,
    items
  };
}

export async function listRenewalWatchItems(params: {
  supabase: DbClient;
  orgId: string;
  ownerId?: string;
  statuses?: RenewalWatchItem["renewalStatus"][];
  limit?: number;
}): Promise<RenewalWatchItem[]> {
  let query = (params.supabase as any)
    .from("renewal_watch_items")
    .select("*")
    .eq("org_id", params.orgId)
    .order("updated_at", { ascending: false })
    .limit(params.limit ?? 200);

  if (params.ownerId) query = query.eq("owner_id", params.ownerId);
  if (params.statuses?.length) query = query.in("renewal_status", params.statuses);

  const res = await query;
  if (res.error) throw new Error(res.error.message);

  const rows = (res.data ?? []) as RenewalWatchRow[];
  const customerIds = Array.from(new Set(rows.map((item) => item.customer_id)));
  const customersRes = await (params.supabase as any)
    .from("customers")
    .select("id,company_name")
    .eq("org_id", params.orgId)
    .in("id", customerIds.length > 0 ? customerIds : ["00000000-0000-0000-0000-000000000000"]);
  if (customersRes.error) throw new Error(customersRes.error.message);

  const customerMap = new Map<string, string>();
  for (const row of (customersRes.data ?? []) as Array<{ id: string; company_name: string }>) {
    customerMap.set(row.id, row.company_name);
  }

  return rows.map((row) => mapRow(row, customerMap.get(row.customer_id)));
}


