import type {
  AutomationRuleSeed,
  AutomationRuleSeverity,
  BusinessEventStatus,
  BusinessEventType,
  CustomerHealthBand
} from "@/types/automation";

export interface RuleMatchTarget {
  entityType: "customer" | "opportunity" | "deal_room" | "trial_org" | "work_item" | "onboarding_run" | "touchpoint";
  entityId: string;
  ownerId?: string | null;
  customerId?: string | null;
  dealRoomId?: string | null;
  severity: AutomationRuleSeverity;
  eventType: BusinessEventType;
  summary: string;
  evidence: string[];
  recommendedAction: string;
}

export function getDefaultAutomationRuleSeeds(): AutomationRuleSeed[] {
  return [
    {
      ruleKey: "high_risk_customer_inactivity",
      ruleName: "High-risk customer no follow-up for N days",
      ruleScope: "customer_health",
      triggerType: "inactivity",
      severity: "critical",
      conditionsJson: { daysWithoutFollowup: 5, riskLevelIn: ["high"] },
      actionJson: { createWorkItem: true, workType: "followup_call", createManagerCheckin: true }
    },
    {
      ruleKey: "quoted_no_reply",
      ruleName: "No external reply N days after quote",
      ruleScope: "external_touchpoint",
      triggerType: "inactivity",
      severity: "warning",
      conditionsJson: { daysAfterQuoteNoReply: 7 },
      actionJson: { createWorkItem: true, workType: "send_quote" }
    },
    {
      ruleKey: "trial_activated_no_first_value",
      ruleName: "Trial activated but no first-value signal",
      ruleScope: "trial_conversion",
      triggerType: "missing_step",
      severity: "critical",
      conditionsJson: { daysAfterActivation: 7 },
      actionJson: { createWorkItem: true, workType: "manager_checkin", createExecutiveBrief: true }
    },
    {
      ruleKey: "onboarding_stuck",
      ruleName: "Onboarding stuck for N days",
      ruleScope: "onboarding",
      triggerType: "inactivity",
      severity: "warning",
      conditionsJson: { onboardingStuckDays: 5 },
      actionJson: { createWorkItem: true, workType: "review_customer" }
    },
    {
      ruleKey: "blocked_checkpoint_timeout",
      ruleName: "Blocked checkpoint over N days",
      ruleScope: "deal_progress",
      triggerType: "threshold",
      severity: "critical",
      conditionsJson: { blockedDays: 3 },
      actionJson: { createWorkItem: true, workType: "revive_stalled_deal", createInterventionRequest: true }
    },
    {
      ruleKey: "high_priority_deal_no_touchpoint",
      ruleName: "High-priority deal with no recent external touchpoint",
      ruleScope: "external_touchpoint",
      triggerType: "inactivity",
      severity: "warning",
      conditionsJson: { touchpointMissingDays: 5, dealPriorityIn: ["strategic", "critical"] },
      actionJson: { createWorkItem: true, workType: "followup_call" }
    },
    {
      ruleKey: "renewal_activity_decline",
      ruleName: "Renewal customer health dropped to watch/at-risk",
      ruleScope: "retention",
      triggerType: "health_score",
      severity: "critical",
      conditionsJson: { healthBandIn: ["watch", "at_risk", "critical"] },
      actionJson: { createWorkItem: true, workType: "manager_checkin", updateRenewalWatch: true }
    },
    {
      ruleKey: "high_value_customer_no_next_action",
      ruleName: "High-value customer with no owner next action",
      ruleScope: "customer_health",
      triggerType: "missing_step",
      severity: "warning",
      conditionsJson: { minWinProbability: 70 },
      actionJson: { createWorkItem: true, workType: "review_customer" }
    },
    {
      ruleKey: "manager_attention_no_new_action",
      ruleName: "Manager-attention deal without fresh action",
      ruleScope: "manager_attention",
      triggerType: "inactivity",
      severity: "warning",
      conditionsJson: { staleDays: 3 },
      actionJson: { createWorkItem: true, workType: "manager_checkin", createInterventionRequest: true }
    },
    {
      ruleKey: "trial_org_no_core_activity",
      ruleName: "Trial org has no today/briefing/deal activity",
      ruleScope: "trial_conversion",
      triggerType: "missing_step",
      severity: "warning",
      conditionsJson: { inactivityDays: 5 },
      actionJson: { createWorkItem: true, workType: "manager_checkin", createExecutiveBrief: true }
    }
  ];
}

export function isBusinessEventStatusTransitionAllowed(from: BusinessEventStatus, to: BusinessEventStatus): boolean {
  if (from === to) return true;
  const map: Record<BusinessEventStatus, BusinessEventStatus[]> = {
    open: ["acknowledged", "resolved", "ignored"],
    acknowledged: ["resolved", "ignored", "open"],
    resolved: ["open"],
    ignored: ["open", "resolved"]
  };
  return map[from].includes(to);
}

export function scoreToHealthBand(score: number): CustomerHealthBand {
  if (score >= 75) return "healthy";
  if (score >= 55) return "watch";
  if (score >= 35) return "at_risk";
  return "critical";
}

export function getBusinessEventDedupeKey(params: {
  entityType: RuleMatchTarget["entityType"];
  entityId: string;
  eventType: BusinessEventType;
}): string {
  return `${params.entityType}:${params.entityId}:${params.eventType}`;
}

export function matchAutomationRuleTargets(params: {
  ruleKey: string;
  eventTargets: RuleMatchTarget[];
}): RuleMatchTarget[] {
  return params.eventTargets.filter((item) => {
    switch (params.ruleKey) {
      case "high_risk_customer_inactivity":
        return item.eventType === "health_declined" && item.severity === "critical";
      case "quoted_no_reply":
        return item.eventType === "no_recent_touchpoint";
      case "trial_activated_no_first_value":
        return item.eventType === "trial_stalled";
      case "onboarding_stuck":
        return item.eventType === "onboarding_stuck";
      case "blocked_checkpoint_timeout":
        return item.eventType === "deal_blocked";
      case "high_priority_deal_no_touchpoint":
        return item.eventType === "no_recent_touchpoint";
      case "renewal_activity_decline":
        return item.eventType === "renewal_risk_detected";
      case "high_value_customer_no_next_action":
        return item.eventType === "health_declined";
      case "manager_attention_no_new_action":
        return item.eventType === "manager_attention_escalated";
      case "trial_org_no_core_activity":
        return item.eventType === "trial_stalled" || item.eventType === "onboarding_stuck";
      default:
        return false;
    }
  });
}
