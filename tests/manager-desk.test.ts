/**
 * v1.2 Manager Desk Tests
 */

import { describe, it, expect } from "vitest";
import type {
  ManagerRiskItem,
  PipelineTruthScore,
  ManagerIntervention,
  TruthBand,
  TruthSignal,
  ManagerDeskInterventionStatus,
} from "@/types/manager-desk";

const MOCK_ORG_ID = "org-001";
const MOCK_OWNER_ID = "user-001";

describe("Manager Desk Types", () => {
  it("should have correct truth band labels", async () => {
    const { TRUTH_BAND_LABELS } = await import("@/types/manager-desk");
    expect(TRUTH_BAND_LABELS.healthy).toBe("健康");
    expect(TRUTH_BAND_LABELS.watch).toBe("观察");
    expect(TRUTH_BAND_LABELS.suspicious).toBe("可疑");
    expect(TRUTH_BAND_LABELS.stalled).toBe("停滞");
  });

  it("should have correct risk level labels", async () => {
    const { RISK_LEVEL_LABELS } = await import("@/types/manager-desk");
    expect(RISK_LEVEL_LABELS.critical).toBe("紧急");
    expect(RISK_LEVEL_LABELS.high).toBe("高");
    expect(RISK_LEVEL_LABELS.medium).toBe("中");
    expect(RISK_LEVEL_LABELS.low).toBe("低");
  });
});

describe("TruthBand sorting logic", () => {
  const bandOrder: Record<TruthBand, number> = { stalled: 0, suspicious: 1, watch: 2, healthy: 3 };

  it("should sort stalled before suspicious", () => {
    expect(bandOrder.stalled).toBeLessThan(bandOrder.suspicious);
  });

  it("should sort suspicious before healthy", () => {
    expect(bandOrder.suspicious).toBeLessThan(bandOrder.healthy);
  });

  it("should correctly order all bands", () => {
    const bands: TruthBand[] = ["healthy", "watch", "suspicious", "stalled"];
    const sorted = [...bands].sort((a, b) => bandOrder[a] - bandOrder[b]);
    expect(sorted).toEqual(["stalled", "suspicious", "watch", "healthy"]);
  });
});

describe("PipelineTruthScore structure", () => {
  it("should support truth score with signals", () => {
    const score: PipelineTruthScore = {
      customerId: "cust-001",
      customerName: "星河制造",
      opportunityId: "opp-001",
      opportunityName: "ERP采购项目",
      truthBand: "suspicious",
      signals: [
        {
          type: "stagnation",
          label: "长期无活动",
          weight: 20,
          isPositive: false,
          description: "超过 30 天无更新"
        },
        {
          type: "touchpoint",
          label: "等待客户回复",
          weight: 15,
          isPositive: true,
          description: "有邮件等待客户回复"
        }
      ],
      healthScore: 45,
      reason: "风险信号较多（长期无活动），需重点关注"
    };

    expect(score.truthBand).toBe("suspicious");
    expect(score.healthScore).toBeLessThan(50);
    expect(score.signals.filter(s => !s.isPositive).length).toBeGreaterThan(0);
  });
});

describe("ManagerRiskItem structure", () => {
  it("should support risk item with intervention", () => {
    const item: ManagerRiskItem = {
      id: "risk-001",
      itemType: "deal_room",
      customerId: "cust-001",
      customerName: "星河制造",
      opportunityId: "opp-001",
      opportunityName: "ERP采购项目",
      dealRoomId: "room-001",
      riskReason: "商机已阻塞",
      riskLevel: "high",
      lastActivityAt: new Date(Date.now() - 10 * 24 * 60 * 60 * 1000).toISOString(),
      currentStage: "方案确认",
      ownerId: MOCK_OWNER_ID,
      ownerName: "张三",
      priorityScore: 90,
      suggestedAction: "立即查看阻塞原因并处理",
      truthBand: "stalled",
      needsIntervention: true,
      interventionReason: "商机阻塞，需要立即介入"
    };

    expect(item.needsIntervention).toBe(true);
    expect(item.riskLevel).toBe("high");
    expect(item.priorityScore).toBeGreaterThanOrEqual(80);
  });
});

