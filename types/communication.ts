export type CommunicationSourceType =
  | "manual_note"
  | "pasted_chat"
  | "call_summary"
  | "meeting_note"
  | "voice_transcript"
  | "imported_text";

export type CommunicationExtractionStatus = "pending" | "processing" | "completed" | "failed";

export interface CommunicationInputItem {
  id: string;
  orgId: string;
  customerId: string | null;
  customerName: string | null;
  ownerId: string;
  ownerName: string;
  sourceType: CommunicationSourceType;
  title: string;
  rawContent: string;
  inputLanguage: string;
  occurredAt: string;
  extractedFollowupId: string | null;
  extractionStatus: CommunicationExtractionStatus;
  extractionError: string | null;
  extractedData: Record<string, unknown>;
  createdBy: string;
  createdAt: string;
  updatedAt: string;
}

export interface CaptureExtractInput {
  sourceType: CommunicationSourceType;
  title?: string;
  rawContent: string;
  customerId?: string | null;
  inputLanguage?: string;
  occurredAt?: string;
}

export interface CaptureExtractResult {
  inputId: string;
  extractionStatus: CommunicationExtractionStatus;
  extractionError: string | null;
  matchedCustomerId: string | null;
  matchedCustomerName: string | null;
  confidenceOfMatch: number | null;
  autoApplied: boolean;
  requiresConfirmation: boolean;
  followupId: string | null;
  draftStatus: "draft" | "confirmed" | null;
  extracted: Record<string, unknown> | null;
  trace?: CaptureDownstreamTrace;
}

export interface CaptureConfirmInput {
  inputId: string;
  customerId?: string | null;
}

export interface CaptureConfirmResult {
  inputId: string;
  followupId: string | null;
  status: "confirmed" | "skipped";
  message: string;
  trace?: CaptureDownstreamTrace;
}

export interface CaptureDownstreamTrace {
  followupAnalysisRunId: string | null;
  leakAlertAction: "created" | "updated" | "deduped" | null;
  linkedWorkItemId: string | null;
  linkedWorkItemCreated: boolean | null;
  businessEventIds: string[];
  businessEventCreatedCount: number;
  businessEventUpdatedCount: number;
  downstreamErrors: string[];
}
