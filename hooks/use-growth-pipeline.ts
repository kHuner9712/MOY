"use client";

import { useCallback, useEffect, useState } from "react";

import { growthPipelineClientService } from "@/services/growth-pipeline-client-service";
import type { GrowthSummary, InboundLead, TrialRequest } from "@/types/commercialization";

interface ConversionEventLite {
  id: string;
  eventType: string;
  eventSummary: string;
  createdAt: string;
  leadId: string | null;
  targetOrgId: string | null;
}

export function useGrowthPipeline(periodDays = 30, enabled = true) {
  const [summary, setSummary] = useState<GrowthSummary | null>(null);
  const [leads, setLeads] = useState<InboundLead[]>([]);
  const [trialRequests, setTrialRequests] = useState<TrialRequest[]>([]);
  const [events, setEvents] = useState<ConversionEventLite[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [summaryRes, leadsRes, trialRes, eventsRes] = await Promise.all([
        growthPipelineClientService.getSummary(periodDays),
        growthPipelineClientService.listLeads({
          limit: 80
        }),
        growthPipelineClientService.listTrials({
          limit: 80
        }),
        growthPipelineClientService.listConversionEvents(40)
      ]);

      setSummary(summaryRes.summary);
      setLeads(leadsRes.leads);
      setTrialRequests(trialRes.trialRequests);
      setEvents(eventsRes.events);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to load growth pipeline.");
    } finally {
      setLoading(false);
    }
  }, [periodDays]);

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    void load();
  }, [enabled, load]);

  const convertLead = useCallback(
    async (leadId: string) => {
      const result = await growthPipelineClientService.convertLead(leadId);
      await load();
      return result;
    },
    [load]
  );

  const activateTrial = useCallback(
    async (trialRequestId: string) => {
      const result = await growthPipelineClientService.activateTrial(trialRequestId);
      await load();
      return result;
    },
    [load]
  );

  return {
    summary,
    leads,
    trialRequests,
    events,
    loading,
    error,
    reload: load,
    convertLead,
    activateTrial
  };
}
