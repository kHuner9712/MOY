import assert from "node:assert/strict";

import { evaluateAlertRules } from "../lib/alert-rules";
import { matchAutomationRuleTargets, getDefaultAutomationRuleSeeds } from "../lib/automation-ops";
import { decideCaptureApplyMode } from "../lib/capture-flow";
import { computeOutcomeOverview } from "../lib/closed-loop";
import { buildFallbackExecutiveBriefSummary } from "../lib/executive-fallback";
import { computeTaskPriority } from "../lib/task-priority";
import { buildWorkItemDraftFromAlert } from "../lib/work-item-builder";
import type { AlertItem } from "../types/alert";
import type { Customer } from "../types/customer";
import type { FollowupRecord } from "../types/followup";
import type { Opportunity } from "../types/opportunity";

export function runGoldenPathSmokeTests(logPass: (name: string) => void): void {
  const now = new Date("2026-03-18T09:00:00.000Z");
  const tenDaysAgo = new Date(now.getTime() - 10 * 24 * 60 * 60 * 1000).toISOString();
  const tomorrow = new Date(now.getTime() + 24 * 60 * 60 * 1000).toISOString();

  const applyMode = decideCaptureApplyMode({
    shouldCreateFollowup: true,
    hasMatchedCustomer: true,
    extractionConfidence: 0.92,
    matchConfidence: 0.9,
    hasSummary: true,
    hasNextStep: true
  });
  assert.equal(applyMode, "auto");
  logPass("golden path smoke: capture auto apply");

  const customer: Customer = {
    id: "smoke-customer-1",
    customerName: "Golden Manufacturing",
    companyName: "Golden Manufacturing Ltd",
    contactName: "Li Wei",
    phone: "13800138000",
    email: "liwei@golden.example.com",
    sourceChannel: "website",
    stage: "proposal",
    ownerId: "owner-1",
    ownerName: "Alice",
    lastFollowupAt: tenDaysAgo,
    nextFollowupAt: tomorrow,
    winProbability: 82,
    riskLevel: "high",
    tags: ["high_value"],
    aiSummary: "",
    aiSuggestion: "",
    aiRiskJudgement: "",
    stalledDays: 10,
    hasDecisionMaker: false,
    createdAt: tenDaysAgo,
    updatedAt: now.toISOString()
  };

  const followups: FollowupRecord[] = [
    {
      id: "smoke-followup-1",
      customerId: customer.id,
      ownerId: customer.ownerId,
      ownerName: customer.ownerName,
      method: "phone",
      summary: "Customer is interested and asks for quote details.",
      customerNeeds: "Needs phased quote and rollout scope.",
      objections: "Budget approval cycle is long.",
      nextPlan: "Prepare proposal with ROI summary.",
      nextFollowupAt: tomorrow,
      needsAiAnalysis: true,
      sourceInputId: null,
      draftStatus: "confirmed",
      createdAt: tenDaysAgo
    }
  ];

  const opportunities: Opportunity[] = [
    {
      id: "smoke-opportunity-1",
      customerId: customer.id,
      customerName: customer.companyName,
      name: "Annual License Expansion",
      expectedAmount: 180000,
      stage: "proposal",
      ownerId: customer.ownerId,
      ownerName: customer.ownerName,
      lastProgressAt: tenDaysAgo,
      riskLevel: "high",
      closeDate: "2026-04-20"
    }
  ];

  const hits = evaluateAlertRules({
    now,
    customer,
    followups,
    opportunities
  });
  assert.ok(hits.some((item) => item.ruleType === "no_followup_timeout"));
  assert.ok(hits.some((item) => item.ruleType === "quoted_but_stalled"));
  logPass("golden path smoke: risk rules triggered");

  const primaryHit = hits.find((item) => item.ruleType === "no_followup_timeout");
  assert.ok(primaryHit);

  const alert: AlertItem = {
    id: "smoke-alert-1",
    customerId: customer.id,
    customerName: customer.companyName,
    opportunityId: opportunities[0]?.id ?? null,
    ownerId: customer.ownerId,
    ownerName: customer.ownerName,
    ruleType: primaryHit?.ruleType ?? "no_followup_timeout",
    source: primaryHit?.source ?? "rule",
    level: primaryHit?.level ?? "critical",
    status: "open",
    title: primaryHit?.title ?? "No followup timeout",
    message: primaryHit?.description ?? "Customer has no followup for too long.",
    evidence: primaryHit?.evidence ?? ["no_followup_timeout"],
    suggestedOwnerAction: primaryHit?.suggestedOwnerAction ?? ["Contact customer within 24 hours."],
    dueAt: primaryHit?.dueAt ?? tomorrow,
    resolvedAt: null,
    createdAt: now.toISOString(),
    updatedAt: now.toISOString()
  };

  const workDraft = buildWorkItemDraftFromAlert(alert);
  assert.equal(workDraft.sourceType, "alert");
  assert.equal(workDraft.workType, "resolve_alert");

  const priority = computeTaskPriority({
    workType: workDraft.workType,
    customerValueScore: 88,
    riskLevel: customer.riskLevel,
    dueAt: alert.dueAt,
    scheduledFor: now.toISOString(),
    customerStage: customer.stage,
    lastFollowupAt: customer.lastFollowupAt,
    highProbabilityOpportunity: true,
    managerFlagged: true,
    rhythmFit: "good",
    backlogSize: 6
  });
  assert.ok(priority.priorityBand === "high" || priority.priorityBand === "critical");
  logPass("golden path smoke: task generated and prioritized");

  const matchedTargets = matchAutomationRuleTargets({
    ruleKey: "high_risk_customer_inactivity",
    eventTargets: [
      {
        entityType: "customer",
        entityId: customer.id,
        customerId: customer.id,
        severity: "critical",
        eventType: "health_declined",
        summary: alert.title,
        evidence: alert.evidence,
        recommendedAction: alert.suggestedOwnerAction[0] ?? "Run owner follow-up."
      }
    ]
  });
  assert.equal(matchedTargets.length, 1);

  const seed = getDefaultAutomationRuleSeeds().find((item) => item.ruleKey === "high_risk_customer_inactivity");
  assert.ok(seed);
  assert.equal(Boolean(seed?.actionJson.createWorkItem), true);
  assert.equal(Boolean(seed?.actionJson.createManagerCheckin), true);

  const executiveBrief = buildFallbackExecutiveBriefSummary({
    openEvents: matchedTargets.length,
    criticalRisks: 1,
    trialStalled: 0,
    dealBlocked: 0,
    renewalAtRisk: 0
  });
  assert.ok(executiveBrief.headline.length > 0);
  assert.ok(executiveBrief.suggestedActions.length > 0);
  logPass("golden path smoke: manager and executive visibility");

  const outcomeOverview = computeOutcomeOverview({
    outcomes: [
      {
        id: "smoke-outcome-1",
        resultStatus: "positive_progress",
        outcomeType: "followup_result",
        stageChanged: true,
        usedPrepCard: true,
        usedDraft: true
      }
    ],
    adoptions: [
      {
        id: "smoke-adoption-1",
        linkedOutcomeId: "smoke-outcome-1",
        adoptionType: "adopted"
      }
    ]
  });
  assert.equal(outcomeOverview.totalOutcomes, 1);
  assert.equal(outcomeOverview.positiveRate, 1);
  assert.equal(outcomeOverview.adoptionPositiveRate, 1);
  logPass("golden path smoke: customer-visible value outcome");
}
