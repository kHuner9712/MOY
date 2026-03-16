export type ActionOutcomeType =
  | "followup_result"
  | "quote_result"
  | "meeting_result"
  | "task_result"
  | "manager_intervention_result";

export type ActionOutcomeStatus =
  | "positive_progress"
  | "neutral"
  | "stalled"
  | "risk_increased"
  | "closed_won"
  | "closed_lost";

export type CustomerSentimentShift = "improved" | "unchanged" | "worsened" | "unknown";

export type OutcomeUsefulnessRating = "helpful" | "somewhat_helpful" | "not_helpful" | "unknown";

export interface ActionOutcome {
  id: string;
  orgId: string;
  ownerId: string;
  customerId: string | null;
  opportunityId: string | null;
  workItemId: string | null;
  followupId: string | null;
  communicationInputId: string | null;
  prepCardId: string | null;
  contentDraftId: string | null;
  outcomeType: ActionOutcomeType;
  resultStatus: ActionOutcomeStatus;
  stageChanged: boolean;
  oldStage: string | null;
  newStage: string | null;
  customerSentimentShift: CustomerSentimentShift;
  keyOutcomeSummary: string;
  newObjections: string[];
  newRisks: string[];
  nextStepDefined: boolean;
  nextStepText: string | null;
  followupDueAt: string | null;
  usedPrepCard: boolean;
  usedDraft: boolean;
  usefulnessRating: OutcomeUsefulnessRating;
  notes: string | null;
  createdBy: string;
  createdAt: string;
  updatedAt: string;
}

export type SuggestionTargetType = "prep_card" | "content_draft" | "task_action_suggestion" | "morning_brief";

export type SuggestionAdoptionType = "viewed" | "copied" | "edited" | "adopted" | "dismissed" | "partially_used";

export type SuggestionAdoptionContext =
  | "before_followup"
  | "before_quote"
  | "before_meeting"
  | "during_task_execution"
  | "after_review";

export interface SuggestionAdoption {
  id: string;
  orgId: string;
  userId: string;
  targetType: SuggestionTargetType;
  targetId: string;
  adoptionType: SuggestionAdoptionType;
  editDistanceHint: number | null;
  adoptionContext: SuggestionAdoptionContext;
  linkedOutcomeId: string | null;
  createdAt: string;
}

export type OutcomeReviewScope = "user" | "team" | "org";
export type OutcomeReviewStatus = "generating" | "completed" | "failed";

export interface OutcomeReview {
  id: string;
  orgId: string;
  reviewScope: OutcomeReviewScope;
  targetUserId: string | null;
  periodStart: string;
  periodEnd: string;
  status: OutcomeReviewStatus;
  title: string;
  executiveSummary: string;
  effectivePatterns: string[];
  ineffectivePatterns: string[];
  repeatedFailures: string[];
  coachingActions: string[];
  playbookCandidates: string[];
  sourceSnapshot: Record<string, unknown>;
  generatedBy: string;
  aiRunId: string | null;
  createdAt: string;
  updatedAt: string;
}
