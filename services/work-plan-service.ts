import type { DailyWorkPlanGenerationResult } from "@/types/ai";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { mapDailyWorkPlanItemRow, mapDailyWorkPlanRow, mapWorkAgentRunRow, mapWorkItemRow } from "@/services/mappers";
import type { Database, Json } from "@/types/database";
import type { DailyWorkPlan, DailyWorkPlanItem, TodayPlanView, WorkAgentRun, WorkItem } from "@/types/work";
type DbClient = ServerSupabaseClient;

function getTodayDate(): string {
  return new Date().toISOString().slice(0, 10);
}

export async function saveDailyPlan(params: {
  supabase: DbClient;
  orgId: string;
  userId: string;
  generatedBy: string;
  planDate?: string;
  status?: Database["public"]["Enums"]["daily_plan_status"];
  result: DailyWorkPlanGenerationResult;
  sourceSnapshot: Json;
}): Promise<{ plan: DailyWorkPlan; planItems: DailyWorkPlanItem[] }> {
  const planDate = params.planDate ?? getTodayDate();
  const status = params.status ?? "active";

  const upsertPayload: Database["public"]["Tables"]["daily_work_plans"]["Insert"] = {
    org_id: params.orgId,
    user_id: params.userId,
    plan_date: planDate,
    status,
    summary: params.result.plan_summary,
    total_items: params.result.prioritized_items.length,
    critical_items: params.result.prioritized_items.filter((item) => params.result.must_do_item_ids.includes(item.work_item_id)).length,
    focus_theme: params.result.focus_theme,
    source_snapshot: params.sourceSnapshot as Json,
    generated_by: params.generatedBy
  };

  const { data: upserted, error: upsertError } = await params.supabase
    .from("daily_work_plans")
    .upsert(upsertPayload, { onConflict: "org_id,user_id,plan_date" })
    .select("*")
    .single();

  if (upsertError || !upserted) throw new Error(upsertError?.message ?? "Failed to save daily plan");
  const plan = mapDailyWorkPlanRow(upserted as never);

  const { error: clearError } = await params.supabase.from("daily_work_plan_items").delete().eq("plan_id", plan.id);
  if (clearError) throw new Error(clearError.message);

  const itemPayloads: Database["public"]["Tables"]["daily_work_plan_items"]["Insert"][] = params.result.prioritized_items.map((item) => ({
    org_id: params.orgId,
    plan_id: plan.id,
    work_item_id: item.work_item_id,
    sequence_no: item.sequence_no,
    planned_time_block: item.planned_time_block,
    recommendation_reason: item.recommendation_reason,
    must_do: params.result.must_do_item_ids.includes(item.work_item_id)
  }));

  let planItems: DailyWorkPlanItem[] = [];
  if (itemPayloads.length > 0) {
    const { data: insertedItems, error: insertItemsError } = await params.supabase
      .from("daily_work_plan_items")
      .insert(itemPayloads)
      .select("*");
    if (insertItemsError) throw new Error(insertItemsError.message);
    planItems = (insertedItems ?? []).map((item: Database["public"]["Tables"]["daily_work_plan_items"]["Row"]) => mapDailyWorkPlanItemRow(item as never));
  }

  return { plan, planItems };
}

export async function getTodayPlanView(params: {
  supabase: DbClient;
  orgId: string;
  userId: string;
  date?: string;
}): Promise<TodayPlanView | null> {
  const date = params.date ?? getTodayDate();
  const { data: planRaw, error: planError } = await params.supabase
    .from("daily_work_plans")
    .select("*")
    .eq("org_id", params.orgId)
    .eq("user_id", params.userId)
    .eq("plan_date", date)
    .maybeSingle();

  if (planError) throw new Error(planError.message);
  if (!planRaw) return null;
  const plan = mapDailyWorkPlanRow(planRaw as never);

  const { data: itemRows, error: itemError } = await params.supabase
    .from("daily_work_plan_items")
    .select("*")
    .eq("org_id", params.orgId)
    .eq("plan_id", plan.id)
    .order("sequence_no", { ascending: true });
  if (itemError) throw new Error(itemError.message);

  const planItems = (itemRows ?? []).map((item: Database["public"]["Tables"]["daily_work_plan_items"]["Row"]) => mapDailyWorkPlanItemRow(item as never));
  const ids = planItems.map((item: DailyWorkPlanItem) => item.workItemId);

  let workItems: WorkItem[] = [];
  if (ids.length > 0) {
    const { data: workRows, error: workError } = await params.supabase
      .from("work_items")
      .select("*, owner:profiles!work_items_owner_id_fkey(id, display_name), customer:customers!work_items_customer_id_fkey(id, company_name)")
      .in("id", ids);
    if (workError) throw new Error(workError.message);
    workItems = (workRows ?? []).map((item: Database["public"]["Tables"]["work_items"]["Row"]) => mapWorkItemRow(item as never));
  }

  const { data: runRaw, error: runError } = await params.supabase
    .from("work_agent_runs")
    .select("*")
    .eq("org_id", params.orgId)
    .eq("run_scope", "user_daily_plan")
    .eq("user_id", params.userId)
    .order("created_at", { ascending: false })
    .limit(1)
    .maybeSingle();
  if (runError) throw new Error(runError.message);

  const latestRun = runRaw ? mapWorkAgentRunRow(runRaw as never) : null;

  return {
    plan,
    planItems,
    workItems,
    latestRun,
    usedFallback: latestRun?.resultSource === "fallback"
  };
}

export async function listRecentWorkAgentRuns(params: {
  supabase: DbClient;
  orgId: string;
  userId?: string;
  runScope?: Database["public"]["Enums"]["work_agent_run_scope"];
  limit?: number;
}): Promise<WorkAgentRun[]> {
  let query = params.supabase
    .from("work_agent_runs")
    .select("*")
    .eq("org_id", params.orgId)
    .order("created_at", { ascending: false })
    .limit(params.limit ?? 20);

  if (params.userId) query = query.eq("user_id", params.userId);
  if (params.runScope) query = query.eq("run_scope", params.runScope);

  const { data, error } = await query;
  if (error) throw new Error(error.message);
  return (data ?? []).map((item: Database["public"]["Tables"]["work_agent_runs"]["Row"]) => mapWorkAgentRunRow(item as never));
}
