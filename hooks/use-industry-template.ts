"use client";

import { useEffect, useState } from "react";

import { settingsClientService } from "@/services/settings-client-service";
import type { IndustryTemplateContext } from "@/types/productization";

interface IndustryTemplateHookData extends IndustryTemplateContext {
  role: string;
}

export function useIndustryTemplate(enabled = true): {
  data: IndustryTemplateHookData | null;
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
} {
  const [data, setData] = useState<IndustryTemplateHookData | null>(null);
  const [loading, setLoading] = useState<boolean>(enabled);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    if (!enabled) {
      setData(null);
      setLoading(false);
      setError(null);
      return;
    }
    try {
      setLoading(true);
      setError(null);
      const result = await settingsClientService.getCurrentTemplate();
      setData(result);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to load industry template context");
      setData(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled]);

  return {
    data,
    loading,
    error,
    reload: load
  };
}
