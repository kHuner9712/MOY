/**
 * v1.5 Manager Insights Trends Hook
 * 支持 weekly/monthly 切换 + backfill
 */

import { useState, useEffect, useCallback } from "react";
import { managerInsightsTrendsClientService } from "@/services/manager-insights-trends-client-service";
import type { ManagerInsightsTrends, BackfillSnapshotResult } from "@/types/manager-insights-snapshot";

export function useManagerInsightsTrends(snapshotType: "weekly" | "monthly" = "weekly", periods = 8) {
  const [data, setData] = useState<ManagerInsightsTrends | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const [backfilling, setBackfilling] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await managerInsightsTrendsClientService.getTrends(snapshotType, periods);
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load trends");
    } finally {
      setLoading(false);
    }
  }, [snapshotType, periods]);

  useEffect(() => {
    void load();
  }, [load]);

  const generateSnapshot = useCallback(async (periodDays = 7) => {
    setGenerating(true);
    try {
      await managerInsightsTrendsClientService.generateSnapshot(periodDays, snapshotType);
      await load();
    } finally {
      setGenerating(false);
    }
  }, [load, snapshotType]);

  const backfillSnapshots = useCallback(async (periodsToBackfill = 8): Promise<BackfillSnapshotResult | null> => {
    setBackfilling(true);
    try {
      const result = await managerInsightsTrendsClientService.backfillSnapshots(snapshotType, periodsToBackfill);
      await load();
      return result;
    } finally {
      setBackfilling(false);
    }
  }, [load, snapshotType]);

  return { data, loading, error, reload: load, generateSnapshot, generating, backfillSnapshots, backfilling };
}
