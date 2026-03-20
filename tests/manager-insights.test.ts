/**
 * v1.3 Manager Insights Tests
 */

import { describe, it, expect } from "vitest";
import type { TruthBand } from "@/types/manager-desk";

describe("Truth Band distribution logic", () => {
  const TRUTH_BAND_LABELS: Record<TruthBand, string> = {
    healthy: "健康",
    watch: "观察",
    suspicious: "可疑",
    stalled: "停滞"
  };

  type BandCount = { band: TruthBand; label: string; count: number; percentage: number };
  type Room = { id: string; room_status: string; updated_at: string };

  function computeTruthBandDistribution(dealRooms: Room[]): BandCount[] {
    const now = Date.now();
    const bands: Record<TruthBand, number> = { healthy: 0, watch: 0, suspicious: 0, stalled: 0 };
    const STALL_THRESHOLD = 30;
    const SUSPICIOUS_THRESHOLD = 14;
    const WATCH_THRESHOLD = 7;

    for (const room of dealRooms) {
      if (room.room_status === "blocked" || room.room_status === "escalated") {
        bands.stalled++;
        continue;
      }
      const updatedMs = new Date(room.updated_at).getTime();
      const daysSince = (now - updatedMs) / (1000 * 60 * 60 * 24);
      if (daysSince > STALL_THRESHOLD) bands.stalled++;
      else if (daysSince > SUSPICIOUS_THRESHOLD) bands.suspicious++;
      else if (daysSince > WATCH_THRESHOLD) bands.watch++;
      else bands.healthy++;
    }

    const total = dealRooms.length;
    return (["healthy", "watch", "suspicious", "stalled"] as TruthBand[]).map(band => ({
      band,
      label: TRUTH_BAND_LABELS[band],
      count: bands[band],
      percentage: total > 0 ? bands[band] / total : 0
    }));
  }

  it("should classify blocked room as stalled", () => {
    const now = new Date().toISOString();
    const rooms: Room[] = [{ id: "1", room_status: "blocked", updated_at: now }];
    const result = computeTruthBandDistribution(rooms);
    expect(result.find(b => b.band === "stalled")?.count).toBe(1);
    expect(result.find(b => b.band === "healthy")?.count).toBe(0);
  });

  it("should classify escalated room as stalled", () => {
    const now = new Date().toISOString();
    const rooms: Room[] = [{ id: "1", room_status: "escalated", updated_at: now }];
    const result = computeTruthBandDistribution(rooms);
    expect(result.find(b => b.band === "stalled")?.count).toBe(1);
  });

  it("should classify room updated today as healthy", () => {
    const today = new Date().toISOString();
    const rooms: Room[] = [{ id: "1", room_status: "active", updated_at: today }];
    const result = computeTruthBandDistribution(rooms);
    expect(result.find(b => b.band === "healthy")?.count).toBe(1);
  });

  it("should classify room updated 10 days ago as suspicious", () => {
    const tenDaysAgo = new Date(Date.now() - 10 * 24 * 60 * 60 * 1000).toISOString();
    const rooms: Room[] = [{ id: "1", room_status: "active", updated_at: tenDaysAgo }];
    const result = computeTruthBandDistribution(rooms);
    expect(result.find(b => b.band === "suspicious")?.count).toBe(1);
  });

  it("should classify room updated 20 days ago as suspicious", () => {
    const twentyDaysAgo = new Date(Date.now() - 20 * 24 * 60 * 60 * 1000).toISOString();
    const rooms: Room[] = [{ id: "1", room_status: "active", updated_at: twentyDaysAgo }];
    const result = computeTruthBandDistribution(rooms);
    expect(result.find(b => b.band === "suspicious")?.count).toBe(1);
  });

  it("should classify room updated 31 days ago as stalled", () => {
    const thirtyOneDaysAgo = new Date(Date.now() - 31 * 24 * 60 * 60 * 1000).toISOString();
    const rooms: Room[] = [{ id: "1", room_status: "active", updated_at: thirtyOneDaysAgo }];
    const result = computeTruthBandDistribution(rooms);
    expect(result.find(b => b.band === "stalled")?.count).toBe(1);
  });

  it("should give correct percentages", () => {
    const now = new Date();
    const fiveDaysAgo = new Date(now.getTime() - 5 * 24 * 60 * 60 * 1000).toISOString();
    const twentyDaysAgo = new Date(now.getTime() - 20 * 24 * 60 * 60 * 1000).toISOString();
    const rooms: Room[] = [
      { id: "1", room_status: "active", updated_at: fiveDaysAgo },
      { id: "2", room_status: "active", updated_at: twentyDaysAgo },
      { id: "3", room_status: "blocked", updated_at: fiveDaysAgo }
    ];
    const result = computeTruthBandDistribution(rooms);
    expect(result.find(b => b.band === "healthy")?.percentage).toBeCloseTo(0.333, 2);
    expect(result.find(b => b.band === "suspicious")?.percentage).toBeCloseTo(0.333, 2);
    expect(result.find(b => b.band === "stalled")?.percentage).toBeCloseTo(0.333, 2);
  });

  it("should handle empty room list", () => {
    const result = computeTruthBandDistribution([]);
    expect(result.every(b => b.count === 0 && b.percentage === 0)).toBe(true);
  });
});

