import type { TaskActionType, WorkItemStatus } from "@/types/work";

export type WorkItemOperation = "start" | "complete" | "snooze" | "cancel" | "resume";

export interface WorkItemTransitionResult {
  valid: boolean;
  nextStatus: WorkItemStatus;
  actionType: TaskActionType;
}

const TRANSITION_MAP: Record<WorkItemStatus, Partial<Record<WorkItemOperation, WorkItemTransitionResult>>> = {
  todo: {
    start: { valid: true, nextStatus: "in_progress", actionType: "started" },
    complete: { valid: true, nextStatus: "done", actionType: "completed" },
    snooze: { valid: true, nextStatus: "snoozed", actionType: "snoozed" },
    cancel: { valid: true, nextStatus: "cancelled", actionType: "cancelled" }
  },
  in_progress: {
    complete: { valid: true, nextStatus: "done", actionType: "completed" },
    snooze: { valid: true, nextStatus: "snoozed", actionType: "snoozed" },
    cancel: { valid: true, nextStatus: "cancelled", actionType: "cancelled" }
  },
  snoozed: {
    resume: { valid: true, nextStatus: "todo", actionType: "reprioritized" },
    start: { valid: true, nextStatus: "todo", actionType: "reprioritized" },
    cancel: { valid: true, nextStatus: "cancelled", actionType: "cancelled" }
  },
  done: {},
  cancelled: {}
};

export function resolveWorkItemTransition(currentStatus: WorkItemStatus, operation: WorkItemOperation): WorkItemTransitionResult {
  const found = TRANSITION_MAP[currentStatus][operation];
  if (found) return found;
  return {
    valid: false,
    nextStatus: currentStatus,
    actionType: "marked_blocked"
  };
}
