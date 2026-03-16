import type { ActionOutcome } from "@/types/outcome";

interface ApiPayload<T> {
  success: boolean;
  data: T | null;
  error: string | null;
}

export const outcomeClientService = {
  async capture(input: {
    ownerId?: string;
    customerId?: string;
    opportunityId?: string;
    workItemId?: string;
    followupId?: string;
    communicationInputId?: string;
    prepCardId?: string;
    contentDraftId?: string;
    outcomeType?: "followup_result" | "quote_result" | "meeting_result" | "task_result" | "manager_intervention_result";
    resultStatus?: "positive_progress" | "neutral" | "stalled" | "risk_increased" | "closed_won" | "closed_lost";
    stageChanged?: boolean;
    oldStage?: string;
    newStage?: string;
    customerSentimentShift?: "improved" | "unchanged" | "worsened" | "unknown";
    keyOutcomeSummary?: string;
    newObjections?: string[];
    newRisks?: string[];
    nextStepDefined?: boolean;
    nextStepText?: string;
    followupDueAt?: string;
    usedPrepCard?: boolean;
    usedDraft?: boolean;
    usefulnessRating?: "helpful" | "somewhat_helpful" | "not_helpful" | "unknown";
    notes?: string;
    autoInfer?: boolean;
    summaryHint?: string;
    linkAdoptionIds?: string[];
  }): Promise<{
    outcome: ActionOutcome;
    runId: string | null;
    usedFallback: boolean;
    fallbackReason: string | null;
  }> {
    const response = await fetch("/api/outcomes/capture", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });
    const payload = (await response.json()) as ApiPayload<{
      outcome: ActionOutcome;
      runId: string | null;
      usedFallback: boolean;
      fallbackReason: string | null;
    }>;
    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to capture action outcome");
    }
    return payload.data;
  },

  async list(params?: {
    ownerId?: string;
    customerId?: string;
    outcomeType?: "followup_result" | "quote_result" | "meeting_result" | "task_result" | "manager_intervention_result";
    limit?: number;
  }): Promise<ActionOutcome[]> {
    const query = new URLSearchParams();
    if (params?.ownerId) query.set("ownerId", params.ownerId);
    if (params?.customerId) query.set("customerId", params.customerId);
    if (params?.outcomeType) query.set("outcomeType", params.outcomeType);
    if (params?.limit) query.set("limit", String(params.limit));

    const response = await fetch(`/api/outcomes${query.size > 0 ? `?${query.toString()}` : ""}`, {
      method: "GET"
    });
    const payload = (await response.json()) as ApiPayload<ActionOutcome[]>;
    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to load action outcomes");
    }
    return payload.data;
  }
};
