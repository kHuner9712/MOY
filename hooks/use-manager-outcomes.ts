"use client";

import { useCallback, useEffect, useState } from "react";

import { managerOutcomeClientService, type ManagerOutcomePayload } from "@/services/manager-outcome-client-service";

export function useManagerOutcomes(initialPeriod: "weekly" | "monthly" = "weekly", enabled = true) {
  const [periodType, setPeriodType] = useState<"weekly" | "monthly">(initialPeriod);
  const [data, setData] = useState<ManagerOutcomePayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (period = periodType) => {
      setLoading(true);
      setError(null);
      try {
        const result = await managerOutcomeClientService.get(period);
        setData(result);
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "Failed to load manager outcomes");
      } finally {
        setLoading(false);
      }
    },
    [periodType]
  );

  const generateReview = useCallback(
    async (input?: {
      reviewScope?: "team" | "org" | "user";
      targetUserId?: string;
      periodStart?: string;
      periodEnd?: string;
    }) => {
      setLoading(true);
      setError(null);
      try {
        const result = await managerOutcomeClientService.generateReview(input);
        await load(periodType);
        return result;
      } catch (cause) {
        const text = cause instanceof Error ? cause.message : "Failed to generate outcome review";
        setError(text);
        throw cause;
      } finally {
        setLoading(false);
      }
    },
    [load, periodType]
  );

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    void load(periodType);
  }, [enabled, load, periodType]);

  return {
    periodType,
    setPeriodType,
    data,
    loading,
    error,
    reload: load,
    generateReview
  };
}