describe("Intervention analytics computation", () => {
  it("should compute completion rate correctly", () => {
    const total = 10;
    const completed = 7;
    const completionRate = total > 0 ? completed / total : 0;
    expect(completionRate).toBe(0.7);
  });

  it("should compute dismiss rate correctly", () => {
    const total = 10;
    const dismissed = 2;
    const dismissRate = total > 0 ? dismissed / total : 0;
    expect(dismissRate).toBe(0.2);
  });

  it("should handle zero total for rates", () => {
    const total = 0;
    const completionRate = total > 0 ? 7 / total : 0;
    expect(completionRate).toBe(0);
  });

  it("should aggregate by risk reason correctly", () => {
    const interventions = [
      { risk_reason: "商机已阻塞", resolution_status: "completed" },
      { risk_reason: "商机已阻塞", resolution_status: "dismissed" },
      { risk_reason: "商机长期停滞", resolution_status: "completed" },
      { risk_reason: "商机已阻塞", resolution_status: "completed" }
    ];
    const map = new Map<string, number>();
    for (const i of interventions) {
      if (i.risk_reason) map.set(i.risk_reason, (map.get(i.risk_reason) ?? 0) + 1);
    }
    const sorted = Array.from(map.entries()).sort((a, b) => b[1] - a[1]);
    expect(sorted[0][0]).toBe("商机已阻塞");
    expect(sorted[0][1]).toBe(3);
    expect(sorted[1][0]).toBe("商机长期停滞");
    expect(sorted[1][1]).toBe(1);
  });

  it("should classify work item types correctly", () => {
    const workItems = [
      { work_type: "manager_checkin" },
      { work_type: "manager_checkin" },
      { work_type: "revive_stalled_deal" },
      { work_type: "follow_up" }
    ];
    const byType = {
      coach: workItems.filter(w => w.work_type === "manager_checkin").length,
      escalate: workItems.filter(w => w.work_type === "revive_stalled_deal").length,
      followUp: workItems.filter(w => w.work_type === "follow_up").length,
      support: workItems.filter(w => w.work_type === "support").length
    };
    expect(byType.coach).toBe(2);
    expect(byType.escalate).toBe(1);
    expect(byType.followUp).toBe(1);
    expect(byType.support).toBe(0);
  });
});

describe("Risk improvement computation", () => {
  it("should compute blocked improvement rate", () => {
    const periodStartIso = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString();
    const dealRooms = [
      { id: "1", room_status: "active", updated_at: new Date(Date.now() - 3 * 24 * 60 * 60 * 1000).toISOString() },
      { id: "2", room_status: "watchlist", updated_at: new Date(Date.now() - 5 * 24 * 60 * 60 * 1000).toISOString() },
      { id: "3", room_status: "blocked", updated_at: new Date().toISOString() }
    ];
    const totalBlocked = dealRooms.filter(r => r.room_status === "blocked").length;
    const resolvedBlocked = dealRooms.filter(
      r => r.room_status !== "blocked" && new Date(r.updated_at) >= new Date(periodStartIso)
    ).length;
    const improvementRate = totalBlocked > 0 ? resolvedBlocked / totalBlocked : 0;
    expect(totalBlocked).toBe(1);
    expect(resolvedBlocked).toBe(2);
    expect(improvementRate).toBe(1);
  });

  it("should handle zero blocked for improvement rate", () => {
    const dealRooms = [
      { id: "1", room_status: "active", updated_at: new Date().toISOString() }
    ];
    const totalBlocked = dealRooms.filter(r => r.room_status === "blocked").length;
    const improvementRate = totalBlocked > 0 ? 0.5 / totalBlocked : 0;
    expect(improvementRate).toBe(0);
  });
});

describe("ManagerInsightsResult structure", () => {
  it("should have required fields", () => {
    const result = {
      periodStart: "2026-03-13",
      periodEnd: "2026-03-20",
      truthBandDistribution: [
        { band: "healthy" as TruthBand, label: "健康", count: 5, percentage: 0.5 },
        { band: "stalled" as TruthBand, label: "停滞", count: 5, percentage: 0.5 }
      ],
      interventionAnalytics: {
        totalCreated: 10,
        totalCompleted: 7,
        totalDismissed: 2,
        completionRate: 0.7,
        dismissRate: 0.2,
        byType: { coach: 5, escalate: 3, followUp: 1, support: 1 },
        byRiskReason: [{ reason: "商机已阻塞", count: 4 }]
      },
      riskImprovement: {
        resolvedBlockedCount: 2,
        resolvedEscalatedCount: 1,
        totalTrackedRooms: 10,
        improvementRate: 0.2,
        notes: "近似口径"
      },
      newRiskSignals: {
        newCriticalCount: 1,
        newHighRiskCount: 3,
        newBlockedCount: 1,
        newStalledCount: 2
      }
    };
    expect(result.truthBandDistribution).toHaveLength(4);
    expect(result.interventionAnalytics.totalCreated).toBe(10);
    expect(result.riskImprovement.improvementRate).toBeCloseTo(0.2);
    expect(result.newRiskSignals.newBlockedCount).toBe(1);
  });
});

describe("Period date calculation", () => {
  it("should calculate 7-day period correctly", () => {
    const now = new Date();
    const periodStart = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
    const diffDays = (now.getTime() - periodStart.getTime()) / (1000 * 60 * 60 * 24);
    expect(diffDays).toBeGreaterThanOrEqual(7);
    expect(diffDays).toBeLessThan(8);
  });

  it("should calculate 30-day period correctly", () => {
    const now = new Date();
    const periodStart = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000);
    const diffDays = (now.getTime() - periodStart.getTime()) / (1000 * 60 * 60 * 24);
    expect(diffDays).toBeGreaterThanOrEqual(30);
    expect(diffDays).toBeLessThan(31);
  });
});
