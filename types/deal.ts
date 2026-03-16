import type { AlertItem } from "@/types/alert";
import type { ActionOutcome } from "@/types/outcome";
import type { PlaybookWithEntries } from "@/types/playbook";
import type { ContentDraft, PrepCard } from "@/types/preparation";
import type { WorkItem } from "@/types/work";

export type DealRoomStatus = "active" | "watchlist" | "escalated" | "blocked" | "won" | "lost" | "archived";

export type DealPriorityBand = "normal" | "important" | "strategic" | "critical";

export type CollaborationThreadType =
  | "strategy"
  | "blocker"
  | "quote_review"
  | "next_step"
  | "risk_discussion"
  | "manager_intervention"
  | "playbook_application";

export type CollaborationThreadStatus = "open" | "resolved" | "archived";

export type CollaborationMessageType = "comment" | "decision_note" | "ai_summary" | "system_event";

export type DecisionType =
  | "quote_strategy"
  | "discount_exception"
  | "trial_offer"
  | "manager_intervention"
  | "resource_support"
  | "contract_risk"
  | "stage_commitment";

export type DecisionStatus = "proposed" | "approved" | "rejected" | "superseded" | "completed";

export type DealParticipantRole = "owner" | "collaborator" | "manager" | "reviewer" | "observer";

export type DealCheckpointType =
  | "qualification"
  | "need_confirmed"
  | "proposal_sent"
  | "quote_sent"
  | "decision_maker_confirmed"
  | "budget_confirmed"
  | "trial_started"
  | "contract_review"
  | "closing";

export type DealCheckpointStatus = "pending" | "completed" | "blocked" | "skipped";

export type InterventionRequestType =
  | "manager_join_call"
  | "pricing_support"
  | "proposal_review"
  | "objection_help"
  | "contract_support"
  | "executive_escalation";

export type InterventionPriorityBand = "low" | "medium" | "high" | "critical";

export type InterventionRequestStatus = "open" | "accepted" | "declined" | "completed" | "expired";

export interface DealRoom {
  id: string;
  orgId: string;
  customerId: string;
  customerName: string;
  opportunityId: string | null;
  opportunityTitle: string | null;
  ownerId: string;
  ownerName: string;
  roomStatus: DealRoomStatus;
  priorityBand: DealPriorityBand;
  title: string;
  commandSummary: string;
  currentGoal: string;
  currentBlockers: string[];
  nextMilestone: string | null;
  nextMilestoneDueAt: string | null;
  managerAttentionNeeded: boolean;
  sourceSnapshot: Record<string, unknown>;
  createdBy: string;
  createdAt: string;
  updatedAt: string;
}

export interface CollaborationThread {
  id: string;
  orgId: string;
  dealRoomId: string;
  threadType: CollaborationThreadType;
  title: string;
  status: CollaborationThreadStatus;
  summary: string;
  createdBy: string;
  createdAt: string;
  updatedAt: string;
}

export interface CollaborationMessage {
  id: string;
  orgId: string;
  threadId: string;
  authorUserId: string;
  authorName: string;
  messageType: CollaborationMessageType;
  bodyMarkdown: string;
  mentions: string[];
  sourceRefType: string | null;
  sourceRefId: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface DecisionRecord {
  id: string;
  orgId: string;
  dealRoomId: string;
  customerId: string;
  opportunityId: string | null;
  decisionType: DecisionType;
  status: DecisionStatus;
  title: string;
  contextSummary: string;
  optionsConsidered: string[];
  recommendedOption: string | null;
  decisionReason: string | null;
  decidedBy: string | null;
  requestedBy: string;
  dueAt: string | null;
  completedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface DealParticipant {
  id: string;
  orgId: string;
  dealRoomId: string;
  userId: string;
  userName: string;
  roleInRoom: DealParticipantRole;
  isActive: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface DealCheckpoint {
  id: string;
  orgId: string;
  dealRoomId: string;
  checkpointType: DealCheckpointType;
  status: DealCheckpointStatus;
  title: string;
  description: string;
  dueAt: string | null;
  completedAt: string | null;
  ownerId: string | null;
  ownerName: string | null;
  evidenceSnapshot: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
}

export interface InterventionRequest {
  id: string;
  orgId: string;
  dealRoomId: string;
  requestedBy: string;
  requestedByName: string;
  targetUserId: string | null;
  targetUserName: string | null;
  requestType: InterventionRequestType;
  priorityBand: InterventionPriorityBand;
  status: InterventionRequestStatus;
  requestSummary: string;
  contextSnapshot: Record<string, unknown>;
  dueAt: string | null;
  completedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface DealRoomDetail {
  room: DealRoom;
  participants: DealParticipant[];
  checkpoints: DealCheckpoint[];
  threads: CollaborationThread[];
  messages: CollaborationMessage[];
  decisions: DecisionRecord[];
  interventions: InterventionRequest[];
}

export interface DealRoomRelatedData {
  workItems: WorkItem[];
  prepCards: PrepCard[];
  contentDrafts: ContentDraft[];
  outcomes: ActionOutcome[];
  playbooks: PlaybookWithEntries[];
  alerts: AlertItem[];
}

export interface DealRoomDetailView extends DealRoomDetail {
  related: DealRoomRelatedData;
}
