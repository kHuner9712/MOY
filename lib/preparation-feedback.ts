import type { ContentDraftStatus, PrepCardStatus, PrepFeedbackType } from "@/types/preparation";

export function derivePrepCardStatusFromFeedback(feedbackType: PrepFeedbackType): PrepCardStatus | null {
  if (feedbackType === "outdated") return "stale";
  if (feedbackType === "inaccurate") return "archived";
  return null;
}

export function deriveContentDraftStatusFromFeedback(feedbackType: PrepFeedbackType): ContentDraftStatus | null {
  if (feedbackType === "adopted") return "adopted";
  if (feedbackType === "not_useful" || feedbackType === "inaccurate") return "discarded";
  return null;
}

