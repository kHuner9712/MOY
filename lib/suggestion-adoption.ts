export function mapFeedbackToAdoptionType(
  feedbackType: "useful" | "not_useful" | "inaccurate" | "outdated" | "adopted"
): "viewed" | "copied" | "edited" | "adopted" | "dismissed" | "partially_used" {
  if (feedbackType === "adopted") return "adopted";
  if (feedbackType === "useful") return "partially_used";
  if (feedbackType === "not_useful") return "dismissed";
  if (feedbackType === "inaccurate") return "dismissed";
  return "edited";
}
