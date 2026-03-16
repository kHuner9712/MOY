export interface CaptureApplyDecisionInput {
  shouldCreateFollowup: boolean;
  hasMatchedCustomer: boolean;
  extractionConfidence: number;
  matchConfidence: number;
  hasSummary: boolean;
  hasNextStep: boolean;
  autoApplyThreshold?: number;
}

export function decideCaptureApplyMode(input: CaptureApplyDecisionInput): "auto" | "manual" | "none" {
  if (!input.shouldCreateFollowup || !input.hasMatchedCustomer) {
    return "none";
  }

  const threshold = input.autoApplyThreshold ?? 0.78;
  const confidence = Math.min(input.extractionConfidence, input.matchConfidence);
  const structureReady = input.hasSummary && input.hasNextStep;

  if (structureReady && confidence >= threshold) {
    return "auto";
  }

  return "manual";
}
