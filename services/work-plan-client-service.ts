import type { TodayPlanView } from "@/types/work";

interface ApiPayload<T> {
  success: boolean;
  data: T | null;
  error: string | null;
}

export const workPlanClientService = {
  async getTodayPlan(date?: string): Promise<TodayPlanView | null> {
    const qs = date ? `?date=${encodeURIComponent(date)}` : "";
    const response = await fetch(`/api/today/plan${qs}`, {
      method: "GET"
    });
    const payload = (await response.json()) as ApiPayload<TodayPlanView | null>;
    if (!response.ok || !payload.success) {
      throw new Error(payload.error ?? "Failed to load today plan");
    }
    return payload.data ?? null;
  },

  async generateTodayPlan(force = true): Promise<{
    planDate: string;
    focusTheme: string;
    usedFallback: boolean;
    runId: string;
  }> {
    const response = await fetch("/api/today/generate-plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ force })
    });
    const payload = (await response.json()) as ApiPayload<{
      planDate: string;
      focusTheme: string;
      usedFallback: boolean;
      runId: string;
    }>;
    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to generate today plan");
    }
    return payload.data;
  }
};
