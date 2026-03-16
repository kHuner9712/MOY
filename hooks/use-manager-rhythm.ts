"use client";

import { useCallback, useEffect, useState } from "react";

import { teamRhythmClientService, type ManagerRhythmPayload } from "@/services/team-rhythm-client-service";

export function useManagerRhythm(initialPeriod: "daily" | "weekly" = "daily", enabled = true) {
  const [periodType, setPeriodType] = useState<"daily" | "weekly">(initialPeriod);
  const [data, setData] = useState<ManagerRhythmPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (period = periodType) => {
      setLoading(true);
      setError(null);
      try {
        const result = await teamRhythmClientService.get(period);
        setData(result);
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "Failed to load manager rhythm");
      } finally {
        setLoading(false);
      }
    },
    [periodType]
  );

  const generate = useCallback(
    async (period = periodType) => {
      setLoading(true);
      setError(null);
      try {
        const result = await teamRhythmClientService.generate(period);
        setData(result);
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "Failed to generate manager rhythm insight");
      } finally {
        setLoading(false);
      }
    },
    [periodType]
  );

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    void load(periodType);
  }, [periodType, load, enabled]);

  return {
    periodType,
    setPeriodType,
    data,
    loading,
    error,
    reload: load,
    generate
  };
}
