import type { OutcomeEffectivenessReviewResult, PersonalEffectivenessUpdateResult } from "@/types/ai";

export function buildFallbackOutcomeReview(params: {
  periodLabel: string;
  positiveRate: number;
  adoptionRate: number;
  repeatedFailures: string[];
}): OutcomeEffectivenessReviewResult {
  return {
    title: `Outcome review (fallback) | ${params.periodLabel}`,
    executive_summary: `Positive progress rate ${(params.positiveRate * 100).toFixed(0)}%, suggestion adoption ${(params.adoptionRate * 100).toFixed(0)}%.`,
    effective_patterns: [
      "Tasks with explicit next-step closure have better progression outcomes.",
      "High-risk customers with timely follow-up show lower stall risk."
    ],
    ineffective_patterns: [
      "Repeated shallow updates without stage movement.",
      "Late risk response after critical alerts."
    ],
    repeated_failures: params.repeatedFailures.slice(0, 8),
    coaching_actions: [
      "Review top stalled patterns in weekly coaching.",
      "Require dated next-step confirmation on must-do tasks."
    ],
    playbook_candidates: [
      "objection_handling: budget sensitivity path",
      "followup_rhythm: 48-hour second-touch pattern"
    ]
  };
}

export function buildFallbackPersonalEffectivenessUpdate(params: {
  positiveRateAfterAdoption: number;
  positiveRateWithoutAdoption: number;
}): PersonalEffectivenessUpdateResult {
  return {
    summary:
      params.positiveRateAfterAdoption >= params.positiveRateWithoutAdoption
        ? "Adopted suggestions currently correlate with better progression outcomes in this sample window."
        : "Current sample does not show clear uplift from suggestion adoption; keep monitoring and adjust selectively.",
    helpful_suggestion_patterns: [
      "Action suggestions that include decision-owner confirmation.",
      "Talk tracks with explicit next checkpoint."
    ],
    ineffective_suggestion_patterns: [
      "Generic reminders without customer-specific blocker handling."
    ],
    rhythm_adjustments: [
      "For high-risk customers, schedule second touch within 2-3 days.",
      "Avoid stacking low-value tasks before critical risk handling."
    ],
    coaching_focus_updates: [
      "Prioritize progression-oriented followups over activity volume.",
      "Close each action with owner + timeline evidence."
    ],
    confidence_score: 0.56,
    uncertainty_notes: ["Fallback based on deterministic metrics without provider inference."]
  };
}
