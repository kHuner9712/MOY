/**
 * v1.2 Manager Desk Service
 * 经理作战台核心服务
 * 复用现有 services，聚合经理视角数据
 */

import { customerService } from "./customer-service";
import { touchpointClientService } from "./touchpoint-client-service";
import { dealRoomClientService } from "./deal-room-client-service";
import { executiveClientService } from "./executive-client-service";
import { workItemClientService } from "./work-item-client-service";
import type {
  ManagerDeskResult,
  ManagerRiskItem,
  PipelineTruthScore,
  ManagerIntervention,
  TruthBand,
  TruthSignal,
  RiskLevel,
} from "@/types/manager-desk";

const STALL_THRESHOLD_DAYS = 30;
const SUSPICIOUS_THRESHOLD_DAYS = 14;
const WATCH_THRESHOLD_DAYS = 7;
const HIGH_INTENT_DAYS = 48;

export class ManagerDeskService {
  async getManagerDesk(params: {
    orgId: string;
    ownerId: string;
  }): Promise<ManagerDeskResult> {
    const { orgId, ownerId } = params;

    const [customers, touchpointHub, dealRoomsData, alerts] = await Promise.all([
      customerService.listCustomers({ ownerId }),
      touchpointClientService.getHub({ ownerId, limit: 200 }).catch(() => null),
      dealRoomClientService.list({ statuses: ["active", "watchlist", "escalated", "blocked"], limit: 200 }).catch(() => null),
      executiveClientService.getExecutiveEvents({ status: ["open"], limit: 100 }).catch(() => null),
    ]);

    const customerMap = new Map(customers.map(c => [c.id, c]));
    const rooms = dealRoomsData ?? [];
    const riskItems: ManagerRiskItem[] = [];

    for (const room of rooms) {
      const customer = customerMap.get(room.customerId);
      if (!customer) continue;

      const truthBand = this.calculateTruthBand(room.updatedAt, touchpointHub, room.currentGoal);

      if (room.roomStatus === "blocked" || room.roomStatus === "escalated") {
        riskItems.push({
          id: room.id,
          itemType: "deal_room",
          customerId: customer.id,
          customerName: customer.companyName,
          opportunityId: room.opportunityId ?? undefined,
          opportunityName: room.opportunityTitle ?? undefined,
          dealRoomId: room.id,
          riskReason: room.roomStatus === "blocked" ? "商机已阻塞" : "商机已升级",
          riskLevel: room.roomStatus === "blocked" ? "high" : "critical",
          lastActivityAt: room.updatedAt,
          currentStage: room.nextMilestone ?? undefined,
          ownerId: room.ownerId,
          ownerName: room.ownerName,
          priorityScore: room.roomStatus === "blocked" ? 90 : 95,
          suggestedAction: room.roomStatus === "blocked" ? "立即查看阻塞原因并处理" : "关注商机升级原因",
          truthBand,
          needsIntervention: true,
          interventionReason: room.roomStatus === "blocked" ? "商机阻塞，需要立即介入" : "商机升级，需要关注"
        });
      }

      if (truthBand === "stalled") {
        riskItems.push({
          id: `stalled-${room.id}`,
          itemType: "deal_room",
          customerId: customer.id,
          customerName: customer.companyName,
          opportunityId: room.opportunityId ?? undefined,
          opportunityName: room.opportunityTitle ?? undefined,
          dealRoomId: room.id,
          riskReason: "商机长期停滞",
          riskLevel: "high",
          lastActivityAt: room.updatedAt,
          currentStage: room.nextMilestone ?? undefined,
          ownerId: room.ownerId,
          ownerName: room.ownerName,
          priorityScore: 80,
          suggestedAction: "与销售确认最新进展",
          truthBand,
          needsIntervention: true,
          interventionReason: "商机超过7天无活动"
        });
      }

      if (truthBand === "suspicious") {
        riskItems.push({
          id: `suspicious-${room.id}`,
          itemType: "deal_room",
          customerId: customer.id,
          customerName: customer.companyName,
          opportunityId: room.opportunityId ?? undefined,
          opportunityName: room.opportunityTitle ?? undefined,
          dealRoomId: room.id,
          riskReason: "商机进展可疑",
          riskLevel: "medium",
          lastActivityAt: room.updatedAt,
          currentStage: room.nextMilestone ?? undefined,
          ownerId: room.ownerId,
          ownerName: room.ownerName,
          priorityScore: 60,
          suggestedAction: "跟进确认商机状态",
          truthBand,
          needsIntervention: false
        });
      }
    }

    for (const alert of alerts?.events ?? []) {
      if (alert.entityType !== "customer" || !alert.entityId) continue;
      const customer = customerMap.get(alert.entityId);
      if (!customer) continue;

      const existing = riskItems.find(i => i.customerId === alert.entityId);
      if (existing) continue;

      if (alert.severity === "warning" || alert.severity === "critical") {
        riskItems.push({
          id: `alert-${alert.id}`,
          itemType: "customer",
          customerId: customer.id,
          customerName: customer.companyName,
          riskReason: alert.eventSummary ?? alert.eventType,
          riskLevel: alert.severity === "critical" ? "high" : "medium",
          lastActivityAt: alert.createdAt,
          ownerId: customer.ownerId,
          ownerName: customer.ownerName,
          priorityScore: 60,
          suggestedAction: "查看并处理预警",
          needsIntervention: false
        });
      }
    }

    const truthScores: PipelineTruthScore[] = [];
    for (const room of rooms) {
      const customer = customerMap.get(room.customerId);
      if (!customer) continue;
      const signals = this.gatherTruthSignals(room.updatedAt, touchpointHub, room.currentGoal);
      truthScores.push({
        customerId: customer.id,
        customerName: customer.companyName,
        opportunityId: room.opportunityId ?? undefined,
        opportunityName: room.opportunityTitle ?? undefined,
        truthBand: this.calculateTruthBand(room.updatedAt, touchpointHub, room.currentGoal),
        signals,
        healthScore: this.calculateHealthScore(signals),
        reason: this.generateTruthReason(signals)
      });
    }

    const interventions: ManagerIntervention[] = riskItems
      .filter(item => item.needsIntervention)
      .slice(0, 10)
      .map(item => this.generateIntervention(item));

    return {
      riskQueue: riskItems.sort((a, b) => b.priorityScore - a.priorityScore),
      truthScores: truthScores.sort((a, b) => {
        const bandOrder: Record<TruthBand, number> = { stalled: 0, suspicious: 1, watch: 2, healthy: 3 };
        return bandOrder[a.truthBand] - bandOrder[b.truthBand];
      }),
      interventions,
      summary: {
        totalRisks: riskItems.length,
        criticalCount: riskItems.filter(i => i.riskLevel === "critical").length,
        suspiciousCount: truthScores.filter(s => s.truthBand === "suspicious").length,
        stalledCount: truthScores.filter(s => s.truthBand === "stalled").length,
        needsInterventionCount: interventions.length
      }
    };
  }

