export type MemoryFeedbackType = "accurate" | "inaccurate" | "outdated" | "useful" | "not_useful";
export type MemoryItemStatus = "active" | "hidden" | "rejected";

export function deriveMemoryItemStatusFromFeedback(feedbackType: MemoryFeedbackType): MemoryItemStatus | null {
  if (feedbackType === "inaccurate") return "rejected";
  if (feedbackType === "not_useful" || feedbackType === "outdated") return "hidden";
  return null;
}
