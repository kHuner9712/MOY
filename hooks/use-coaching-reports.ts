"use client";

import { useCallback, useEffect, useState } from "react";

import { coachingReportClientService } from "@/services/coaching-report-client-service";
import type { CoachingReport, CoachingReportScope, QualityPeriodType } from "@/types/quality";

export function useCoachingReports(params?: { scope?: CoachingReportScope; targetUserId?: string }) {
  const [items, setItems] = useState<CoachingReport[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await coachingReportClientService.list({
        scope: params?.scope,
        targetUserId: params?.targetUserId
      });
      setItems(data);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to load coaching reports");
    } finally {
      setLoading(false);
    }
  }, [params?.scope, params?.targetUserId]);

  useEffect(() => {
    void load();
  }, [load]);

  const generate = useCallback(
    async (input: { scope: CoachingReportScope; periodType?: QualityPeriodType; targetUserId?: string | null }) => {
      const created = await coachingReportClientService.generate(input);
      await load();
      return created;
    },
    [load]
  );

  return {
    items,
    loading,
    error,
    reload: load,
    generate
  };
}