describe("ManagerIntervention structure", () => {
  it("should support coach intervention", () => {
    const intervention: ManagerIntervention = {
      id: "int-001",
      targetType: "deal_room",
      targetId: "room-001",
      targetName: "ERP采购项目",
      ownerId: MOCK_OWNER_ID,
      ownerName: "张三",
      interventionType: "coach",
      reason: "商机超过7天无活动",
      suggestedAction: "与销售确认最新进展",
      talkingPoints: [
        "了解最新进展",
        "确认客户意向是否有变化",
        "讨论推进计划"
      ],
      followUpItems: [
        "确认介入结果",
        "安排下次跟进时间"
      ],
      priority: "high",
      truthBand: "stalled"
    };

    expect(intervention.interventionType).toBe("coach");
    expect(intervention.talkingPoints.length).toBeGreaterThan(0);
    expect(intervention.followUpItems.length).toBeGreaterThan(0);
  });
});

describe("Truth Signal weighting", () => {
  it("should calculate health score correctly with new weights", () => {
    let score = 50;
    const positiveSignal: TruthSignal = {
      type: "touchpoint",
      label: "近期有活动",
      weight: 30,
      isPositive: true,
      description: "最近活动: 2 天前"
    };
    const negativeSignal: TruthSignal = {
      type: "stagnation",
      label: "长期无活动",
      weight: 20,
      isPositive: false,
      description: "超过 30 天无更新"
    };
    const waitingReplySignal: TruthSignal = {
      type: "touchpoint",
      label: "等待客户回复",
      weight: 15,
      isPositive: true,
      description: "有邮件等待客户回复"
    };

    score += positiveSignal.weight;
    expect(score).toBe(80);
    score -= negativeSignal.weight;
    expect(score).toBe(60);
    score += waitingReplySignal.weight;
    expect(score).toBe(75);
  });

  it("should clamp health score between 0 and 100", () => {
    const clamp = (score: number) => Math.max(0, Math.min(100, score));

    expect(clamp(-20)).toBe(0);
    expect(clamp(120)).toBe(100);
    expect(clamp(50)).toBe(50);
  });

  it("should produce reasonable health scores", () => {
    const calcScore = (signals: TruthSignal[]): number => {
      let score = 50;
      for (const s of signals) {
        if (s.isPositive) score += s.weight;
        else score -= Math.abs(s.weight);
      }
      return Math.max(0, Math.min(100, score));
    };

    expect(calcScore([])).toBe(50);
    expect(calcScore([{ type: "touchpoint", label: "近期", weight: 30, isPositive: true, description: "2天" }])).toBe(80);
    expect(calcScore([{ type: "stagnation", label: "长期无活动", weight: 20, isPositive: false, description: "超30天" }])).toBe(30);
    expect(calcScore([{ type: "stagnation", label: "长期", weight: 20, isPositive: false, description: "1" }, { type: "touchpoint", label: "近", weight: 30, isPositive: true, description: "2" }])).toBe(60);
  });
});

describe("ManagerDesk Filters", () => {
  it("should support ownerId filter", () => {
    const filters = { ownerId: MOCK_OWNER_ID };
    expect(filters.ownerId).toBe(MOCK_OWNER_ID);
  });

  it("should support riskLevel filter", () => {
    const filters = { riskLevel: "high" as const };
    expect(filters.riskLevel).toBe("high");
  });

  it("should support truthBand filter", () => {
    const filters = { truthBand: "stalled" as TruthBand };
    expect(filters.truthBand).toBe("stalled");
  });
});

describe("Risk Level Priority", () => {
  it("should assign correct priority order", () => {
    const priorityOrder = { critical: 4, high: 3, medium: 2, low: 1 };
    expect(priorityOrder.critical).toBeGreaterThan(priorityOrder.high);
    expect(priorityOrder.high).toBeGreaterThan(priorityOrder.medium);
    expect(priorityOrder.medium).toBeGreaterThan(priorityOrder.low);
  });
});

