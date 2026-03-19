import type { ValueMetricsResult, ValueMetricsSummary } from "@/types/value-metrics";

interface ApiPayload<T> {
  success: boolean;
  data: T | null;
  error: string | null;
  meta?: {
    usedFallback: boolean;
    fallbackReason: string | null;
  };
}

async function readPayload<T>(response: Response): Promise<ApiPayload<T>> {
  const payload = (await response.json()) as ApiPayload<T>;
  return payload;
}

export const valueMetricsClientService = {
  async getWeeklyMetrics(input?: {
    ownerId?: string;
  }): Promise<ValueMetricsResult> {
    const query = new URLSearchParams();
    if (input?.ownerId) query.set("ownerId", input.ownerId);
    const response = await fetch(`/api/value-metrics${query.toString() ? `?${query.toString()}` : ""}`, {
      method: "GET"
    });
    const payload = await readPayload<ValueMetricsResult>(response);
    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to get value metrics");
    }
    return payload.data;
  },

  async getSummary(input?: {
    ownerId?: string;
  }): Promise<{
    summary: ValueMetricsSummary;
    usedFallback: boolean;
    fallbackReason: string | null;
  }> {
    const query = new URLSearchParams();
    query.set("summary", "true");
    if (input?.ownerId) query.set("ownerId", input.ownerId);
    const response = await fetch(`/api/value-metrics?${query.toString()}`, {
      method: "GET"
    });
    const payload = await readPayload<ValueMetricsSummary>(response);
    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to get value metrics summary");
    }
    return {
      summary: payload.data,
      usedFallback: payload.meta?.usedFallback ?? true,
      fallbackReason: payload.meta?.fallbackReason ?? null
    };
  }
};
