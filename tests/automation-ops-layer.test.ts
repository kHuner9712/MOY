import assert from "node:assert/strict";

import {
  getBusinessEventDedupeKey,
  getDefaultAutomationRuleSeeds,
  isBusinessEventStatusTransitionAllowed,
  matchAutomationRuleTargets,
  scoreToHealthBand,
  type RuleMatchTarget
} from "../lib/automation-ops";
import {
  buildFallbackAutomationActionRecommendation,
  buildFallbackCustomerHealthSummary,
  buildFallbackExecutiveBriefSummary
} from "../lib/executive-fallback";
import { inferRenewalStatus } from "../lib/renewal-watch";

export function runAutomationOpsLayerTests(logPass: (name: string) => void): void {
  const targets: RuleMatchTarget[] = [
    {
      entityType: "customer",
      entityId: "c-1",
      customerId: "c-1",
      severity: "critical",
      eventType: "health_declined",
      summary: "health declined",
      evidence: ["band=critical"],
      recommendedAction: "Run owner check-in."
    },
    {
      entityType: "deal_room",
      entityId: "d-1",
      dealRoomId: "d-1",
      severity: "warning",
      eventType: "no_recent_touchpoint",
      summary: "no touchpoint",
      evidence: ["touchpoint_missing"],
      recommendedAction: "Create outbound task."
    },
    {
      entityType: "deal_room",
      entityId: "d-2",
      dealRoomId: "d-2",
      severity: "critical",
      eventType: "deal_blocked",
      summary: "checkpoint blocked",
      evidence: ["blocked_checkpoint"],
      recommendedAction: "Escalate."
    }
  ];

  const matchedHighRisk = matchAutomationRuleTargets({
    ruleKey: "high_risk_customer_inactivity",
    eventTargets: targets
  });
  assert.equal(matchedHighRisk.length, 1);
  assert.equal(matchedHighRisk[0].eventType, "health_declined");
  logPass("automation rule matching - high risk inactivity");

  const matchedDealBlocked = matchAutomationRuleTargets({
    ruleKey: "blocked_checkpoint_timeout",
    eventTargets: targets
  });
  assert.equal(matchedDealBlocked.length, 1);
  assert.equal(matchedDealBlocked[0].eventType, "deal_blocked");
  logPass("automation rule matching - blocked checkpoint");

  const key = getBusinessEventDedupeKey({
    entityType: "customer",
    entityId: "c-1",
    eventType: "health_declined"
  });
  assert.equal(key, "customer:c-1:health_declined");
  assert.equal(isBusinessEventStatusTransitionAllowed("open", "acknowledged"), true);
  assert.equal(isBusinessEventStatusTransitionAllowed("acknowledged", "resolved"), true);
  assert.equal(isBusinessEventStatusTransitionAllowed("resolved", "acknowledged"), false);
  logPass("business event dedupe and status flow");

  const fallbackHealth = buildFallbackCustomerHealthSummary({
    customerName: "Demo Co",
    healthScore: 44,
    healthBand: "at_risk",
    riskFlags: ["followup_inactive"],
    positiveSignals: ["pipeline_stage_progressed"]
  });
  assert.ok(fallbackHealth.healthSummary.includes("Demo Co"));
  assert.ok(fallbackHealth.recommendedActions.length > 0);
  logPass("customer health fallback summary");

  const fallbackBrief = buildFallbackExecutiveBriefSummary({
    openEvents: 12,
    criticalRisks: 3,
    trialStalled: 2,
    dealBlocked: 1,
    renewalAtRisk: 4
  });
  assert.ok(fallbackBrief.headline.length > 0);
  assert.ok(fallbackBrief.topRisks.length > 0);
  assert.ok(fallbackBrief.suggestedActions.length > 0);
  logPass("executive brief fallback summary");

  const actionFallback = buildFallbackAutomationActionRecommendation({
    eventType: "renewal_risk_detected",
    severity: "critical"
  });
  assert.equal(actionFallback.urgency, "high");
  assert.ok(actionFallback.suggestedAction.length > 0);
  logPass("automation action fallback recommendation");

  const seeds = getDefaultAutomationRuleSeeds();
  const blockedSeed = seeds.find((item) => item.ruleKey === "blocked_checkpoint_timeout");
  assert.ok(blockedSeed);
  assert.equal(Boolean(blockedSeed?.actionJson.createWorkItem), true);
  assert.equal(Boolean(blockedSeed?.actionJson.createInterventionRequest), true);

  const trialSeed = seeds.find((item) => item.ruleKey === "trial_activated_no_first_value");
  assert.ok(trialSeed);
  assert.equal(Boolean(trialSeed?.actionJson.createExecutiveBrief), true);
  logPass("rule linkage seed actions");

  const renewalDueSoon = inferRenewalStatus({
    healthBand: "healthy",
    overallHealthScore: 80,
    renewalDueAt: new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString()
  });
  assert.equal(renewalDueSoon, "due_soon");

  const renewalAtRisk = inferRenewalStatus({
    healthBand: "critical",
    overallHealthScore: 30,
    renewalDueAt: null
  });
  assert.equal(renewalAtRisk, "at_risk");

  const renewalExpansion = inferRenewalStatus({
    healthBand: "healthy",
    overallHealthScore: 86,
    renewalDueAt: null
  });
  assert.equal(renewalExpansion, "expansion_candidate");
  logPass("renewal watch basic derivation");

  assert.equal(scoreToHealthBand(80), "healthy");
  assert.equal(scoreToHealthBand(60), "watch");
  assert.equal(scoreToHealthBand(40), "at_risk");
  assert.equal(scoreToHealthBand(20), "critical");
  logPass("health score to band");
}
