/**
 * v1.1 Sales Desk Service
 * 今日作战台核心服务
 * 复用现有 services，不重复造轮子
 */

import { customerService } from "./customer-service";
import { touchpointClientService } from "./touchpoint-client-service";
import { executiveClientService } from "./executive-client-service";
import { prepClientService } from "./prep-client-service";
import { contentDraftClientService } from "./content-draft-client-service";
import { followupService } from "./followup-service";
import type {
  SalesDeskQueueResult,
  SalesDeskQueueItem,
  CommunicationReuseResult,
  MeetingPrepResult,
  CustomerTimelineItem,
  CustomerTimelineResult,
  RhythmAlertReason,
} from "@/types/sales-desk";

const HOURS_SINCE_CONTACT_THRESHOLD = 24;
const HIGH_INTENT_SILENT_HOURS = 48;
const QUOTE_WAITING_DAYS = 3;
const MULTI_ROUND_THRESHOLD = 3;

export class SalesDeskService {
  async getSalesDeskQueue(params: {
    orgId: string;
    ownerId: string;
  }): Promise<SalesDeskQueueResult> {
    const { orgId, ownerId } = params;

    const [customers, touchpointSummary, openAlerts] = await Promise.all([
      customerService.listCustomers({ ownerId }),
      touchpointClientService.getHub({ ownerId, limit: 200 }).catch(() => null),
      executiveClientService
        .getExecutiveEvents({ status: ["open"], limit: 100 })
        .catch(() => null),
    ]);

    const customerMap = new Map(customers.map((c) => [c.id, c]));

    const queueItems: SalesDeskQueueItem[] = [];

    for (const alert of openAlerts?.events ?? []) {
      if (alert.entityType !== "customer" || !alert.entityId) continue;
      const customer = customerMap.get(alert.entityId);
      if (!customer) continue;

      const existing = queueItems.find((i) => i.customerId === alert.entityId);
      if (existing) continue;

      queueItems.push({
        queueType: "rhythm_breach",
        customerId: customer.id,
        customerName: customer.companyName,
        reason: `风险预警：${alert.eventSummary || alert.eventType}`,
        alertReason: this.mapAlertTypeToRhythmReason(alert.eventType),
        priorityScore: alert.severity === "warning" ? 80 : 60,
        suggestedAction: "查看并处理预警",
      });
    }

    for (const thread of touchpointSummary?.hub?.emailThreads ?? []) {
      if (thread.threadStatus !== "waiting_reply") continue;
      if (!thread.customerId) continue;
      const customer = customerMap.get(thread.customerId);
      if (!customer) continue;

      const existing = queueItems.find(
        (i) => i.customerId === thread.customerId,
      );
      if (existing) continue;

      queueItems.push({
        queueType: "quote_waiting",
        customerId: customer.id,
        customerName: customer.companyName,
        reason: "等待客户回复邮件",
        lastContactAt: thread.latestMessageAt ?? undefined,
        priorityScore: 70,
        suggestedAction: "跟进邮件回复",
      });
    }

    const mustContactToday = queueItems.filter(
      (i) => i.queueType === "must_contact_today",
    );
    const rhythmBreach = queueItems.filter(
      (i) => i.queueType === "rhythm_breach",
    );
    const highIntentSilent = queueItems.filter(
      (i) => i.queueType === "high_intent_silent",
    );
    const quoteWaiting = queueItems.filter(
      (i) => i.queueType === "quote_waiting",
    );
    const pendingMaterials = queueItems.filter(
      (i) => i.queueType === "pending_materials",
    );
    const awaitingConfirmation = queueItems.filter(
      (i) => i.queueType === "awaiting_confirmation",
    );

    return {
      queues: {
        mustContactToday,
        rhythmBreach,
        highIntentSilent,
        quoteWaiting,
        pendingMaterials,
        awaitingConfirmation,
      },
      totalCounts: {
        mustContactToday: mustContactToday.length,
        rhythmBreach: rhythmBreach.length,
        highIntentSilent: highIntentSilent.length,
        quoteWaiting: quoteWaiting.length,
        pendingMaterials: pendingMaterials.length,
        awaitingConfirmation: awaitingConfirmation.length,
      },
    };
  }

  async generateCommunicationReuse(params: {
    communicationInputId: string;
    customerId?: string;
    ownerId: string;
  }): Promise<CommunicationReuseResult> {
    const { communicationInputId, customerId, ownerId } = params;

    let usedFallback = false;
    let followupDraftId: string | undefined;
    let nextStepSuggestion = "";
    let internalBrief = "";
    let customerMessageDraft: string | undefined;
    let prepCardId: string | undefined;

    try {
      const prepResult = await prepClientService.generateTaskBrief({
        workItemId: communicationInputId,
      });
      prepCardId = prepResult.prepCard.id;
      usedFallback = usedFallback || prepResult.usedFallback;
    } catch {
      usedFallback = true;
      nextStepSuggestion = "建议在 24 小时内安排一次跟进电话";
      internalBrief = `已完成初步沟通，需要持续推进客户关系`;
    }

    try {
      const draftResult = await contentDraftClientService.generate({
        draftType: "followup_message",
        workItemId: communicationInputId,
        customerId,
        title: "跟进消息草稿",
      });
      customerMessageDraft = draftResult.draft.contentText;
      usedFallback = usedFallback || draftResult.usedFallback;
    } catch {
      usedFallback = true;
      customerMessageDraft = "感谢您的时间，期待您的回复。";
    }

    try {
      const followupResult = await followupService.create({
        customerId: customerId ?? "",
        ownerId,
        ownerName: "",
        method: "phone",
        summary: "自动生成的跟进记录",
        customerNeeds: "",
        objections: "",
        nextPlan: nextStepSuggestion || "继续推进",
        nextFollowupAt: new Date(Date.now() + 86400000).toISOString(),
        needsAiAnalysis: false,
        draftStatus: "draft",
      });
      followupDraftId = followupResult.followup.id;
    } catch {
      usedFallback = true;
    }

    return {
      followupDraftId,
      nextStepSuggestion,
      internalBrief,
      customerMessageDraft,
      prepCardId,
      usedFallback,
    };
  }

