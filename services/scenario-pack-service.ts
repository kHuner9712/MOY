import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { getCurrentOrgTemplateContext, getIndustryTemplateDetail } from "@/services/industry-template-service";
import type { ScenarioPack } from "@/types/productization";

type DbClient = ServerSupabaseClient;

export async function listScenarioPacksForTemplate(params: {
  supabase: DbClient;
  templateIdOrKey: string;
}): Promise<ScenarioPack[]> {
  const detail = await getIndustryTemplateDetail({
    supabase: params.supabase,
    templateIdOrKey: params.templateIdOrKey
  });
  return detail.scenarioPacks;
}

export async function listCurrentOrgScenarioPacks(params: {
  supabase: DbClient;
  orgId: string;
}): Promise<ScenarioPack[]> {
  const context = await getCurrentOrgTemplateContext({
    supabase: params.supabase,
    orgId: params.orgId
  });
  return context.scenarioPacks;
}

