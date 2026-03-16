import type { SuggestionAdoption, SuggestionAdoptionContext, SuggestionAdoptionType, SuggestionTargetType } from "@/types/outcome";

interface ApiPayload<T> {
  success: boolean;
  data: T | null;
  error: string | null;
}

export const suggestionAdoptionClientService = {
  async track(input: {
    targetType: SuggestionTargetType;
    targetId: string;
    adoptionType: SuggestionAdoptionType;
    adoptionContext: SuggestionAdoptionContext;
    editDistanceHint?: number;
    linkedOutcomeId?: string;
  }): Promise<SuggestionAdoption> {
    const response = await fetch("/api/adoptions/track", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });

    const payload = (await response.json()) as ApiPayload<SuggestionAdoption>;
    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to track suggestion adoption");
    }
    return payload.data;
  }
};
