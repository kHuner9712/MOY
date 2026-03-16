import type { InterventionRequestStatus } from "@/types/deal";

const TRANSITION_MAP: Record<InterventionRequestStatus, InterventionRequestStatus[]> = {
  open: ["accepted", "declined", "expired"],
  accepted: ["completed", "declined", "expired"],
  declined: ["open"],
  completed: [],
  expired: ["open"]
};

export function isInterventionStatusTransitionAllowed(
  currentStatus: InterventionRequestStatus,
  nextStatus: InterventionRequestStatus
): boolean {
  if (currentStatus === nextStatus) return true;
  return TRANSITION_MAP[currentStatus].includes(nextStatus);
}

