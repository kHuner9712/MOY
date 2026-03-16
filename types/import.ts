export type ImportType = "customers" | "opportunities" | "followups" | "mixed";
export type ImportSourceType = "csv" | "xlsx" | "manual_table" | "demo_bootstrap";
export type ImportJobStatus =
  | "uploaded"
  | "parsing"
  | "mapping"
  | "validating"
  | "preview_ready"
  | "importing"
  | "completed"
  | "failed"
  | "cancelled";
export type ImportRowStatus = "pending" | "valid" | "invalid" | "duplicate_candidate" | "merge_candidate" | "imported" | "skipped" | "failed";
export type ImportMergeResolution = "create_new" | "merge_existing" | "skip";
export type ImportEntityType = "customer" | "opportunity" | "followup" | "mixed";

export type ImportAuditEventType =
  | "uploaded"
  | "parsed"
  | "mapping_saved"
  | "validation_run"
  | "dedupe_reviewed"
  | "import_started"
  | "row_imported"
  | "row_failed"
  | "completed"
  | "cancelled";

export interface ImportJob {
  id: string;
  orgId: string;
  initiatedBy: string;
  importType: ImportType;
  sourceType: ImportSourceType;
  fileName: string;
  storagePath: string | null;
  jobStatus: ImportJobStatus;
  totalRows: number;
  validRows: number;
  invalidRows: number;
  duplicateRows: number;
  importedRows: number;
  skippedRows: number;
  mergedRows: number;
  errorRows: number;
  summary: string | null;
  detailSnapshot: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
}

export interface ImportJobColumn {
  id: string;
  orgId: string;
  importJobId: string;
  sourceColumnName: string;
  sourceColumnIndex: number;
  detectedType: string | null;
  mappedTargetEntity: ImportEntityType | null;
  mappedTargetField: string | null;
  mappingConfidence: number | null;
  normalizationRule: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
}

export interface ImportJobRow {
  id: string;
  orgId: string;
  importJobId: string;
  sourceRowNo: number;
  rawPayload: Record<string, unknown>;
  normalizedPayload: Record<string, unknown>;
  rowStatus: ImportRowStatus;
  validationErrors: string[];
  duplicateCandidates: Array<Record<string, unknown>>;
  mergeResolution: ImportMergeResolution | null;
  importedEntityType: ImportEntityType | null;
  importedEntityId: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface ImportTemplate {
  id: string;
  orgId: string;
  templateName: string;
  importType: ImportType;
  columnMapping: Record<string, unknown>;
  normalizationConfig: Record<string, unknown>;
  isDefault: boolean;
  createdBy: string;
  createdAt: string;
  updatedAt: string;
}

export interface DedupeMatchGroup {
  id: string;
  orgId: string;
  importJobId: string;
  entityType: ImportEntityType;
  sourceRowIds: string[];
  existingEntityIds: string[];
  matchReason: string;
  confidenceScore: number;
  resolutionStatus: "pending" | "confirmed" | "ignored";
  resolutionAction: "create_new" | "merge" | "skip" | null;
  createdAt: string;
  updatedAt: string;
}

export interface ImportAuditEvent {
  id: string;
  orgId: string;
  importJobId: string;
  actorUserId: string | null;
  eventType: ImportAuditEventType;
  eventSummary: string;
  eventPayload: Record<string, unknown>;
  createdAt: string;
}
