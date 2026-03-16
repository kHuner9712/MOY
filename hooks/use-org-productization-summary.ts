"use client";

import { useEffect, useState } from "react";

interface SettingsSummary {
  role: string;
  onboardingCompleted: boolean;
  onboardingProgress: number;
  aiProviderConfigured: boolean;
  aiProviderReason: string | null;
  planTier: string;
  planStatus: string;
  quotaNearLimit: boolean;
  quotaExceeded: boolean;
  aiRunUsedMonthly: number;
  aiRunLimitMonthly: number;
  currentTemplateKey: string | null;
  currentTemplateName: string | null;
  templateApplied: boolean;
}

interface ApiPayload<T> {
  success: boolean;
  data: T | null;
  error: string | null;
}

export function useOrgProductizationSummary(enabled: boolean): {
  data: SettingsSummary | null;
  loading: boolean;
  error: string | null;
} {
  const [data, setData] = useState<SettingsSummary | null>(null);
  const [loading, setLoading] = useState<boolean>(enabled);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      setData(null);
      setError(null);
      return;
    }

    let mounted = true;

    const load = async () => {
      try {
        setLoading(true);
        setError(null);

        const response = await fetch("/api/settings/summary", {
          method: "GET"
        });
        const payload = (await response.json()) as ApiPayload<SettingsSummary>;
        if (!response.ok || !payload.success || !payload.data) {
          throw new Error(payload.error ?? "Failed to load settings summary");
        }

        if (mounted) setData(payload.data);
      } catch (err) {
        if (mounted) setError(err instanceof Error ? err.message : "Failed to load settings summary");
      } finally {
        if (mounted) setLoading(false);
      }
    };

    void load();

    return () => {
      mounted = false;
    };
  }, [enabled]);

  return {
    data,
    loading,
    error
  };
}
