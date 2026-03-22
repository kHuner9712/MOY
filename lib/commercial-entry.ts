import type { InboundLeadSource, LeadQualificationAssistResult } from "@/types/commercialization";

const TRACE_VERSION = "commercial_entry_v1";
const SOURCE_CHANNEL = "public_web_form";

function normalizeOptionalText(value: string | null | undefined, max: number): string | null {
  if (typeof value !== "string") return null;
  const normalized = value.trim();
  if (!normalized) return null;
  return normalized.slice(0, max);
}

function normalizeSubmittedAt(value: string | null | undefined, fallback: string): string {
  const normalized = normalizeOptionalText(value, 64);
  if (!normalized) return fallback;
  const parsed = new Date(normalized);
  if (Number.isNaN(parsed.getTime())) return fallback;
  return parsed.toISOString();
}

function normalizeLandingPage(value: string | null | undefined, fallback: string): string {
  const normalized = normalizeOptionalText(value, 220) ?? fallback;
  if (normalized.startsWith("http://") || normalized.startsWith("https://") || normalized.startsWith("/")) {
    return normalized;
  }
  return `/${normalized.replace(/^\/+/, "")}`;
}

function normalizeTraceId(value: string | null | undefined): string {
  const normalized = normalizeOptionalText(value, 120);
  if (normalized) return normalized;
  return generateCommercialTraceId();
}

