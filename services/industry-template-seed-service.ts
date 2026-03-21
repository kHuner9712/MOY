import { INDUSTRY_TEMPLATE_SEEDS_V1 } from "@/data/industry-template-seeds-v1";
import type { IndustryTemplateDefinition } from "@/types/template";

export function listIndustryTemplateSeeds(): IndustryTemplateDefinition[] {
  return [...INDUSTRY_TEMPLATE_SEEDS_V1];
}

export function getIndustryTemplateSeedByKey(templateKey: string): IndustryTemplateDefinition | null {
  const key = templateKey.trim().toLowerCase();
  if (!key) return null;
  return INDUSTRY_TEMPLATE_SEEDS_V1.find((item) => item.templateKey === key) ?? null;
}

