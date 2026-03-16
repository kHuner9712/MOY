export type MemoryItemType =
  | "customer_preference"
  | "communication_pattern"
  | "objection_pattern"
  | "tactic_pattern"
  | "followup_rhythm"
  | "risk_pattern"
  | "coaching_hint";

export type MemoryItemStatus = "active" | "hidden" | "rejected";
export type MemoryFeedbackType = "accurate" | "inaccurate" | "outdated" | "useful" | "not_useful";

export interface UserMemoryProfile {
  id: string;
  orgId: string;
  userId: string;
  memoryVersion: string;
  summary: string;
  preferredCustomerTypes: string[];
  preferredCommunicationStyles: string[];
  commonObjections: string[];
  effectiveTactics: string[];
  commonFollowupRhythm: string[];
  quotingStyleNotes: string[];
  riskBlindSpots: string[];
  managerCoachingFocus: string[];
  confidenceScore: number;
  sourceWindowDays: number;
  lastCompiledAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface UserMemoryItem {
  id: string;
  orgId: string;
  userId: string;
  memoryType: MemoryItemType;
  title: string;
  description: string;
  evidenceSnapshot: Record<string, unknown>;
  confidenceScore: number;
  sourceCount: number;
  status: MemoryItemStatus;
  createdBySystem: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface MemoryFeedback {
  id: string;
  orgId: string;
  userId: string;
  memoryItemId: string;
  feedbackType: MemoryFeedbackType;
  feedbackText: string | null;
  createdAt: string;
}
