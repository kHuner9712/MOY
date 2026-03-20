/**
 * v1.5 Manager Insights Trends Client Service
 */

import type { ManagerInsightsTrends, BackfillSnapshotResult } from "@/types/manager-insights-snapshot";
import type { CreateSnapshotResult } from "@/types/manager-insights-snapshot";

class ManagerInsightsTrendsClientService {
  async getTrends(snapshotType = "weekly", periods = 8): Promise<ManagerInsightsTrends> {
    const url = `/api/manager/insights/trends?snapshotType=${snapshotType}&periods=${periods}`;
    const response = await fetch(url, { method: "GET", headers: { "Content-Type": "application/json" } });
    if (!response.ok) throw new Error("Failed to load trends");
    return response.json() as Promise<ManagerInsightsTrends>;
  }

  async generateSnapshot(periodDays = 7, snapshotType = "weekly"): Promise<CreateSnapshotResult> {
    const response = await fetch("/api/manager/insights/snapshots", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ periodDays, snapshotType })
    });
    if (!response.ok) throw new Error("Failed to generate snapshot");
    return response.json() as Promise<CreateSnapshotResult>;
  }

  async backfillSnapshots(snapshotType = "weekly", periodsToBackfill = 8): Promise<BackfillSnapshotResult> {
    const response = await fetch("/api/manager/insights/snapshots/backfill", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ snapshotType, periodsToBackfill })
    });
    if (!response.ok) throw new Error("Failed to backfill snapshots");
    return response.json() as Promise<BackfillSnapshotResult>;
  }
}

export const managerInsightsTrendsClientService = new ManagerInsightsTrendsClientService();
