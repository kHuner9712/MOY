export type ExternalProviderType = "email" | "calendar" | "storage";
export type ExternalProviderName = "gmail" | "outlook" | "google_calendar" | "google_drive" | "dropbox" | "manual_upload";
export type ExternalConnectionStatus = "connected" | "disconnected" | "error";

export interface ExternalAccount {
  id: string;
  orgId: string;
  userId: string;
  providerType: ExternalProviderType;
  providerName: ExternalProviderName;
  accountLabel: string;
  connectionStatus: ExternalConnectionStatus;
  metadata: Record<string, unknown>;
  connectedAt: string;
  updatedAt: string;
}

export type EmailThreadStatus = "open" | "waiting_reply" | "replied" | "archived";
export type EmailSentimentHint = "positive" | "neutral" | "negative" | "unknown";
export type EmailMessageDirection = "inbound" | "outbound" | "draft";
export type EmailMessageStatus = "draft" | "sent" | "received" | "failed";
export type EmailMessageSourceType = "imported" | "manual" | "ai_generated";

export interface EmailThread {
  id: string;
  orgId: string;
  ownerId: string;
  ownerName: string;
  customerId: string | null;
  customerName: string | null;
  opportunityId: string | null;
  dealRoomId: string | null;
  externalAccountId: string | null;
  externalThreadRef: string | null;
  subject: string;
  participants: string[];
  latestMessageAt: string | null;
  threadStatus: EmailThreadStatus;
  sentimentHint: EmailSentimentHint;
  summary: string;
  sourceSnapshot: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
}

export interface EmailMessage {
  id: string;
  orgId: string;
  threadId: string;
  senderUserId: string | null;
  senderName: string | null;
  direction: EmailMessageDirection;
  externalMessageRef: string | null;
  messageSubject: string;
  messageBodyText: string;
  messageBodyMarkdown: string;
  sentAt: string | null;
  receivedAt: string | null;
  status: EmailMessageStatus;
  sourceType: EmailMessageSourceType;
  aiRunId: string | null;
  createdAt: string;
  updatedAt: string;
}

export type CalendarEventType = "customer_meeting" | "demo" | "proposal_review" | "internal_strategy" | "manager_intervention";
export type CalendarMeetingStatus = "scheduled" | "completed" | "cancelled" | "no_show";

export interface CalendarEvent {
  id: string;
  orgId: string;
  ownerId: string;
  ownerName: string;
  customerId: string | null;
  customerName: string | null;
  opportunityId: string | null;
  dealRoomId: string | null;
  externalAccountId: string | null;
  externalEventRef: string | null;
  eventType: CalendarEventType;
  title: string;
  description: string;
  attendees: string[];
  startAt: string;
  endAt: string;
  meetingStatus: CalendarMeetingStatus;
  agendaSummary: string;
  notesSummary: string;
  sourceSnapshot: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
}

export type DocumentAssetSourceType = "upload" | "email_attachment" | "generated" | "imported";
export type DocumentAssetType = "proposal" | "quote" | "contract_draft" | "meeting_note" | "case_study" | "product_material" | "other";

export interface DocumentAsset {
  id: string;
  orgId: string;
  ownerId: string;
  ownerName: string;
  customerId: string | null;
  customerName: string | null;
  opportunityId: string | null;
  dealRoomId: string | null;
  sourceType: DocumentAssetSourceType;
  documentType: DocumentAssetType;
  title: string;
  fileName: string;
  mimeType: string;
  storagePath: string | null;
  extractedText: string;
  summary: string;
  tags: string[];
  linkedPrepCardId: string | null;
  linkedDraftId: string | null;
  createdAt: string;
  updatedAt: string;
}

export type ExternalTouchpointType = "email" | "meeting" | "document";
export type ExternalTouchpointEventType =
  | "email_received"
  | "email_sent"
  | "draft_created"
  | "meeting_scheduled"
  | "meeting_completed"
  | "document_uploaded"
  | "document_reviewed"
  | "attachment_extracted";

export interface ExternalTouchpointEvent {
  id: string;
  orgId: string;
  ownerId: string | null;
  ownerName: string | null;
  customerId: string | null;
  customerName: string | null;
  opportunityId: string | null;
  dealRoomId: string | null;
  touchpointType: ExternalTouchpointType;
  eventType: ExternalTouchpointEventType;
  relatedRefType: string | null;
  relatedRefId: string | null;
  eventSummary: string;
  eventPayload: Record<string, unknown>;
  createdAt: string;
}

export interface EmailDraftResult {
  subject: string;
  opening: string;
  body: string;
  cta: string;
  cautionNotes: string[];
}

export interface MeetingAgendaResult {
  meetingGoal: string;
  agendaPoints: string[];
  mustCover: string[];
  riskNotes: string[];
  expectedNextStep: string[];
}

export interface MeetingFollowupResult {
  meetingSummary: string;
  decisionsMade: string[];
  nextActions: string[];
  followupMessageDraftHint: string;
  checkpointUpdateHint: string[];
}

export interface DocumentAssetSummaryResult {
  documentTypeGuess: DocumentAssetType;
  summary: string;
  riskFlags: string[];
  recommendedActions: string[];
  relatedCheckpointHint: string[];
}

export interface ExternalTouchpointReviewResult {
  externalProgressAssessment: string;
  stalledTouchpoints: string[];
  missingTouchpoints: string[];
  recommendedNextMoves: string[];
}

export interface TouchpointHubView {
  emailThreads: EmailThread[];
  calendarEvents: CalendarEvent[];
  documentAssets: DocumentAsset[];
  events: ExternalTouchpointEvent[];
}

