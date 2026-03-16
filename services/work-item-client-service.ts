import type { TaskActionSuggestionResult } from "@/types/ai";
import type { WorkItem } from "@/types/work";

interface ApiPayload<T> {
  success: boolean;
  data: T | null;
  error: string | null;
}

export const workItemClientService = {
  async start(workItemId: string): Promise<WorkItem> {
    const response = await fetch(`/api/work-items/${workItemId}/start`, {
      method: "POST"
    });
    const payload = (await response.json()) as ApiPayload<WorkItem>;
    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to start work item");
    }
    return payload.data;
  },

  async complete(workItemId: string, note?: string): Promise<WorkItem> {
    const response = await fetch(`/api/work-items/${workItemId}/complete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note })
    });
    const payload = (await response.json()) as ApiPayload<WorkItem>;
    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to complete work item");
    }
    return payload.data;
  },

  async completeToFollowup(workItemId: string, note?: string): Promise<{ workItem: WorkItem; followupId: string | null }> {
    const response = await fetch(`/api/work-items/${workItemId}/complete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note, convertToFollowup: true })
    });
    const payload = (await response.json()) as ApiPayload<WorkItem & { followupId?: string }>;
    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to convert work item to followup");
    }
    const { followupId, ...workItem } = payload.data;
    return {
      workItem: workItem as WorkItem,
      followupId: followupId ?? null
    };
  },

  async snooze(workItemId: string, snoozedUntil?: string, note?: string): Promise<WorkItem> {
    const response = await fetch(`/api/work-items/${workItemId}/snooze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ snoozedUntil, note })
    });
    const payload = (await response.json()) as ApiPayload<WorkItem>;
    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to snooze work item");
    }
    return payload.data;
  },

  async cancel(workItemId: string, note?: string): Promise<WorkItem> {
    const response = await fetch(`/api/work-items/${workItemId}/cancel`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note })
    });
    const payload = (await response.json()) as ApiPayload<WorkItem>;
    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to cancel work item");
    }
    return payload.data;
  },

  async createFromAlert(alertId: string): Promise<{ workItem: WorkItem; created: boolean }> {
    const response = await fetch("/api/work-items/from-alert", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ alertId })
    });
    const payload = (await response.json()) as ApiPayload<{ workItem: WorkItem; created: boolean }>;
    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to convert alert to work item");
    }
    return payload.data;
  },

  async getByCustomer(customerId: string): Promise<WorkItem[]> {
    const response = await fetch(`/api/today/plan?customerId=${encodeURIComponent(customerId)}`, {
      method: "GET"
    });
    const payload = (await response.json()) as ApiPayload<{ workItems: WorkItem[] }>;
    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to load customer work items");
    }
    return payload.data.workItems;
  },

  async getTaskSuggestion(workItemId: string): Promise<{
    runId: string;
    result: TaskActionSuggestionResult;
    usedFallback: boolean;
  }> {
    const response = await fetch(`/api/work-items/${workItemId}/action-suggestion`, {
      method: "POST"
    });
    const payload = (await response.json()) as ApiPayload<{
      runId: string;
      result: TaskActionSuggestionResult;
      usedFallback: boolean;
    }>;
    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to load task action suggestion");
    }
    return payload.data;
  }
};
