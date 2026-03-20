/**
 * v1.3 Manager Insights Hook
 */

import { useState, useEffect, useCallback } from "react";
import { managerInsightsClientService } from "@/services/manager-insights-client-service";
import type { ManagerInsightsResult } from "@/services/manager-insights-service";

export function useManagerInsights(periodDays = 7) {
  const [data, setData] = useState<ManagerInsightsResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await managerInsightsClientService.getInsights(periodDays);
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load manager insights");
    } finally {
      setLoading(false);
    }
  }, [periodDays]);

  useEffect(() => {
    void load();
  }, [load]);

  return { data, loading, error, reload: load };
}
