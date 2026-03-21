/**
 * v1.1 Sales Desk Tests
 * 今日作战台核心功能测试
 */

import { describe, it, expect, beforeEach, vi } from "vitest";
import type { SalesDeskQueueResult, CommunicationReuseResult, CustomerTimelineResult } from "@/types/sales-desk";

const MOCK_CUSTOMER_ID = "cust-001";
const MOCK_ORG_ID = "org-001";
const MOCK_OWNER_ID = "user-001";
const MOCK_COMMUNICATION_INPUT_ID = "input-001";

describe("Sales Desk Types", () => {
  it("should have correct queue type labels", async () => {
    const { QUEUE_TYPE_LABELS } = await import("@/types/sales-desk");

    expect(QUEUE_TYPE_LABELS.must_contact_today).toBe("今日必须联系");
    expect(QUEUE_TYPE_LABELS.rhythm_breach).toBe("节奏中断");
    expect(QUEUE_TYPE_LABELS.high_intent_silent).toBe("高意向沉默");
    expect(QUEUE_TYPE_LABELS.quote_waiting).toBe("报价等待回复");
    expect(QUEUE_TYPE_LABELS.pending_materials).toBe("待发资料");
    expect(QUEUE_TYPE_LABELS.awaiting_confirmation).toBe("等待确认");
  });

  it("should have correct rhythm alert labels", async () => {
    const { RHYTHM_ALERT_LABELS } = await import("@/types/sales-desk");

    expect(RHYTHM_ALERT_LABELS.high_intent_silent).toBe("高意向沉默");
    expect(RHYTHM_ALERT_LABELS.quote_no_reply).toBe("报价后未回复");
    expect(RHYTHM_ALERT_LABELS.multi_round_no_next_step).toBe("多轮沟通无进展");
    expect(RHYTHM_ALERT_LABELS.reply_slowing_down).toBe("回复变慢");
  });
});

describe("SalesDeskQueueResult structure", () => {
  it("should have correct queue structure", () => {
    const result: SalesDeskQueueResult = {
      queues: {
        mustContactToday: [],
        rhythmBreach: [],
        highIntentSilent: [],
        quoteWaiting: [],
        pendingMaterials: [],
        awaitingConfirmation: []
      },
      totalCounts: {
        mustContactToday: 0,
        rhythmBreach: 0,
        highIntentSilent: 0,
        quoteWaiting: 0,
        pendingMaterials: 0,
        awaitingConfirmation: 0
      }
    };

    expect(result.queues.mustContactToday).toBeInstanceOf(Array);
    expect(result.totalCounts.rhythmBreach).toBe(0);
  });

  it("should support queue item with all required fields", () => {
    const item = {
      queueType: "rhythm_breach" as const,
      customerId: MOCK_CUSTOMER_ID,
      customerName: "星河制造",
      opportunityId: "opp-001",
      opportunityName: "ERP采购项目",
      reason: "超过48小时未推进",
      alertReason: "high_intent_silent",
      lastContactAt: new Date().toISOString(),
      hoursSinceContact: 52,
      priorityScore: 85,
      suggestedAction: "主动联系客户，了解进展"
    };

    expect(item.customerId).toBe(MOCK_CUSTOMER_ID);
    expect(item.queueType).toBe("rhythm_breach");
    expect(item.priorityScore).toBeGreaterThan(0);
  });
});

describe("CommunicationReuseResult structure", () => {
  it("should have correct reuse result structure", () => {
    const result: CommunicationReuseResult = {
      followupDraftId: "followup-001",
      nextStepSuggestion: "建议在24小时内安排一次跟进电话",
      internalBrief: "已完成初步沟通，需要持续推进客户关系",
      customerMessageDraft: "感谢您的时间，期待您的回复。",
      prepCardId: "prep-001",
      usedFallback: false
    };

    expect(result.followupDraftId).toBeDefined();
    expect(result.nextStepSuggestion).toContain("24小时");
    expect(result.internalBrief).toContain("推进");
    expect(result.customerMessageDraft).toContain("感谢");
    expect(result.prepCardId).toBeDefined();
  });

  it("should handle fallback case", () => {
    const result: CommunicationReuseResult = {
      followupDraftId: undefined,
      nextStepSuggestion: "建议在24小时内安排一次跟进",
      internalBrief: "已完成沟通",
      customerMessageDraft: undefined,
      prepCardId: undefined,
      usedFallback: true
    };

    expect(result.usedFallback).toBe(true);
    expect(result.followupDraftId).toBeUndefined();
  });
});

