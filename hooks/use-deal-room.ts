"use client";

import { useCallback, useEffect, useState } from "react";

import { dealRoomClientService } from "@/services/deal-room-client-service";
import type { DealRoomDetailView } from "@/types/deal";

export function useDealRoom(dealRoomId: string | null | undefined) {
  const [data, setData] = useState<DealRoomDetailView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!dealRoomId) {
      setData(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const detail = await dealRoomClientService.getById(dealRoomId);
      setData(detail);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to load deal room");
    } finally {
      setLoading(false);
    }
  }, [dealRoomId]);

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

