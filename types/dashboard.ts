export interface SalesDashboardStats {
  todayPendingFollowups: number;
  weeklyNewCustomers: number;
  keyCustomerReminders: number;
  recentFollowupsCount: number;
  aiSuggestions: string[];
  riskTips: string[];
}

export interface ManagerDashboardStats {
  weeklyNewCustomers: number;
  followupCompletionRate: number;
  overdueCustomers: number;
  topSalesRankings: Array<{
    salesId: string;
    salesName: string;
    newCustomers: number;
    followups: number;
    conversionRate: number;
  }>;
}
