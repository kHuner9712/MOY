import { computeOutcomeOverview } from "@/lib/closed-loop";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { mapActionOutcomeRow, mapOutcomeReviewRow, mapPlaybookRow, mapSuggestionAdoptionRow } from "@/services/mappers";
import type { Database } from "@/types/database";
import type { ActionOutcome, OutcomeReview, SuggestionAdoption } from "@/types/outcome";
import type { Playbook } from "@/types/playbook";

type DbClient = ServerSupabaseClient;

function getRange(periodType: "weekly" | "monthly") {
  const end = new Date();
  const start = new Date();
  if (periodType === "monthly") {
    start.setDate(end.getDate() - 29);
  } else {
    start.setDate(end.getDate() - 6);
  }
  return {
    periodStart: start.toISOString().slice(0, 10),
    periodEnd: end.toISOString().slice(0, 10)
  };
}

function topText(values: string[], count = 6): string[] {
  const map = new Map<string, number>();
  for (const value of values) {
    const key = value.trim();
    if (!key) continue;
    map.set(key, (map.get(key) ?? 0) + 1);
  }
  return Array.from(map.entries())
    .sort((a, b) => b[1] - a[1])
    .slice(0, count)
    .map(([key, c]) => `${key} (${c})`);
}

export async function getManagerOutcomeInsight(params: {
  supabase: DbClient;
  orgId: string;
  periodType?: "weekly" | "monthly";
}): Promise<{
  periodType: "weekly" | "monthly";
  periodStart: string;
  periodEnd: string;
  summary: {
    totalOutcomes: number;
    positiveProgressRate: number;
    adoptionRate: number;
    adoptionPositiveRate: number;
  };
  bySales: Array<{
    userId: string;
    userName: string;
    totalOutcomes: number;
    positiveProgressRate: number;
    adoptionRate: number;
    adoptionPositiveRate: number;
  }>;
  effectivePatterns: string[];
  ineffectivePatterns: string[];
  customerStallReasons: string[];
  repeatedFailurePatterns: string[];
  recentReviews: OutcomeReview[];
  recentPlaybooks: Playbook[];
}> {
  const periodType = params.periodType ?? "weekly";
  const { periodStart, periodEnd } = getRange(periodType);

  const [profilesRes, outcomesRes, adoptionsRes, reviewsRes, playbooksRes] = await Promise.all([
    params.supabase.from("profiles").select("id, display_name").eq("org_id", params.orgId).eq("is_active", true),
    params.supabase
      .from("action_outcomes")
      .select("*")
      .eq("org_id", params.orgId)
      .gte("created_at", `${periodStart}T00:00:00.000Z`)
      .lte("created_at", `${periodEnd}T23:59:59.999Z`)
      .order("created_at", { ascending: false })
      .limit(600),
    params.supabase
      .from("suggestion_adoptions")
      .select("*")
      .eq("org_id", params.orgId)
      .gte("created_at", `${periodStart}T00:00:00.000Z`)
      .lte("created_at", `${periodEnd}T23:59:59.999Z`)
      .order("created_at", { ascending: false })
      .limit(600),
    params.supabase
      .from("outcome_reviews")
      .select("*")
      .eq("org_id", params.orgId)
      .order("created_at", { ascending: false })
      .limit(12),
    params.supabase
      .from("playbooks")
      .select("*")
      .eq("org_id", params.orgId)
      .eq("status", "active")
      .order("updated_at", { ascending: false })
      .limit(20)
  ]);

  if (profilesRes.error) throw new Error(profilesRes.error.message);
  if (outcomesRes.error) throw new Error(outcomesRes.error.message);
  if (adoptionsRes.error) throw new Error(adoptionsRes.error.message);
  if (reviewsRes.error) throw new Error(reviewsRes.error.message);
  if (playbooksRes.error) throw new Error(playbooksRes.error.message);

  const profiles = (profilesRes.data ?? []) as Array<{ id: string; display_name: string }>;
  const nameMap = new Map(profiles.map((item) => [item.id, item.display_name]));

  const outcomes: ActionOutcome[] = (outcomesRes.data ?? []).map((row: Database["public"]["Tables"]["action_outcomes"]["Row"]) =>
    mapActionOutcomeRow(row)
  );
  const adoptions: SuggestionAdoption[] = (adoptionsRes.data ?? []).map((row: Database["public"]["Tables"]["suggestion_adoptions"]["Row"]) =>
    mapSuggestionAdoptionRow(row)
  );
  const recentReviews: OutcomeReview[] = (reviewsRes.data ?? []).map((row: Database["public"]["Tables"]["outcome_reviews"]["Row"]) =>
    mapOutcomeReviewRow(row)
  );
  const recentPlaybooks: Playbook[] = (playbooksRes.data ?? []).map((row: Database["public"]["Tables"]["playbooks"]["Row"]) => mapPlaybookRow(row));

  const overview = computeOutcomeOverview({
    outcomes: outcomes.map((item) => ({
      id: item.id,
      resultStatus: item.resultStatus,
      outcomeType: item.outcomeType,
      newRisks: item.newRisks,
      newObjections: item.newObjections,
      stageChanged: item.stageChanged,
      usedPrepCard: item.usedPrepCard,
      usedDraft: item.usedDraft
    })),
    adoptions: adoptions.map((item) => ({
      id: item.id,
      linkedOutcomeId: item.linkedOutcomeId,
      adoptionType: item.adoptionType
    }))
  });

  const bySales = Array.from(new Set(outcomes.map((item) => item.ownerId))).map((userId) => {
    const rows = outcomes.filter((item) => item.ownerId === userId);
    const linkedIds = new Set(rows.map((item) => item.id));
    const userAdoptions = adoptions.filter((item) => linkedIds.has(item.linkedOutcomeId ?? ""));

    const stats = computeOutcomeOverview({
      outcomes: rows.map((item) => ({
        id: item.id,
        resultStatus: item.resultStatus,
        outcomeType: item.outcomeType
      })),
      adoptions: userAdoptions.map((item) => ({
        id: item.id,
        linkedOutcomeId: item.linkedOutcomeId,
        adoptionType: item.adoptionType
      }))
    });

    return {
      userId,
      userName: nameMap.get(userId) ?? "Unknown",
      totalOutcomes: stats.totalOutcomes,
      positiveProgressRate: stats.positiveRate,
      adoptionRate: stats.adoptionRate,
      adoptionPositiveRate: stats.adoptionPositiveRate
    };
  });

  const effectivePatterns = topText([
    ...recentReviews.flatMap((item) => item.effectivePatterns),
    ...recentPlaybooks.map((item) => item.title)
  ]);

  const ineffectivePatterns = topText([
    ...recentReviews.flatMap((item) => item.ineffectivePatterns),
    ...recentReviews.flatMap((item) => item.repeatedFailures)
  ]);

  const customerStallReasons = topText(outcomes.flatMap((item) => item.newRisks ?? []));

  return {
    periodType,
    periodStart,
    periodEnd,
    summary: {
      totalOutcomes: overview.totalOutcomes,
      positiveProgressRate: overview.positiveRate,
      adoptionRate: overview.adoptionRate,
      adoptionPositiveRate: overview.adoptionPositiveRate
    },
    bySales: bySales.sort((a, b) => b.totalOutcomes - a.totalOutcomes),
    effectivePatterns,
    ineffectivePatterns,
    customerStallReasons,
    repeatedFailurePatterns: overview.repeatedFailures,
    recentReviews,
    recentPlaybooks
  };
}
