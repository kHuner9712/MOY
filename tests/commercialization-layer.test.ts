import assert from "node:assert/strict";

import { computeTrialReadinessScores, pickLeadAssignmentRule } from "../lib/commercialization";
import {
  buildFallbackGrowthPipelineSummary,
  buildFallbackLeadQualification,
  buildFallbackTrialConversionReview
} from "../lib/commercialization-fallback";
import type { LeadAssignmentRule } from "../types/commercialization";

export function runCommercializationLayerTests(logPass: (name: string) => void): void {
  const fallbackLead = buildFallbackLeadQualification({
    leadSource: "website_trial",
    industryHint: "b2b software",
    teamSizeHint: "20 sales",
    useCaseHint: "Need faster trial onboarding and conversion visibility."
  });
  assert.ok(fallbackLead.fitScore >= 60);
  assert.equal(fallbackLead.suggestedNextActions.length > 0, true);
  logPass("commercialization lead qualification fallback");

  const fallbackTrial = buildFallbackTrialConversionReview({
    activationScore: 72,
    engagementScore: 66,
    readinessScore: 70,
    riskFlags: ["onboarding_incomplete"]
  });
  assert.equal(fallbackTrial.recommendedConversionActions.length > 0, true);
  logPass("commercialization trial conversion fallback");

  const rules: LeadAssignmentRule[] = [
    {
      id: "rule-low-priority",
      orgId: "org-1",
      ruleName: "general",
      sourceFilter: [],
      industryFilter: [],
      teamSizeFilter: [],
      assignToUserId: "u-2",
      priority: 99,
      isActive: true,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString()
    },
    {
      id: "rule-high-priority",
      orgId: "org-1",
      ruleName: "trial-software",
      sourceFilter: ["website_trial"],
      industryFilter: ["software"],
      teamSizeFilter: ["20"],
      assignToUserId: "u-1",
      priority: 1,
      isActive: true,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString()
    }
  ];
  const picked = pickLeadAssignmentRule({
    leadSource: "website_trial",
    industryHint: "B2B Software",
    teamSizeHint: "20 sales",
    rules
  });
  assert.equal(picked?.id, "rule-high-priority");
  logPass("commercialization lead assignment rule matching");

  const readiness = computeTrialReadinessScores({
    onboardingCompleted: true,
    customerCount: 8,
    dealRoomCount: 2,
    briefCount: 4,
    prepCount: 5,
    touchpointCount7d: 8,
    activeUserCount7d: 3
  });
  assert.ok(readiness.readinessScore >= 55);
  assert.ok(readiness.stage === "active_trial" || readiness.stage === "conversion_discussion" || readiness.stage === "verbally_committed");
  logPass("commercialization readiness scoring");

  const growthFallback = buildFallbackGrowthPipelineSummary({
    leadsTotal: 24,
    demoRequested: 15,
    demoCompleted: 9,
    trialRequested: 11,
    trialActivated: 6,
    convertedCount: 2
  });
  assert.equal(growthFallback.nextBestActions.length > 0, true);
  logPass("commercialization growth summary fallback");
}
