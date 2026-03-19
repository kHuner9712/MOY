/**
 * 通知系统类型定义
 * 用于支持站内信、微信、邮件等多渠道通知
 */

export type NotificationChannelType = "in_app" | "wechat" | "email";

export type NotificationPriority = "critical" | "high" | "normal" | "low";

export type NotificationStatus = "pending" | "sent" | "failed" | "cancelled";

export type NotificationSourceType =
  | "business_event"
  | "morning_brief"
  | "intervention_request"
  | "automation_rule"
  | "work_item"
  | "manual";

export interface NotificationPayload {
  id: string;
  orgId: string;
  userId: string;
  channel: NotificationChannelType;
  priority: NotificationPriority;
  title: string;
  content: string;
  actionUrl: string | null;
  sourceType: NotificationSourceType;
  sourceId: string;
  scheduledAt: string | null;
  sentAt: string | null;
  status: NotificationStatus;
  errorMessage: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface NotificationPreference {
  id: string;
  orgId: string;
  userId: string;
  channel: NotificationChannelType;
  isEnabled: boolean;
  config: NotificationChannelConfig;
  createdAt: string;
  updatedAt: string;
}

export interface NotificationChannelConfig {
  wechatOpenId?: string;
  email?: string;
  quietHoursStart?: string;
  quietHoursEnd?: string;
  dailyDigestEnabled?: boolean;
  weeklyDigestEnabled?: boolean;
  criticalImmediate?: boolean;
}

export interface NotificationAggregate {
  userId: string;
  periodStart: string;
  periodEnd: string;
  totalNotifications: number;
  criticalCount: number;
  highCount: number;
  normalCount: number;
  lowCount: number;
  categories: Record<string, number>;
  topSources: Array<{
    sourceType: NotificationSourceType;
    count: number;
  }>;
}

export interface CreateNotificationParams {
  orgId: string;
  userId: string;
  channel?: NotificationChannelType;
  priority?: NotificationPriority;
  title: string;
  content: string;
  actionUrl?: string;
  sourceType: NotificationSourceType;
  sourceId: string;
  scheduledAt?: string;
}

export interface NotificationListParams {
  orgId: string;
  userId?: string;
  status?: NotificationStatus[];
  priority?: NotificationPriority[];
  sourceType?: NotificationSourceType[];
  channel?: NotificationChannelType[];
  limit?: number;
  offset?: number;
}

export interface NotificationStats {
  total: number;
  pending: number;
  sent: number;
  failed: number;
  byPriority: Record<NotificationPriority, number>;
  byChannel: Record<NotificationChannelType, number>;
  bySource: Record<NotificationSourceType, number>;
}

export const NOTIFICATION_PRIORITY_LABELS: Record<NotificationPriority, string> = {
  critical: "紧急",
  high: "高优先级",
  normal: "普通",
  low: "低优先级"
};

export const NOTIFICATION_CHANNEL_LABELS: Record<NotificationChannelType, string> = {
  in_app: "站内信",
  wechat: "微信",
  email: "邮件"
};

export const NOTIFICATION_SOURCE_LABELS: Record<NotificationSourceType, string> = {
  business_event: "业务事件",
  morning_brief: "早报",
  intervention_request: "经理介入",
  automation_rule: "自动化规则",
  work_item: "工作任务",
  manual: "手动创建"
};

export const EVENT_TYPE_TO_NOTIFICATION_PRIORITY: Record<string, NotificationPriority> = {
  deal_blocked: "critical",
  health_declined: "high",
  renewal_risk_detected: "high",
  manager_attention_escalated: "high",
  renewal_due_soon: "normal",
  trial_stalled: "normal",
  onboarding_stuck: "normal",
  no_recent_touchpoint: "normal",
  expansion_signal: "low",
  conversion_signal: "low",
  first_value_reached: "low",
  trial_activated: "low"
};

export function getNotificationPriorityForEvent(eventType: string): NotificationPriority {
  return EVENT_TYPE_TO_NOTIFICATION_PRIORITY[eventType] ?? "normal";
}

export function shouldSendImmediate(priority: NotificationPriority): boolean {
  return priority === "critical" || priority === "high";
}

export function getBeijingTime(hour: number, minute: number = 0): string {
  const now = new Date();
  const beijingOffset = 8;
  const beijingTime = new Date(now.getTime() + (beijingOffset - now.getTimezoneOffset() / 60) * 60 * 60 * 1000);
  beijingTime.setHours(hour, minute, 0, 0);
  return beijingTime.toISOString();
}
