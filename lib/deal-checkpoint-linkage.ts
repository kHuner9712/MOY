import type { AlertLevel, AlertRuleType, AlertSource } from "@/types/alert";
import type { DealRoomStatus } from "@/types/deal";

export interface BlockedCheckpointLinkageResult {
  roomPatch: {
    managerAttentionNeeded: boolean;
    roomStatus: DealRoomStatus;
  };
  alert: {
    ruleType: AlertRuleType;
    source: AlertSource;
    level: AlertLevel;
    title: string;
    description: string;
    evidence: string[];
    suggestedOwnerAction: string[];
    dueAt: string | null;
  };
}

export function buildBlockedCheckpointLinkage(params: {
  checkpointTitle: string;
  checkpointType: string;
  checkpointDescription: string;
  checkpointDueAt: string | null;
  dealRoomTitle: string;
}): BlockedCheckpointLinkageResult {
  const description = params.checkpointDescription.trim().length > 0 ? params.checkpointDescription : "Checkpoint blocked and requires immediate support.";
  return {
    roomPatch: {
      managerAttentionNeeded: true,
      roomStatus: "blocked"
    },
    alert: {
      ruleType: "ai_detected",
      source: "hybrid",
      level: "critical",
      title: `Blocked checkpoint: ${params.checkpointTitle}`,
      description,
      evidence: [`deal_room=${params.dealRoomTitle}`, `checkpoint=${params.checkpointType}`],
      suggestedOwnerAction: ["Clear blocker owner and deadline", "Request manager intervention support"],
      dueAt: params.checkpointDueAt
    }
  };
}

