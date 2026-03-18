"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { executiveClientService, type ExecutiveHealthPayload } from "@/services/executive-client-service";
import type { ExecutiveBriefType } from "@/types/automation";

type EventAction = "ack" | "resolve" | "ignore";

export function useExecutiveActions(params: {
  enabled: boolean;
  reload: (refresh?: boolean) => Promise<void>;
}) {
  const [health, setHealth] = useState<ExecutiveHealthPayload | null>(null);
  const [healthLoading, setHealthLoading] = useState(true);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [busyEventId, setBusyEventId] = useState<string | null>(null);
  const [generatingBrief, setGeneratingBrief] = useState(false);
  const [briefType, setBriefType] = useState<ExecutiveBriefType>("executive_daily");

  const loadHealth = useCallback(async (): Promise<void> => {
    setHealthLoading(true);
    try {
      const payload = await executiveClientService.getExecutiveHealth();
      setHealth(payload);
    } catch {
      setHealth({
        healthSnapshots: [],
        renewalWatch: []
      });
    } finally {
      setHealthLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!params.enabled) return;
    void loadHealth();
  }, [params.enabled, loadHealth]);

  const runEventAction = useCallback(
    async (eventId: string, action: EventAction): Promise<void> => {
      setBusyEventId(eventId);
      setActionMessage(null);
      try {
        if (action === "ack") await executiveClientService.ackBusinessEvent(eventId);
        if (action === "resolve") await executiveClientService.resolveBusinessEvent(eventId);
        if (action === "ignore") await executiveClientService.ignoreBusinessEvent(eventId);
        setActionMessage(`Event ${action} completed.`);
        await params.reload(false);
      } catch (cause) {
        setActionMessage(cause instanceof Error ? cause.message : "Failed to update event");
      } finally {
        setBusyEventId(null);
      }
    },
    [params]
  );

  const runRefresh = useCallback(async (): Promise<void> => {
    setActionMessage(null);
    await Promise.all([params.reload(true), loadHealth()]);
  }, [params, loadHealth]);

  const generateBrief = useCallback(async (): Promise<void> => {
    setGeneratingBrief(true);
    setActionMessage(null);
    try {
      const result = await executiveClientService.generateExecutiveBrief({ briefType });
      setActionMessage(
        result.usedFallback
          ? `Executive brief generated with fallback (${result.fallbackReason ?? "rule"})`
          : "Executive brief generated."
      );
      await params.reload(false);
    } catch (cause) {
      setActionMessage(cause instanceof Error ? cause.message : "Failed to generate executive brief");
    } finally {
      setGeneratingBrief(false);
    }
  }, [briefType, params]);

  const riskSnapshots = useMemo(
    () => (health?.healthSnapshots ?? []).filter((item) => item.healthBand === "critical" || item.healthBand === "at_risk").slice(0, 12),
    [health?.healthSnapshots]
  );

  return {
    health,
    healthLoading,
    actionMessage,
    busyEventId,
    generatingBrief,
    briefType,
    riskSnapshots,
    setBriefType,
    runEventAction,
    runRefresh,
    generateBrief
  };
}