function asRecord(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function readBoolean(value: unknown): boolean {
  return value === true;
}

function readNullableString(payload: Record<string, unknown>, key: string, max: number): string | null {
  return normalizeOptionalText(typeof payload[key] === "string" ? payload[key] : null, max);
}

function randomIdPart(): string {
  const native = globalThis.crypto?.randomUUID?.();
  if (typeof native === "string" && native.length > 0) {
    return native.replaceAll("-", "");
  }
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
}

export function generateCommercialTraceId(prefix = "entry"): string {
  const normalizedPrefix = normalizeOptionalText(prefix, 24)?.replace(/[^a-zA-Z0-9_-]/g, "") ?? "entry";
  return `${normalizedPrefix}_${randomIdPart()}`;
}

export interface PublicCommercialEntryContextInput {
  sourceCampaign?: string | null;
  landingPage?: string | null;
  referrer?: string | null;
  utmSource?: string | null;
  utmMedium?: string | null;
  utmCampaign?: string | null;
  utmTerm?: string | null;
  utmContent?: string | null;
  locale?: string | null;
  timezone?: string | null;
  submittedAt?: string | null;
  entryTraceId?: string | null;
}

export interface PublicCommercialEntryContext {
  entryTraceId: string;
  source: InboundLeadSource;
  sourceChannel: "public_web_form";
  sourceCampaign: string | null;
  landingPage: string;
  referrer: string | null;
  utmSource: string | null;
  utmMedium: string | null;
  utmCampaign: string | null;
  utmTerm: string | null;
  utmContent: string | null;
  locale: string | null;
  timezone: string | null;
  submittedAt: string;
}

export interface PublicCommercialEntryTracePayload {
  traceVersion: string;
  traceId: string;
  sourceChannel: string;
  leadSource: string | null;
  sourceCampaign: string | null;
  landingPage: string | null;
  referrer: string | null;
  utmSource: string | null;
  utmMedium: string | null;
  utmCampaign: string | null;
  utmTerm: string | null;
  utmContent: string | null;
  locale: string | null;
  timezone: string | null;
  submittedAt: string | null;
}

export interface TrialOnboardingIntent {
  intentVersion: "v1";
  needsDataImport: boolean;
  preferredTemplateKey: string | null;
  useCaseHint: string | null;
  onboardingMotion: "assisted_import" | "guided_activation";
  kickoffPriority: "high" | "normal";
  expectedFirstOutcome: string;
}

export interface LeadQualificationSnapshot {
  qualification_run_id: string;
  qualification_fit_score: number;
  qualification_risk_flags: string[];
  qualification_assessment: string;
  qualification_likely_use_case: string;
  qualification_suggested_owner_type: "sales" | "manager";
  qualification_suggested_next_actions: string[];
  qualification_used_fallback: boolean;
  qualification_fallback_reason: string | null;
  assignment_owner_id: string;
  assignment_owner_name: string;
  matched_rule_id: string | null;
}

export type LeadPipelineHandoffStatus =
  | "not_requested"
  | "requested_pending"
  | "created_new_pipeline"
  | "reused_existing_pipeline"
  | "skipped_unqualified"
  | "attempted_without_pipeline_ref";

export interface LeadPipelineHandoffSnapshot {
  requested: boolean;
  attempted: boolean;
  status: LeadPipelineHandoffStatus;
  pipeline_created: boolean;
  customer_id: string | null;
  opportunity_id: string | null;
  deal_room_id: string | null;
  skip_reason: "lead_unqualified" | null;
  evaluated_at: string;
}

export function buildClientPublicCommercialEntryInput(params: {
  fallbackLandingPage: string;
  entryTraceId?: string | null;
  sourceCampaign?: string | null;
  now?: string;
}): PublicCommercialEntryContextInput {
  if (typeof window === "undefined") {
    return {
      sourceCampaign: params.sourceCampaign ?? null,
      landingPage: params.fallbackLandingPage,
      submittedAt: params.now ?? new Date().toISOString(),
      entryTraceId: params.entryTraceId ?? null
    };
  }

  const search = new URLSearchParams(window.location.search);
  const utmCampaign = search.get("utm_campaign");
  const campaignFallback = search.get("campaign");

  const locale = typeof navigator !== "undefined" ? navigator.language : null;
  const timezone =
    typeof Intl !== "undefined" && typeof Intl.DateTimeFormat === "function"
      ? Intl.DateTimeFormat().resolvedOptions().timeZone
      : null;
  const referrer = typeof document !== "undefined" ? document.referrer : null;

  return {
    sourceCampaign: params.sourceCampaign ?? utmCampaign ?? campaignFallback,
    landingPage: window.location.pathname || params.fallbackLandingPage,
    referrer,
    utmSource: search.get("utm_source"),
    utmMedium: search.get("utm_medium"),
    utmCampaign,
    utmTerm: search.get("utm_term"),
    utmContent: search.get("utm_content"),
    locale,
    timezone,
    submittedAt: params.now ?? new Date().toISOString(),
    entryTraceId: params.entryTraceId ?? null
  };
}

export function buildPublicCommercialEntryContext(params: {
  source: InboundLeadSource;
  fallbackLandingPage: string;
  input?: PublicCommercialEntryContextInput | null;
  now?: string;
}): PublicCommercialEntryContext {
  const now = params.now ?? new Date().toISOString();
  const input = params.input ?? {};

  return {
    entryTraceId: normalizeTraceId(input.entryTraceId),
    source: params.source,
    sourceChannel: SOURCE_CHANNEL,
    sourceCampaign: normalizeOptionalText(input.sourceCampaign, 120),
    landingPage: normalizeLandingPage(input.landingPage, params.fallbackLandingPage),
    referrer: normalizeOptionalText(input.referrer, 400),
    utmSource: normalizeOptionalText(input.utmSource, 120),
    utmMedium: normalizeOptionalText(input.utmMedium, 120),
    utmCampaign: normalizeOptionalText(input.utmCampaign, 120),
    utmTerm: normalizeOptionalText(input.utmTerm, 120),
    utmContent: normalizeOptionalText(input.utmContent, 120),
    locale: normalizeOptionalText(input.locale, 32),
    timezone: normalizeOptionalText(input.timezone, 80),
    submittedAt: normalizeSubmittedAt(input.submittedAt, now)
  };
}

export function toPublicCommercialEntryTracePayload(context: PublicCommercialEntryContext): Record<string, unknown> {
  return {
    trace_version: TRACE_VERSION,
    trace_id: context.entryTraceId,
    source_channel: context.sourceChannel,
    lead_source: context.source,
    source_campaign: context.sourceCampaign,
    landing_page: context.landingPage,
    referrer: context.referrer,
    utm_source: context.utmSource,
    utm_medium: context.utmMedium,
    utm_campaign: context.utmCampaign,
    utm_term: context.utmTerm,
    utm_content: context.utmContent,
    locale: context.locale,
    timezone: context.timezone,
    submitted_at: context.submittedAt
  };
}

export function readPublicCommercialEntryTrace(snapshot: Record<string, unknown> | null | undefined): PublicCommercialEntryTracePayload | null {
  const entryTrace = asRecord(asRecord(snapshot).entry_trace);
  const traceId = readNullableString(entryTrace, "trace_id", 120);
  if (!traceId) return null;

  return {
    traceVersion: readNullableString(entryTrace, "trace_version", 48) ?? TRACE_VERSION,
    traceId,
    sourceChannel: readNullableString(entryTrace, "source_channel", 64) ?? SOURCE_CHANNEL,
    leadSource: readNullableString(entryTrace, "lead_source", 64),
    sourceCampaign: readNullableString(entryTrace, "source_campaign", 120),
    landingPage: readNullableString(entryTrace, "landing_page", 220),
    referrer: readNullableString(entryTrace, "referrer", 400),
    utmSource: readNullableString(entryTrace, "utm_source", 120),
    utmMedium: readNullableString(entryTrace, "utm_medium", 120),
    utmCampaign: readNullableString(entryTrace, "utm_campaign", 120),
    utmTerm: readNullableString(entryTrace, "utm_term", 120),
    utmContent: readNullableString(entryTrace, "utm_content", 120),
    locale: readNullableString(entryTrace, "locale", 32),
    timezone: readNullableString(entryTrace, "timezone", 80),
    submittedAt: readNullableString(entryTrace, "submitted_at", 64)
  };
}

export function extractPublicCommercialEntryTraceId(snapshot: Record<string, unknown> | null | undefined): string | null {
  return readPublicCommercialEntryTrace(snapshot)?.traceId ?? null;
}

export function buildLeadQualificationSnapshot(params: {
  qualificationRunId: string;
  qualification: LeadQualificationAssistResult;
  qualificationUsedFallback: boolean;
  qualificationFallbackReason: string | null;
  assignmentOwnerId: string;
  assignmentOwnerName: string;
  matchedRuleId: string | null;
}): LeadQualificationSnapshot {
  return {
    qualification_run_id: params.qualificationRunId,
    qualification_fit_score: params.qualification.fitScore,
    qualification_risk_flags: params.qualification.riskFlags,
    qualification_assessment: params.qualification.qualificationAssessment,
    qualification_likely_use_case: params.qualification.likelyUseCase,
    qualification_suggested_owner_type: params.qualification.suggestedOwnerType,
    qualification_suggested_next_actions: params.qualification.suggestedNextActions,
    qualification_used_fallback: params.qualificationUsedFallback,
    qualification_fallback_reason: params.qualificationFallbackReason,
    assignment_owner_id: params.assignmentOwnerId,
    assignment_owner_name: params.assignmentOwnerName,
    matched_rule_id: params.matchedRuleId
  };
}

export function buildLeadPipelineHandoffSnapshot(params: {
  requested: boolean;
  attempted: boolean;
  pipelineCreated: boolean;
  customerId?: string | null;
  opportunityId?: string | null;
  dealRoomId?: string | null;
  skippedReason?: "lead_unqualified" | null;
  evaluatedAt?: string;
}): LeadPipelineHandoffSnapshot {
  const customerId = normalizeOptionalText(params.customerId ?? null, 64);
  const opportunityId = normalizeOptionalText(params.opportunityId ?? null, 64);
  const dealRoomId = normalizeOptionalText(params.dealRoomId ?? null, 64);

  let status: LeadPipelineHandoffStatus = "not_requested";
  if (!params.requested) {
    status = "not_requested";
  } else if (!params.attempted) {
    status = params.skippedReason === "lead_unqualified" ? "skipped_unqualified" : "requested_pending";
  } else if (params.pipelineCreated) {
    status = "created_new_pipeline";
  } else if (customerId || opportunityId || dealRoomId) {
    status = "reused_existing_pipeline";
  } else {
    status = "attempted_without_pipeline_ref";
  }

  return {
    requested: params.requested,
    attempted: params.attempted,
    status,
    pipeline_created: params.pipelineCreated,
    customer_id: customerId,
    opportunity_id: opportunityId,
    deal_room_id: dealRoomId,
    skip_reason: params.skippedReason ?? null,
    evaluated_at: params.evaluatedAt ?? new Date().toISOString()
  };
}

export function buildTrialOnboardingIntent(params: {
  needImportData: boolean;
  preferredTemplateKey?: string | null;
  useCaseHint?: string | null;
  industryHint?: string | null;
  teamSizeHint?: string | null;
}): TrialOnboardingIntent {
  const useCaseHint = normalizeOptionalText(params.useCaseHint, 1000);
  const preferredTemplateKey = normalizeOptionalText(params.preferredTemplateKey, 80);
  const expectedFirstOutcome =
    useCaseHint ??
    (params.needImportData ? "完成首批客户数据导入并验证第一条跟进闭环。" : "完成首个业务场景配置并验证团队执行路径。");

  const hasComplexContext = Boolean(params.needImportData || normalizeOptionalText(params.teamSizeHint, 40) || normalizeOptionalText(params.industryHint, 80));

  return {
    intentVersion: "v1",
    needsDataImport: params.needImportData,
    preferredTemplateKey,
    useCaseHint,
    onboardingMotion: params.needImportData ? "assisted_import" : "guided_activation",
    kickoffPriority: hasComplexContext ? "high" : "normal",
    expectedFirstOutcome
  };
}

export function readTrialOnboardingIntentFromLeadSnapshot(snapshot: Record<string, unknown> | null | undefined): TrialOnboardingIntent | null {
  const payload = asRecord(asRecord(snapshot).trial_onboarding_intent);
  if (Object.keys(payload).length === 0) return null;

  const intentVersion = readNullableString(payload, "intent_version", 16) ?? "v1";
  if (intentVersion !== "v1") return null;

  return buildTrialOnboardingIntent({
    needImportData: readBoolean(payload.need_import_data),
    preferredTemplateKey: readNullableString(payload, "preferred_template_key", 80),
    useCaseHint: readNullableString(payload, "use_case_hint", 1000),
    industryHint: readNullableString(payload, "industry_hint", 80),
    teamSizeHint: readNullableString(payload, "team_size_hint", 40)
  });
}

export function toTrialOnboardingIntentPayload(intent: TrialOnboardingIntent): Record<string, unknown> {
  return {
    intent_version: intent.intentVersion,
    need_import_data: intent.needsDataImport,
    preferred_template_key: intent.preferredTemplateKey,
    use_case_hint: intent.useCaseHint,
    onboarding_motion: intent.onboardingMotion,
    kickoff_priority: intent.kickoffPriority,
    expected_first_outcome: intent.expectedFirstOutcome
  };
}
