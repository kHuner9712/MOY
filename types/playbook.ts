export type PlaybookScopeType = "org" | "team" | "user";

export type PlaybookType =
  | "objection_handling"
  | "customer_segment"
  | "quote_strategy"
  | "meeting_strategy"
  | "followup_rhythm"
  | "risk_recovery";

export type PlaybookStatus = "active" | "draft" | "archived";

export interface Playbook {
  id: string;
  orgId: string;
  scopeType: PlaybookScopeType;
  ownerUserId: string | null;
  playbookType: PlaybookType;
  title: string;
  summary: string;
  status: PlaybookStatus;
  confidenceScore: number;
  applicabilityNotes: string;
  sourceSnapshot: Record<string, unknown>;
  generatedBy: string;
  aiRunId: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface PlaybookEntry {
  id: string;
  orgId: string;
  playbookId: string;
  entryTitle: string;
  entrySummary: string;
  conditions: Record<string, unknown>;
  recommendedActions: string[];
  cautionNotes: string[];
  evidenceSnapshot: Record<string, unknown>;
  successSignal: Record<string, unknown>;
  failureModes: string[];
  confidenceScore: number;
  sortOrder: number;
  createdAt: string;
  updatedAt: string;
}

export type PlaybookFeedbackType = "useful" | "not_useful" | "outdated" | "inaccurate" | "adopted";

export interface PlaybookFeedback {
  id: string;
  orgId: string;
  userId: string;
  playbookId: string;
  playbookEntryId: string | null;
  feedbackType: PlaybookFeedbackType;
  feedbackText: string | null;
  createdAt: string;
}

export interface PlaybookWithEntries {
  playbook: Playbook;
  entries: PlaybookEntry[];
}
