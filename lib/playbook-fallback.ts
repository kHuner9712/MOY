import type { PlaybookCompileResult } from "@/types/ai";

export function buildFallbackPlaybook(params: {
  title: string;
  playbookType: PlaybookCompileResult["playbook_type"];
  effectivePatternHints: string[];
  ineffectivePatternHints: string[];
}): PlaybookCompileResult {
  return {
    playbook_type: params.playbookType,
    title: params.title,
    summary: "Fallback playbook generated from deterministic pattern aggregation.",
    confidence_score: 0.58,
    applicability_notes: "Use as baseline guidance and validate with new outcomes weekly.",
    entries: [
      {
        entry_title: "Recommended baseline action",
        entry_summary: params.effectivePatternHints[0] ?? "Prioritize clear next-step closure after each action.",
        conditions: {
          sample_size: "limited"
        },
        recommended_actions: params.effectivePatternHints.length > 0
          ? params.effectivePatternHints.slice(0, 4)
          : ["Confirm owner + deadline in every followup", "Escalate decision-maker clarity gaps early"],
        caution_notes: params.ineffectivePatternHints.length > 0
          ? params.ineffectivePatternHints.slice(0, 3)
          : ["Avoid repeated shallow followups without stage progression"],
        evidence_snapshot: {
          source: "rule_fallback"
        },
        success_signal: {
          signal: "next_step_confirmed"
        },
        failure_modes: params.ineffectivePatternHints.length > 0
          ? params.ineffectivePatternHints.slice(0, 4)
          : ["No decision owner", "No dated next step"],
        confidence_score: 0.58,
        sort_order: 100
      }
    ]
  };
}
