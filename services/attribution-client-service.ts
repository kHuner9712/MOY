import type { AttributionChain, OrgAttributionStats } from "@/services/attribution-service";

interface AttributionBriefingSummary {
  headline: string;
  highlights: string[];
  keyNumbers: Array<{
    label: string;
    value: number;
    trend: "up" | "down" | "stable";
    change: number;
  }>;
  recommendation: string;
  topEventTypes: string[];
  topHandlers: string[];
}

interface ApiPayload<T> {
  success: boolean;
  data: T | null;
  error: string | null;
}

async function readPayload<T>(response: Response): Promise<ApiPayload<T>> {
  const payload = (await response.json()) as ApiPayload<T>;
  return payload;
}

export const attributionClientService = {
  async getStats(params?: {
    periodStart?: string;
    periodEnd?: string;
    ownerId?: string;
  }): Promise<OrgAttributionStats> {
    const query = new URLSearchParams();
    if (params?.periodStart) query.set("periodStart", params.periodStart);
    if (params?.periodEnd) query.set("periodEnd", params.periodEnd);
    if (params?.ownerId) query.set("ownerId", params.ownerId);

    const response = await fetch(`/api/attribution?${query.toString()}`, {
      method: "GET"
    });

    const payload = await readPayload<OrgAttributionStats>(response);

    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to get attribution stats");
    }

    return payload.data;
  },

  async traceEvent(eventId: string): Promise<AttributionChain | null> {
    const query = new URLSearchParams();
    query.set("action", "trace");
    query.set("eventId", eventId);

    const response = await fetch(`/api/attribution?${query.toString()}`, {
      method: "GET"
    });

    const payload = await readPayload<AttributionChain | null>(response);

    if (!response.ok || !payload.success) {
      throw new Error(payload.error ?? "Failed to trace event");
    }

    return payload.data;
  },

  async getBriefingSummary(params?: {
    periodStart?: string;
    periodEnd?: string;
  }): Promise<AttributionBriefingSummary> {
    const query = new URLSearchParams();
    query.set("action", "briefing");
    if (params?.periodStart) query.set("periodStart", params.periodStart);
    if (params?.periodEnd) query.set("periodEnd", params.periodEnd);

    const response = await fetch(`/api/attribution?${query.toString()}`, {
      method: "GET"
    });

    const payload = await readPayload<AttributionBriefingSummary>(response);

    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to get attribution briefing summary");
    }

    return payload.data;
  }
};
