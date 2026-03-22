import type {
  AutomationRule,
  AutomationRuleRun,
  BusinessEvent,
  CustomerHealthSnapshot,
  ExecutiveBrief,
  ExecutiveBriefType,
  ExecutiveCockpitSummary,
  RenewalWatchItem
} from "@/types/automation";
import type { OrgMemberRole } from "@/types/productization";

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

export interface AutomationCenterPayload {
  rules: AutomationRule[];
  recentRuns: AutomationRuleRun[];
  openEvents: number;
  access: {
    role: OrgMemberRole;
    canManageRules: boolean;
    canRunRules: boolean;
  };
}

export interface AutomationRunPayload {
  totalRules: number;
  totalMatches: number;
  totalActions: number;
  failedRules: string[];
  runs: AutomationRuleRun[];
}

export interface ExecutiveHealthPayload {
  healthSnapshots: CustomerHealthSnapshot[];
  renewalWatch: RenewalWatchItem[];
}

export interface CustomerHealthDetailPayload {
  customerId: string;
  snapshot: CustomerHealthSnapshot | null;
  relatedEvents: BusinessEvent[];
  renewalWatch: RenewalWatchItem | null;
}

export interface DealOpsEventsPayload {
  dealRoomId: string;
  customerId: string;
  events: BusinessEvent[];
  recommendedActions: string[];
}

export const executiveClientService = {
  async getAutomationCenter(): Promise<AutomationCenterPayload> {
    const response = await fetch("/api/settings/automation", { method: "GET" });
    return readPayload<AutomationCenterPayload>(response);
  },

  async toggleAutomationRule(ruleId: string, isEnabled: boolean): Promise<AutomationRule> {
    const response = await fetch("/api/settings/automation", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ruleId, isEnabled })
    });
    return readPayload<AutomationRule>(response);
  },

  async runAutomationRules(input?: { ruleIds?: string[]; ownerId?: string }): Promise<AutomationRunPayload> {
    const response = await fetch("/api/settings/automation/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ruleIds: input?.ruleIds,
        ownerId: input?.ownerId
      })
    });
    return readPayload<AutomationRunPayload>(response);
  },

  async getExecutiveSummary(input?: { refresh?: boolean; ownerId?: string }): Promise<ExecutiveCockpitSummary> {
    const query = new URLSearchParams();
    if (input?.refresh) query.set("refresh", "true");
    if (input?.ownerId) query.set("ownerId", input.ownerId);
    const response = await fetch(`/api/executive/summary${query.toString() ? `?${query.toString()}` : ""}`, { method: "GET" });
    return readPayload<ExecutiveCockpitSummary>(response);
  },

  async getExecutiveEvents(input?: {
    status?: Array<"open" | "acknowledged" | "resolved" | "ignored">;
    eventType?: Array<BusinessEvent["eventType"]>;
    limit?: number;
  }): Promise<{ events: BusinessEvent[] }> {
    const query = new URLSearchParams();
    for (const status of input?.status ?? []) query.append("status", status);
    for (const eventType of input?.eventType ?? []) query.append("eventType", eventType);
    if (typeof input?.limit === "number") query.set("limit", String(input.limit));
    const response = await fetch(`/api/executive/events${query.toString() ? `?${query.toString()}` : ""}`, { method: "GET" });
    return readPayload<{ events: BusinessEvent[] }>(response);
  },

  async getExecutiveHealth(input?: { ownerId?: string }): Promise<ExecutiveHealthPayload> {
    const query = new URLSearchParams();
    if (input?.ownerId) query.set("ownerId", input.ownerId);
    const response = await fetch(`/api/executive/health${query.toString() ? `?${query.toString()}` : ""}`, { method: "GET" });
    return readPayload<ExecutiveHealthPayload>(response);
  },

  async getExecutiveBriefs(input?: {
    briefType?: ExecutiveBriefType[];
    limit?: number;
  }): Promise<{ briefs: ExecutiveBrief[] }> {
    const query = new URLSearchParams();
    for (const briefType of input?.briefType ?? []) query.append("briefType", briefType);
    if (typeof input?.limit === "number") query.set("limit", String(input.limit));
    const response = await fetch(`/api/executive/briefs${query.toString() ? `?${query.toString()}` : ""}`, { method: "GET" });
    return readPayload<{ briefs: ExecutiveBrief[] }>(response);
  },

  async generateExecutiveBrief(input: {
    briefType: ExecutiveBriefType;
    ownerId?: string;
    targetUserId?: string | null;
  }): Promise<{ brief: ExecutiveBrief; usedFallback: boolean; fallbackReason: string | null }> {
    const response = await fetch("/api/executive/briefs/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });
    return readPayload<{ brief: ExecutiveBrief; usedFallback: boolean; fallbackReason: string | null }>(response);
  },

  async ackBusinessEvent(eventId: string): Promise<BusinessEvent> {
    const response = await fetch(`/api/executive/events/${eventId}/ack`, { method: "POST" });
    const payload = await readPayload<{ event: BusinessEvent }>(response);
    return payload.event;
  },

  async resolveBusinessEvent(eventId: string): Promise<BusinessEvent> {
    const response = await fetch(`/api/executive/events/${eventId}/resolve`, { method: "POST" });
    const payload = await readPayload<{ event: BusinessEvent }>(response);
    return payload.event;
  },

  async ignoreBusinessEvent(eventId: string): Promise<BusinessEvent> {
    const response = await fetch(`/api/executive/events/${eventId}/ignore`, { method: "POST" });
    const payload = await readPayload<{ event: BusinessEvent }>(response);
    return payload.event;
  },

  async getCustomerHealthDetail(customerId: string): Promise<CustomerHealthDetailPayload> {
    const response = await fetch(`/api/customers/${customerId}/health`, { method: "GET" });
    return readPayload<CustomerHealthDetailPayload>(response);
  },

  async getDealOpsEvents(dealRoomId: string): Promise<DealOpsEventsPayload> {
    const response = await fetch(`/api/deals/${dealRoomId}/ops-events`, { method: "GET" });
    return readPayload<DealOpsEventsPayload>(response);
  }
};