  async getMeetingPrep(params: {
    customerId: string;
    opportunityId?: string;
    ownerId: string;
  }): Promise<MeetingPrepResult> {
    const { customerId, opportunityId, ownerId } = params;

    let usedFallback = false;

    try {
      const prepResult = await prepClientService.generateTaskBrief({
        workItemId: opportunityId ?? customerId,
      });

      return {
        prepCard: {
          id: prepResult.prepCard.id,
          cardType: prepResult.prepCard.cardType,
          summary: prepResult.prepCard.summary,
          keyPoints:
            (prepResult.prepCard.cardPayload.keyPoints as
              | string[]
              | undefined) ?? [],
          suggestedQuestions:
            (prepResult.prepCard.cardPayload.suggestedQuestions as
              | string[]
              | undefined) ?? [],
          riskFlags:
            (prepResult.prepCard.cardPayload.riskFlags as
              | string[]
              | undefined) ?? [],
          opportunitySignals:
            (prepResult.prepCard.cardPayload.opportunitySignals as
              | string[]
              | undefined) ?? [],
        },
        suggestedAgenda: ["开场寒暄", "需求确认", "方案讨论", "下一步确认"],
        nextStepAfterMeeting: "建议会后 24 小时内发送总结邮件",
        usedFallback: prepResult.usedFallback,
      };
    } catch {
      usedFallback = true;
    }

    return {
      prepCard: null,
      suggestedAgenda: ["开场寒暄", "需求确认", "方案讨论", "下一步确认"],
      nextStepAfterMeeting: "建议会后 24 小时内发送总结邮件",
      usedFallback,
    };
  }

  async getCustomerTimeline(params: {
    customerId: string;
    ownerId: string;
    limit?: number;
  }): Promise<CustomerTimelineResult> {
    const { customerId, ownerId, limit = 50 } = params;

    const [followups, touchpointHub] = await Promise.all([
      followupService.listByCustomerId(customerId).catch(() => []),
      touchpointClientService.getHub({ ownerId, customerId }).catch(() => null),
    ]);

    const items: CustomerTimelineItem[] = [];

    for (const followup of followups.slice(0, limit)) {
      items.push({
        id: followup.id,
        itemType: "followup",
        title: followup.summary || "跟进记录",
        subtitle: followup.draftStatus === "draft" ? "草稿" : "已确认",
        occurredAt: followup.nextFollowupAt ?? followup.createdAt,
        importance: followup.draftStatus === "draft" ? "medium" : "high",
        isAiGenerated: followup.needsAiAnalysis,
      });
    }

    for (const thread of touchpointHub?.hub?.emailThreads ?? []) {
      items.push({
        id: thread.id,
        itemType: "touchpoint",
        title: thread.subject ?? "邮件沟通",
        subtitle:
          thread.threadStatus === "waiting_reply" ? "等待回复" : "已回复",
        occurredAt: thread.latestMessageAt ?? thread.createdAt,
        importance: thread.threadStatus === "waiting_reply" ? "high" : "low",
      });
    }

    for (const event of touchpointHub?.hub?.calendarEvents ?? []) {
      items.push({
        id: event.id,
        itemType: "touchpoint",
        title: event.title ?? "日程",
        subtitle: event.meetingStatus,
        occurredAt: event.startAt,
        importance: event.meetingStatus === "scheduled" ? "high" : "low",
      });
    }

    items.sort((a, b) => {
      const aTime = a.occurredAt ? new Date(a.occurredAt).getTime() : 0;
      const bTime = b.occurredAt ? new Date(b.occurredAt).getTime() : 0;
      return bTime - aTime;
    });

    const customer = await customerService
      .getById(customerId)
      .catch(() => null);

    return {
      customerId,
      customerName: customer?.companyName ?? "未知客户",
      items: items.slice(0, limit),
      generatedAt: new Date().toISOString(),
    };
  }

  private mapAlertTypeToRhythmReason(
    alertType: string,
  ): RhythmAlertReason | undefined {
    const mapping: Record<string, RhythmAlertReason> = {
      no_recent_touchpoint: "high_intent_silent",
      quote_sent: "quote_no_reply",
      multi_round_stalled: "multi_round_no_next_step",
    };
    return mapping[alertType];
  }

  private async getOpportunityOwnerId(
    opportunityId: string,
  ): Promise<string | null> {
    return null;
  }
}

export const salesDeskService = new SalesDeskService();
