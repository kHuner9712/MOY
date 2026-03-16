"use client";

import { useCallback, useEffect, useState } from "react";

import { executiveClientService } from "@/services/executive-client-service";
import type { BusinessEvent, ExecutiveBrief, ExecutiveCockpitSummary } from "@/types/automation";

interface ExecutiveCockpitState {
  summary: ExecutiveCockpitSummary | null;
  events: BusinessEvent[];
  briefs: ExecutiveBrief[];
  loading: boolean;
  error: string | null;
}

export function useExecutiveCockpit(enabled: boolean) {
  const [state, setState] = useState<ExecutiveCockpitState>({
    summary: null,
    events: [],
    briefs: [],
    loading: enabled,
    error: null
  });

  const load = useCallback(
    async (refresh = false) => {
      if (!enabled) {
        setState({
          summary: null,
          events: [],
          briefs: [],
          loading: false,
          error: null
        });
        return;
      }

      setState((prev) => ({ ...prev, loading: true, error: null }));
      try {
        const [summary, eventsPayload, briefsPayload] = await Promise.all([
          executiveClientService.getExecutiveSummary({ refresh }),
          executiveClientService.getExecutiveEvents({ status: ["open", "acknowledged"], limit: 80 }),
          executiveClientService.getExecutiveBriefs({ limit: 10 })
        ]);
        setState({
          summary,
          events: eventsPayload.events,
          briefs: briefsPayload.briefs,
          loading: false,
          error: null
        });
      } catch (cause) {
        setState((prev) => ({
          ...prev,
          loading: false,
          error: cause instanceof Error ? cause.message : "Failed to load executive cockpit"
        }));
      }
    },
    [enabled]
  );

  useEffect(() => {
    void load(false);
  }, [load]);

  return {
    ...state,
    reload: load
  };
}

