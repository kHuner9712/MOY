import type { CommunicationType } from "@/types/ai";
import type { Database } from "@/types/database";

const CN = {
  lead: "\u7ebf\u7d22",
  initialContact: "\u521d\u804a",
  needsConfirmed: "\u9700\u6c42\u786e\u8ba4",
  proposal: "\u62a5\u4ef7",
  negotiation: "\u8c08\u5224",
  won: "\u8d62\u5355",
  lost: "\u4e22\u5355",
  low: "\u4f4e",
  medium: "\u4e2d",
  high: "\u9ad8",
  phone: "\u7535\u8bdd",
  wechat: "\u5fae\u4fe1",
  email: "\u90ae\u4ef6",
  meeting: "\u9762\u8c08",
  other: "\u5176\u4ed6"
} as const;

const CUSTOMER_STAGE_MAP: Record<string, Database["public"]["Enums"]["customer_stage"]> = {
  lead: "lead",
  [CN.lead]: "lead",
  initial_contact: "initial_contact",
  initialcontact: "initial_contact",
  [CN.initialContact]: "initial_contact",
  needs_confirmed: "needs_confirmed",
  needsconfirmed: "needs_confirmed",
  [CN.needsConfirmed]: "needs_confirmed",
  proposal: "proposal",
  [CN.proposal]: "proposal",
  negotiation: "negotiation",
  [CN.negotiation]: "negotiation",
  won: "won",
  [CN.won]: "won",
  lost: "lost",
  [CN.lost]: "lost"
};

const OPPORTUNITY_STAGE_MAP: Record<string, Database["public"]["Enums"]["opportunity_stage"]> = {
  discovery: "discovery",
  qualification: "qualification",
  proposal: "proposal",
  business_review: "business_review",
  businessreview: "business_review",
  negotiation: "negotiation",
  won: "won",
  lost: "lost",
  [CN.proposal]: "proposal",
  [CN.negotiation]: "negotiation",
  [CN.won]: "won",
  [CN.lost]: "lost"
};

const RISK_MAP: Record<string, "low" | "medium" | "high"> = {
  low: "low",
  [CN.low]: "low",
  medium: "medium",
  [CN.medium]: "medium",
  high: "high",
  [CN.high]: "high"
};

const COMMUNICATION_MAP: Record<string, CommunicationType> = {
  phone: "phone",
  [CN.phone]: "phone",
  wechat: "wechat",
  [CN.wechat]: "wechat",
  email: "email",
  [CN.email]: "email",
  meeting: "meeting",
  [CN.meeting]: "meeting",
  other: "other",
  [CN.other]: "other"
};

function cleanString(value: unknown): string {
  if (value === null || value === undefined) return "";
  return String(value).trim();
}

export function normalizeDateValue(value: unknown): string | null {
  const raw = cleanString(value);
  if (!raw) return null;

  // 2026-03-15 / 2026/03/15
  if (/^\d{4}[-/]\d{1,2}[-/]\d{1,2}/.test(raw)) {
    const normalized = raw.replace(/\//g, "-");
    const date = new Date(normalized);
    return Number.isNaN(date.getTime()) ? null : date.toISOString();
  }

  // 2026年3月15日
  if (/^\d{4}\s*\u5e74\s*\d{1,2}\s*\u6708\s*\d{1,2}\s*\u65e5?$/.test(raw)) {
    const normalized = raw
      .replace(/\u5e74/g, "-")
      .replace(/\u6708/g, "-")
      .replace(/\u65e5/g, "");
    const date = new Date(normalized);
    return Number.isNaN(date.getTime()) ? null : date.toISOString();
  }

  // Excel serial number
  if (/^\d+(\.\d+)?$/.test(raw)) {
    const numeric = Number(raw);
    if (numeric > 20000 && numeric < 100000) {
      const excelEpoch = new Date(Date.UTC(1899, 11, 30));
      excelEpoch.setUTCDate(excelEpoch.getUTCDate() + Math.floor(numeric));
      return excelEpoch.toISOString();
    }
  }

  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed.toISOString();
}

export function normalizeCurrencyAmount(value: unknown): number | null {
  const raw = cleanString(value);
  if (!raw) return null;
  const sanitized = raw
    .replace(/[\u00a5\uffe5$,\s]/g, "")
    .replace(/,/g, "");
  const amount = Number(sanitized);
  return Number.isFinite(amount) ? amount : null;
}

export function normalizeTags(value: unknown): string[] {
  const raw = cleanString(value);
  if (!raw) return [];
  return raw
    .split(/[,;\uFF1B\u3001]/)
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

export function normalizeCustomerStage(value: unknown): Database["public"]["Enums"]["customer_stage"] | null {
  const raw = cleanString(value).toLowerCase().replace(/\s+/g, "_");
  return CUSTOMER_STAGE_MAP[raw] ?? null;
}

export function normalizeOpportunityStage(value: unknown): Database["public"]["Enums"]["opportunity_stage"] | null {
  const raw = cleanString(value).toLowerCase().replace(/\s+/g, "_");
  return OPPORTUNITY_STAGE_MAP[raw] ?? null;
}

export function normalizeRiskLevel(value: unknown): "low" | "medium" | "high" | null {
  const raw = cleanString(value).toLowerCase();
  return RISK_MAP[raw] ?? null;
}

export function normalizeCommunicationType(value: unknown): CommunicationType | null {
  const raw = cleanString(value).toLowerCase();
  return COMMUNICATION_MAP[raw] ?? null;
}

export function normalizeOwnerKey(value: unknown): string | null {
  const raw = cleanString(value).toLowerCase();
  return raw || null;
}

