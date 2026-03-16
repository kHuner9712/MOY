export type PrepCardType = "followup_prep" | "quote_prep" | "meeting_prep" | "task_brief" | "manager_attention";
export type PrepCardStatus = "draft" | "ready" | "stale" | "archived";

export type MorningBriefType = "sales_morning" | "manager_morning";
export type MorningBriefStatus = "generating" | "completed" | "failed";

export type ContentDraftType =
  | "followup_message"
  | "quote_explanation"
  | "meeting_opening"
  | "meeting_summary"
  | "manager_checkin_note"
  | "internal_update";
export type ContentDraftStatus = "draft" | "adopted" | "discarded" | "archived";

export type PrepFeedbackTargetType = "prep_card" | "content_draft" | "morning_brief";
export type PrepFeedbackType = "useful" | "not_useful" | "inaccurate" | "outdated" | "adopted";

export interface PrepCard {
  id: string;
  orgId: string;
  ownerId: string | null;
  customerId: string | null;
  opportunityId: string | null;
  workItemId: string | null;
  cardType: PrepCardType;
  status: PrepCardStatus;
  title: string;
  summary: string;
  cardPayload: Record<string, unknown>;
  sourceSnapshot: Record<string, unknown>;
  generatedBy: string;
  aiRunId: string | null;
  validUntil: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface MorningBrief {
  id: string;
  orgId: string;
  targetUserId: string | null;
  briefType: MorningBriefType;
  briefDate: string;
  status: MorningBriefStatus;
  headline: string;
  executiveSummary: string;
  briefPayload: Record<string, unknown>;
  sourceSnapshot: Record<string, unknown>;
  generatedBy: string;
  aiRunId: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface ContentDraft {
  id: string;
  orgId: string;
  ownerId: string;
  customerId: string | null;
  opportunityId: string | null;
  prepCardId: string | null;
  workItemId: string | null;
  draftType: ContentDraftType;
  status: ContentDraftStatus;
  title: string;
  contentMarkdown: string;
  contentText: string;
  rationale: string;
  sourceSnapshot: Record<string, unknown>;
  generatedBy: string;
  aiRunId: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface PrepFeedback {
  id: string;
  orgId: string;
  userId: string;
  targetType: PrepFeedbackTargetType;
  targetId: string;
  feedbackType: PrepFeedbackType;
  feedbackText: string | null;
  createdAt: string;
}

export interface BriefingHubView {
  morningBrief: MorningBrief | null;
  prepCards: PrepCard[];
  contentDrafts: ContentDraft[];
}
