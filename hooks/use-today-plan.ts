"use client";

import { useCallback, useEffect, useState } from "react";

import { workPlanClientService } from "@/services/work-plan-client-service";
import type { TodayPlanView } from "@/types/work";

export function useTodayPlan() {
  const [planView, setPlanView] = useState<TodayPlanView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await workPlanClientService.getTodayPlan();
      setPlanView(data);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to load today plan");
    } finally {
      setLoading(false);
    }
  }, []);

  const generate = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await workPlanClientService.generateTodayPlan(true);
      setMessage(result.usedFallback ? "今日计划已生成（规则回退）" : "今日计划已生成");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to generate today plan");
    } finally {
      setLoading(false);
    }
  }, [load]);

  useEffect(() => {
    void load();
  }, [load]);

  return {
    planView,
    loading,
    error,
    message,
    load,
    generate
  };
}
