/**
 * v1.3 Manager Insights Client Service
 */

import type { ManagerInsightsResult } from "@/services/manager-insights-service";

class ManagerInsightsClientService {
  private basePath = "/api/manager/insights";

  async getInsights(periodDays = 7): Promise<ManagerInsightsResult> {
    const url = `${this.basePath}?periodDays=${periodDays}`;
    const response = await fetch(url, { method: "GET", headers: { "Content-Type": "application/json" } });
    if (!response.ok) throw new Error("Failed to load manager insights");
    return response.json() as Promise<ManagerInsightsResult>;
  }
}

export const managerInsightsClientService = new ManagerInsightsClientService();