  private calculateTruthBand(lastActivityAt: string | null | undefined, touchpointHub: { hub?: { emailThreads?: Array<{ latestMessageAt?: string | null; threadStatus?: string }>; calendarEvents?: Array<{ startAt?: string | null; meetingStatus?: string }> } } | null, currentStage: string | undefined): TruthBand {
    const now = Date.now();
    const lastActivity = lastActivityAt ? new Date(lastActivityAt).getTime() : 0;
    const daysSinceActivity = lastActivity ? (now - lastActivity) / (1000 * 60 * 60 * 24) : Infinity;

    if (daysSinceActivity > STALL_THRESHOLD_DAYS) return "stalled";
    if (daysSinceActivity > SUSPICIOUS_THRESHOLD_DAYS) return "suspicious";
    if (daysSinceActivity > WATCH_THRESHOLD_DAYS) return "watch";

    return "healthy";
  }

  private gatherTruthSignals(
    lastActivityAt: string | null | undefined,
    touchpointHub: { hub?: { emailThreads?: Array<{ latestMessageAt?: string | null; threadStatus?: string }>; calendarEvents?: Array<{ startAt?: string | null; meetingStatus?: string }> } } | null,
    currentStage: string | undefined
  ): TruthSignal[] {
    const signals: TruthSignal[] = [];
    const now = Date.now();

    if (lastActivityAt) {
      const daysSince = (now - new Date(lastActivityAt).getTime()) / (1000 * 60 * 60 * 24);
      if (daysSince > 30) {
        signals.push({
          type: "stagnation",
          label: "长期无活动",
          weight: 20,
          isPositive: false,
          description: `超过 ${Math.round(daysSince)} 天无更新`
        });
      } else if (daysSince <= 7) {
        signals.push({
          type: "touchpoint",
          label: "近期有活动",
          weight: 30,
          isPositive: true,
          description: `最近活动: ${Math.round(daysSince)} 天前`
        });
      } else {
        signals.push({
          type: "touchpoint",
          label: "最近有活动",
          weight: 10,
          isPositive: true,
          description: `最后活动: ${Math.round(daysSince)} 天前`
        });
      }
    } else {
      signals.push({
        type: "no_contact",
        label: "无活动记录",
        weight: 20,
        isPositive: false,
        description: "尚无任何活动记录"
      });
    }

    const hasWaitingReply = touchpointHub?.hub?.emailThreads?.some(t => t.threadStatus === "waiting_reply");
    if (hasWaitingReply) {
      signals.push({
        type: "touchpoint",
        label: "等待客户回复",
        weight: 15,
        isPositive: true,
        description: "有邮件等待客户回复"
      });
    }

    const hasUpcomingMeeting = touchpointHub?.hub?.calendarEvents?.some(e =>
      e.meetingStatus === "scheduled" && e.startAt && new Date(e.startAt).getTime() > now
    );
    if (hasUpcomingMeeting) {
      signals.push({
        type: "touchpoint",
        label: "即将有会议",
        weight: 20,
        isPositive: true,
        description: "近期有安排的会议"
      });
    }

    if (currentStage === "quote" || currentStage === "contract") {
      signals.push({
        type: "quote",
        label: "报价/合同阶段",
        weight: 20,
        isPositive: true,
        description: `当前阶段: ${currentStage}`
      });
    }

    return signals;
  }

