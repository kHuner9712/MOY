"use client";

import { useCallback, useEffect, useState } from "react";

import { playbookClientService } from "@/services/playbook-client-service";
import type { PlaybookWithEntries } from "@/types/playbook";

export function usePlaybooks(input?: {
  ownerUserId?: string;
  scopeType?: "org" | "team" | "user";
  playbookType?: "objection_handling" | "customer_segment" | "quote_strategy" | "meeting_strategy" | "followup_rhythm" | "risk_recovery";
  includeEntries?: boolean;
  limit?: number;
}) {
  const [data, setData] = useState<PlaybookWithEntries[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const rows = await playbookClientService.list(input);
      setData(rows);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to load playbooks");
    } finally {
      setLoading(false);
    }
  }, [input]);

  useEffect(() => {
    void load();
  }, [load]);

  const compile = useCallback(
    async (params?: {
      scopeType?: "org" | "team" | "user";
      ownerUserId?: string | null;
      periodStart?: string;
      periodEnd?: string;
      title?: string;
    }) => {
      setLoading(true);
      setError(null);
      setMessage(null);
      try {
        const result = await playbookClientService.compile(params);
        setMessage(result.usedFallback ? "Playbook compiled with fallback." : "Playbook compiled successfully.");
        await load();
        return result;
      } catch (cause) {
        const text = cause instanceof Error ? cause.message : "Failed to compile playbook";
        setError(text);
        throw cause;
      } finally {
        setLoading(false);
      }
    },
    [load]
  );

  const feedback = useCallback(
    async (playbookId: string, params: {
      playbookEntryId?: string;
      feedbackType: "useful" | "not_useful" | "outdated" | "inaccurate" | "adopted";
      feedbackText?: string;
    }) => {
      await playbookClientService.feedback(playbookId, params);
      await load();
    },
    [load]
  );

  return {
    data,
    loading,
    error,
    message,
    reload: load,
    compile,
    feedback
  };
}
