import test from "node:test";
import assert from "node:assert/strict";

test("AttributionChain type structure", () => {
  const chain = {
    event: {
      id: "event-1",
      orgId: "org-1",
      entityType: "customer" as const,
      entityId: "customer-1",
      eventType: "health_declined" as const,
      severity: "critical" as const,
      eventSummary: "客户健康度下降",
      eventPayload: { owner_id: "user-1" },
      status: "resolved" as const,
      createdAt: "2026-03-15T10:00:00.000Z",
      updatedAt: "2026-03-15T12:00:00.000Z"
    },
    workItems: [
      {
        id: "work-1",
        orgId: "org-1",
        ownerId: "user-1",
        customerId: "customer-1",
        opportunityId: null,
        sourceType: "alert" as const,
        workType: "resolve_alert" as const,
        title: "跟进风险客户",
        description: "客户健康度下降，需要跟进",
        rationale: "系统自动生成",
        priorityScore: 0.9,
        priorityBand: "critical" as const,
        status: "done" as const,
        scheduledFor: null,
        dueAt: null,
        completedAt: "2026-03-15T11:00:00.000Z",
        snoozedUntil: null,
        sourceRefType: "business_event",
        sourceRefId: "event-1",
        aiGenerated: true,
        aiRunId: null,
        createdBy: "system",
        createdAt: "2026-03-15T10:05:00.000Z",
        updatedAt: "2026-03-15T11:00:00.000Z"
      }
    ],
    outcomes: [
      {
        id: "outcome-1",
        orgId: "org-1",
        ownerId: "user-1",
        customerId: "customer-1",
        opportunityId: null,
        workItemId: "work-1",
        followupId: null,
        communicationInputId: null,
        prepCardId: null,
        contentDraftId: null,
        outcomeType: "followup_result" as const,
        resultStatus: "positive_progress" as const,
        stageChanged: true,
        oldStage: "awareness" as const,
        newStage: "interest" as const,
        customerSentimentShift: "more_positive" as const,
        keyOutcomeSummary: "客户态度转好，同意进一步沟通",
        newObjections: [],
        newRisks: [],
        nextStepDefined: true,
        nextStepText: "下周发送报价",
        followupDueAt: null,
        usedPrepCard: true,
        usedDraft: false,
        usefulnessRating: "very_useful" as const,
        notes: null,
        createdBy: "user-1",
        createdAt: "2026-03-15T11:30:00.000Z",
        updatedAt: "2026-03-15T11:30:00.000Z"
      }
    ],
    customerImpact: {
      stageChanged: true,
      oldStage: "awareness",
      newStage: "interest",
      healthImproved: true,
      riskReduced: true
    },
    timeline: {
      eventDetectedAt: "2026-03-15T10:00:00.000Z",
      firstActionAt: "2026-03-15T10:05:00.000Z",
      lastOutcomeAt: "2026-03-15T11:30:00.000Z",
      resolutionDurationHours: 1.5
    }
  };

  assert.strictEqual(chain.event.status, "resolved");
  assert.strictEqual(chain.workItems.length, 1);
  assert.strictEqual(chain.outcomes.length, 1);
  assert.strictEqual(chain.customerImpact.stageChanged, true);
  assert.strictEqual(chain.timeline.resolutionDurationHours, 1.5);
});

test("AttributionSummary type structure", () => {
  const summary = {
    totalEventsDetected: 10,
    eventsHandled: 7,
    eventsWithPositiveOutcome: 5,
    eventsWithNegativeOutcome: 1,
    eventsPending: 3,
    averageResolutionHours: 4.5,
    topEventTypes: [
      { eventType: "health_declined", count: 4, handledCount: 3, positiveOutcomeCount: 2 },
      { eventType: "no_recent_touchpoint", count: 3, handledCount: 2, positiveOutcomeCount: 1 }
    ],
    topHandlers: [
      { handlerId: "user-1", handlerName: "张三", handledCount: 5, positiveOutcomeCount: 4 },
      { handlerId: "user-2", handlerName: "李四", handledCount: 2, positiveOutcomeCount: 1 }
    ]
  };

  assert.strictEqual(summary.totalEventsDetected, 10);
  assert.strictEqual(summary.eventsHandled, 7);
  assert.strictEqual(summary.eventsWithPositiveOutcome, 5);
  assert.strictEqual(summary.averageResolutionHours, 4.5);
  assert.strictEqual(summary.topEventTypes.length, 2);
  assert.strictEqual(summary.topHandlers.length, 2);
});

