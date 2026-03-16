"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { touchpointClientService, type TouchpointHubSummary } from "@/services/touchpoint-client-service";
import type { ExternalTouchpointReviewResult, TouchpointHubView } from "@/types/touchpoint";

interface UseTouchpointsInput {
  ownerId?: string;
  customerId?: string;
  dealRoomId?: string;
  type?: "email" | "meeting" | "document";
  limit?: number;
  enabled?: boolean;
}

interface TouchpointReviewState {
  result: ExternalTouchpointReviewResult | null;
  runId: string | null;
  usedFallback: boolean;
  fallbackReason: string | null;
}

const defaultHub: TouchpointHubView = {
  emailThreads: [],
  calendarEvents: [],
  documentAssets: [],
  events: []
};

const defaultSummary: TouchpointHubSummary = {
  totalEvents: 0,
  waitingReplyThreads: 0,
  upcomingMeetings: 0,
  documentUpdates: 0
};

export function useTouchpoints(input: UseTouchpointsInput = {}) {
  const [hub, setHub] = useState<TouchpointHubView>(defaultHub);
  const [summary, setSummary] = useState<TouchpointHubSummary>(defaultSummary);
  const [review, setReview] = useState<TouchpointReviewState>({
    result: null,
    runId: null,
    usedFallback: false,
    fallbackReason: null
  });
  const [loading, setLoading] = useState(true);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const query = useMemo(
    () => ({
      ownerId: input.ownerId,
      customerId: input.customerId,
      dealRoomId: input.dealRoomId,
      type: input.type,
      limit: input.limit
    }),
    [input.customerId, input.dealRoomId, input.limit, input.ownerId, input.type]
  );

  const queryKey = JSON.stringify(query);

  const reload = useCallback(async () => {
    if (input.enabled === false) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const result = await touchpointClientService.getHub(query);
      setHub(result.hub);
      setSummary(result.summary);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to load touchpoints");
    } finally {
      setLoading(false);
    }
  }, [input.enabled, query]);

  const generateReview = useCallback(
    async (reviewInput?: {
      ownerId?: string;
      customerId?: string;
      dealRoomId?: string;
      sinceDays?: number;
    }) => {
      setReviewLoading(true);
      setError(null);
      try {
        const result = await touchpointClientService.review({
          ownerId: reviewInput?.ownerId ?? query.ownerId,
          customerId: reviewInput?.customerId ?? query.customerId,
          dealRoomId: reviewInput?.dealRoomId ?? query.dealRoomId,
          sinceDays: reviewInput?.sinceDays
        });
        setReview({
          result: result.result,
          runId: result.runId,
          usedFallback: result.usedFallback,
          fallbackReason: result.fallbackReason
        });
        return result;
      } finally {
        setReviewLoading(false);
      }
    },
    [query.customerId, query.dealRoomId, query.ownerId]
  );

  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryKey, input.enabled]);

  return {
    hub,
    summary,
    review,
    loading,
    reviewLoading,
    error,
    reload,
    generateReview
  };
}

