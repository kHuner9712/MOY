import { ok } from "@/lib/api-response";
import { createSupabaseServerClient } from "@/lib/supabase/server";
import { BUILTIN_INDUSTRY_TEMPLATE_SEEDS } from "@/data/industry-templates";
import { listIndustryTemplates } from "@/services/industry-template-service";

export async function GET() {
  const supabase = createSupabaseServerClient();
  const fallbackTemplates = BUILTIN_INDUSTRY_TEMPLATE_SEEDS.map((item) => ({
    id: `builtin:${item.templateKey}`,
    templateKey: item.templateKey,
    displayName: item.displayName,
    industryFamily: item.industryFamily,
    summary: item.summary
  }));

  if (!supabase) {
    return ok({
      templates: fallbackTemplates
    });
  }

  const templates = await listIndustryTemplates({
    supabase
  }).catch(() => []);
  return ok({
    templates: (templates.length > 0 ? templates : fallbackTemplates).map((item) => ({
      id: item.id,
      templateKey: item.templateKey,
      displayName: item.displayName,
      industryFamily: item.industryFamily,
      summary: item.summary
    }))
  });
}
