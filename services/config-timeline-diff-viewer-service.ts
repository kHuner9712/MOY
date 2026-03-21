import { canViewManagerWorkspace } from "@/lib/role-capability";
import type { ServerSupabaseClient } from "@/lib/supabase/types";
import {
  listRecentOrgConfigAuditLogs,
  type RecentOrgConfigAuditLogResult
} from "@/services/org-config-audit-service";
import {
  getRuntimeExplainDebugPanelData,
  type RuntimeExplainDebugPanelData
} from "@/services/runtime-explain-debug-service";
import type { OrgConfigAuditLog } from "@/types/productization";

type DbClient = ServerSupabaseClient;

type TimelineTargetType =
  | "org_template_override"
  | "org_settings"
  | "org_ai_settings"
  | "org_feature_flags";

const SUPPORTED_TARGET_TYPES: TimelineTargetType[] = [
  "org_template_override",
  "org_settings",
  "org_ai_settings",
  "org_feature_flags"
];

const SENSITIVE_FIELD_PATTERN = /(secret|token|password|api[_-]?key|access[_-]?key|credential)/i;

export interface ConfigTimelineDiffViewerAccessInput {
  role?: "sales" | "manager" | null;
  orgRole?: "owner" | "admin" | "manager" | "sales" | "viewer" | null;
}

export interface ConfigTimelineDiffSummary {
  status: "available" | "summary_only" | "not_available";
  compareSource: "payload_preview" | "normalized_payload" | "summary_object";
  changedKeys: string[];
  addedKeys: string[];
  removedKeys: string[];
  totalChanged: number;
  redactedFields: string[];
  note: string;
}

export interface ConfigTimelineViewerItem {
  id: string;
  targetType: TimelineTargetType;
  targetKey: string | null;
  actionType: string;
  versionLabel: string;
  versionNumber: number;
  createdAt: string;
  actorUserId: string;
  availability: "available" | "summary_only" | "not_available";
  diagnosticsPreview: string[];
  runtimeImpactSummary: string | null;
  rollbackSource: {
    sourceAuditId: string | null;
    sourceVersionLabel: string | null;
    sourceVersionNumber: number | null;
    previewGeneratedAt: string | null;
  } | null;
  detail: {
    beforeSummary: Record<string, unknown> | null;
    afterSummary: Record<string, unknown> | null;
    snapshotSummary: Record<string, unknown> | null;
    diagnosticsSummary: Record<string, unknown> | null;
    diffSummary: ConfigTimelineDiffSummary;
    note: string;
  };
}

export interface ConfigTimelineViewerData {
  generatedAt: string;
  timeline: {
    availability: "available" | "empty" | "not_available";
    note: string;
    items: ConfigTimelineViewerItem[];
  };
  statusSignals: Array<{
    status: "fallback" | "degraded" | "not_available";
    domain: "runtime" | "timeline" | "template_override" | "org_config";
    detail: string;
  }>;
  limitations: string[];
}

