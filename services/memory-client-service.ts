import type { UserMemoryItem, UserMemoryProfile } from "@/types/memory";

interface ApiPayload<T> {
  success: boolean;
  data: T | null;
  error: string | null;
}

export const memoryClientService = {
  async getProfile(userId?: string): Promise<{ profile: UserMemoryProfile | null; items: UserMemoryItem[] }> {
    const query = userId ? `?userId=${encodeURIComponent(userId)}` : "";
    const response = await fetch(`/api/memory/profile${query}`, {
      method: "GET"
    });
    const payload = (await response.json()) as ApiPayload<{ profile: UserMemoryProfile | null; items: UserMemoryItem[] }>;
    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to load memory profile");
    }
    return payload.data;
  },

  async refresh(params?: { userId?: string; sourceWindowDays?: number }): Promise<{
    profile: UserMemoryProfile;
    items: UserMemoryItem[];
    usedFallback: boolean;
    fallbackReason: string | null;
  }> {
    const response = await fetch("/api/memory/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        userId: params?.userId,
        sourceWindowDays: params?.sourceWindowDays
      })
    });
    const payload = (await response.json()) as ApiPayload<{
      profile: UserMemoryProfile;
      items: UserMemoryItem[];
      usedFallback: boolean;
      fallbackReason: string | null;
    }>;
    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to refresh memory");
    }
    return payload.data;
  },

  async feedback(params: {
    memoryItemId: string;
    feedbackType: "accurate" | "inaccurate" | "outdated" | "useful" | "not_useful";
    feedbackText?: string;
  }): Promise<{ feedbackId: string }> {
    const response = await fetch(`/api/memory/items/${params.memoryItemId}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        feedbackType: params.feedbackType,
        feedbackText: params.feedbackText
      })
    });

    const payload = (await response.json()) as ApiPayload<{ feedbackId: string }>;
    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to submit memory feedback");
    }
    return payload.data;
  }
};
