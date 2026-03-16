import type { AlertItem } from "@/types/alert";
import type { Customer, CustomerStage } from "@/types/customer";
import type { FollowupRecord } from "@/types/followup";
import type { Opportunity, OpportunityStage } from "@/types/opportunity";

function isWithinDays(isoText: string, days: number): boolean {
  const diff = Date.now() - new Date(isoText).getTime();
  return diff <= days * 24 * 60 * 60 * 1000;
}

function isActiveCustomer(customer: Customer): boolean {
  return customer.stage !== "won" && customer.stage !== "lost";
}

export function getSalesDashboardData({
  userId,
  customers,
  followups,
  alerts
}: {
  userId: string;
  customers: Customer[];
  followups: FollowupRecord[];
  alerts: AlertItem[];
}): {
  todayPendingFollowups: number;
  weeklyNewCustomers: number;
  keyCustomers: Customer[];
  recentFollowups: FollowupRecord[];
  aiSuggestions: string[];
  riskAlerts: AlertItem[];
} {
  const scopedCustomers = customers.filter((item) => item.ownerId === userId);
  const scopedFollowups = followups.filter((item) => item.ownerId === userId && item.draftStatus === "confirmed");
  const scopedAlerts = alerts.filter((item) => item.status !== "resolved");

  const keyCustomers = [...scopedCustomers]
    .filter(isActiveCustomer)
    .sort((a, b) => b.winProbability - a.winProbability)
    .slice(0, 4);

  const aiSuggestions = keyCustomers
    .map((item) => item.aiSuggestion)
    .filter((item) => Boolean(item?.trim()))
    .slice(0, 3);

  return {
    todayPendingFollowups: scopedCustomers.filter((item) => isActiveCustomer(item) && new Date(item.nextFollowupAt) <= new Date()).length,
    weeklyNewCustomers: scopedCustomers.filter((item) => isWithinDays(item.createdAt, 7)).length,
    keyCustomers,
    recentFollowups: [...scopedFollowups].sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime()).slice(0, 6),
    aiSuggestions: aiSuggestions.length > 0 ? aiSuggestions : ["当前暂无 AI 建议，先补充更多跟进记录。"],
    riskAlerts: scopedAlerts.slice(0, 5)
  };
}

export function getManagerDashboardData({
  customers,
  followups,
  opportunities,
  alerts
}: {
  customers: Customer[];
  followups: FollowupRecord[];
  opportunities: Opportunity[];
  alerts: AlertItem[];
}): {
  weeklyNewCustomers: number;
  followupCompletionRate: number;
  overdueCustomers: Customer[];
  opportunityStageDist: Array<{ stage: OpportunityStage; count: number }>;
  topSalesRankings: Array<{
    salesId: string;
    salesName: string;
    newCustomers: number;
    followups: number;
    conversionRate: number;
  }>;
  highRiskOpportunities: Opportunity[];
  salesWorkload: Array<{
    salesName: string;
    customerCount: number;
    followupsLast7Days: number;
  }>;
  longTimeNoFollowups: Customer[];
  highRiskCustomers: Customer[];
} {
  const weeklyNewCustomers = customers.filter((item) => isWithinDays(item.createdAt, 7)).length;
  const activeCustomers = customers.filter(isActiveCustomer);

  const customersWithRecentFollowup = new Set(
    followups.filter((item) => item.draftStatus === "confirmed" && isWithinDays(item.createdAt, 7)).map((item) => item.customerId)
  );
  const followupCompletionRate =
    activeCustomers.length === 0 ? 0 : Math.round((customersWithRecentFollowup.size / activeCustomers.length) * 100);

  const stageMap = opportunities.reduce<Record<OpportunityStage, number>>(
    (acc, item) => {
      acc[item.stage] += 1;
      return acc;
    },
    {
      discovery: 0,
      qualification: 0,
      proposal: 0,
      business_review: 0,
      negotiation: 0,
      won: 0,
      lost: 0
    }
  );

  const opportunityStageDist = Object.entries(stageMap).map(([stage, count]) => ({
    stage: stage as OpportunityStage,
    count
  }));

  const highRiskOpportunities = opportunities
    .filter((item) => item.riskLevel === "high" && item.stage !== "won" && item.stage !== "lost")
    .sort((a, b) => new Date(a.lastProgressAt).getTime() - new Date(b.lastProgressAt).getTime())
    .slice(0, 8);

  const owners = Array.from(new Map(customers.map((item) => [item.ownerId, item.ownerName])).entries());

  const salesWorkload = owners.map(([ownerId, ownerName]) => ({
    salesName: ownerName,
    customerCount: customers.filter((item) => item.ownerId === ownerId).length,
    followupsLast7Days: followups.filter((item) => item.ownerId === ownerId && item.draftStatus === "confirmed" && isWithinDays(item.createdAt, 7)).length
  }));

  const topSalesRankings = owners
    .map(([ownerId, ownerName]) => {
      const ownerNewCustomers = customers.filter((item) => item.ownerId === ownerId && isWithinDays(item.createdAt, 7)).length;
      const ownerFollowups = followups.filter((item) => item.ownerId === ownerId && item.draftStatus === "confirmed" && isWithinDays(item.createdAt, 7)).length;
      const ownerOpportunities = opportunities.filter((item) => item.ownerId === ownerId);
      const ownerWon = ownerOpportunities.filter((item) => item.stage === "won").length;
      const conversionRate = ownerOpportunities.length === 0 ? 0 : Math.round((ownerWon / ownerOpportunities.length) * 100);
      return {
        salesId: ownerId,
        salesName: ownerName,
        newCustomers: ownerNewCustomers,
        followups: ownerFollowups,
        conversionRate
      };
    })
    .sort((a, b) => b.conversionRate - a.conversionRate || b.followups - a.followups);

  const overdueCustomers = activeCustomers
    .filter((item) => new Date(item.nextFollowupAt) < new Date())
    .sort((a, b) => new Date(a.nextFollowupAt).getTime() - new Date(b.nextFollowupAt).getTime())
    .slice(0, 8);

  const longTimeNoFollowups = activeCustomers
    .filter((item) => item.stalledDays >= 10)
    .sort((a, b) => b.stalledDays - a.stalledDays);

  const highRiskCustomers = activeCustomers
    .filter((item) => item.riskLevel === "high")
    .sort((a, b) => b.winProbability - a.winProbability)
    .slice(0, 10);

  const extraHighRiskFromAlerts = alerts.filter((item) => item.level === "critical" && item.status !== "resolved");
  if (extraHighRiskFromAlerts.length > 0 && highRiskCustomers.length === 0) {
    const customerIds = new Set(extraHighRiskFromAlerts.map((item) => item.customerId));
    highRiskCustomers.push(...activeCustomers.filter((item) => customerIds.has(item.id)).slice(0, 5));
  }

  return {
    weeklyNewCustomers,
    followupCompletionRate,
    overdueCustomers,
    opportunityStageDist,
    topSalesRankings,
    highRiskOpportunities,
    salesWorkload,
    longTimeNoFollowups,
    highRiskCustomers
  };
}

export function getStageStats(customers: Customer[]): Array<{ stage: CustomerStage; count: number }> {
  const map: Record<CustomerStage, number> = {
    lead: 0,
    initial_contact: 0,
    needs_confirmed: 0,
    proposal: 0,
    negotiation: 0,
    won: 0,
    lost: 0
  };
  customers.forEach((item) => {
    map[item.stage] += 1;
  });
  return Object.entries(map).map(([stage, count]) => ({
    stage: stage as CustomerStage,
    count
  }));
}
