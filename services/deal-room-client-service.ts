import type { DealCheckpoint, DealRoom, DealRoomDetailView, DecisionRecord, InterventionRequest } from "@/types/deal";

interface ApiPayload<T> {
  success: boolean;
  data: T | null;
  error: string | null;
}

export const dealRoomClientService = {
  async list(input?: {
    statuses?: string[];
    priorityBands?: string[];
    ownerId?: string;
    managerAttentionNeeded?: boolean;
    limit?: number;
  }): Promise<DealRoom[]> {
    const query = new URLSearchParams();
    if (input?.statuses?.length) query.set("status", input.statuses.join(","));
    if (input?.priorityBands?.length) query.set("priorityBand", input.priorityBands.join(","));
    if (input?.ownerId) query.set("ownerId", input.ownerId);
    if (input?.managerAttentionNeeded !== undefined) query.set("managerAttentionNeeded", String(input.managerAttentionNeeded));
    if (input?.limit !== undefined) query.set("limit", String(input.limit));

    const response = await fetch(`/api/deals${query.toString() ? `?${query.toString()}` : ""}`, { method: "GET" });
    const payload = (await response.json()) as ApiPayload<DealRoom[]>;
    if (!response.ok || !payload.success || !payload.data) throw new Error(payload.error ?? "Failed to load deal rooms");
    return payload.data;
  },

  async create(input: {
    customerId: string;
    opportunityId?: string;
    ownerId?: string;
    title?: string;
    priorityBand?: "normal" | "important" | "strategic" | "critical";
    currentGoal?: string;
    currentBlockers?: string[];
    nextMilestone?: string;
    nextMilestoneDueAt?: string;
    managerAttentionNeeded?: boolean;
  }): Promise<{ room: DealRoom; created: boolean }> {
    const response = await fetch("/api/deals/create", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });
    const payload = (await response.json()) as ApiPayload<{ room: DealRoom; created: boolean }>;
    if (!response.ok || !payload.success || !payload.data) throw new Error(payload.error ?? "Failed to create deal room");
    return payload.data;
  },

  async getById(dealRoomId: string): Promise<DealRoomDetailView> {
    const response = await fetch(`/api/deals/${dealRoomId}`, { method: "GET" });
    const payload = (await response.json()) as ApiPayload<DealRoomDetailView>;
    if (!response.ok || !payload.success || !payload.data) throw new Error(payload.error ?? "Failed to load deal room");
    return payload.data;
  },

  async createThread(dealRoomId: string, input: { threadType: string; title: string; summary?: string }) {
    const response = await fetch(`/api/deals/${dealRoomId}/threads`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });
    const payload = (await response.json()) as ApiPayload<unknown>;
    if (!response.ok || !payload.success || !payload.data) throw new Error(payload.error ?? "Failed to create thread");
    return payload.data;
  },

  async addMessage(
    dealRoomId: string,
    input: {
      threadId: string;
      bodyMarkdown: string;
      messageType?: "comment" | "decision_note" | "ai_summary" | "system_event";
      mentions?: string[];
      sourceRefType?: string;
      sourceRefId?: string;
    }
  ) {
    const response = await fetch(`/api/deals/${dealRoomId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });
    const payload = (await response.json()) as ApiPayload<unknown>;
    if (!response.ok || !payload.success || !payload.data) throw new Error(payload.error ?? "Failed to add message");
    return payload.data;
  },

  async createDecision(
    dealRoomId: string,
    input: {
      decisionType: string;
      title: string;
      contextSummary?: string;
      optionsConsidered?: string[];
      recommendedOption?: string;
      decisionReason?: string;
      status?: "proposed" | "approved" | "rejected" | "superseded" | "completed";
      dueAt?: string;
      ownerIdForLinkedTask?: string;
      includeDecisionSupport?: boolean;
    }
  ): Promise<{ decision: DecisionRecord; decisionSupport: unknown | null }> {
    const response = await fetch(`/api/deals/${dealRoomId}/decision-records`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });
    const payload = (await response.json()) as ApiPayload<{ decision: DecisionRecord; decisionSupport: unknown | null }>;
    if (!response.ok || !payload.success || !payload.data) throw new Error(payload.error ?? "Failed to create decision");
    return payload.data;
  },

  async updateDecisionStatus(
    dealRoomId: string,
    input: {
      decisionId: string;
      status: "proposed" | "approved" | "rejected" | "superseded" | "completed";
      decisionReason?: string;
      ownerIdForLinkedTask?: string;
    }
  ): Promise<{ decision: DecisionRecord }> {
    const response = await fetch(`/api/deals/${dealRoomId}/decision-records`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });
    const payload = (await response.json()) as ApiPayload<{ decision: DecisionRecord }>;
    if (!response.ok || !payload.success || !payload.data) throw new Error(payload.error ?? "Failed to update decision");
    return payload.data;
  },

  async createIntervention(
    dealRoomId: string,
    input: {
      requestType: string;
      requestSummary: string;
      targetUserId?: string;
      priorityBand?: "low" | "medium" | "high" | "critical";
      dueAt?: string;
      includeRecommendation?: boolean;
    }
  ): Promise<{ intervention: InterventionRequest; recommendation: unknown | null }> {
    const response = await fetch(`/api/deals/${dealRoomId}/interventions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });
    const payload = (await response.json()) as ApiPayload<{ intervention: InterventionRequest; recommendation: unknown | null }>;
    if (!response.ok || !payload.success || !payload.data) throw new Error(payload.error ?? "Failed to create intervention request");
    return payload.data;
  },

  async updateInterventionStatus(
    dealRoomId: string,
    input: {
      interventionRequestId: string;
      status: "open" | "accepted" | "declined" | "completed" | "expired";
      note?: string;
    }
  ): Promise<{ intervention: InterventionRequest }> {
    const response = await fetch(`/api/deals/${dealRoomId}/interventions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });
    const payload = (await response.json()) as ApiPayload<{ intervention: InterventionRequest }>;
    if (!response.ok || !payload.success || !payload.data) throw new Error(payload.error ?? "Failed to update intervention status");
    return payload.data;
  },

  async createCheckpoint(
    dealRoomId: string,
    input: {
      checkpointType: string;
      title: string;
      description?: string;
      status?: "pending" | "completed" | "blocked" | "skipped";
      ownerId?: string;
      dueAt?: string;
      blockedReason?: string;
    }
  ): Promise<DealCheckpoint> {
    const response = await fetch(`/api/deals/${dealRoomId}/checkpoints`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });
    const payload = (await response.json()) as ApiPayload<DealCheckpoint>;
    if (!response.ok || !payload.success || !payload.data) throw new Error(payload.error ?? "Failed to create checkpoint");
    return payload.data;
  },

  async updateCheckpointStatus(
    dealRoomId: string,
    input: {
      checkpointId: string;
      status: "pending" | "completed" | "blocked" | "skipped";
      blockedReason?: string;
      dueAt?: string;
    }
  ): Promise<DealCheckpoint> {
    const response = await fetch(`/api/deals/${dealRoomId}/checkpoints`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });
    const payload = (await response.json()) as ApiPayload<DealCheckpoint>;
    if (!response.ok || !payload.success || !payload.data) throw new Error(payload.error ?? "Failed to update checkpoint");
    return payload.data;
  },

  async refreshCommand(dealRoomId: string): Promise<{
    room: DealRoom;
    result: unknown;
    runId: string;
    usedFallback: boolean;
    fallbackReason: string | null;
    playbookMapping: unknown;
  }> {
    const response = await fetch(`/api/deals/${dealRoomId}/command-refresh`, {
      method: "POST"
    });
    const payload = (await response.json()) as ApiPayload<{
      room: DealRoom;
      result: unknown;
      runId: string;
      usedFallback: boolean;
      fallbackReason: string | null;
      playbookMapping: unknown;
    }>;
    if (!response.ok || !payload.success || !payload.data) throw new Error(payload.error ?? "Failed to refresh command summary");
    return payload.data;
  },

  async summarizeThread(dealRoomId: string, threadId: string): Promise<{
    summary: unknown;
    runId: string;
    usedFallback: boolean;
    fallbackReason: string | null;
  }> {
    const response = await fetch(`/api/deals/${dealRoomId}/thread-summary`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ threadId })
    });
    const payload = (await response.json()) as ApiPayload<{
      summary: unknown;
      runId: string;
      usedFallback: boolean;
      fallbackReason: string | null;
    }>;
    if (!response.ok || !payload.success || !payload.data) throw new Error(payload.error ?? "Failed to summarize thread");
    return payload.data;
  }
};

