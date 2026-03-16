import type { ActionOutcomeStatus, SuggestionAdoptionType } from "@/types/outcome";

export interface OutcomeLite {
  id: string;
  resultStatus: ActionOutcomeStatus;
  outcomeType: string;
  newRisks?: string[];
  newObjections?: string[];
  stageChanged?: boolean;
  usedPrepCard?: boolean;
  usedDraft?: boolean;
}

export interface AdoptionLite {
  id: string;
  linkedOutcomeId: string | null;
  adoptionType: SuggestionAdoptionType;
}

export function isPositiveOutcome(status: ActionOutcomeStatus): boolean {
  return status === "positive_progress" || status === "closed_won";
}

export function computeOutcomeOverview(params: {
  outcomes: OutcomeLite[];
  adoptions: AdoptionLite[];
}): {
  totalOutcomes: number;
  positiveRate: number;
  adoptionRate: number;
  adoptionPositiveRate: number;
  repeatedFailures: string[];
} {
  const totalOutcomes = params.outcomes.length;
  const positiveCount = params.outcomes.filter((item) => isPositiveOutcome(item.resultStatus)).length;
  const positiveRate = totalOutcomes === 0 ? 0 : positiveCount / totalOutcomes;

  const adoptedEvents = params.adoptions.filter((item) => ["adopted", "partially_used", "copied", "edited"].includes(item.adoptionType));
  const adoptionRate = totalOutcomes === 0 ? 0 : adoptedEvents.length / totalOutcomes;

  const adoptionOutcomeIds = new Set(adoptedEvents.map((item) => item.linkedOutcomeId).filter((item): item is string => Boolean(item)));
  const adoptedOutcomes = params.outcomes.filter((item) => adoptionOutcomeIds.has(item.id));
  const adoptionPositive = adoptedOutcomes.filter((item) => isPositiveOutcome(item.resultStatus)).length;
  const adoptionPositiveRate = adoptedOutcomes.length === 0 ? 0 : adoptionPositive / adoptedOutcomes.length;

  const repeatedFailureMap = new Map<string, number>();
  for (const item of params.outcomes) {
    if (item.resultStatus !== "stalled" && item.resultStatus !== "risk_increased" && item.resultStatus !== "closed_lost") continue;
    const key = `${item.outcomeType}:${item.resultStatus}`;
    repeatedFailureMap.set(key, (repeatedFailureMap.get(key) ?? 0) + 1);
  }

  const repeatedFailures = Array.from(repeatedFailureMap.entries())
    .filter(([, count]) => count >= 2)
    .sort((a, b) => b[1] - a[1])
    .map(([key, count]) => `${key} x${count}`);

  return {
    totalOutcomes,
    positiveRate,
    adoptionRate,
    adoptionPositiveRate,
    repeatedFailures
  };
}

export function summarizeOutcomePatterns(params: { outcomes: OutcomeLite[] }): {
  effectivePatterns: string[];
  ineffectivePatterns: string[];
  repeatedFailures: string[];
} {
  const effective: string[] = [];
  const ineffective: string[] = [];

  const withPrepPositive = params.outcomes.filter((item) => isPositiveOutcome(item.resultStatus) && item.usedPrepCard).length;
  if (withPrepPositive > 0) effective.push(`Prep-supported actions showed ${withPrepPositive} positive progress outcomes.`);

  const withDraftPositive = params.outcomes.filter((item) => isPositiveOutcome(item.resultStatus) && item.usedDraft).length;
  if (withDraftPositive > 0) effective.push(`Draft-assisted actions showed ${withDraftPositive} positive progress outcomes.`);

  const stalledCount = params.outcomes.filter((item) => item.resultStatus === "stalled").length;
  if (stalledCount > 0) ineffective.push(`Stalled outcomes appeared ${stalledCount} times.`);

  const riskIncreasedCount = params.outcomes.filter((item) => item.resultStatus === "risk_increased").length;
  if (riskIncreasedCount > 0) ineffective.push(`Risk increased after action ${riskIncreasedCount} times.`);

  const repeatedFailures = computeOutcomeOverview({
    outcomes: params.outcomes,
    adoptions: []
  }).repeatedFailures;

  return {
    effectivePatterns: effective,
    ineffectivePatterns: ineffective,
    repeatedFailures
  };
}

export function inferPlaybookTypeFromOutcomeType(outcomeType: string):
  | "followup_rhythm"
  | "quote_strategy"
  | "meeting_strategy"
  | "risk_recovery"
  | "objection_handling"
  | "customer_segment" {
  if (outcomeType === "quote_result") return "quote_strategy";
  if (outcomeType === "meeting_result") return "meeting_strategy";
  if (outcomeType === "manager_intervention_result") return "risk_recovery";
  if (outcomeType === "followup_result") return "followup_rhythm";
  return "objection_handling";
}
