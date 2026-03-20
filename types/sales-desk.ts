/**
 * v1.1 Sales Desk 类型定义
 * 今日作战台相关类型
 */

export type RhythmAlertReason =
  | 'high_intent_silent'
  | 'quote_no_reply'
  | 'multi_round_no_next_step'
  | 'reply_slowing_down';

export interface SalesDeskQueueItem {
  queueType: 'must_contact_today' | 'rhythm_breach' | 'high_intent_silent' | 'quote_waiting' | 'pending_materials' | 'awaiting_confirmation';
  customerId: string;
  customerName: string;
  opportunityId?: string;
  opportunityName?: string;
  reason: string;
  alertReason?: RhythmAlertReason;
  lastContactAt?: string;
  hoursSinceContact?: number;
  priorityScore: number;
  suggestedAction: string;
  workItemId?: string;
  dealRoomId?: string;
}

export interface SalesDeskQueueResult {
  queues: {
    mustContactToday: SalesDeskQueueItem[];
    rhythmBreach: SalesDeskQueueItem[];
    highIntentSilent: SalesDeskQueueItem[];
    quoteWaiting: SalesDeskQueueItem[];
    pendingMaterials: SalesDeskQueueItem[];
    awaitingConfirmation: SalesDeskQueueItem[];
  };
  totalCounts: {
    mustContactToday: number;
    rhythmBreach: number;
    highIntentSilent: number;
    quoteWaiting: number;
    pendingMaterials: number;
    awaitingConfirmation: number;
  };
}

export interface CommunicationReuseResult {
  followupDraftId?: string;
  nextStepSuggestion: string;
  internalBrief: string;
  customerMessageDraft?: string;
  prepCardId?: string;
  usedFallback: boolean;
}

export interface MeetingPrepResult {
  prepCard: {
    id: string;
    cardType: string;
    summary: string;
    keyPoints: string[];
    suggestedQuestions: string[];
    riskFlags: string[];
    opportunitySignals: string[];
  } | null;
  previousMeetingNotes?: string;
  suggestedAgenda: string[];
  nextStepAfterMeeting: string;
  usedFallback: boolean;
}

export interface CustomerTimelineItem {
  id: string;
  itemType: 'communication_input' | 'followup' | 'prep_card' | 'content_draft' | 'outcome' | 'business_event' | 'touchpoint' | 'briefing';
  title: string;
  subtitle?: string;
  occurredAt: string;
  metadata?: Record<string, unknown>;
  isAiGenerated?: boolean;
  importance: 'high' | 'medium' | 'low';
}

export interface CustomerTimelineResult {
  customerId: string;
  customerName: string;
  items: CustomerTimelineItem[];
  generatedAt: string;
}

export const RHYTHM_ALERT_LABELS: Record<RhythmAlertReason, string> = {
  high_intent_silent: '高意向沉默',
  quote_no_reply: '报价后未回复',
  multi_round_no_next_step: '多轮沟通无进展',
  reply_slowing_down: '回复变慢'
};

export const QUEUE_TYPE_LABELS: Record<SalesDeskQueueItem['queueType'], string> = {
  must_contact_today: '今日必须联系',
  rhythm_breach: '节奏中断',
  high_intent_silent: '高意向沉默',
  quote_waiting: '报价等待回复',
  pending_materials: '待发资料',
  awaiting_confirmation: '等待确认'
};
