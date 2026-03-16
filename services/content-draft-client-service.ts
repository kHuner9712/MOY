import type { ContentDraft, ContentDraftType } from "@/types/preparation";

interface ApiPayload<T> {
  success: boolean;
  data: T | null;
  error: string | null;
}

export const contentDraftClientService = {
  async generate(input: {
    draftType: ContentDraftType;
    customerId?: string;
    opportunityId?: string;
    prepCardId?: string;
    workItemId?: string;
    title?: string;
  }): Promise<{ draft: ContentDraft; usedFallback: boolean; runId: string }> {
    const response = await fetch("/api/drafts/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });
    const payload = (await response.json()) as ApiPayload<{ draft: ContentDraft; usedFallback: boolean; runId: string }>;
    if (!response.ok || !payload.success || !payload.data) throw new Error(payload.error ?? "Failed to generate content draft");
    return payload.data;
  },

  async feedback(draftId: string, feedbackType: "useful" | "not_useful" | "adopted" | "inaccurate", feedbackText?: string): Promise<void> {
    const response = await fetch(`/api/drafts/${draftId}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        feedbackType,
        feedbackText
      })
    });
    const payload = (await response.json()) as ApiPayload<unknown>;
    if (!response.ok || !payload.success) throw new Error(payload.error ?? "Failed to save draft feedback");
  }
};