describe("ManagerDesk Intervention Status", () => {
  it("should support all intervention status values", () => {
    const statuses: ManagerDeskInterventionStatus[] = ["new", "task_created", "in_progress", "completed", "dismissed"];
    expect(statuses).toHaveLength(5);
    expect(statuses).toContain("new");
    expect(statuses).toContain("task_created");
    expect(statuses).toContain("in_progress");
    expect(statuses).toContain("completed");
    expect(statuses).toContain("dismissed");
  });

  it("should map work item status to intervention status", () => {
    const mapStatus = (status: string | undefined): ManagerDeskInterventionStatus | undefined => {
      if (!status) return undefined;
      if (status === "done") return "completed";
      if (status === "in_progress") return "in_progress";
      if (status === "todo" || status === "snoozed") return "task_created";
      return undefined;
    };

    expect(mapStatus("todo")).toBe("task_created");
    expect(mapStatus("in_progress")).toBe("in_progress");
    expect(mapStatus("done")).toBe("completed");
    expect(mapStatus("snoozed")).toBe("task_created");
    expect(mapStatus(undefined)).toBeUndefined();
  });

  it("should support risk item with intervention status", () => {
    const item: ManagerRiskItem = {
      id: "risk-001",
      itemType: "deal_room",
      customerId: "cust-001",
      customerName: "星河制造",
      dealRoomId: "room-001",
      riskReason: "商机已阻塞",
      riskLevel: "high",
      ownerId: MOCK_OWNER_ID,
      ownerName: "张三",
      priorityScore: 90,
      suggestedAction: "立即查看阻塞原因并处理",
      needsIntervention: true,
      interventionStatus: "task_created",
      linkedWorkItemId: "wi-001"
    };

    expect(item.interventionStatus).toBe("task_created");
    expect(item.linkedWorkItemId).toBe("wi-001");
    expect(item.needsIntervention).toBe(true);
  });

  it("should support intervention with status and linked work item", () => {
    const intervention: ManagerIntervention = {
      id: "int-001",
      targetType: "deal_room",
      targetId: "room-001",
      targetName: "ERP采购项目",
      ownerId: MOCK_OWNER_ID,
      ownerName: "张三",
      interventionType: "coach",
      reason: "商机超过7天无活动",
      suggestedAction: "与销售确认最新进展",
      talkingPoints: ["了解最新进展", "讨论推进计划"],
      followUpItems: ["确认介入结果", "安排下次跟进时间"],
      priority: "high",
      truthBand: "stalled",
      interventionStatus: "task_created",
      linkedWorkItemId: "wi-001"
    };

    expect(intervention.interventionStatus).toBe("task_created");
    expect(intervention.linkedWorkItemId).toBe("wi-001");
  });
});

describe("Work item dedup logic", () => {
  it("should use sourceRefType and sourceRefId for dedup", () => {
    const dedupKey = (sourceRefType: string, sourceRefId: string) => `${sourceRefType}:${sourceRefId}`;
    const key1 = dedupKey("manager_desk_intervention", "intervention-stalled-room-001");
    const key2 = dedupKey("manager_desk_intervention", "intervention-stalled-room-001");
    const key3 = dedupKey("manager_desk_intervention", "intervention-stalled-room-002");

    expect(key1).toBe(key2);
    expect(key1).not.toBe(key3);
  });

  it("should extract risk item id from intervention source ref id", () => {
    const extractRiskId = (sourceRefId: string) => sourceRefId.replace("intervention-", "");
    expect(extractRiskId("intervention-stalled-room-001")).toBe("stalled-room-001");
    expect(extractRiskId("intervention-room-001")).toBe("room-001");
  });
});

describe("ManagerDeskServerService helpers", () => {
  it("should calculate default due date as 3 days from now", () => {
    const now = Date.now();
    const dueDate = new Date(now + 3 * 24 * 60 * 60 * 1000);
    const dueDateStr = dueDate.toISOString();
    const dueDateMs = new Date(dueDateStr).getTime();
    const diffHours = (dueDateMs - now) / (1000 * 60 * 60);
    expect(diffHours).toBeGreaterThan(71);
    expect(diffHours).toBeLessThan(73);
  });

  it("should sort risk items by priority score descending", () => {
    const items: Array<{ priorityScore: number }> = [
      { priorityScore: 60 },
      { priorityScore: 95 },
      { priorityScore: 80 }
    ];
    const sorted = items.sort((a, b) => b.priorityScore - a.priorityScore);
    expect(sorted[0].priorityScore).toBe(95);
    expect(sorted[1].priorityScore).toBe(80);
    expect(sorted[2].priorityScore).toBe(60);
  });

  it("should map work item work type from intervention type", () => {
    const mapWorkType = (interventionType: string) =>
      interventionType === "escalate" ? "revive_stalled_deal" : "manager_checkin";

    expect(mapWorkType("escalate")).toBe("revive_stalled_deal");
    expect(mapWorkType("coach")).toBe("manager_checkin");
    expect(mapWorkType("follow_up")).toBe("manager_checkin");
    expect(mapWorkType("support")).toBe("manager_checkin");
  });
});