function asObject(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

function asNullableNumber(value: unknown): number | null {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function isSupportedTargetType(value: OrgConfigAuditLog["targetType"]): value is TimelineTargetType {
  return SUPPORTED_TARGET_TYPES.includes(value as TimelineTargetType);
}

function normalizePath(base: string, child: string): string {
  return base ? `${base}.${child}` : child;
}

function stableSortObject(value: unknown): unknown {
  if (Array.isArray(value)) return value.map((item) => stableSortObject(item));
  if (!value || typeof value !== "object") return value;
  const entries = Object.entries(asObject(value)).sort(([left], [right]) => left.localeCompare(right));
  return Object.fromEntries(entries.map(([key, child]) => [key, stableSortObject(child)]));
}

function redactObjectForDisplay(value: unknown, path = ""): { value: unknown; redactedFields: string[] } {
  if (Array.isArray(value)) {
    const next = value.map((item, index) => redactObjectForDisplay(item, normalizePath(path, String(index))));
    return {
      value: next.map((item) => item.value),
      redactedFields: next.flatMap((item) => item.redactedFields)
    };
  }
  if (!value || typeof value !== "object") {
    if (typeof value === "string" && path.endsWith("payloadPreview")) {
      const parsedPreview = parseJsonObjectString(value);
      if (parsedPreview) {
        const previewRedacted = redactObjectForDisplay(parsedPreview, path);
        return {
          value: JSON.stringify(stableSortObject(previewRedacted.value)),
          redactedFields: previewRedacted.redactedFields
        };
      }
    }
    if (path && SENSITIVE_FIELD_PATTERN.test(path)) {
      return {
        value: "***REDACTED***",
        redactedFields: [path]
      };
    }
    return {
      value,
      redactedFields: []
    };
  }

  const output: Record<string, unknown> = {};
  const redactedFields: string[] = [];
  for (const [key, child] of Object.entries(asObject(value))) {
    const nextPath = normalizePath(path, key);
    if (SENSITIVE_FIELD_PATTERN.test(nextPath)) {
      output[key] = "***REDACTED***";
      redactedFields.push(nextPath);
      continue;
    }
    const nested = redactObjectForDisplay(child, nextPath);
    output[key] = nested.value;
    redactedFields.push(...nested.redactedFields);
  }
  return {
    value: output,
    redactedFields
  };
}

function flattenValue(value: unknown, path = "", output: Map<string, string>): void {
  if (Array.isArray(value)) {
    if (value.length === 0 && path) output.set(path, "[]");
    value.forEach((item, index) => flattenValue(item, normalizePath(path, String(index)), output));
    return;
  }
  if (!value || typeof value !== "object") {
    if (!path) return;
    output.set(path, JSON.stringify(value));
    return;
  }

  const obj = asObject(value);
  const entries = Object.entries(obj);
  if (entries.length === 0 && path) {
    output.set(path, "{}");
    return;
  }
  entries.forEach(([key, child]) => flattenValue(child, normalizePath(path, key), output));
}

function parseJsonObjectString(value: unknown): Record<string, unknown> | null {
  const text = asString(value);
  if (!text) return null;
  try {
    const parsed = JSON.parse(text);
    const obj = asObject(parsed);
    return Object.keys(obj).length > 0 ? obj : null;
  } catch {
    return null;
  }
}

function resolveComparableSummary(summary: Record<string, unknown>): {
  source: ConfigTimelineDiffSummary["compareSource"];
  payload: Record<string, unknown> | null;
} {
  const fromPayloadPreview = parseJsonObjectString(summary.payloadPreview);
  if (fromPayloadPreview) {
    return {
      source: "payload_preview",
      payload: fromPayloadPreview
    };
  }

  const normalizedPayload = asObject(summary.normalizedPayload);
  if (Object.keys(normalizedPayload).length > 0) {
    return {
      source: "normalized_payload",
      payload: normalizedPayload
    };
  }

  if (Object.keys(summary).length > 0) {
    return {
      source: "summary_object",
      payload: summary
    };
  }

  return {
    source: "summary_object",
    payload: null
  };
}

function uniqueSorted(values: string[]): string[] {
  return Array.from(new Set(values)).sort((left, right) => left.localeCompare(right));
}

export function buildAuditDiffSummary(params: {
  beforeSummary: Record<string, unknown>;
  afterSummary: Record<string, unknown>;
}): ConfigTimelineDiffSummary {
  const beforeComparable = resolveComparableSummary(params.beforeSummary);
  const afterComparable = resolveComparableSummary(params.afterSummary);
  const compareSource: ConfigTimelineDiffSummary["compareSource"] =
    beforeComparable.source === "payload_preview" || afterComparable.source === "payload_preview"
      ? "payload_preview"
      : beforeComparable.source === "normalized_payload" || afterComparable.source === "normalized_payload"
        ? "normalized_payload"
        : "summary_object";

  const beforeSanitized = beforeComparable.payload
    ? redactObjectForDisplay(beforeComparable.payload)
    : { value: null, redactedFields: [] as string[] };
  const afterSanitized = afterComparable.payload
    ? redactObjectForDisplay(afterComparable.payload)
    : { value: null, redactedFields: [] as string[] };

  const beforeFlat = new Map<string, string>();
  const afterFlat = new Map<string, string>();
  if (beforeSanitized.value) flattenValue(stableSortObject(beforeSanitized.value), "", beforeFlat);
  if (afterSanitized.value) flattenValue(stableSortObject(afterSanitized.value), "", afterFlat);

  if (beforeFlat.size === 0 && afterFlat.size === 0) {
    return {
      status: "not_available",
      compareSource,
      changedKeys: [],
      addedKeys: [],
      removedKeys: [],
      totalChanged: 0,
      redactedFields: uniqueSorted([...beforeSanitized.redactedFields, ...afterSanitized.redactedFields]),
      note:
        "No comparable before/after payload found in current audit summaries. Only summary view is available for this record."
    };
  }

  const beforeKeys = Array.from(beforeFlat.keys());
  const afterKeys = Array.from(afterFlat.keys());
  const addedKeys = afterKeys.filter((key) => !beforeFlat.has(key));
  const removedKeys = beforeKeys.filter((key) => !afterFlat.has(key));
  const changedKeys = beforeKeys.filter(
    (key) => afterFlat.has(key) && beforeFlat.get(key) !== afterFlat.get(key)
  );

  const totalChanged = addedKeys.length + removedKeys.length + changedKeys.length;
  return {
    status: totalChanged > 0 ? "available" : "summary_only",
    compareSource,
    changedKeys: uniqueSorted(changedKeys),
    addedKeys: uniqueSorted(addedKeys),
    removedKeys: uniqueSorted(removedKeys),
    totalChanged,
    redactedFields: uniqueSorted([...beforeSanitized.redactedFields, ...afterSanitized.redactedFields]),
    note:
      totalChanged > 0
        ? "Diff summary is computed from redacted comparable payload fields."
        : "No key-level difference detected in comparable payload summaries."
  };
}

function buildTimelineItem(item: OrgConfigAuditLog): ConfigTimelineViewerItem {
  const beforeSummary = asObject(item.beforeSummary);
  const afterSummary = asObject(item.afterSummary);
  const snapshotSummary = asObject(item.snapshotSummary);
  const diagnosticsSummary = asObject(item.diagnosticsSummary);

  const redactedBefore = redactObjectForDisplay(beforeSummary);
  const redactedAfter = redactObjectForDisplay(afterSummary);
  const redactedSnapshot = redactObjectForDisplay(snapshotSummary);
  const redactedDiagnostics = redactObjectForDisplay(diagnosticsSummary);

  const diffSummary = buildAuditDiffSummary({
    beforeSummary: beforeSummary,
    afterSummary: afterSummary
  });

  const rollbackFromDiagnostics = asObject(diagnosticsSummary.rollbackSource);
  const rollbackFromSnapshot = asObject(snapshotSummary.rollbackSource);
  const rollbackSourceObject =
    Object.keys(rollbackFromDiagnostics).length > 0
      ? rollbackFromDiagnostics
      : Object.keys(rollbackFromSnapshot).length > 0
        ? rollbackFromSnapshot
        : null;

  const diagnosticsPreview = asStringArray(diagnosticsSummary.diagnostics).slice(0, 8);
  const runtimeImpactSummary = asString(diagnosticsSummary.runtimeImpactSummary);
  const note =
    diffSummary.status === "not_available"
      ? "Current record does not contain enough comparable before/after payload to compute key-level diff."
      : "Detail and structured diff are generated from persisted audit summaries.";

  return {
    id: item.id,
    targetType: item.targetType as TimelineTargetType,
    targetKey: item.targetKey,
    actionType: item.actionType,
    versionLabel: item.versionLabel,
    versionNumber: item.versionNumber,
    createdAt: item.createdAt,
    actorUserId: item.actorUserId,
    availability: diffSummary.status,
    diagnosticsPreview,
    runtimeImpactSummary,
    rollbackSource: rollbackSourceObject
      ? {
          sourceAuditId: asString(rollbackSourceObject.sourceAuditId),
          sourceVersionLabel: asString(rollbackSourceObject.sourceVersionLabel),
          sourceVersionNumber: asNullableNumber(rollbackSourceObject.sourceVersionNumber),
          previewGeneratedAt: asString(rollbackSourceObject.previewGeneratedAt)
        }
      : null,
    detail: {
      beforeSummary:
        Object.keys(asObject(redactedBefore.value)).length > 0
          ? (redactedBefore.value as Record<string, unknown>)
          : null,
      afterSummary:
        Object.keys(asObject(redactedAfter.value)).length > 0
          ? (redactedAfter.value as Record<string, unknown>)
          : null,
      snapshotSummary:
        Object.keys(asObject(redactedSnapshot.value)).length > 0
          ? (redactedSnapshot.value as Record<string, unknown>)
          : null,
      diagnosticsSummary:
        Object.keys(asObject(redactedDiagnostics.value)).length > 0
          ? (redactedDiagnostics.value as Record<string, unknown>)
          : null,
      diffSummary: {
        ...diffSummary,
        redactedFields: uniqueSorted([
          ...diffSummary.redactedFields,
          ...redactedBefore.redactedFields,
          ...redactedAfter.redactedFields,
          ...redactedSnapshot.redactedFields,
          ...redactedDiagnostics.redactedFields
        ])
      },
      note
    }
  };
}

function buildStatusSignals(params: {
  runtimePanel: RuntimeExplainDebugPanelData;
  timelineAvailability: ConfigTimelineViewerData["timeline"]["availability"];
  timelineNote: string;
}): ConfigTimelineViewerData["statusSignals"] {
  const signals: ConfigTimelineViewerData["statusSignals"] = [];
  if (params.runtimePanel.runtime.resolvedMode === "seed_only") {
    signals.push({
      status: "fallback",
      domain: "runtime",
      detail: "Runtime explain is currently in seed_only mode."
    });
  }
  if (params.timelineAvailability === "not_available") {
    signals.push({
      status: "not_available",
      domain: "timeline",
      detail: params.timelineNote
    });
  }
  if (params.timelineAvailability === "empty") {
    signals.push({
      status: "degraded",
      domain: "timeline",
      detail: params.timelineNote
    });
  }
  if (params.runtimePanel.overrideWriteGovernance.availability === "not_available") {
    signals.push({
      status: "degraded",
      domain: "template_override",
      detail: params.runtimePanel.overrideWriteGovernance.note
    });
  }
  if (params.runtimePanel.orgConfigGovernance.items.some((item) => item.availability !== "available")) {
    signals.push({
      status: "degraded",
      domain: "org_config",
      detail:
        "One or more org config targets are empty/not_available in persisted governance summary. Check runtime debug for details."
    });
  }
  return signals;
}

function buildLimitations(params: {
  recentAudits: RecentOrgConfigAuditLogResult;
  items: ConfigTimelineViewerItem[];
}): string[] {
  const limitations: string[] = [
    "Timeline diff is key-level summary from persisted audit summaries; it is not a full field-by-field history replay."
  ];
  if (params.recentAudits.availability !== "available") {
    limitations.push(
      "Recent audit history is empty or unavailable, so timeline may not reflect complete recent change activity."
    );
  }
  if (params.items.some((item) => item.detail.diffSummary.status !== "available")) {
    limitations.push(
      "Some records do not contain enough comparable before/after payload for detailed diff and are shown as summary_only/not_available."
    );
  }
  return limitations;
}

export function canAccessConfigTimelineDiffViewer(
  input: ConfigTimelineDiffViewerAccessInput | null | undefined
): boolean {
  return canViewManagerWorkspace(input);
}

export function buildConfigTimelineViewerDataFromSource(params: {
  recentAudits: RecentOrgConfigAuditLogResult;
  runtimePanel: RuntimeExplainDebugPanelData;
  generatedAt?: string;
}): ConfigTimelineViewerData {
  const items = params.recentAudits.items
    .filter((item) => isSupportedTargetType(item.targetType))
    .map((item) => buildTimelineItem(item));
  const signals = buildStatusSignals({
    runtimePanel: params.runtimePanel,
    timelineAvailability: params.recentAudits.availability,
    timelineNote: params.recentAudits.note
  });

  return {
    generatedAt: params.generatedAt ?? new Date().toISOString(),
    timeline: {
      availability: params.recentAudits.availability,
      note: params.recentAudits.note,
      items
    },
    statusSignals: signals,
    limitations: buildLimitations({
      recentAudits: params.recentAudits,
      items
    })
  };
}

export async function getConfigTimelineViewerData(params: {
  supabase: DbClient;
  orgId: string;
  recentLimit?: number;
}): Promise<ConfigTimelineViewerData> {
  const [recentAudits, runtimePanel] = await Promise.all([
    listRecentOrgConfigAuditLogs({
      supabase: params.supabase,
      orgId: params.orgId,
      limit: params.recentLimit ?? 20
    }),
    getRuntimeExplainDebugPanelData({
      supabase: params.supabase,
      orgId: params.orgId
    })
  ]);

  return buildConfigTimelineViewerDataFromSource({
    recentAudits,
    runtimePanel
  });
}