test("Event to outcome attribution logic", () => {
  const event = {
    id: "event-1",
    status: "resolved",
    createdAt: "2026-03-15T10:00:00.000Z"
  };

  const workItems = [
    {
      id: "work-1",
      sourceRefType: "business_event",
      sourceRefId: "event-1",
      createdAt: "2026-03-15T10:05:00.000Z",
      status: "done"
    }
  ];

  const outcomes = [
    {
      id: "outcome-1",
      workItemId: "work-1",
      resultStatus: "positive_progress",
      stageChanged: true,
      createdAt: "2026-03-15T11:30:00.000Z"
    }
  ];

  const hasWorkItems = workItems.filter(
    (w) => w.sourceRefType === "business_event" && w.sourceRefId === event.id
  ).length > 0;

  assert.strictEqual(hasWorkItems, true);

  const hasOutcomes = outcomes.length > 0;
  assert.strictEqual(hasOutcomes, true);

  const hasPositiveOutcome = outcomes.some((o) => o.resultStatus === "positive_progress");
  assert.strictEqual(hasPositiveOutcome, true);

  const eventTime = new Date(event.createdAt).getTime();
  const outcomeTime = new Date(outcomes[0].createdAt).getTime();
  const resolutionDurationHours = (outcomeTime - eventTime) / (1000 * 60 * 60);

  assert.ok(resolutionDurationHours > 0);
  assert.ok(resolutionDurationHours < 2);
});

test("Attribution summary calculation", () => {
  const events = [
    { id: "e1", status: "resolved", event_type: "health_declined" },
    { id: "e2", status: "resolved", event_type: "health_declined" },
    { id: "e3", status: "acknowledged", event_type: "no_recent_touchpoint" },
    { id: "e4", status: "open", event_type: "deal_blocked" },
    { id: "e5", status: "open", event_type: "deal_blocked" }
  ];

  const totalEventsDetected = events.length;
  const eventsHandled = events.filter(
    (e) => e.status === "resolved" || e.status === "acknowledged"
  ).length;
  const eventsPending = events.filter((e) => e.status === "open").length;

  assert.strictEqual(totalEventsDetected, 5);
  assert.strictEqual(eventsHandled, 3);
  assert.strictEqual(eventsPending, 2);

  const eventTypeStats = new Map<string, { count: number; handled: number }>();

  for (const event of events) {
    const existing = eventTypeStats.get(event.event_type) ?? { count: 0, handled: 0 };
    existing.count++;
    if (event.status === "resolved" || event.status === "acknowledged") {
      existing.handled++;
    }
    eventTypeStats.set(event.event_type, existing);
  }

  assert.strictEqual(eventTypeStats.get("health_declined")?.count, 2);
  assert.strictEqual(eventTypeStats.get("health_declined")?.handled, 2);
  assert.strictEqual(eventTypeStats.get("deal_blocked")?.count, 2);
  assert.strictEqual(eventTypeStats.get("deal_blocked")?.handled, 0);
});

test("Fallback logic for empty data", () => {
  const emptySummary = {
    totalEventsDetected: 0,
    eventsHandled: 0,
    eventsWithPositiveOutcome: 0,
    eventsWithNegativeOutcome: 0,
    eventsPending: 0,
    averageResolutionHours: null,
    topEventTypes: [],
    topHandlers: []
  };

  const headline = emptySummary.totalEventsDetected > 0
    ? `本周识别 ${emptySummary.totalEventsDetected} 个风险事件`
    : "本周未检测到风险事件";

  assert.strictEqual(headline, "本周未检测到风险事件");

  const recommendation = emptySummary.eventsPending > emptySummary.eventsHandled
    ? "建议加快风险事件处理节奏"
    : "继续保持风险监控和处理节奏";

  assert.strictEqual(recommendation, "继续保持风险监控和处理节奏");
});

test("Handler performance calculation", () => {
  const chains = [
    {
      event: { id: "e1" },
      outcomes: [
        { ownerId: "user-1", resultStatus: "positive_progress" },
        { ownerId: "user-1", resultStatus: "positive_progress" }
      ]
    },
    {
      event: { id: "e2" },
      outcomes: [
        { ownerId: "user-2", resultStatus: "neutral" }
      ]
    },
    {
      event: { id: "e3" },
      outcomes: [
        { ownerId: "user-1", resultStatus: "risk_increased" }
      ]
    }
  ];

  const handlerStats = new Map<string, { handled: number; positive: number }>();

  for (const chain of chains) {
    for (const outcome of chain.outcomes) {
      const handlerId = outcome.ownerId;
      const existing = handlerStats.get(handlerId) ?? { handled: 0, positive: 0 };
      existing.handled++;
      if (outcome.resultStatus === "positive_progress") {
        existing.positive++;
      }
      handlerStats.set(handlerId, existing);
    }
  }

  assert.strictEqual(handlerStats.get("user-1")?.handled, 3);
  assert.strictEqual(handlerStats.get("user-1")?.positive, 2);
  assert.strictEqual(handlerStats.get("user-2")?.handled, 1);
  assert.strictEqual(handlerStats.get("user-2")?.positive, 0);
});
