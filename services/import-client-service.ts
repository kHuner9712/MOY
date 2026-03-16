import type { Database } from "@/types/database";
import type { DedupeMatchGroup, ImportAuditEvent, ImportJob, ImportJobColumn, ImportJobRow, ImportTemplate } from "@/types/import";

interface ApiPayload<T> {
  success: boolean;
  data: T | null;
  error: string | null;
}

async function readPayload<T>(response: Response): Promise<T> {
  const payload = (await response.json()) as ApiPayload<T>;
  if (!response.ok || !payload.success || !payload.data) {
    throw new Error(payload.error ?? "Request failed");
  }
  return payload.data;
}

export const importClientService = {
  async listJobs(limit = 40): Promise<{ jobs: ImportJob[]; canWrite: boolean }> {
    const response = await fetch(`/api/imports?limit=${limit}`, { method: "GET" });
    return readPayload<{ jobs: ImportJob[]; canWrite: boolean }>(response);
  },

  async createJob(payload: {
    importType: Database["public"]["Enums"]["import_type"];
    sourceType: Database["public"]["Enums"]["import_source_type"];
    fileName: string;
    storagePath?: string;
  }): Promise<{ job: ImportJob }> {
    const response = await fetch("/api/imports/create", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return readPayload<{ job: ImportJob }>(response);
  },

  async upload(payload: {
    jobId: string;
    sourceType: Database["public"]["Enums"]["import_source_type"];
    fileText?: string;
    fileBase64?: string;
  }): Promise<{ columns: ImportJobColumn[]; totalRows: number }> {
    const response = await fetch(`/api/imports/${payload.jobId}/upload`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return readPayload<{ columns: ImportJobColumn[]; totalRows: number }>(response);
  },

  async detectMapping(payload: {
    jobId: string;
    enableAiAssist?: boolean;
  }): Promise<{
    suggestions: Array<{
      sourceColumnName: string;
      mappedTargetEntity: Database["public"]["Enums"]["import_entity_type"] | null;
      mappedTargetField: string | null;
      confidence: number;
      detectedType: string | null;
      warning: string | null;
    }>;
    usedFallback: boolean;
    fallbackReason: string | null;
  }> {
    const response = await fetch(`/api/imports/${payload.jobId}/detect-mapping`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return readPayload(response);
  },

  async saveMapping(payload: {
    jobId: string;
    mapping: Array<{
      columnId: string;
      mappedTargetEntity: Database["public"]["Enums"]["import_entity_type"] | null;
      mappedTargetField: string | null;
      normalizationRule?: Record<string, unknown>;
    }>;
  }): Promise<{ ok: true }> {
    const response = await fetch(`/api/imports/${payload.jobId}/save-mapping`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return readPayload<{ ok: true }>(response);
  },

  async validate(jobId: string): Promise<{
    totalRows: number;
    validRows: number;
    invalidRows: number;
    duplicateRows: number;
  }> {
    const response = await fetch(`/api/imports/${jobId}/validate`, {
      method: "POST"
    });
    return readPayload(response);
  },

  async dedupe(jobId: string): Promise<{
    groups: DedupeMatchGroup[];
    candidateRows: number;
  }> {
    const response = await fetch(`/api/imports/${jobId}/dedupe`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({})
    });
    return readPayload(response);
  },

  async applyDedupeResolution(payload: {
    jobId: string;
    resolutions: Array<{
      groupId: string;
      action: Database["public"]["Enums"]["dedupe_resolution_action"];
    }>;
  }): Promise<{ groups: DedupeMatchGroup[] }> {
    const response = await fetch(`/api/imports/${payload.jobId}/dedupe`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ resolutions: payload.resolutions })
    });
    return readPayload(response);
  },

  async execute(payload: {
    jobId: string;
    runReviewSummary?: boolean;
  }): Promise<{
    job: ImportJob;
    counters: {
      importedRows: number;
      skippedRows: number;
      mergedRows: number;
      errorRows: number;
      importedCustomers: number;
      importedOpportunities: number;
      importedFollowups: number;
    };
    bootstrap: {
      generatedAlerts: number;
      generatedWorkItems: number;
      suggestedDealRooms: number;
      touchedCustomers: number;
    };
    review: {
      review: {
        summary: string;
        issues: string[];
        recommended_cleanup: string[];
        recommended_next_steps: string[];
      };
      runId: string;
      usedFallback: boolean;
      fallbackReason: string | null;
    } | null;
  }> {
    const response = await fetch(`/api/imports/${payload.jobId}/execute`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return readPayload(response);
  },

  async getJob(jobId: string): Promise<{
    job: ImportJob;
    columns: ImportJobColumn[];
    dedupeGroups: DedupeMatchGroup[];
    canWrite: boolean;
  }> {
    const response = await fetch(`/api/imports/${jobId}`, { method: "GET" });
    return readPayload(response);
  },

  async getRows(jobId: string, limit = 120, offset = 0): Promise<{ rows: ImportJobRow[] }> {
    const response = await fetch(`/api/imports/${jobId}/rows?limit=${limit}&offset=${offset}`, { method: "GET" });
    return readPayload(response);
  },

  async getAudit(jobId: string, limit = 120): Promise<{ events: ImportAuditEvent[] }> {
    const response = await fetch(`/api/imports/${jobId}/audit?limit=${limit}`, { method: "GET" });
    return readPayload(response);
  },

  async listTemplates(importType?: Database["public"]["Enums"]["import_type"]): Promise<{ templates: ImportTemplate[] }> {
    const params = new URLSearchParams();
    if (importType) params.set("importType", importType);
    const response = await fetch(`/api/imports/templates${params.toString() ? `?${params.toString()}` : ""}`, { method: "GET" });
    return readPayload(response);
  },

  async createTemplate(payload: {
    templateName: string;
    importType: Database["public"]["Enums"]["import_type"];
    columnMapping: Record<string, unknown>;
    normalizationConfig?: Record<string, unknown>;
    isDefault?: boolean;
  }): Promise<{ template: ImportTemplate }> {
    const response = await fetch("/api/imports/templates", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    return readPayload(response);
  }
};

