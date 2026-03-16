"use client";

import { useCallback, useEffect, useState } from "react";

import { memoryClientService } from "@/services/memory-client-service";
import type { UserMemoryItem, UserMemoryProfile } from "@/types/memory";

export function useUserMemory(targetUserId?: string) {
  const [profile, setProfile] = useState<UserMemoryProfile | null>(null);
  const [items, setItems] = useState<UserMemoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await memoryClientService.getProfile(targetUserId);
      setProfile(data.profile);
      setItems(data.items);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to load memory");
    } finally {
      setLoading(false);
    }
  }, [targetUserId]);

  useEffect(() => {
    void load();
  }, [load]);

  const refresh = useCallback(
    async (sourceWindowDays?: number) => {
      const data = await memoryClientService.refresh({
        userId: targetUserId,
        sourceWindowDays
      });
      setProfile(data.profile);
      setItems(data.items);
      return data;
    },
    [targetUserId]
  );

  const feedback = useCallback(
    async (params: {
      memoryItemId: string;
      feedbackType: "accurate" | "inaccurate" | "outdated" | "useful" | "not_useful";
      feedbackText?: string;
    }) => {
      await memoryClientService.feedback(params);
      await load();
    },
    [load]
  );

  return {
    profile,
    items,
    loading,
    error,
    refresh,
    feedback,
    reload: load
  };
}
