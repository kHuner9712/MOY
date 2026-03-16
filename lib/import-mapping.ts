import type { ImportEntityType } from "@/types/import";

export interface ImportFieldSuggestion {
  sourceColumnName: string;
  mappedTargetEntity: ImportEntityType | null;
  mappedTargetField: string | null;
  confidence: number;
  detectedType: string | null;
  warning: string | null;
}

interface KeywordRule {
  keywords: string[];
  entity: ImportEntityType;
  field: string;
  detectedType: string;
}

const CN = {
  customerName: "\u5ba2\u6237\u540d\u79f0",
  customer: "\u5ba2\u6237",
  companyName: "\u516c\u53f8\u540d",
  company: "\u516c\u53f8",
  contact: "\u8054\u7cfb\u4eba",
  phone: "\u7535\u8bdd",
  mobile: "\u624b\u673a",
  email: "\u90ae\u7bb1",
  owner: "\u8d1f\u8d23\u4eba",
  sales: "\u9500\u552e",
  stage: "\u9636\u6bb5",
  risk: "\u98ce\u9669",
  nextFollowup: "\u4e0b\u6b21\u8ddf\u8fdb",
  tags: "\u6807\u7b7e",
  source: "\u6765\u6e90\u6e20\u9053",
  opportunityTitle: "\u5546\u673a\u6807\u9898",
  amount: "\u91d1\u989d",
  expectedClose: "\u9884\u8ba1\u6210\u4ea4",
  summary: "\u6c9f\u901a\u6458\u8981",
  communicationType: "\u6c9f\u901a\u65b9\u5f0f",
  followupTime: "\u8ddf\u8fdb\u65f6\u95f4",
  customerNeed: "\u5ba2\u6237\u9700\u6c42",
  objection: "\u5f02\u8bae",
  nextStep: "\u4e0b\u4e00\u6b65",
  note: "\u5907\u6ce8"
} as const;

const RULES: KeywordRule[] = [
  { keywords: ["customer_name", "customername", CN.customerName, CN.customer, "name"], entity: "customer", field: "name", detectedType: "string" },
  { keywords: ["company", "company_name", "enterprise", CN.companyName, CN.company], entity: "customer", field: "company_name", detectedType: "string" },
  { keywords: ["contact", "contact_name", "person", CN.contact], entity: "customer", field: "contact_name", detectedType: "string" },
  { keywords: ["phone", "mobile", "tel", CN.phone, CN.mobile], entity: "customer", field: "phone", detectedType: "phone" },
  { keywords: ["email", "mail", CN.email], entity: "customer", field: "email", detectedType: "email" },
  { keywords: ["owner", "owner_name", "sales", "sales_name", CN.owner, CN.sales], entity: "customer", field: "owner", detectedType: "user" },
  { keywords: ["stage", "status", CN.stage], entity: "customer", field: "stage", detectedType: "enum" },
  { keywords: ["risk", "risk_level", CN.risk], entity: "customer", field: "risk_level", detectedType: "enum" },
  { keywords: ["next_followup", "next_followup_at", "next_contact", CN.nextFollowup], entity: "customer", field: "next_followup_at", detectedType: "date" },
  { keywords: ["tags", "tag", CN.tags], entity: "customer", field: "tags", detectedType: "array" },
  { keywords: ["source", "source_channel", "channel", CN.source], entity: "customer", field: "source_channel", detectedType: "string" },

  { keywords: ["opportunity", "opportunity_title", "deal_title", CN.opportunityTitle, "title"], entity: "opportunity", field: "title", detectedType: "string" },
  { keywords: ["amount", "deal_amount", "opportunity_amount", "price", CN.amount], entity: "opportunity", field: "amount", detectedType: "number" },
  { keywords: ["expected_close", "expected_close_date", "close_date", CN.expectedClose], entity: "opportunity", field: "expected_close_date", detectedType: "date" },
  { keywords: ["opportunity_stage", "deal_stage"], entity: "opportunity", field: "stage", detectedType: "enum" },
  { keywords: ["opportunity_risk", "deal_risk"], entity: "opportunity", field: "risk_level", detectedType: "enum" },
  { keywords: ["opportunity_owner", "deal_owner"], entity: "opportunity", field: "owner", detectedType: "user" },

  { keywords: ["followup", "followup_summary", "communication_summary", "summary", CN.summary, CN.note], entity: "followup", field: "summary", detectedType: "string" },
  { keywords: ["communication_type", "contact_method", "method", CN.communicationType], entity: "followup", field: "communication_type", detectedType: "enum" },
  { keywords: ["occurred_at", "created_at", "followup_time", "contact_time", CN.followupTime], entity: "followup", field: "occurred_at", detectedType: "date" },
  { keywords: ["followup_owner", "record_owner"], entity: "followup", field: "owner", detectedType: "user" },
  { keywords: ["customer_needs", "needs", "requirement", CN.customerNeed], entity: "followup", field: "customer_needs", detectedType: "string" },
  { keywords: ["objection", "objections", CN.objection], entity: "followup", field: "objections", detectedType: "string" },
  { keywords: ["next_step", "action_plan", CN.nextStep], entity: "followup", field: "next_step", detectedType: "string" },
  { keywords: ["next_followup", "next_followup_at", "next_contact"], entity: "followup", field: "next_followup_at", detectedType: "date" }
];

function normalizeKey(value: string): string {
  return value.trim().toLowerCase().replace(/\s+/g, "_");
}

function scoreRule(normalizedColumn: string, rule: KeywordRule): number {
  let score = 0;
  for (const keyword of rule.keywords) {
    const normalizedKeyword = normalizeKey(keyword);
    if (normalizedColumn === normalizedKeyword) score = Math.max(score, 0.98);
    if (normalizedColumn.includes(normalizedKeyword)) score = Math.max(score, 0.86);
    if (normalizedKeyword.includes(normalizedColumn) && normalizedColumn.length >= 3) score = Math.max(score, 0.72);
  }
  return score;
}

export function guessImportField(sourceColumnName: string): ImportFieldSuggestion {
  const normalized = normalizeKey(sourceColumnName);
  let best: { rule: KeywordRule; score: number } | null = null;

  for (const rule of RULES) {
    const score = scoreRule(normalized, rule);
    if (!best || score > best.score) best = { rule, score };
  }

  if (!best || best.score < 0.58) {
    return {
      sourceColumnName,
      mappedTargetEntity: null,
      mappedTargetField: null,
      confidence: 0,
      detectedType: null,
      warning: "No reliable mapping rule matched this column."
    };
  }

  return {
    sourceColumnName,
    mappedTargetEntity: best.rule.entity,
    mappedTargetField: best.rule.field,
    confidence: Number(best.score.toFixed(3)),
    detectedType: best.rule.detectedType,
    warning: best.score < 0.8 ? "Low confidence mapping, please confirm manually." : null
  };
}

export function buildRuleBasedMappingSuggestions(columns: string[]): ImportFieldSuggestion[] {
  return columns.map((column) => guessImportField(column));
}

