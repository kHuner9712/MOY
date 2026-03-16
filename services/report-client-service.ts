import type { GenerateReportInput, GeneratedReport, ReportType } from "@/types/report";

interface ApiPayload<T> {
  success: boolean;
  data: T | null;
  error: string | null;
}

export const reportClientService = {
  async list(params?: { reportType?: ReportType; from?: string; to?: string; limit?: number }): Promise<GeneratedReport[]> {
    const query = new URLSearchParams();
    if (params?.reportType) query.set("reportType", params.reportType);
    if (params?.from) query.set("from", params.from);
    if (params?.to) query.set("to", params.to);
    if (params?.limit) query.set("limit", String(params.limit));

    const response = await fetch(`/api/reports${query.toString() ? `?${query.toString()}` : ""}`, {
      method: "GET"
    });

    const payload = (await response.json()) as ApiPayload<GeneratedReport[]>;
    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to list reports");
    }
    return payload.data;
  },

  async getById(reportId: string): Promise<GeneratedReport> {
    const response = await fetch(`/api/reports/${reportId}`, {
      method: "GET"
    });

    const payload = (await response.json()) as ApiPayload<GeneratedReport>;
    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to load report");
    }
    return payload.data;
  },

  async generate(input: GenerateReportInput): Promise<GeneratedReport> {
    const response = await fetch("/api/reports/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });

    const payload = (await response.json()) as ApiPayload<GeneratedReport>;
    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to generate report");
    }
    return payload.data;
  }
};
