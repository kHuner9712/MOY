import type {
  AutomationActionRecommendationResult,
  CustomerHealthSummaryResult,
  ExecutiveBriefSummaryResult,
  RetentionWatchReviewResult
} from "@/types/automation";

export function buildFallbackCustomerHealthSummary(params: {
  customerName: string;
  healthScore: number;
  healthBand: string;
  riskFlags: string[];
  positiveSignals: string[];
}): CustomerHealthSummaryResult {
  return {
    healthSummary: `${params.customerName} health ${params.healthScore}/100 (${params.healthBand}).`,
    riskFlags: params.riskFlags.length > 0 ? params.riskFlags : ["insufficient_signal"],
    positiveSignals: params.positiveSignals,
    recommendedActions: [
      "Confirm next owner action and due date.",
      "Add one external touchpoint within 48 hours.",
      "Review blockers in deal/checkpoint timeline."
    ]
  };
}

export function buildFallbackExecutiveBriefSummary(params: {
  openEvents: number;
  criticalRisks: number;
  trialStalled: number;
  dealBlocked: number;
  renewalAtRisk: number;
}): ExecutiveBriefSummaryResult {
  return {
    headline: `Ops watch: ${params.openEvents} open events, ${params.criticalRisks} critical risks.`,
    topRisks: [
      `Trial stalled: ${params.trialStalled}`,
      `Blocked deals: ${params.dealBlocked}`,
      `Renewal at-risk: ${params.renewalAtRisk}`
    ],
    topOpportunities: ["Prioritize accounts with recent positive signal", "Push one unblock action for each blocked strategic deal"],
    suggestedActions: [
      "Assign explicit owner and due date for each critical event.",
      "Trigger manager check-in for trial/deal with no progress > 3 days.",
      "Review top 5 at-risk accounts and confirm retention move."
    ],
    watchItems: ["Open events backlog", "No recent external touchpoint", "Onboarding completion lag"]
  };
}

export function buildFallbackAutomationActionRecommendation(params: {
  eventType: string;
  severity: "info" | "warning" | "critical";
}): AutomationActionRecommendationResult {
  const urgency = params.severity === "critical" ? "high" : params.severity === "warning" ? "medium" : "low";
  return {
    whyItMatters: `Event ${params.eventType} indicates execution or retention risk that can accumulate quickly if ignored.`,
    suggestedAction: "Create owner task with due date and confirm next external touchpoint.",
    urgency,
    ownerHint: urgency === "high" ? "manager" : "sales_owner"
  };
}

export function buildFallbackRetentionWatchReview(params: {
  atRiskCount: number;
  expansionCount: number;
}): RetentionWatchReviewResult {
  return {
    atRiskCustomers: [`Total at-risk customers: ${params.atRiskCount}`],
    expansionCandidates: [`Total expansion candidates: ${params.expansionCount}`],
    recommendedRetentionMoves: [
      "Run executive check-in for top at-risk customers this week.",
      "Reconfirm success criteria and next milestone in writing.",
      "Set proactive touchpoint cadence for renewal window."
    ],
    recommendedOwnerActions: [
      "Owner: update next action and due date for each watch item.",
      "Manager: review unresolved renewal risks in weekly cockpit."
    ]
  };
}
