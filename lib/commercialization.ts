import type { InboundLeadSource, LeadAssignmentRule, TrialConversionStage } from "@/types/commercialization";

function normalize(value: string | null | undefined): string {
  return (value ?? "").trim().toLowerCase();
}

function containsKeyword(value: string, keywords: string[]): boolean {
  if (keywords.length === 0) return true;
  return keywords.some((item) => value.includes(normalize(item)));
}

function matchTeamSizeHint(teamSizeHint: string | null, filters: string[]): boolean {
  if (filters.length === 0) return true;
  const hint = normalize(teamSizeHint);
  if (!hint) return false;
  return filters.some((item) => hint.includes(normalize(item)));
}

export function isLeadRuleMatched(params: {
  leadSource: InboundLeadSource;
  industryHint: string | null;
  teamSizeHint: string | null;
  rule: LeadAssignmentRule;
}): boolean {
  if (!params.rule.isActive) return false;
  if (params.rule.sourceFilter.length > 0 && !params.rule.sourceFilter.includes(params.leadSource)) return false;
  if (!containsKeyword(normalize(params.industryHint), params.rule.industryFilter)) return false;
  if (!matchTeamSizeHint(params.teamSizeHint, params.rule.teamSizeFilter)) return false;
  return true;
}

export function pickLeadAssignmentRule(params: {
  leadSource: InboundLeadSource;
  industryHint: string | null;
  teamSizeHint: string | null;
  rules: LeadAssignmentRule[];
}): LeadAssignmentRule | null {
  const matched = params.rules
    .filter((rule) =>
      isLeadRuleMatched({
        leadSource: params.leadSource,
        industryHint: params.industryHint,
        teamSizeHint: params.teamSizeHint,
        rule
      })
    )
    .sort((a, b) => a.priority - b.priority);

  return matched[0] ?? null;
}

export interface TrialUsageSnapshot {
  onboardingCompleted: boolean;
  customerCount: number;
  dealRoomCount: number;
  briefCount: number;
  prepCount: number;
  touchpointCount7d: number;
  activeUserCount7d: number;
}

export function computeTrialReadinessScores(snapshot: TrialUsageSnapshot): {
  activationScore: number;
  engagementScore: number;
  readinessScore: number;
  stage: TrialConversionStage;
  riskFlags: string[];
} {
  const riskFlags: string[] = [];

  const activationScore = Math.max(
    0,
    Math.min(
      100,
      (snapshot.onboardingCompleted ? 35 : 0) +
        Math.min(snapshot.customerCount * 8, 24) +
        Math.min(snapshot.dealRoomCount * 10, 20) +
        Math.min(snapshot.activeUserCount7d * 5, 21)
    )
  );

  const engagementScore = Math.max(
    0,
    Math.min(
      100,
      Math.min(snapshot.briefCount * 8, 24) +
        Math.min(snapshot.prepCount * 6, 24) +
        Math.min(snapshot.touchpointCount7d * 4, 28) +
        Math.min(snapshot.activeUserCount7d * 8, 24)
    )
  );

  const readinessScore = Math.round(activationScore * 0.45 + engagementScore * 0.55);

  if (!snapshot.onboardingCompleted) riskFlags.push("onboarding_incomplete");
  if (snapshot.customerCount === 0) riskFlags.push("no_customer_data");
  if (snapshot.dealRoomCount === 0) riskFlags.push("no_deal_room");
  if (snapshot.briefCount === 0) riskFlags.push("no_brief_generated");
  if (snapshot.touchpointCount7d === 0) riskFlags.push("no_recent_touchpoint");
  if (snapshot.activeUserCount7d <= 1) riskFlags.push("low_team_activity");

  let stage: TrialConversionStage = "activated";
  if (snapshot.onboardingCompleted) stage = "onboarding_completed";
  if (snapshot.onboardingCompleted && snapshot.customerCount > 0 && snapshot.dealRoomCount > 0) stage = "first_value_seen";
  if (readinessScore >= 55) stage = "active_trial";
  if (readinessScore >= 72) stage = "conversion_discussion";
  if (readinessScore >= 86) stage = "verbally_committed";

  return {
    activationScore,
    engagementScore,
    readinessScore,
    stage,
    riskFlags
  };
}
