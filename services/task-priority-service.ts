import { computeTaskPriority } from "@/lib/task-priority";
import type { Customer } from "@/types/customer";
import type { WorkItemSourceType, WorkPriorityBand, WorkType } from "@/types/work";

export interface WorkCandidate {
  sourceType: WorkItemSourceType;
  workType: WorkType;
  title: string;
  description: string;
  customer: Customer | null;
  dueAt: string | null;
  scheduledFor: string | null;
  managerFlagged: boolean;
  highProbabilityOpportunity: boolean;
  rhythmFit: "good" | "neutral" | "poor";
  backlogSize: number;
  extraRationale?: string;
}

export interface PrioritizedWorkCandidate extends WorkCandidate {
  priorityScore: number;
  priorityBand: WorkPriorityBand;
  rationale: string;
}

function getCustomerValueScore(customer: Customer | null): number {
  if (!customer) return 30;
  const winPart = Math.min(100, Math.max(0, customer.winProbability));
  const riskPart = customer.riskLevel === "high" ? 10 : customer.riskLevel === "medium" ? 5 : 0;
  return Math.min(100, winPart + riskPart);
}

function getRhythmFit(params: { rhythmHints: string[]; dueAt: string | null }): "good" | "neutral" | "poor" {
  if (!params.dueAt || params.rhythmHints.length === 0) return "neutral";
  const hint = params.rhythmHints.join(" ").toLowerCase();
  if (hint.includes("2") || hint.includes("3")) return "good";
  if (hint.includes("7")) return "poor";
  return "neutral";
}

export function prioritizeWorkCandidate(candidate: WorkCandidate): PrioritizedWorkCandidate {
  const base = computeTaskPriority({
    workType: candidate.workType,
    customerValueScore: getCustomerValueScore(candidate.customer),
    riskLevel: candidate.customer?.riskLevel ?? null,
    dueAt: candidate.dueAt,
    scheduledFor: candidate.scheduledFor,
    customerStage: candidate.customer?.stage ?? null,
    lastFollowupAt: candidate.customer?.lastFollowupAt ?? null,
    highProbabilityOpportunity: candidate.highProbabilityOpportunity,
    managerFlagged: candidate.managerFlagged,
    rhythmFit: candidate.rhythmFit,
    backlogSize: candidate.backlogSize
  });

  const rationale = candidate.extraRationale ? `${base.rationale}；${candidate.extraRationale}` : base.rationale;
  return {
    ...candidate,
    priorityScore: base.priorityScore,
    priorityBand: base.priorityBand,
    rationale
  };
}

export function prioritizeWorkCandidates(params: {
  candidates: WorkCandidate[];
  memoryRhythmHints?: string[];
}): PrioritizedWorkCandidate[] {
  const rhythmHints = params.memoryRhythmHints ?? [];
  return params.candidates
    .map((candidate) => {
      const rhythmFit = candidate.rhythmFit === "neutral" ? getRhythmFit({ rhythmHints, dueAt: candidate.dueAt }) : candidate.rhythmFit;
      return prioritizeWorkCandidate({
        ...candidate,
        rhythmFit
      });
    })
    .sort((a, b) => b.priorityScore - a.priorityScore);
}
