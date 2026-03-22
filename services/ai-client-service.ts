import type { AiRun, CustomerHealthResult, FollowupAnalysisResult, LeakAlertInferenceResult } from "@/types/ai";

interface ApiPayload<T> {
  success: boolean;
  data: T | null;
  error: string | null;
}

export const aiClientService = {
  async runCustomerAnalysis(customerId: string): Promise<{
    customerHealthRunId: string;
    leakRunId: string | null;
    customerHealth: CustomerHealthResult;
    leakInference: LeakAlertInferenceResult | null;
    leakAlertAction: "created" | "updated" | "deduped" | null;
    alertWorkItem: { alertId: string; workItemId: string; created: boolean } | null;
    usedFallback: boolean;
  }> {
    const response = await fetch("/api/ai/customer-analysis", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ customerId })
    });

    const payload = (await response.json()) as ApiPayload<{
      customerHealthRunId: string;
      leakRunId: string | null;
      customerHealth: CustomerHealthResult;
      leakInference: LeakAlertInferenceResult | null;
      leakAlertAction: "created" | "updated" | "deduped" | null;
      alertWorkItem: { alertId: string; workItemId: string; created: boolean } | null;
      usedFallback: boolean;
    }>;

    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "AI 重新分析失败");
    }
    return payload.data;
  },

  async listCustomerRuns(customerId: string, limit = 8): Promise<AiRun[]> {
    const response = await fetch(`/api/ai/runs?customerId=${encodeURIComponent(customerId)}&limit=${limit}`, {
      method: "GET"
    });
    const payload = (await response.json()) as ApiPayload<AiRun[]>;
    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "获取 AI 历史失败");
    }
    return payload.data;
  },

  async getConfigStatus(): Promise<{
    provider: string;
    configured: boolean;
    model: string;
    reasonerModel: string;
    strictModeEnabled: boolean;
  }> {
    const response = await fetch("/api/ai/config-status", {
      method: "GET"
    });
    const payload = (await response.json()) as ApiPayload<{
      provider: string;
      configured: boolean;
      model: string;
      reasonerModel: string;
      strictModeEnabled: boolean;
    }>;
    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "获取 AI 配置状态失败");
    }
    return payload.data;
  },

  async runFollowupAnalysis(customerId: string, followupId: string): Promise<{
    runId: string;
    status: string;
    resultSource: "provider" | "fallback";
    usedFallback: boolean;
    fallbackReason: string | null;
    result: FollowupAnalysisResult;
    leakInference: LeakAlertInferenceResult | null;
    leakAlertAction: "created" | "updated" | "deduped" | null;
    alertWorkItem: { alertId: string; workItemId: string; created: boolean } | null;
  }> {
    const response = await fetch("/api/ai/followup-analysis", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ customerId, followupId })
    });

    const payload = (await response.json()) as ApiPayload<{
      runId: string;
      status: string;
      resultSource: "provider" | "fallback";
      usedFallback: boolean;
      fallbackReason: string | null;
      result: FollowupAnalysisResult;
      leakInference: LeakAlertInferenceResult | null;
      leakAlertAction: "created" | "updated" | "deduped" | null;
      alertWorkItem: { alertId: string; workItemId: string; created: boolean } | null;
    }>;

    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "跟进分析失败");
    }
    return payload.data;
  }
};
