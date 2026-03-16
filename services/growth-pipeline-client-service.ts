import type { GrowthSummary, InboundLead, TrialRequest } from "@/types/commercialization";

interface ApiPayload<T> {
  success: boolean;
  data: T | null;
  error: string | null;
}

async function readPayload<T>(response: Response): Promise<T> {
  const payload = (await response.json()) as ApiPayload<T>;
  if (!response.ok || !payload.success || !payload.data) {
    throw new Error(payload.error ?? "Request failed");
  }
  return payload.data;
}

export const growthPipelineClientService = {
  async getSummary(periodDays = 30): Promise<{
    summary: GrowthSummary;
  }> {
    const response = await fetch(`/api/growth/summary?periodDays=${periodDays}`, {
      method: "GET"
    });
    return readPayload(response);
  },

  async listLeads(params?: {
    ownerId?: string;
    statuses?: string[];
    sources?: string[];
    limit?: number;
  }): Promise<{
    leads: InboundLead[];
  }> {
    const search = new URLSearchParams();
    if (params?.ownerId) search.set("ownerId", params.ownerId);
    if (params?.limit) search.set("limit", String(params.limit));
    for (const status of params?.statuses ?? []) search.append("status", status);
    for (const source of params?.sources ?? []) search.append("source", source);
    const query = search.toString();
    const response = await fetch(`/api/growth/leads${query ? `?${query}` : ""}`, {
      method: "GET"
    });
    return readPayload(response);
  },

  async convertLead(leadId: string): Promise<{
    converted: boolean;
    customerId: string | null;
    opportunityId: string | null;
    dealRoomId: string | null;
  }> {
    const response = await fetch(`/api/growth/leads/${encodeURIComponent(leadId)}/convert`, {
      method: "POST"
    });
    return readPayload(response);
  },

  async listTrials(params?: {
    statuses?: string[];
    limit?: number;
  }): Promise<{
    trialRequests: TrialRequest[];
  }> {
    const search = new URLSearchParams();
    if (params?.limit) search.set("limit", String(params.limit));
    for (const status of params?.statuses ?? []) search.append("status", status);
    const query = search.toString();
    const response = await fetch(`/api/growth/trials${query ? `?${query}` : ""}`, {
      method: "GET"
    });
    return readPayload(response);
  },

  async activateTrial(trialRequestId: string): Promise<{
    trialRequest: TrialRequest;
    targetOrgId: string;
    conversionTrackId: string;
  }> {
    const response = await fetch(`/api/growth/trials/${encodeURIComponent(trialRequestId)}/activate`, {
      method: "POST"
    });
    return readPayload(response);
  },

  async listConversionEvents(limit = 40): Promise<{
    events: Array<{
      id: string;
      eventType: string;
      eventSummary: string;
      createdAt: string;
      leadId: string | null;
      targetOrgId: string | null;
    }>;
  }> {
    const response = await fetch(`/api/growth/conversion-events?limit=${limit}`, {
      method: "GET"
    });
    return readPayload(response);
  }
};
