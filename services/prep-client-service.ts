import type { PrepCard, PrepFeedbackType } from "@/types/preparation";

interface ApiPayload<T> {
  success: boolean;
  data: T | null;
  error: string | null;
}

export const prepClientService = {
  async generateFollowup(input: {
    customerId?: string;
    opportunityId?: string;
    workItemId?: string;
  }): Promise<{ prepCard: PrepCard; runId: string; usedFallback: boolean }> {
    const response = await fetch("/api/prep/followup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });
    const payload = (await response.json()) as ApiPayload<{ prepCard: PrepCard; runId: string; usedFallback: boolean }>;
    if (!response.ok || !payload.success || !payload.data) throw new Error(payload.error ?? "Failed to generate followup prep card");
    return payload.data;
  },

  async generateQuote(input: {
    customerId?: string;
    opportunityId?: string;
    workItemId?: string;
  }): Promise<{ prepCard: PrepCard; runId: string; usedFallback: boolean }> {
    const response = await fetch("/api/prep/quote", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });
    const payload = (await response.json()) as ApiPayload<{ prepCard: PrepCard; runId: string; usedFallback: boolean }>;
    if (!response.ok || !payload.success || !payload.data) throw new Error(payload.error ?? "Failed to generate quote prep card");
    return payload.data;
  },

  async generateMeeting(input: {
    customerId?: string;
    opportunityId?: string;
    workItemId?: string;
    meetingPurpose?: string;
  }): Promise<{ prepCard: PrepCard; runId: string; usedFallback: boolean }> {
    const response = await fetch("/api/prep/meeting", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });
    const payload = (await response.json()) as ApiPayload<{ prepCard: PrepCard; runId: string; usedFallback: boolean }>;
    if (!response.ok || !payload.success || !payload.data) throw new Error(payload.error ?? "Failed to generate meeting prep card");
    return payload.data;
  },

  async generateTaskBrief(input: { workItemId: string }): Promise<{ prepCard: PrepCard; runId: string; usedFallback: boolean }> {
    const response = await fetch("/api/prep/task-brief", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });
    const payload = (await response.json()) as ApiPayload<{ prepCard: PrepCard; runId: string; usedFallback: boolean }>;
    if (!response.ok || !payload.success || !payload.data) throw new Error(payload.error ?? "Failed to generate task brief card");
    return payload.data;
  },

  async generateManagerAttention(input: {
    customerId?: string;
    opportunityId?: string;
    workItemId?: string;
  }): Promise<{ prepCard: PrepCard; runId: string; usedFallback: boolean }> {
    const response = await fetch("/api/prep/manager-attention", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });
    const payload = (await response.json()) as ApiPayload<{ prepCard: PrepCard; runId: string; usedFallback: boolean }>;
    if (!response.ok || !payload.success || !payload.data) throw new Error(payload.error ?? "Failed to generate manager attention card");
    return payload.data;
  },

  async feedback(prepCardId: string, feedbackType: PrepFeedbackType, feedbackText?: string): Promise<void> {
    const response = await fetch(`/api/prep/${prepCardId}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        feedbackType,
        feedbackText
      })
    });
    const payload = (await response.json()) as ApiPayload<unknown>;
    if (!response.ok || !payload.success) throw new Error(payload.error ?? "Failed to save prep feedback");
  }
};