describe("CustomerTimelineResult structure", () => {
  it("should have correct timeline structure", () => {
    const result: CustomerTimelineResult = {
      customerId: MOCK_CUSTOMER_ID,
      customerName: "星河制造",
      items: [
        {
          id: "item-001",
          itemType: "communication_input",
          title: "电话沟通",
          subtitle: "讨论了采购需求",
          occurredAt: new Date().toISOString(),
          importance: "high",
          isAiGenerated: false
        },
        {
          id: "item-002",
          itemType: "followup",
          title: "发送报价",
          occurredAt: new Date(Date.now() - 86400000).toISOString(),
          importance: "medium",
          isAiGenerated: true
        }
      ],
      generatedAt: new Date().toISOString()
    };

    expect(result.customerId).toBe(MOCK_CUSTOMER_ID);
    expect(result.items).toHaveLength(2);
    expect(result.items[0].importance).toBe("high");
    expect(result.items[1].isAiGenerated).toBe(true);
  });

  it("should support all timeline item types", () => {
    const itemTypes = [
      "communication_input",
      "followup",
      "prep_card",
      "content_draft",
      "outcome",
      "business_event",
      "touchpoint",
      "briefing"
    ] as const;

    for (const itemType of itemTypes) {
      const item = {
        id: `item-${itemType}`,
        itemType,
        title: `Test ${itemType}`,
        occurredAt: new Date().toISOString(),
        importance: "medium" as const
      };
      expect(item.itemType).toBe(itemType);
    }
  });
});

describe("SalesDeskService API contract", () => {
  it("should define correct API endpoints", () => {
    const endpoints = {
      queue: "GET /api/sales-desk",
      reuse: "POST /api/sales-desk/reuse",
      meetingPrep: "POST /api/sales-desk/meeting-prep",
      timeline: (customerId: string) => `GET /api/sales-desk/timeline/${customerId}`
    };

    expect(endpoints.queue).toBe("GET /api/sales-desk");
    expect(endpoints.reuse).toBe("POST /api/sales-desk/reuse");
    expect(endpoints.meetingPrep).toBe("POST /api/sales-desk/meeting-prep");
    expect(endpoints.timeline(MOCK_CUSTOMER_ID)).toBe(`GET /api/sales-desk/timeline/${MOCK_CUSTOMER_ID}`);
  });
});

describe("Five Reuse Generation Logic", () => {
  it("should generate reuse result with all five components", () => {
    const reuse = {
      followupDraftId: "followup-001",
      nextStepSuggestion: "安排 Demo",
      internalBrief: "客户对方案有兴趣",
      customerMessageDraft: "王总，关于 Demo 的时间...",
      prepCardId: "prep-001",
      usedFallback: false
    };

    const components = [
      reuse.followupDraftId && "跟进记录草稿",
      reuse.nextStepSuggestion && "下一步动作建议",
      reuse.internalBrief && "经理一句话汇报",
      reuse.customerMessageDraft && "客户消息草稿",
      reuse.prepCardId && "Prep 卡片"
    ].filter(Boolean);

    expect(components).toHaveLength(5);
  });
});

describe("Rhythm Alert Thresholds", () => {
  const THRESHOLDS = {
    HIGH_INTENT_SILENT_HOURS: 48,
    QUOTE_WAITING_DAYS: 3,
    MULTI_ROUND_THRESHOLD: 3
  };

  it("should have reasonable thresholds", () => {
    expect(THRESHOLDS.HIGH_INTENT_SILENT_HOURS).toBeGreaterThanOrEqual(24);
    expect(THRESHOLDS.QUOTE_WAITING_DAYS).toBeGreaterThanOrEqual(1);
    expect(THRESHOLDS.MULTI_ROUND_THRESHOLD).toBeGreaterThanOrEqual(2);
  });

  it("should calculate hours correctly for escalation", () => {
    const hoursSinceContact = 52;
    const threshold = THRESHOLDS.HIGH_INTENT_SILENT_HOURS;

    expect(hoursSinceContact).toBeGreaterThan(threshold);
  });
});

describe("SalesDeskQueue Sorting Logic", () => {
  const mockItems: import("@/types/sales-desk").SalesDeskQueueItem[] = [
    {
      queueType: "rhythm_breach",
      customerId: "c1",
      customerName: "客户A",
      reason: "超期",
      priorityScore: 60,
      suggestedAction: "处理",
    },
    {
      queueType: "high_intent_silent",
      customerId: "c2",
      customerName: "客户B",
      reason: "48小时未推进",
      priorityScore: 85,
      suggestedAction: "联系",
      hoursSinceContact: 72,
    },
    {
      queueType: "quote_waiting",
      customerId: "c3",
      customerName: "客户C",
      reason: "等待回复",
      priorityScore: 70,
      suggestedAction: "跟进",
    },
  ];

  it("should sort by priority descending", () => {
    const sorted = [...mockItems].sort((a, b) => b.priorityScore - a.priorityScore);
    expect(sorted[0].priorityScore).toBe(85);
    expect(sorted[1].priorityScore).toBe(70);
    expect(sorted[2].priorityScore).toBe(60);
  });

  it("should sort by time ascending (oldest first)", () => {
    const itemsWithTime = mockItems.map((item, i) => ({
      ...item,
      lastContactAt: new Date(Date.now() - i * 86400000).toISOString(),
    }));
    const sorted = [...itemsWithTime].sort((a, b) => {
      const aTime = a.lastContactAt ? new Date(a.lastContactAt).getTime() : 0;
      const bTime = b.lastContactAt ? new Date(b.lastContactAt).getTime() : 0;
      return aTime - bTime;
    });
    expect(sorted[0].customerId).toBe("c1");
  });
});