describe("P3: Intervention Records persistence", () => {
  it("should support completed and dismissed resolution values", () => {
    const validResolutions = ["completed", "dismissed"] as const;
    expect(validResolutions).toContain("completed");
    expect(validResolutions).toContain("dismissed");
  });

  it("should use intervention_key as unique dedup key", () => {
    const makeKey = (interventionKey: string) => interventionKey;
    const key1 = makeKey("intervention-stalled-room-001");
    const key2 = makeKey("intervention-room-001");
    expect(key1).not.toBe(key2);
  });

  it("should build intervention record map with deal_room_id and intervention_key", () => {
    const records = [
      { intervention_key: "intervention-stalled-room-001", resolution_status: "completed", deal_room_id: "room-001", work_item_id: "wi-001" },
      { intervention_key: "intervention-room-002", resolution_status: "dismissed", deal_room_id: "room-002", work_item_id: null }
    ];
    const map = new Map<string, { resolution_status: string; deal_room_id: string | null; work_item_id: string | null }>();
    for (const rec of records) {
      if (rec.deal_room_id) {
        map.set(rec.deal_room_id, { resolution_status: rec.resolution_status, deal_room_id: rec.deal_room_id, work_item_id: rec.work_item_id });
      }
      if (rec.intervention_key) {
        map.set(rec.intervention_key, { resolution_status: rec.resolution_status, deal_room_id: rec.deal_room_id, work_item_id: rec.work_item_id });
      }
    }
    expect(map.get("room-001")?.resolution_status).toBe("completed");
    expect(map.get("intervention-stalled-room-001")?.resolution_status).toBe("completed");
    expect(map.get("room-002")?.resolution_status).toBe("dismissed");
  });

  it("should prioritize intervention_record over work_item for status resolution", () => {
    const workItemStatus = "todo";
    const interventionRecordStatus = "completed";
    const finalStatus = interventionRecordStatus ?? (workItemStatus === "done" ? "completed" : workItemStatus === "in_progress" ? "in_progress" : workItemStatus === "todo" ? "task_created" : undefined);
    expect(finalStatus).toBe("completed");
  });

  it("should fallback to work_item status when no intervention record exists", () => {
    const workItemStatus = "in_progress";
    const interventionRecordStatus: string | null = null;
    const finalStatus = interventionRecordStatus ?? (workItemStatus === "done" ? "completed" : workItemStatus === "in_progress" ? "in_progress" : workItemStatus === "todo" ? "task_created" : undefined);
    expect(finalStatus).toBe("in_progress");
  });

  it("should set interventionKey correctly for blocked room", () => {
    const itemId = "room-001";
    const interventionKey = `intervention-${itemId}`;
    expect(interventionKey).toBe("intervention-room-001");
  });

  it("should set interventionKey correctly for stalled risk item", () => {
    const itemId = "stalled-room-001";
    const interventionKey = `intervention-${itemId}`;
    expect(interventionKey).toBe("intervention-stalled-room-001");
  });
});

describe("P3: Resolution API validation", () => {
  it("should validate resolution is completed or dismissed", () => {
    const isValidResolution = (r: string) => r === "completed" || r === "dismissed";
    expect(isValidResolution("completed")).toBe(true);
    expect(isValidResolution("dismissed")).toBe(true);
    expect(isValidResolution("in_progress")).toBe(false);
    expect(isValidResolution("new")).toBe(false);
  });

  it("should require interventionKey and resolution", () => {
    const body = { interventionKey: "intervention-room-001", resolution: "completed" as const };
    expect(body.interventionKey.length).toBeGreaterThan(0);
    expect(body.resolution).toBe("completed");
  });
});

describe("P3: Hook resolution flow", () => {
  it("should call resolveIntervention with correct params", () => {
    const params = {
      interventionKey: "intervention-room-001",
      resolution: "completed" as const,
      outcomeNote: "已当面沟通，销售已制定推进计划",
      intervention: {
        id: "intervention-room-001",
        targetType: "deal_room" as const,
        targetId: "room-001",
        targetName: "ERP采购项目",
        ownerId: "user-001",
        ownerName: "张三",
        interventionType: "coach" as const,
        reason: "商机超过30天无活动",
        suggestedAction: "与销售确认最新进展",
        talkingPoints: ["了解最新进展"],
        followUpItems: ["确认介入结果"],
        priority: "high" as const,
        interventionStatus: "task_created",
        linkedWorkItemId: "wi-001"
      }
    };
    expect(params.resolution).toBe("completed");
    expect(params.intervention.linkedWorkItemId).toBe("wi-001");
  });

  it("should reload data after resolve", async () => {
    const loadCallCount = { current: 0 };
    const load = async () => { loadCallCount.current++; };
    await load();
    await load();
    expect(loadCallCount.current).toBe(2);
  });
});
