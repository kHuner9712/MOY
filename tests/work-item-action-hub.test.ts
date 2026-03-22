import assert from "node:assert/strict";

import {
  buildWorkItemTraceContext,
  deriveWorkItemTriggerOrigin,
  findReusableWorkItemIdByStatus,
  isWorkItemActiveStatus
} from "../services/work-item-service";
import type { WorkItem } from "../types/work";

function buildWorkItem(overrides: Partial<WorkItem> = {}): WorkItem {
  return {
    id: "work-1",
    orgId: "org-1",
    ownerId: "owner-1",
    ownerName: "Owner",
    customerId: null,
    customerName: null,
    opportunityId: null,
    sourceType: "manual",
    workType: "review_customer",
    title: "Task",
    description: "Task description",
    rationale: "Task rationale",
    priorityScore: 70,
    priorityBand: "medium",
    status: "todo",
    scheduledFor: null,
    dueAt: null,
    completedAt: null,
    snoozedUntil: null,
    sourceRefType: null,
    sourceRefId: null,
    aiGenerated: false,
    aiRunId: null,
    createdBy: "owner-1",
    createdAt: "2026-03-22T00:00:00.000Z",
    updatedAt: "2026-03-22T00:00:00.000Z",
    ...overrides
  };
}

export function runWorkItemActionHubTests(logPass: (name: string) => void): void {
  assert.equal(isWorkItemActiveStatus("todo"), true);
  assert.equal(isWorkItemActiveStatus("in_progress"), true);
  assert.equal(isWorkItemActiveStatus("snoozed"), true);
  assert.equal(isWorkItemActiveStatus("done"), false);
  assert.equal(isWorkItemActiveStatus("cancelled"), false);

  assert.equal(
    findReusableWorkItemIdByStatus([
      { id: "done-1", status: "done" },
      { id: "todo-1", status: "todo" },
      { id: "snoozed-1", status: "snoozed" }
    ]),
    "todo-1"
  );
  assert.equal(
    findReusableWorkItemIdByStatus([
      { id: "done-1", status: "done" },
      { id: "cancelled-1", status: "cancelled" }
    ]),
    null
  );
  logPass("work item action hub: reusable status selection");

  assert.equal(
    deriveWorkItemTriggerOrigin({
      sourceType: "ai_suggested",
      sourceRefType: "business_event",
      aiGenerated: true
    }),
    "rule"
  );
  assert.equal(
    deriveWorkItemTriggerOrigin({
      sourceType: "manager_assigned",
      sourceRefType: "intervention_request",
      aiGenerated: false
    }),
    "manager"
  );
  assert.equal(
    deriveWorkItemTriggerOrigin({
      sourceType: "ai_suggested",
      sourceRefType: "customer",
      aiGenerated: true
    }),
    "ai"
  );
  assert.equal(
    deriveWorkItemTriggerOrigin({
      sourceType: "manual",
      sourceRefType: null,
      aiGenerated: false
    }),
    "manual"
  );
  logPass("work item action hub: trigger origin classification");

  const eventTrace = buildWorkItemTraceContext({
    item: buildWorkItem({
      sourceType: "ai_suggested",
      sourceRefType: "business_event",
      sourceRefId: "event-1",
      aiGenerated: false
    }),
    businessEvent: {
      id: "event-1",
      entity_type: "deal_room",
      entity_id: "deal-room-1",
      event_payload: {
        customer_id: "customer-1",
        deal_room_id: "deal-room-1"
      }
    }
  });
  assert.equal(eventTrace.triggerOrigin, "rule");
  assert.equal(eventTrace.triggerEntityType, "deal_room");
  assert.equal(eventTrace.triggerEntityId, "deal-room-1");
  assert.equal(eventTrace.linkedBusinessEventId, "event-1");
  assert.equal(eventTrace.linkedCustomerId, "customer-1");
  assert.equal(eventTrace.linkedDealRoomId, "deal-room-1");

  const interventionTrace = buildWorkItemTraceContext({
    item: buildWorkItem({
      sourceType: "manager_assigned",
      sourceRefType: "intervention_request",
      sourceRefId: "intervention-1"
    }),
    intervention: {
      id: "intervention-1",
      deal_room_id: "deal-room-2",
      request_type: "manager_coaching"
    }
  });
  assert.equal(interventionTrace.triggerOrigin, "manager");
  assert.equal(interventionTrace.triggerEntityType, "deal_room");
  assert.equal(interventionTrace.triggerEntityId, "deal-room-2");
  assert.equal(interventionTrace.linkedInterventionRequestId, "intervention-1");
  assert.equal(interventionTrace.linkedDealRoomId, "deal-room-2");

  const customerTrace = buildWorkItemTraceContext({
    item: buildWorkItem({
      sourceType: "followup_due",
      sourceRefType: "customer",
      sourceRefId: "customer-2",
      customerId: "customer-2"
    })
  });
  assert.equal(customerTrace.triggerOrigin, "system");
  assert.equal(customerTrace.triggerEntityType, "customer");
  assert.equal(customerTrace.triggerEntityId, "customer-2");
  assert.equal(customerTrace.linkedCustomerId, "customer-2");

  logPass("work item action hub: trace context linkage");
}
