"use client";

import { useCallback, useEffect, useState } from "react";

import { briefingHubClientService } from "@/services/briefing-hub-client-service";
import type { BriefingHubView, MorningBriefType } from "@/types/preparation";

export function useBriefings(date?: string) {
  const [data, setData] = useState<BriefingHubView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await briefingHubClientService.get(date);
      setData(result);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to load briefings");
    } finally {
      setLoading(false);
    }
  }, [date]);

  const generateMorningBrief = useCallback(
    async (input?: { briefType?: MorningBriefType; briefDate?: string }) => {
      setLoading(true);
      setError(null);
      setMessage(null);
      try {
        const result = await briefingHubClientService.generateMorningBrief(input);
        setMessage(result.usedFallback ? "Morning brief generated with fallback." : "Morning brief generated.");
        await load();
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "Failed to generate morning brief");
      } finally {
        setLoading(false);
      }
    },
    [load]
  );

  useEffect(() => {
    void load();
  }, [load]);

  return {
    data,
    loading,
    error,
    message,
    reload: load,
    generateMorningBrief
  };
}
