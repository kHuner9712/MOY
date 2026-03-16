export type FollowupMethod = "phone" | "wechat" | "email" | "meeting" | "other";

export interface FollowupRecord {
  id: string;
  customerId: string;
  ownerId: string;
  ownerName: string;
  method: FollowupMethod;
  summary: string;
  customerNeeds: string;
  objections: string;
  nextPlan: string;
  nextFollowupAt: string;
  needsAiAnalysis: boolean;
  sourceInputId: string | null;
  draftStatus: "draft" | "confirmed";
  createdAt: string;
}

export interface FollowupInput {
  customerId: string;
  ownerId: string;
  ownerName: string;
  method: FollowupMethod;
  summary: string;
  customerNeeds: string;
  objections: string;
  nextPlan: string;
  nextFollowupAt: string;
  needsAiAnalysis: boolean;
  sourceInputId?: string | null;
  draftStatus?: "draft" | "confirmed";
}

export type FollowupAnalysisStatus = "skipped" | "completed" | "failed" | "fallback";

export interface FollowupCreateResult {
  followup: FollowupRecord;
  analysisStatus: FollowupAnalysisStatus;
  analysisMessage: string;
  aiRunId: string | null;
}
