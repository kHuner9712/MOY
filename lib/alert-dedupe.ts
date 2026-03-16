import type { AlertLevel, AlertSource } from "@/types/alert";
import type { Database } from "@/types/database";

export interface AlertDedupeDecision {
  shouldUpdate: boolean;
  shouldUpgradeSeverity: boolean;
  nextSource: AlertSource;
}

export function severityRank(level: AlertLevel | Database["public"]["Enums"]["alert_severity"]): number {
  if (level === "critical") return 3;
  if (level === "warning") return 2;
  return 1;
}

export function mergeAlertSource(oldSource: Database["public"]["Enums"]["alert_source"], newSource: AlertSource): AlertSource {
  if (oldSource === newSource) return oldSource;
  return "hybrid";
}

export function getAlertDedupeDecision(params: {
  existing: {
    source: Database["public"]["Enums"]["alert_source"];
    severity: Database["public"]["Enums"]["alert_severity"];
    title: string;
    description: string | null;
    evidence: unknown;
    suggested_owner_action: string[] | null;
  };
  incoming: {
    source: AlertSource;
    level: AlertLevel;
    title: string;
    description: string;
    evidence: string[];
    suggestedOwnerAction: string[];
  };
}): AlertDedupeDecision {
  const shouldUpgradeSeverity = severityRank(params.incoming.level) > severityRank(params.existing.severity);
  const shouldRefreshBody = (params.existing.description ?? "") !== params.incoming.description || params.existing.title !== params.incoming.title;
  const nextSource = mergeAlertSource(params.existing.source, params.incoming.source);
  const shouldUpdate =
    shouldUpgradeSeverity ||
    shouldRefreshBody ||
    nextSource !== params.existing.source ||
    JSON.stringify(params.existing.evidence ?? []) !== JSON.stringify(params.incoming.evidence) ||
    JSON.stringify(params.existing.suggested_owner_action ?? []) !== JSON.stringify(params.incoming.suggestedOwnerAction);

  return {
    shouldUpdate,
    shouldUpgradeSeverity,
    nextSource
  };
}
