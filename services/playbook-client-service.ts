import type { PlaybookFeedback, PlaybookWithEntries } from "@/types/playbook";

interface ApiPayload<T> {
  success: boolean;
  data: T | null;
  error: string | null;
}

export const playbookClientService = {
  async list(params?: {
    ownerUserId?: string;
    scopeType?: "org" | "team" | "user";
    playbookType?: "objection_handling" | "customer_segment" | "quote_strategy" | "meeting_strategy" | "followup_rhythm" | "risk_recovery";
    includeEntries?: boolean;
    limit?: number;
  }): Promise<PlaybookWithEntries[]> {
    const query = new URLSearchParams();
    if (params?.ownerUserId) query.set("ownerUserId", params.ownerUserId);
    if (params?.scopeType) query.set("scopeType", params.scopeType);
    if (params?.playbookType) query.set("playbookType", params.playbookType);
    if (params?.includeEntries !== undefined) query.set("includeEntries", String(params.includeEntries));
    if (params?.limit) query.set("limit", String(params.limit));

    const response = await fetch(`/api/playbooks${query.size > 0 ? `?${query.toString()}` : ""}`, {
      method: "GET"
    });
    const payload = (await response.json()) as ApiPayload<PlaybookWithEntries[]>;
    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to load playbooks");
    }
    return payload.data;
  },

  async compile(input?: {
    scopeType?: "org" | "team" | "user";
    ownerUserId?: string | null;
    periodStart?: string;
    periodEnd?: string;
    title?: string;
  }): Promise<{
    playbook: PlaybookWithEntries;
    runId: string;
    usedFallback: boolean;
    fallbackReason: string | null;
  }> {
    const response = await fetch("/api/playbooks/compile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input ?? {})
    });
    const payload = (await response.json()) as ApiPayload<{
      playbook: PlaybookWithEntries;
      runId: string;
      usedFallback: boolean;
      fallbackReason: string | null;
    }>;
    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to compile playbook");
    }
    return payload.data;
  },

  async feedback(playbookId: string, input: {
    playbookEntryId?: string;
    feedbackType: "useful" | "not_useful" | "outdated" | "inaccurate" | "adopted";
    feedbackText?: string;
  }): Promise<PlaybookFeedback> {
    const response = await fetch(`/api/playbooks/${playbookId}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });
    const payload = (await response.json()) as ApiPayload<PlaybookFeedback>;
    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to save playbook feedback");
    }
    return payload.data;
  }
};
