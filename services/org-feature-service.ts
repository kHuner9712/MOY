import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { mapOrgFeatureFlagRow } from "@/services/mappers";
import type { Database } from "@/types/database";
import type { OrgFeatureFlag, OrgFeatureKey } from "@/types/productization";

type DbClient = ServerSupabaseClient;
type FeatureRow = Database["public"]["Tables"]["org_feature_flags"]["Row"];

export const DEFAULT_FEATURE_FLAGS: Record<OrgFeatureKey, boolean> = {
  ai_auto_analysis: true,
  ai_auto_planning: true,
  ai_morning_brief: true,
  ai_deal_command: true,
  external_touchpoints: true,
  prep_cards: true,
  playbooks: true,
  manager_quality_view: true,
  outcome_learning: true,
  demo_seed_tools: true
};

export async function getOrgFeatureFlags(params: {
  supabase: DbClient;
  orgId: string;
}): Promise<OrgFeatureFlag[]> {
  const res = await params.supabase.from("org_feature_flags").select("*").eq("org_id", params.orgId);
  if (res.error) throw new Error(res.error.message);

  const rows = (res.data ?? []) as FeatureRow[];

  if (rows.length < Object.keys(DEFAULT_FEATURE_FLAGS).length) {
    const missingRows = (Object.keys(DEFAULT_FEATURE_FLAGS) as OrgFeatureKey[])
      .filter((key) => !rows.some((row) => row.feature_key === key))
      .map((key) => ({
        org_id: params.orgId,
        feature_key: key,
        is_enabled: DEFAULT_FEATURE_FLAGS[key],
        config_json: {}
      }));

    if (missingRows.length > 0) {
      await params.supabase.from("org_feature_flags").insert(missingRows);
      return getOrgFeatureFlags(params);
    }
  }

  return rows.map((row) => mapOrgFeatureFlagRow(row));
}

export async function getOrgFeatureFlagMap(params: {
  supabase: DbClient;
  orgId: string;
}): Promise<Record<OrgFeatureKey, boolean>> {
  const rows = await getOrgFeatureFlags(params);
  const map = { ...DEFAULT_FEATURE_FLAGS };
  for (const row of rows) {
    map[row.featureKey] = row.isEnabled;
  }
  return map;
}

export async function isOrgFeatureEnabled(params: {
  supabase: DbClient;
  orgId: string;
  featureKey: OrgFeatureKey;
}): Promise<boolean> {
  const map = await getOrgFeatureFlagMap({
    supabase: params.supabase,
    orgId: params.orgId
  });
  return map[params.featureKey] ?? false;
}

export async function updateOrgFeatureFlag(params: {
  supabase: DbClient;
  orgId: string;
  featureKey: OrgFeatureKey;
  isEnabled: boolean;
  configJson?: Record<string, unknown>;
}): Promise<OrgFeatureFlag> {
  const res = await params.supabase
    .from("org_feature_flags")
    .upsert(
      {
        org_id: params.orgId,
        feature_key: params.featureKey,
        is_enabled: params.isEnabled,
        config_json: params.configJson ?? {}
      },
      {
        onConflict: "org_id,feature_key"
      }
    )
    .select("*")
    .single();

  if (res.error) throw new Error(res.error.message);
  return mapOrgFeatureFlagRow(res.data as FeatureRow);
}
