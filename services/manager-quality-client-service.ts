import type { QualityPeriodType } from "@/types/quality";

interface ApiPayload<T> {
  success: boolean;
  data: T | null;
  error: string | null;
}

export interface ManagerQualityViewPayload {
  periodType: QualityPeriodType;
  periodStart: string;
  periodEnd: string;
  userRows: Array<{
    userId: string;
    userName: string;
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
  }>;
  aiInsight: {
    executive_summary: string;
    replicable_patterns: string[];
    needs_coaching: Array<{
      user_id: string;
      user_name: string;
      reason: string;
      priority: "low" | "medium" | "high";
    }>;
    management_actions: string[];
    risk_warnings: string[];
  };
  usedFallback: boolean;
}

export const managerQualityClientService = {
  async get(periodType: QualityPeriodType = "weekly"): Promise<ManagerQualityViewPayload> {
    const response = await fetch(`/api/manager/quality?periodType=${periodType}`, {
      method: "GET"
    });
    const payload = (await response.json()) as ApiPayload<ManagerQualityViewPayload>;
    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to load manager quality view");
    }
    return payload.data;
  }
};
