import type { ContentDraft, MorningBrief } from "@/types/preparation";
import type { AlertItem } from "@/types/alert";
import type { WorkItem } from "@/types/work";

export type MobileDraftType = "capture" | "outcome" | "email_draft" | "touchpoint_note";
export type MobileDraftSyncStatus = "pending" | "synced" | "failed" | "discarded";
export type MobileInstallType = "browser" | "pwa";
export type OfflineActionType =
  | "create_capture_draft"
  | "create_outcome_draft"
  | "save_email_draft"
  | "quick_complete_task"
  | "snooze_task";
export type OfflineActionQueueStatus = "queued" | "processing" | "done" | "failed";

export interface MobileDraftSyncJob {
  id: string;
  orgId: string;
  userId: string;
  draftType: MobileDraftType;
  localDraftId: string;
  syncStatus: MobileDraftSyncStatus;
  targetEntityType: string | null;
  targetEntityId: string | null;
  summary: string | null;
  payloadSnapshot: Record<string, unknown>;
  errorMessage: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface MobileDeviceSession {
  id: string;
  orgId: string;
  userId: string;
  deviceLabel: string;
  installType: MobileInstallType;
  lastSeenAt: string;
  appVersion: string | null;
  pushCapable: boolean;
  metadata: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
}

export interface OfflineActionQueueItem {
  id: string;
  orgId: string;
  userId: string;
  actionType: OfflineActionType;
  actionPayload: Record<string, unknown>;
  queueStatus: OfflineActionQueueStatus;
  targetEntityType: string | null;
  targetEntityId: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface MobileTodayView {
  focusTheme: string;
  summary: string;
  mustDo: WorkItem[];
  prioritized: WorkItem[];
  alerts: AlertItem[];
}

export interface MobileBriefingsView {
  compactHeadline: string;
  topPriorities: string[];
  urgentRisks: string[];
  oneLineGuidance: string;
  morningBrief: MorningBrief | null;
  prepCards: Array<{ id: string; title: string; summary: string; status: string; cardType: string }>;
  drafts: ContentDraft[];
}
