export type AlertLevel = "info" | "warning" | "critical";
export type AlertStatus = "open" | "watching" | "resolved";
export type AlertSource = "rule" | "ai" | "hybrid" | "fallback";

/**
 * Keep both legacy and phase-3 rule names for backward compatibility.
 * New writes should prefer the newer names:
 * - no_followup_timeout
 * - positive_reply_but_no_progress
 * - no_decision_maker
 */
export type AlertRuleType =
  | "no_followup_overdue"
  | "active_response_no_quote"
  | "missing_decision_maker"
  | "quoted_but_stalled"
  | "high_probability_stalled"
  | "no_followup_timeout"
  | "positive_reply_but_no_progress"
  | "no_decision_maker"
  | "ai_detected";

export interface AlertItem {
  id: string;
  customerId: string;
  customerName: string;
  opportunityId?: string | null;
  ownerId: string;
  ownerName: string;
  ruleType: AlertRuleType;
  source: AlertSource;
  level: AlertLevel;
  status: AlertStatus;
  title: string;
  message: string;
  evidence: string[];
  suggestedOwnerAction: string[];
  dueAt: string | null;
  resolvedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface AlertRuleHit {
  ruleType: AlertRuleType;
  source: AlertSource;
  level: AlertLevel;
  title: string;
  description: string;
  evidence: string[];
  suggestedOwnerAction: string[];
  dueAt: string | null;
}
