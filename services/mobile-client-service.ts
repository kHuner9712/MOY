import type { MobileBriefingsView } from "@/types/mobile";

interface ApiPayload<T> {
  success: boolean;
  data: T | null;
  error: string | null;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const payload = (await response.json()) as ApiPayload<T>;
  if (!response.ok || !payload.success || !payload.data) {
    throw new Error(payload.error ?? "Mobile request failed");
  }
  return payload.data;
}

export const mobileClientService = {
  async getBootstrap(date?: string) {
    const query = date ? `?date=${encodeURIComponent(date)}` : "";
    return request<{
      today: {
        focusTheme: string;
        summary: string;
        mustDoCount: number;
        prioritizedCount: number;
      };
      briefings: {
        compactHeadline: string;
        topPriorities: string[];
        urgentRisks: string[];
        oneLineGuidance: string;
      };
      touchpoints: {
        waitingReply: number;
        upcomingMeetings: number;
        documentUpdates: number;
      };
      sync: {
        pending: number;
        failed: number;
        recentSessions: Array<{ id: string; deviceLabel: string; installType: string; lastSeenAt: string }>;
      };
      manager: {
        escalatedDeals: number;
        blockedCheckpoints: number;
        openInterventions: number;
      } | null;
    }>(`/api/mobile/bootstrap${query}`);
  },

  async saveDraft(input: {
    localDraftId: string;
    draftType: "capture" | "outcome" | "email_draft" | "touchpoint_note";
    summary?: string;
    payload: Record<string, unknown>;
  }) {
    return request<{ job: { id: string; syncStatus: string } }>("/api/mobile/drafts/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });
  },

  async syncDraft(input: {
    localDraftId: string;
    draftType: "capture" | "outcome" | "email_draft" | "touchpoint_note";
    summary?: string;
    payload: Record<string, unknown>;
  }) {
    return request<{
      syncJobId: string;
      syncStatus: "synced" | "failed";
      targetEntityType: string | null;
      targetEntityId: string | null;
      message: string;
    }>("/api/mobile/drafts/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });
  },

  async getToday(date?: string) {
    const query = date ? `?date=${encodeURIComponent(date)}` : "";
    return request<{ plan: unknown; alerts: unknown[] }>(`/api/mobile/today${query}`);
  },

  async getBriefings(date?: string) {
    const query = date ? `?date=${encodeURIComponent(date)}` : "";
    return request<MobileBriefingsView>(`/api/mobile/briefings${query}`);
  },

  async getDealSummary(dealRoomId: string) {
    return request<{
      room: Record<string, unknown>;
      checkpoints: Array<Record<string, unknown>>;
      decisions: Array<Record<string, unknown>>;
      interventions: Array<Record<string, unknown>>;
      tasks: Array<Record<string, unknown>>;
      prepCards: Array<Record<string, unknown>>;
      outcomes: Array<Record<string, unknown>>;
      touchpoints: Record<string, unknown>;
    }>(`/api/mobile/deals/${dealRoomId}/summary`);
  },

  async getCustomerSummary(customerId: string) {
    return request<{
      customer: Record<string, unknown>;
      recentFollowups: Array<Record<string, unknown>>;
      recentPrepCards: Array<Record<string, unknown>>;
      recentOutcomes: Array<Record<string, unknown>>;
      dealRoom: Record<string, unknown> | null;
      touchpoints: Record<string, unknown>;
    }>(`/api/mobile/customers/${customerId}/summary`);
  },

  async getTouchpoints(params?: { customerId?: string; dealRoomId?: string }) {
    const query = new URLSearchParams();
    if (params?.customerId) query.set("customerId", params.customerId);
    if (params?.dealRoomId) query.set("dealRoomId", params.dealRoomId);
    const suffix = query.toString() ? `?${query.toString()}` : "";
    return request<Record<string, unknown>>(`/api/mobile/touchpoints${suffix}`);
  },

  async registerInstallSession(input: {
    deviceLabel: string;
    installType: "browser" | "pwa";
    appVersion?: string;
    pushCapable?: boolean;
    metadata?: Record<string, unknown>;
  }) {
    return request<{ session: Record<string, unknown> }>("/api/mobile/install-session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });
  }
};
