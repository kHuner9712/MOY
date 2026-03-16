"use client";

import { useEffect, useState } from "react";

import type { ImportJob } from "@/types/import";

interface ApiPayload<T> {
  success: boolean;
  data: T | null;
  error: string | null;
}

export function useImportJobs(enabled: boolean, limit = 6): {
  jobs: ImportJob[];
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
} {
  const [jobs, setJobs] = useState<ImportJob[]>([]);
  const [loading, setLoading] = useState<boolean>(enabled);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    if (!enabled) {
      setJobs([]);
      setError(null);
      setLoading(false);
      return;
    }

    try {
      setLoading(true);
      setError(null);
      const response = await fetch(`/api/imports?limit=${limit}`, {
        method: "GET"
      });
      const payload = (await response.json()) as ApiPayload<{ jobs: ImportJob[] }>;
      if (!response.ok || !payload.success || !payload.data) {
        throw new Error(payload.error ?? "Failed to load import jobs");
      }
      setJobs(payload.data.jobs);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to load import jobs");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, [enabled, limit]);

  return {
    jobs,
    loading,
    error,
    reload: load
  };
}

