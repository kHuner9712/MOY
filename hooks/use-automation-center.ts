"use client";

import { useCallback, useEffect, useState } from "react";

import { executiveClientService, type AutomationCenterPayload } from "@/services/executive-client-service";

export function useAutomationCenter(enabled: boolean) {
  const [data, setData] = useState<AutomationCenterPayload | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!enabled) {
      setData(null);
      setLoading(false);
      setError(null);
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const next = await executiveClientService.getAutomationCenter();
      setData(next);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to load automation center");
    } finally {
      setLoading(false);
    }
  }, [enabled]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return {
    data,
    loading,
    error,
    reload
  };
}