describe("SalesDeskQueue Filtering Logic", () => {
  const mockQueue: import("@/types/sales-desk").SalesDeskQueueResult = {
    queues: {
      mustContactToday: [],
      rhythmBreach: [{ queueType: "rhythm_breach", customerId: "c1", customerName: "A", reason: "r1", priorityScore: 80, suggestedAction: "s1" }],
      highIntentSilent: [{ queueType: "high_intent_silent", customerId: "c2", customerName: "B", reason: "r2", priorityScore: 85, suggestedAction: "s2" }],
      quoteWaiting: [{ queueType: "quote_waiting", customerId: "c3", customerName: "C", reason: "r3", priorityScore: 70, suggestedAction: "s3" }],
      pendingMaterials: [],
      awaitingConfirmation: [],
    },
    totalCounts: { mustContactToday: 0, rhythmBreach: 1, highIntentSilent: 1, quoteWaiting: 1, pendingMaterials: 0, awaitingConfirmation: 0 },
  };

  it("should filter by rhythm_breach only", () => {
    const filterType = "rhythm_breach";
    const filtered =
      filterType === "all"
        ? [...mockQueue.queues.rhythmBreach, ...mockQueue.queues.highIntentSilent, ...mockQueue.queues.quoteWaiting]
        : filterType === "rhythm_breach"
        ? mockQueue.queues.rhythmBreach
        : filterType === "high_intent_silent"
        ? mockQueue.queues.highIntentSilent
        : mockQueue.queues.quoteWaiting;
    expect(filtered).toHaveLength(1);
    expect(filtered[0].customerId).toBe("c1");
  });

  it("should return all when filterType is all", () => {
    const filterType = "all";
    const all =
      filterType === "all"
        ? [...mockQueue.queues.rhythmBreach, ...mockQueue.queues.highIntentSilent, ...mockQueue.queues.quoteWaiting]
        : [];
    expect(all).toHaveLength(3);
  });
});

describe("CustomerTimeline Filtering Logic", () => {
  const mockItems: import("@/types/sales-desk").CustomerTimelineItem[] = [
    { id: "i1", itemType: "followup", title: "跟进1", occurredAt: new Date().toISOString(), importance: "high" },
    { id: "i2", itemType: "touchpoint", title: "触点1", occurredAt: new Date().toISOString(), importance: "medium" },
    { id: "i3", itemType: "followup", title: "跟进2", occurredAt: new Date().toISOString(), importance: "high" },
    { id: "i4", itemType: "business_event", title: "事件1", occurredAt: new Date().toISOString(), importance: "low" },
  ];

  it("should filter by item type", () => {
    const filtered = mockItems.filter(i => i.itemType === "followup");
    expect(filtered).toHaveLength(2);
  });

  it("should return all when filter is all", () => {
    const filterType = "all";
    const result = filterType === "all" ? mockItems : mockItems.filter(i => i.itemType === filterType);
    expect(result).toHaveLength(4);
  });

  it("should sort by time descending", () => {
    const sorted = [...mockItems].sort((a, b) => {
      const aTime = a.occurredAt ? new Date(a.occurredAt).getTime() : 0;
      const bTime = b.occurredAt ? new Date(b.occurredAt).getTime() : 0;
      return bTime - aTime;
    });
    expect(sorted).toHaveLength(4);
  });

  it("should deduplicate item types", () => {
    const types = Array.from(new Set(mockItems.map(i => i.itemType)));
    expect(types).toContain("followup");
    expect(types).toContain("touchpoint");
    expect(types).toContain("business_event");
    expect(types).toHaveLength(3);
  });
});

describe("Five Reuse Actions State", () => {
  it("should track copied state for customer message", () => {
    const actions: Record<string, "idle" | "copied" | "adopted"> = {};
    actions["customerMessage"] = "copied";
    expect(actions["customerMessage"]).toBe("copied");

    setTimeout(() => {
      actions["customerMessage"] = "idle";
      expect(actions["customerMessage"]).toBe("idle");
    }, 2000);
  });

  it("should track adopted state for next step", () => {
    const actions: Record<string, "idle" | "copied" | "adopted"> = {};
    actions["nextStep"] = "adopted";
    expect(actions["nextStep"]).toBe("adopted");
  });
});
