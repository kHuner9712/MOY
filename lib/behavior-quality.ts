export interface QualityCustomer {
  id: string;
  stage: string;
  winProbability: number;
  lastFollowupAt: string;
  nextFollowupAt: string;
}

export interface QualityFollowup {
  id: string;
  customerId: string;
  createdAt: string;
  summary: string;
  customerNeeds: string;
  objections: string;
  nextPlan: string;
  nextFollowupAt: string | null;
  draftStatus: "draft" | "confirmed";
  aiSummary?: string | null;
}

export interface QualityOpportunity {
  id: string;
  customerId: string;
  stage: string;
  updatedAt: string;
}

export interface QualityAlert {
  id: string;
  customerId: string | null;
  severity: "info" | "warning" | "critical";
  status: "open" | "watching" | "resolved";
  createdAt: string;
  updatedAt: string;
}

export interface QualityMetricsResult {
  assignedCustomerCount: number;
  activeCustomerCount: number;
  followupCount: number;
  onTimeFollowupRate: number;
  overdueFollowupRate: number;
  followupCompletenessScore: number;
  stageProgressionScore: number;
  riskResponseScore: number;
  highValueFocusScore: number;
  activityQualityScore: number;
  shallowActivityRatio: number;
  stalledCustomerCount: number;
  highRiskUnhandledCount: number;
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function daysBetween(a: Date, b: Date): number {
  const diff = Math.abs(a.getTime() - b.getTime());
  return Math.floor(diff / (24 * 60 * 60 * 1000));
}

function withinRange(iso: string, start: Date, end: Date): boolean {
  const time = new Date(iso).getTime();
  return time >= start.getTime() && time <= end.getTime();
}

export function calculateFollowupCompletenessScore(followups: QualityFollowup[]): number {
  if (followups.length === 0) return 0;
  const total = followups.reduce((sum, item) => {
    let score = 0;
    if (item.summary.trim().length >= 12) score += 20;
    if (item.customerNeeds.trim().length >= 6) score += 20;
    if (item.objections.trim().length >= 2) score += 15;
    if (item.nextPlan.trim().length >= 6) score += 20;
    if (item.nextFollowupAt) score += 15;
    if (item.draftStatus === "confirmed") score += 10;
    return sum + score;
  }, 0);
  return clamp(Number((total / followups.length).toFixed(2)), 0, 100);
}

export function isShallowFollowup(item: QualityFollowup): boolean {
  const weakSummary = item.summary.trim().length < 12;
  const weakPlan = item.nextPlan.trim().length < 6;
  const weakNeeds = item.customerNeeds.trim().length < 4;
  const noNextFollowup = !item.nextFollowupAt;
  const draftNotConfirmed = item.draftStatus !== "confirmed";
  return weakSummary || weakPlan || weakNeeds || noNextFollowup || draftNotConfirmed;
}

export function calculateShallowActivityRatio(followups: QualityFollowup[]): number {
  if (followups.length === 0) return 0;
  const shallowCount = followups.filter(isShallowFollowup).length;
  return clamp(Number((shallowCount / followups.length).toFixed(4)), 0, 1);
}

function calculateOnTimeRate(followups: QualityFollowup[]): number {
  if (followups.length === 0) return 0;
  let onTime = 0;
  for (const item of followups) {
    if (!item.nextFollowupAt) continue;
    const created = new Date(item.createdAt);
    const next = new Date(item.nextFollowupAt);
    if (created.getTime() <= next.getTime()) onTime += 1;
  }
  return clamp(Number((onTime / followups.length).toFixed(4)), 0, 1);
}

function calculateStageProgressionScore(params: {
  customers: QualityCustomer[];
  opportunities: QualityOpportunity[];
  periodStart: Date;
  periodEnd: Date;
}): number {
  if (params.customers.length === 0) return 0;
  const activeCustomerIds = new Set(params.customers.filter((item) => item.stage !== "won" && item.stage !== "lost").map((item) => item.id));
  if (activeCustomerIds.size === 0) return 0;

  const progressedCustomerIds = new Set(
    params.opportunities
      .filter((item) => activeCustomerIds.has(item.customerId))
      .filter((item) => withinRange(item.updatedAt, params.periodStart, params.periodEnd))
      .filter((item) => !["discovery", "qualification"].includes(item.stage))
      .map((item) => item.customerId)
  );

  return clamp(Number(((progressedCustomerIds.size / activeCustomerIds.size) * 100).toFixed(2)), 0, 100);
}

function calculateRiskResponseScore(alerts: QualityAlert[]): number {
  const highRisk = alerts.filter((item) => item.severity === "critical" || item.severity === "warning");
  if (highRisk.length === 0) return 100;

  let handled = 0;
  for (const item of highRisk) {
    if (item.status === "resolved" || item.status === "watching") {
      const created = new Date(item.createdAt);
      const updated = new Date(item.updatedAt);
      if (daysBetween(created, updated) <= 3) handled += 1;
    }
  }
  return clamp(Number(((handled / highRisk.length) * 100).toFixed(2)), 0, 100);
}

function calculateHighValueFocusScore(params: {
  customers: QualityCustomer[];
  followups: QualityFollowup[];
}): number {
  const highValueIds = new Set(params.customers.filter((item) => item.winProbability >= 70).map((item) => item.id));
  if (highValueIds.size === 0) return 60;
  const focusedCount = params.followups.filter((item) => highValueIds.has(item.customerId)).length;
  const baseline = Math.max(1, params.followups.length);
  return clamp(Number(((focusedCount / baseline) * 100).toFixed(2)), 0, 100);
}

function countStalledCustomers(customers: QualityCustomer[], now: Date): number {
  return customers.filter((item) => {
    if (item.stage === "won" || item.stage === "lost") return false;
    const last = new Date(item.lastFollowupAt);
    return daysBetween(now, last) >= 7;
  }).length;
}

function countUnhandledHighRisk(alerts: QualityAlert[]): number {
  return alerts.filter((item) => item.severity === "critical" && item.status !== "resolved").length;
}

export function computeBehaviorQualityMetrics(params: {
  periodStart: string;
  periodEnd: string;
  customers: QualityCustomer[];
  followups: QualityFollowup[];
  opportunities: QualityOpportunity[];
  alerts: QualityAlert[];
}): QualityMetricsResult {
  const start = new Date(`${params.periodStart}T00:00:00.000Z`);
  const end = new Date(`${params.periodEnd}T23:59:59.999Z`);
  const now = new Date();

  const periodFollowups = params.followups.filter((item) => withinRange(item.createdAt, start, end));
  const assignedCustomerCount = params.customers.length;
  const activeCustomerCount = params.customers.filter((item) => item.stage !== "won" && item.stage !== "lost").length;
  const followupCount = periodFollowups.length;

  const onTimeRate = calculateOnTimeRate(periodFollowups);
  const overdueRate = clamp(Number((1 - onTimeRate).toFixed(4)), 0, 1);
  const completenessScore = calculateFollowupCompletenessScore(periodFollowups);
  const shallowRatio = calculateShallowActivityRatio(periodFollowups);
  const stageProgressScore = calculateStageProgressionScore({
    customers: params.customers,
    opportunities: params.opportunities,
    periodStart: start,
    periodEnd: end
  });
  const riskResponseScore = calculateRiskResponseScore(params.alerts);
  const highValueFocusScore = calculateHighValueFocusScore({
    customers: params.customers,
    followups: periodFollowups
  });

  const activityQualityScore = clamp(
    Number(
      (
        completenessScore * 0.25 +
        stageProgressScore * 0.2 +
        riskResponseScore * 0.2 +
        highValueFocusScore * 0.2 +
        (1 - shallowRatio) * 100 * 0.15
      ).toFixed(2)
    ),
    0,
    100
  );

  const stalledCustomerCount = countStalledCustomers(params.customers, now);
  const highRiskUnhandledCount = countUnhandledHighRisk(params.alerts);

  return {
    assignedCustomerCount,
    activeCustomerCount,
    followupCount,
    onTimeFollowupRate: onTimeRate,
    overdueFollowupRate: overdueRate,
    followupCompletenessScore: completenessScore,
    stageProgressionScore: stageProgressScore,
    riskResponseScore,
    highValueFocusScore,
    activityQualityScore,
    shallowActivityRatio: shallowRatio,
    stalledCustomerCount,
    highRiskUnhandledCount
  };
}
