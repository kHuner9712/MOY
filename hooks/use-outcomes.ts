"use client";

import { useCallback, useEffect, useState } from "react";

import { outcomeClientService } from "@/services/outcome-client-service";
import type { ActionOutcome } from "@/types/outcome";

export function useOutcomes(input?: {
  ownerId?: string;
  customerId?: string;
  outcomeType?: "followup_result" | "quote_result" | "meeting_result" | "task_result" | "manager_intervention_result";
  limit?: number;
}) {
  const [data, setData] = useState<ActionOutcome[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const rows = await outcomeClientService.list(input);
      setData(rows);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to load outcomes");
    } finally {
      setLoading(false);
    }
  }, [input]);

  useEffect(() => {
    void load();
  }, [load]);

  return {
    data,
    loading,
    error,
    reload: load
  };
}
