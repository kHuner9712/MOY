/**
 * v1.2 Manager Desk Server Service
 * 服务端专用，避免 browser-client 混入
 */

import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { listDealRooms } from "@/services/deal-room-service";
import { executiveClientService } from "@/services/executive-client-service";
import { createOrReuseWorkItemBySourceRef } from "@/services/work-item-service";
import { updateWorkItemStatus } from "@/services/work-item-service";
import type { Customer } from "@/types/customer";
import type { DealRoom } from "@/types/deal";
import type { BusinessEvent } from "@/types/automation";
import type {
  ManagerDeskResult,
  ManagerRiskItem,
  PipelineTruthScore,
  ManagerIntervention,
  TruthBand,
  TruthSignal,
  ManagerDeskInterventionStatus,
} from "@/types/manager-desk";

const STALL_THRESHOLD_DAYS = 30;
const SUSPICIOUS_THRESHOLD_DAYS = 14;
const WATCH_THRESHOLD_DAYS = 7;

interface HubResponse {
  hub: {
    emailThreads: Array<{
      latestMessageAt: string | null;
      threadStatus: string;
    }>;
    calendarEvents: Array<{
      startAt: string | null;
      meetingStatus: string;
    }>;
  };
}

export class ManagerDeskServerService {
  async getManagerDesk(params: {
    supabase: ServerSupabaseClient;
    orgId: string;
    ownerId: string;
  }): Promise<ManagerDeskResult> {
    const { supabase, orgId, ownerId } = params;

    const [customersResult, dealRoomsResult, alertsResult, touchpointHub, existingWorkItems, interventionRecords] = await Promise.all([
      this.listCustomers({ supabase, orgId, ownerId }),
      this.listDealRooms({ supabase, orgId }),
      executiveClientService.getExecutiveEvents({ status: ["open"], limit: 100 }).catch(() => null),
      this.getTouchpointHub({ supabase, ownerId }).catch(() => null),
      this.listManagerDeskWorkItems({ supabase, orgId }),
      this.listInterventionRecords({ supabase, orgId }),
    ]);

    const customers = customersResult;
    const customerMap = new Map(customers.map(c => [c.id, c]));
    const rooms = dealRoomsResult;
    const workItemMap = this.buildWorkItemMap(existingWorkItems);
    const interventionRecordMap = this.buildInterventionRecordMap(interventionRecords);
    const riskItems: ManagerRiskItem[] = [];

    for (const room of rooms) {
      const customer = customerMap.get(room.customerId);
      if (!customer) continue;

      const truthBand = this.calculateTruthBand(room.updatedAt, touchpointHub, room.currentGoal);
      const workItem = workItemMap.get(room.id);
      const interventionRecord = interventionRecordMap.get(room.id);
      const baseStatus = workItem ? this.mapWorkItemStatusToIntervention(workItem.status) : undefined;
      const finalStatus = interventionRecord
        ? (interventionRecord.resolution_status as ManagerDeskInterventionStatus)
        : (baseStatus ?? undefined);
      const finalLinkedWorkItemId = workItem?.id ?? interventionRecord?.work_item_id ?? undefined;

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
          interventionReason: room.roomStatus === "blocked" ? "商机阻塞，需要立即介入" : "商机升级，需要关注",
          interventionStatus: finalStatus,
          linkedWorkItemId: finalLinkedWorkItemId,
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
          interventionReason: "商机超过30天无活动",
          interventionStatus: finalStatus,
          linkedWorkItemId: finalLinkedWorkItemId,
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
          needsIntervention: false,
          interventionStatus: finalStatus,
          linkedWorkItemId: finalLinkedWorkItemId,
        });
      }
    }

    for (const alert of alertsResult?.events ?? []) {
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
          needsIntervention: false,
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

  async createInterventionWorkItem(params: {
    supabase: ServerSupabaseClient;
    orgId: string;
    intervention: ManagerIntervention;
    actorUserId: string;
  }): Promise<{ workItemId: string; created: boolean }> {
    const { supabase, orgId, intervention, actorUserId } = params;

    const sourceRefType = "manager_desk_intervention";
    const sourceRefId = intervention.id;

    const created = await createOrReuseWorkItemBySourceRef({
      supabase,
      orgId,
      ownerId: intervention.ownerId,
      customerId: intervention.targetType === "customer" ? intervention.targetId : null,
      opportunityId: intervention.targetType === "opportunity" ? intervention.targetId : null,
      sourceType: "manager_assigned",
      workType: intervention.interventionType === "escalate" ? "revive_stalled_deal" : "manager_checkin",
      title: `【经理介入】${intervention.targetName}`,
      description: intervention.reason,
      rationale: intervention.suggestedAction,
      priorityScore: intervention.priority === "critical" ? 95 : intervention.priority === "high" ? 80 : 60,
      priorityBand: intervention.priority === "critical" ? "critical" : intervention.priority === "high" ? "high" : "medium",
      dueAt: this.defaultDueDate(),
      sourceRefType,
      sourceRefId,
      aiGenerated: false,
      createdBy: actorUserId
    });

    return {
      workItemId: created.workItem.id,
      created: created.created
    };
  }

  async resolveIntervention(params: {
    supabase: ServerSupabaseClient;
    orgId: string;
    interventionKey: string;
    resolution: "completed" | "dismissed";
    actorUserId: string;
    outcomeNote?: string;
    intervention: ManagerIntervention;
  }): Promise<{ id: string }> {
    const { supabase, orgId, interventionKey, resolution, actorUserId, outcomeNote, intervention } = params;

    const { data: existing } = await supabase
      .from("manager_desk_intervention_records")
      .select("id")
      .eq("org_id", orgId)
      .eq("intervention_key", interventionKey)
      .limit(1)
      .maybeSingle();

    if (existing) {
      const { data: updated } = await supabase
        .from("manager_desk_intervention_records")
        .update({
          resolution_status: resolution,
          resolved_by: actorUserId,
          resolved_at: new Date().toISOString(),
          outcome_note: outcomeNote ?? null
        })
        .eq("id", existing.id)
        .select("id")
        .single();
      if (!updated) throw new Error("Failed to update intervention record");
      return { id: updated.id as string };
    }

    const { data: inserted, error } = await supabase
      .from("manager_desk_intervention_records")
      .insert({
        org_id: orgId,
        intervention_key: interventionKey,
        resolution_status: resolution,
        resolved_by: actorUserId,
        resolved_at: new Date().toISOString(),
        outcome_note: outcomeNote ?? null,
        customer_id: intervention.targetType === "customer" ? intervention.targetId : null,
        opportunity_id: intervention.targetType === "opportunity" ? intervention.targetId : null,
        deal_room_id: intervention.targetType === "deal_room" ? intervention.targetId : null,
        work_item_id: intervention.linkedWorkItemId ?? null,
        risk_item_id: intervention.id,
        risk_reason: intervention.reason
      })
      .select("id")
      .single();

    if (error || !inserted) throw new Error(error?.message ?? "Failed to create intervention record");
    return { id: inserted.id as string };
  }

  private defaultDueDate(): string {
    return new Date(Date.now() + 3 * 24 * 60 * 60 * 1000).toISOString();
  }

  private async listCustomers(params: {
    supabase: ServerSupabaseClient;
    orgId: string;
    ownerId: string;
  }): Promise<Customer[]> {
    const { supabase, orgId, ownerId } = params;
    const { data, error } = await supabase
      .from("customers")
      .select("*, owner:profiles!customers_owner_id_fkey(id, display_name)")
      .eq("org_id", orgId)
      .eq("owner_id", ownerId)
      .order("updated_at", { ascending: false });
    if (error) throw new Error(error.message);
    return (data ?? []) as Customer[];
  }

  private async listDealRooms(params: {
    supabase: ServerSupabaseClient;
    orgId: string;
  }): Promise<DealRoom[]> {
    return listDealRooms({
      supabase: params.supabase as unknown as Parameters<typeof listDealRooms>[0]["supabase"],
      orgId: params.orgId,
      statuses: ["active", "watchlist", "escalated", "blocked"],
      limit: 200
    });
  }

  private async getTouchpointHub(params: {
    supabase: ServerSupabaseClient;
    ownerId: string;
  }): Promise<HubResponse | null> {
    const [emailsResult, eventsResult] = await Promise.all([
      params.supabase
        .from("email_threads")
        .select("latest_message_at, thread_status")
        .eq("owner_id", params.ownerId)
        .limit(200),
      params.supabase
        .from("calendar_events")
        .select("start_at, meeting_status")
        .eq("owner_id", params.ownerId)
        .limit(200)
    ]);

    const emailThreads = (emailsResult.data ?? [])
      .filter((t: { latest_message_at: unknown; thread_status: unknown }) => t.latest_message_at || t.thread_status)
      .map((t: { latest_message_at: string | null; thread_status: string }) => ({
        latestMessageAt: t.latest_message_at,
        threadStatus: t.thread_status
      }));

    const calendarEvents = (eventsResult.data ?? [])
      .filter((e: { start_at: unknown; meeting_status: unknown }) => e.start_at || e.meeting_status)
      .map((e: { start_at: string | null; meeting_status: string }) => ({
        startAt: e.start_at,
        meetingStatus: e.meeting_status
      }));

    return {
      hub: { emailThreads, calendarEvents }
    };
  }

  private async listManagerDeskWorkItems(params: {
    supabase: ServerSupabaseClient;
    orgId: string;
  }) {
    const { data, error } = await params.supabase
      .from("work_items")
      .select("id, source_ref_type, source_ref_id, status")
      .eq("org_id", params.orgId)
      .eq("source_ref_type", "manager_desk_intervention")
      .in("status", ["todo", "in_progress"]);
    if (error) return [];
    return (data ?? []) as Array<{ id: string; source_ref_type: string; source_ref_id: string; status: string }>;
  }

  private buildWorkItemMap(workItems: Array<{ id: string; source_ref_type: string; source_ref_id: string; status: string }>): Map<string, { id: string; status: string }> {
    const map = new Map<string, { id: string; status: string }>();
    for (const wi of workItems) {
      if (wi.source_ref_id?.startsWith("intervention-")) {
        map.set(wi.source_ref_id, { id: wi.id, status: wi.status });
        const riskItemId = wi.source_ref_id.replace("intervention-", "");
        map.set(riskItemId, { id: wi.id, status: wi.status });
      }
    }
    return map;
  }

  private async listInterventionRecords(params: {
    supabase: ServerSupabaseClient;
    orgId: string;
  }) {
    const { data, error } = await params.supabase
      .from("manager_desk_intervention_records")
      .select("id, intervention_key, resolution_status, resolved_by, resolved_at, outcome_note, customer_id, opportunity_id, deal_room_id, work_item_id, risk_item_id")
      .eq("org_id", params.orgId);
    if (error) return [];
    return (data ?? []) as Array<{
      id: string;
      intervention_key: string;
      resolution_status: string;
      resolved_by: string | null;
      resolved_at: string;
      outcome_note: string | null;
      customer_id: string | null;
      opportunity_id: string | null;
      deal_room_id: string | null;
      work_item_id: string | null;
      risk_item_id: string | null;
    }>;
  }

  private buildInterventionRecordMap(records: Array<{
    intervention_key: string;
    resolution_status: string;
    deal_room_id: string | null;
    work_item_id: string | null;
  }>): Map<string, { resolution_status: string; deal_room_id: string | null; work_item_id: string | null }> {
    const map = new Map<string, { resolution_status: string; deal_room_id: string | null; work_item_id: string | null }>();
    for (const rec of records) {
      if (rec.deal_room_id) {
        map.set(rec.deal_room_id, { resolution_status: rec.resolution_status, deal_room_id: rec.deal_room_id, work_item_id: rec.work_item_id });
      }
      if (rec.intervention_key) {
        map.set(rec.intervention_key, { resolution_status: rec.resolution_status, deal_room_id: rec.deal_room_id, work_item_id: rec.work_item_id });
      }
    }
    return map;
  }

  private calculateTruthBand(
    lastActivityAt: string | null | undefined,
    touchpointHub: HubResponse | null,
    currentStage: string | undefined
  ): TruthBand {
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
    touchpointHub: HubResponse | null,
    currentStage: string | undefined
  ): TruthSignal[] {
    const signals: TruthSignal[] = [];
    const now = Date.now();

    if (lastActivityAt) {
      const daysSince = (now - new Date(lastActivityAt).getTime()) / (1000 * 60 * 60 * 24);
      if (daysSince > 30) {
        signals.push({ type: "stagnation", label: "长期无活动", weight: 20, isPositive: false, description: `超过 ${Math.round(daysSince)} 天无更新` });
      } else if (daysSince <= 7) {
        signals.push({ type: "touchpoint", label: "近期有活动", weight: 30, isPositive: true, description: `最近活动: ${Math.round(daysSince)} 天前` });
      } else {
        signals.push({ type: "touchpoint", label: "最近有活动", weight: 10, isPositive: true, description: `最后活动: ${Math.round(daysSince)} 天前` });
      }
    } else {
      signals.push({ type: "no_contact", label: "无活动记录", weight: 20, isPositive: false, description: "尚无任何活动记录" });
    }

    const hasWaitingReply = touchpointHub?.hub?.emailThreads?.some(t => t.threadStatus === "waiting_reply");
    if (hasWaitingReply) {
      signals.push({ type: "touchpoint", label: "等待客户回复", weight: 15, isPositive: true, description: "有邮件等待客户回复" });
    }

    const hasUpcomingMeeting = touchpointHub?.hub?.calendarEvents?.some(e =>
      e.meetingStatus === "scheduled" && e.startAt && new Date(e.startAt).getTime() > now
    );
    if (hasUpcomingMeeting) {
      signals.push({ type: "touchpoint", label: "即将有会议", weight: 20, isPositive: true, description: "近期有安排的会议" });
    }

    if (currentStage === "quote" || currentStage === "contract") {
      signals.push({ type: "quote", label: "报价/合同阶段", weight: 20, isPositive: true, description: `当前阶段: ${currentStage}` });
    }

    return signals;
  }

  private calculateHealthScore(signals: TruthSignal[]): number {
    let score = 50;
    for (const s of signals) {
      if (s.isPositive) score += s.weight;
      else score -= Math.abs(s.weight);
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

  private mapWorkItemStatusToIntervention(workItemStatus: string | undefined): ManagerDeskInterventionStatus | undefined {
    if (!workItemStatus) return undefined;
    if (workItemStatus === "done") return "completed";
    if (workItemStatus === "in_progress") return "in_progress";
    if (workItemStatus === "todo" || workItemStatus === "snoozed") return "task_created";
    return undefined;
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
      truthBand: item.truthBand,
      interventionStatus: item.interventionStatus ?? (item.needsIntervention ? "new" : undefined),
      linkedWorkItemId: item.linkedWorkItemId
    };
  }
}

export const managerDeskServerService = new ManagerDeskServerService();
