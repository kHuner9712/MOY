import type { OutcomeReview } from "@/types/outcome";
import type { Playbook } from "@/types/playbook";

interface ApiPayload<T> {
  success: boolean;
  data: T | null;
  error: string | null;
}

export interface ManagerOutcomePayload {
  periodType: "weekly" | "monthly";
  periodStart: string;
  periodEnd: string;
  summary: {
    totalOutcomes: number;
    positiveProgressRate: number;
    adoptionRate: number;
    adoptionPositiveRate: number;
  };
  bySales: Array<{
    userId: string;
    userName: string;
    totalOutcomes: number;
    positiveProgressRate: number;
    adoptionRate: number;
    adoptionPositiveRate: number;
  }>;
  effectivePatterns: string[];
  ineffectivePatterns: string[];
  customerStallReasons: string[];
  repeatedFailurePatterns: string[];
  recentReviews: OutcomeReview[];
  recentPlaybooks: Playbook[];
}

export const managerOutcomeClientService = {
  async get(periodType: "weekly" | "monthly" = "weekly"): Promise<ManagerOutcomePayload> {
    const response = await fetch(`/api/manager/outcomes?periodType=${periodType}`, {
      method: "GET"
    });
    const payload = (await response.json()) as ApiPayload<ManagerOutcomePayload>;
    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to load manager outcomes");
    }
    return payload.data;
  },

  async generateReview(input?: {
    reviewScope?: "team" | "org" | "user";
    targetUserId?: string;
    periodStart?: string;
    periodEnd?: string;
  }): Promise<{
    review: OutcomeReview;
    personalEffectiveness: {
      summary: string;
      helpful_suggestion_patterns: string[];
      ineffective_suggestion_patterns: string[];
      rhythm_adjustments: string[];
      coaching_focus_updates: string[];
      confidence_score: number;
      uncertainty_notes: string[];
    };
    runId: string;
    usedFallback: boolean;
    fallbackReason: string | null;
  }> {
    const response = await fetch("/api/manager/outcomes/review-generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input ?? {})
    });
    const payload = (await response.json()) as ApiPayload<{
      review: OutcomeReview;
      personalEffectiveness: {
        summary: string;
        helpful_suggestion_patterns: string[];
        ineffective_suggestion_patterns: string[];
        rhythm_adjustments: string[];
        coaching_focus_updates: string[];
        confidence_score: number;
        uncertainty_notes: string[];
      };
      runId: string;
      usedFallback: boolean;
      fallbackReason: string | null;
    }>;
    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to generate outcome review");
    }
    return payload.data;
  }
};