  private calculateHealthScore(signals: TruthSignal[]): number {
    let score = 50;
    for (const s of signals) {
      if (s.isPositive) {
        score += s.weight;
      } else {
        score -= Math.abs(s.weight);
      }
    }
    return Math.max(0, Math.min(100, score));
  }

  private generateTruthReason(signals: TruthSignal[]): string {
    if (signals.length === 0) return "缺乏足够信号判断";
    const positiveSignals = signals.filter(s => s.isPositive);
    const negativeSignals = signals.filter(s => !s.isPositive);
    if (positiveSignals.length > 0 && negativeSignals.length === 0) {
      const reasons = positiveSignals.map(s => s.description).join("；");
      return reasons || "有正向推进信号";
    }
    if (negativeSignals.length > 0 && positiveSignals.length === 0) {
      const reasons = negativeSignals.map(s => s.description).join("；");
      return reasons || "缺乏正向推进信号";
    }
    if (positiveSignals.length > negativeSignals.length) {
      return `正向信号占优（${positiveSignals.map(s => s.label).join("、")}），但存在风险点`;
    }
    if (negativeSignals.length > positiveSignals.length) {
      return `风险信号较多（${negativeSignals.map(s => s.label).join("、")}），需重点关注`;
    }
    return "推进信号不明显，建议跟进确认";
  }

  private generateIntervention(item: ManagerRiskItem): ManagerIntervention {
    const talkingPoints: Record<string, string[]> = {
      blocked: ["了解阻塞的具体原因", "确认是否有解决方案", "讨论需要哪些支持"],
      escalated: ["确认升级原因", "评估是否需要更多资源", "确定下一步行动"],
      stalled: ["了解最新进展", "确认客户意向是否有变化", "讨论推进计划"]
    };

    return {
      id: `intervention-${item.id}`,
      targetType: item.itemType,
      targetId: item.itemType === "deal_room" ? item.dealRoomId ?? item.id : item.id,
      targetName: item.opportunityName ?? item.customerName,
      ownerId: item.ownerId,
      ownerName: item.ownerName,
      interventionType: item.riskLevel === "critical" ? "escalate" : "coach",
      reason: item.interventionReason ?? item.riskReason,
      suggestedAction: item.suggestedAction,
      talkingPoints: talkingPoints[item.riskLevel === "critical" ? "escalated" : item.riskLevel === "high" ? "stalled" : "blocked"] ?? ["了解情况", "确认计划"],
      followUpItems: ["确认介入结果", "安排下次跟进时间"],
      priority: item.riskLevel,
      truthBand: item.truthBand
    };
  }
}

export const managerDeskService = new ManagerDeskService();
