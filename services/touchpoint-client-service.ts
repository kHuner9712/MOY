import type { CalendarEvent, DocumentAsset, EmailThread, ExternalTouchpointReviewResult, TouchpointHubView } from "@/types/touchpoint";

interface ApiPayload<T> {
  success: boolean;
  data: T | null;
  error: string | null;
}

interface HubResponse {
  hub: TouchpointHubView;
  summary: {
    totalEvents: number;
    waitingReplyThreads: number;
    upcomingMeetings: number;
    documentUpdates: number;
  };
}

export const touchpointClientService = {
  async getHub(input?: {
    ownerId?: string;
    customerId?: string;
    dealRoomId?: string;
    type?: "email" | "meeting" | "document";
    limit?: number;
  }): Promise<HubResponse> {
    const query = new URLSearchParams();
    if (input?.ownerId) query.set("ownerId", input.ownerId);
    if (input?.customerId) query.set("customerId", input.customerId);
    if (input?.dealRoomId) query.set("dealRoomId", input.dealRoomId);
    if (input?.type) query.set("type", input.type);
    if (input?.limit !== undefined) query.set("limit", String(input.limit));

    const response = await fetch(`/api/touchpoints${query.toString() ? `?${query.toString()}` : ""}`, {
      method: "GET"
    });
    const payload = (await response.json()) as ApiPayload<HubResponse>;
    if (!response.ok || !payload.success || !payload.data) throw new Error(payload.error ?? "Failed to load touchpoint hub");
    return payload.data;
  },

  async createEmailThread(input: {
    threadId?: string;
    ownerId?: string;
    customerId?: string;
    opportunityId?: string;
    dealRoomId?: string;
    subject?: string;
    participants?: string[];
    summary?: string;
    direction?: "inbound" | "outbound" | "draft";
    messageSubject?: string;
    messageBodyText?: string;
    messageBodyMarkdown?: string;
    status?: "draft" | "sent" | "received" | "failed";
  }): Promise<{ threadId: string; message: unknown | null }> {
    const response = await fetch("/api/touchpoints/email-threads", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });
    const payload = (await response.json()) as ApiPayload<{ threadId: string; message: unknown | null }>;
    if (!response.ok || !payload.success || !payload.data) throw new Error(payload.error ?? "Failed to save email touchpoint");
    return payload.data;
  },

  async generateEmailDraft(input: {
    contextType: "followup" | "quote" | "meeting_confirm" | "meeting_followup" | "manager_support";
    customerId?: string;
    opportunityId?: string;
    dealRoomId?: string;
    threadId?: string;
    extraInstruction?: string;
  }): Promise<{
    threadId: string;
    messageId: string;
    runId: string;
    usedFallback: boolean;
    fallbackReason: string | null;
  }> {
    const response = await fetch("/api/touchpoints/email-drafts/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });
    const payload = (await response.json()) as ApiPayload<{
      threadId: string;
      messageId: string;
      runId: string;
      usedFallback: boolean;
      fallbackReason: string | null;
    }>;
    if (!response.ok || !payload.success || !payload.data) throw new Error(payload.error ?? "Failed to generate email draft");
    return payload.data;
  },

  async createCalendarEvent(input: {
    eventId?: string;
    ownerId?: string;
    customerId?: string;
    opportunityId?: string;
    dealRoomId?: string;
    eventType?: "customer_meeting" | "demo" | "proposal_review" | "internal_strategy" | "manager_intervention";
    title?: string;
    description?: string;
    attendees?: string[];
    startAt?: string;
    endAt?: string;
    meetingStatus?: "scheduled" | "completed" | "cancelled" | "no_show";
    agendaSummary?: string;
    notesSummary?: string;
    autoGeneratePrep?: boolean;
    autoGenerateAgenda?: boolean;
    completeAndGenerateFollowup?: boolean;
    captureOutcome?: boolean;
  }): Promise<{
    event: CalendarEvent;
    prepCard?: unknown | null;
    agendaResult?: unknown | null;
    summaryResult?: unknown | null;
    linkedWorkItem?: unknown | null;
    usedFallback?: boolean;
  }> {
    const response = await fetch("/api/touchpoints/calendar-events", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });
    const payload = (await response.json()) as ApiPayload<{
      event: CalendarEvent;
      prepCard?: unknown | null;
      agendaResult?: unknown | null;
      summaryResult?: unknown | null;
      linkedWorkItem?: unknown | null;
      usedFallback?: boolean;
    }>;
    if (!response.ok || !payload.success || !payload.data) throw new Error(payload.error ?? "Failed to operate calendar event");
    return payload.data;
  },

  async completeMeetingFollowup(input: {
    eventId: string;
    notesSummary?: string;
    captureOutcome?: boolean;
  }): Promise<{
    event: CalendarEvent;
    summaryResult: unknown;
    runId: string;
    usedFallback: boolean;
    fallbackReason: string | null;
    draft: unknown;
    linkedWorkItem: unknown;
  }> {
    const response = await fetch("/api/touchpoints/meeting-followup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });
    const payload = (await response.json()) as ApiPayload<{
      event: CalendarEvent;
      summaryResult: unknown;
      runId: string;
      usedFallback: boolean;
      fallbackReason: string | null;
      draft: unknown;
      linkedWorkItem: unknown;
    }>;
    if (!response.ok || !payload.success || !payload.data) throw new Error(payload.error ?? "Failed to complete meeting followup");
    return payload.data;
  },

  async uploadDocument(input: {
    ownerId?: string;
    customerId?: string;
    opportunityId?: string;
    dealRoomId?: string;
    sourceType?: "upload" | "email_attachment" | "generated" | "imported";
    documentType?: "proposal" | "quote" | "contract_draft" | "meeting_note" | "case_study" | "product_material" | "other";
    title: string;
    fileName: string;
    mimeType?: string;
    storagePath?: string;
    extractedText?: string;
    tags?: string[];
    linkedPrepCardId?: string;
    linkedDraftId?: string;
    autoSummarize?: boolean;
  }): Promise<{
    asset: DocumentAsset;
    summaryResult: unknown | null;
    summaryRunId: string | null;
    usedFallback: boolean;
    fallbackReason: string | null;
    linkedWorkItems: unknown[];
  }> {
    const response = await fetch("/api/touchpoints/documents/upload", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });
    const payload = (await response.json()) as ApiPayload<{
      asset: DocumentAsset;
      summaryResult: unknown | null;
      summaryRunId: string | null;
      usedFallback: boolean;
      fallbackReason: string | null;
      linkedWorkItems: unknown[];
    }>;
    if (!response.ok || !payload.success || !payload.data) throw new Error(payload.error ?? "Failed to upload document");
    return payload.data;
  },

  async summarizeDocument(documentId: string): Promise<{
    asset: DocumentAsset;
    result: unknown;
    runId: string;
    usedFallback: boolean;
    fallbackReason: string | null;
    linkedWorkItems: unknown[];
  }> {
    const response = await fetch("/api/touchpoints/documents/summarize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ documentId })
    });
    const payload = (await response.json()) as ApiPayload<{
      asset: DocumentAsset;
      result: unknown;
      runId: string;
      usedFallback: boolean;
      fallbackReason: string | null;
      linkedWorkItems: unknown[];
    }>;
    if (!response.ok || !payload.success || !payload.data) throw new Error(payload.error ?? "Failed to summarize document");
    return payload.data;
  },

  async review(input?: {
    ownerId?: string;
    customerId?: string;
    dealRoomId?: string;
    sinceDays?: number;
  }): Promise<{
    result: ExternalTouchpointReviewResult;
    runId: string;
    usedFallback: boolean;
    fallbackReason: string | null;
  }> {
    const query = new URLSearchParams();
    if (input?.ownerId) query.set("ownerId", input.ownerId);
    if (input?.customerId) query.set("customerId", input.customerId);
    if (input?.dealRoomId) query.set("dealRoomId", input.dealRoomId);
    if (input?.sinceDays !== undefined) query.set("sinceDays", String(input.sinceDays));
    const response = await fetch(`/api/touchpoints/review${query.toString() ? `?${query.toString()}` : ""}`, {
      method: "GET"
    });
    const payload = (await response.json()) as ApiPayload<{
      result: ExternalTouchpointReviewResult;
      runId: string;
      usedFallback: boolean;
      fallbackReason: string | null;
    }>;
    if (!response.ok || !payload.success || !payload.data) throw new Error(payload.error ?? "Failed to generate touchpoint review");
    return payload.data;
  },

  async linkToDeal(input: {
    targetType: "email_thread" | "calendar_event" | "document_asset";
    targetId: string;
    customerId?: string;
    opportunityId?: string;
    dealRoomId?: string;
  }): Promise<void> {
    const response = await fetch("/api/touchpoints/link-to-deal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });
    const payload = (await response.json()) as ApiPayload<{ linked: boolean }>;
    if (!response.ok || !payload.success || !payload.data) throw new Error(payload.error ?? "Failed to link touchpoint to deal");
  }
};

export type TouchpointHubSummary = HubResponse["summary"];
export type TouchpointEmailThread = EmailThread;
