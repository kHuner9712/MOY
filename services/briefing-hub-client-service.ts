import type { BriefingHubView, ContentDraft, MorningBriefType, PrepCard } from "@/types/preparation";

interface ApiPayload<T> {
  success: boolean;
  data: T | null;
  error: string | null;
}

export const briefingHubClientService = {
  async get(date?: string): Promise<BriefingHubView> {
    const query = date ? `?date=${encodeURIComponent(date)}` : "";
    const response = await fetch(`/api/briefings${query}`, {
      method: "GET"
    });
    const payload = (await response.json()) as ApiPayload<BriefingHubView>;
    if (!response.ok || !payload.success || !payload.data) throw new Error(payload.error ?? "Failed to load briefings");
    return payload.data;
  },

  async getCustomer(customerId: string): Promise<{ prepCards: PrepCard[]; contentDrafts: ContentDraft[] }> {
    const response = await fetch(`/api/briefings?customerId=${encodeURIComponent(customerId)}`, {
      method: "GET"
    });
    const payload = (await response.json()) as ApiPayload<{ prepCards: PrepCard[]; contentDrafts: ContentDraft[] }>;
    if (!response.ok || !payload.success || !payload.data) throw new Error(payload.error ?? "Failed to load customer briefings");
    return payload.data;
  },

  async getWorkItemCoverage(workItemIds: string[]): Promise<{
    prepByWorkItemId: Record<string, PrepCard>;
    draftByWorkItemId: Record<string, ContentDraft[]>;
  }> {
    const query = encodeURIComponent(workItemIds.join(","));
    const response = await fetch(`/api/briefings?workItemIds=${query}`, {
      method: "GET"
    });
    const payload = (await response.json()) as ApiPayload<{
      prepByWorkItemId: Record<string, PrepCard>;
      draftByWorkItemId: Record<string, ContentDraft[]>;
    }>;
    if (!response.ok || !payload.success || !payload.data) throw new Error(payload.error ?? "Failed to load work item briefing coverage");
    return payload.data;
  },

  async generateMorningBrief(input?: {
    briefType?: MorningBriefType;
    briefDate?: string;
  }): Promise<{
    brief: BriefingHubView["morningBrief"];
    usedFallback: boolean;
    runId: string;
  }> {
    const response = await fetch("/api/briefings/morning-generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input ?? {})
    });
    const payload = (await response.json()) as ApiPayload<{
      brief: BriefingHubView["morningBrief"];
      usedFallback: boolean;
      runId: string;
    }>;
    if (!response.ok || !payload.success || !payload.data) throw new Error(payload.error ?? "Failed to generate morning brief");
    return payload.data;
  }
};
