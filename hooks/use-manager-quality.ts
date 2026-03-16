"use client";

import { useCallback, useEffect, useState } from "react";

import { managerQualityClientService, type ManagerQualityViewPayload } from "@/services/manager-quality-client-service";
import type { QualityPeriodType } from "@/types/quality";

export function useManagerQuality(initialPeriod: QualityPeriodType = "weekly") {
  const [periodType, setPeriodType] = useState<QualityPeriodType>(initialPeriod);
  const [data, setData] = useState<ManagerQualityViewPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (period = periodType) => {
    setLoading(true);
    setError(null);
    try {
      const result = await managerQualityClientService.get(period);
      setData(result);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to load manager quality");
    } finally {
      setLoading(false);
    }
  }, [periodType]);

  useEffect(() => {
    void load(periodType);
  }, [periodType, load]);

  return {
    periodType,
    setPeriodType,
    data,
    loading,
    error,
    reload: load
  };
}
