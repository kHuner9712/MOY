import type { ManagerTeamRhythmInsightResult } from "@/types/ai";
import type { TeamRhythmUserRow } from "@/types/work";

interface ApiPayload<T> {
  success: boolean;
  data: T | null;
  error: string | null;
}

export interface ManagerRhythmPayload {
  periodType: "daily" | "weekly";
  periodStart: string;
  periodEnd: string;
  userRows: TeamRhythmUserRow[];
  teamTotals: {
    totalTasks: number;
    doneTasks: number;
    overdueTasks: number;
    criticalOpenTasks: number;
    completionRate: number;
    overdueRate: number;
    prepCoverageRate: number;
    highValueWithoutPrepCount: number;
  };
  unattendedCriticalCustomers: string[];
  overloadedUsers: TeamRhythmUserRow[];
  stableUsers: TeamRhythmUserRow[];
  aiInsight: ManagerTeamRhythmInsightResult;
  usedFallback: boolean;
  runId: string;
}

export const teamRhythmClientService = {
  async get(periodType: "daily" | "weekly" = "daily"): Promise<ManagerRhythmPayload> {
    const response = await fetch(`/api/manager/rhythm?periodType=${periodType}`, {
      method: "GET"
    });
    const payload = (await response.json()) as ApiPayload<ManagerRhythmPayload>;
    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to load manager rhythm view");
    }
    return payload.data;
  },

  async generate(periodType: "daily" | "weekly" = "daily"): Promise<ManagerRhythmPayload> {
    const response = await fetch("/api/manager/rhythm/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ periodType })
    });
    const payload = (await response.json()) as ApiPayload<ManagerRhythmPayload>;
    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to generate manager rhythm insight");
    }
    return payload.data;
  }
};
