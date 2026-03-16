"use client";

import { useCallback, useEffect, useState } from "react";

import { dealRoomClientService } from "@/services/deal-room-client-service";
import type { DealRoom } from "@/types/deal";

export function useDeals(input?: {
  statuses?: string[];
  priorityBands?: string[];
  ownerId?: string;
  managerAttentionNeeded?: boolean;
}) {
  const [data, setData] = useState<DealRoom[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const rooms = await dealRoomClientService.list(input);
      setData(rooms);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to load deal rooms");
    } finally {
      setLoading(false);
    }
  }, [input]);

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

