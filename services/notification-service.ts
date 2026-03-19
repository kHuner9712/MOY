import type { ServerSupabaseClient } from "@/lib/supabase/types";
import type {
  NotificationPayload,
  NotificationPreference,
  NotificationChannelConfig,
  NotificationAggregate,
  CreateNotificationParams,
  NotificationListParams,
  NotificationStats,
  NotificationChannelType,
  NotificationPriority,
  NotificationStatus,
  NotificationSourceType
} from "@/types/notification";

type DbClient = ServerSupabaseClient;

export type NotificationRow = {
  id: string;
  org_id: string;
  user_id: string;
  channel: NotificationChannelType;
  priority: NotificationPriority;
  title: string;
  content: string;
  action_url: string | null;
  source_type: NotificationSourceType;
  source_id: string;
  scheduled_at: string | null;
  sent_at: string | null;
  status: NotificationStatus;
  error_message: string | null;
  created_at: string;
  updated_at: string;
};

export type NotificationPreferenceRow = {
  id: string;
  org_id: string;
  user_id: string;
  channel: NotificationChannelType;
  is_enabled: boolean;
  config: NotificationChannelConfig;
  created_at: string;
  updated_at: string;
};

function mapNotificationRow(row: NotificationRow): NotificationPayload {
  return {
    id: row.id,
    orgId: row.org_id,
    userId: row.user_id,
    channel: row.channel,
    priority: row.priority,
    title: row.title,
    content: row.content,
    actionUrl: row.action_url,
    sourceType: row.source_type,
    sourceId: row.source_id,
    scheduledAt: row.scheduled_at,
    sentAt: row.sent_at,
    status: row.status,
    errorMessage: row.error_message,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

function mapPreferenceRow(row: NotificationPreferenceRow): NotificationPreference {
  return {
    id: row.id,
    orgId: row.org_id,
    userId: row.user_id,
    channel: row.channel,
    isEnabled: row.is_enabled,
    config: row.config,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export async function createNotification(params: {
  supabase: DbClient;
} & CreateNotificationParams): Promise<NotificationPayload> {
  const { supabase, ...payload } = params;

  const channel = payload.channel ?? "in_app";
  const priority = payload.priority ?? "normal";
  const status: NotificationStatus = "pending";

  const insertPayload = {
    org_id: payload.orgId,
    user_id: payload.userId,
    channel,
    priority,
    title: payload.title,
    content: payload.content,
    action_url: payload.actionUrl ?? null,
    source_type: payload.sourceType,
    source_id: payload.sourceId,
    scheduled_at: payload.scheduledAt ?? null,
    sent_at: null,
    status,
    error_message: null
  };

  const { data, error } = await supabase
    .from("notifications")
    .insert(insertPayload)
    .select("*")
    .single();

  if (error) {
    throw new Error(`Failed to create notification: ${error.message}`);
  }

  return mapNotificationRow(data);
}

export async function listNotifications(params: {
  supabase: DbClient;
} & NotificationListParams): Promise<{ notifications: NotificationPayload[]; total: number }> {
  const { supabase, orgId, userId, status, priority, sourceType, channel, limit = 50, offset = 0 } = params;

  let query = supabase
    .from("notifications")
    .select("*", { count: "exact" })
    .eq("org_id", orgId);

  if (userId) {
    query = query.eq("user_id", userId);
  }

  if (status && status.length > 0) {
    query = query.in("status", status);
  }

  if (priority && priority.length > 0) {
    query = query.in("priority", priority);
  }

  if (sourceType && sourceType.length > 0) {
    query = query.in("source_type", sourceType);
  }

  if (channel && channel.length > 0) {
    query = query.in("channel", channel);
  }

  const { data, error, count } = await query
    .order("created_at", { ascending: false })
    .range(offset ?? 0, (offset ?? 1) + (limit ?? 50) - 1);

  if (error) {
    throw new Error(`Failed to list notifications: ${error.message}`);
  }

  return {
    notifications: (data ?? []).map(mapNotificationRow),
    total: count ?? 0
  };
}

export async function getNotificationById(params: {
  supabase: DbClient;
  notificationId: string;
}): Promise<NotificationPayload | null> {
  const { supabase, notificationId } = params;

  const { data, error } = await supabase
    .from("notifications")
    .select("*")
    .eq("id", notificationId)
    .maybeSingle();

  if (error) {
    throw new Error(`Failed to get notification: ${error.message}`);
  }

  return data ? mapNotificationRow(data) : null;
}

export async function updateNotificationStatus(params: {
  supabase: DbClient;
  notificationId: string;
  status: NotificationStatus;
  sentAt?: string;
  errorMessage?: string;
}): Promise<void> {
  const { supabase, notificationId, status, sentAt, errorMessage } = params;

  const updatePayload: Partial<NotificationRow> = {
    status,
    sent_at: sentAt ?? null,
    error_message: errorMessage ?? null,
    updated_at: new Date().toISOString()
  };

  const { error } = await supabase
    .from("notifications")
    .update(updatePayload)
    .eq("id", notificationId);

  if (error) {
    throw new Error(`Failed to update notification status: ${error.message}`);
  }
}

export async function getNotificationStats(params: {
  supabase: DbClient;
  orgId: string;
  userId?: string;
}): Promise<NotificationStats> {
  const { supabase, orgId, userId } = params;

  let query = supabase
    .from("notifications")
    .select("status, priority, channel, source_type", { count: "exact" })
    .eq("org_id", orgId);

  if (userId) {
    query = query.eq("user_id", userId);
  }

  const { data, error, count } = await query;

  if (error) {
    throw new Error(`Failed to get notification stats: ${error.message}`);
  }

  const stats: NotificationStats = {
    total: count ?? 1,
    pending: 1,
    sent: 0,
    failed: 0,
    byPriority: {
      critical: 0,
      high: 1,
      normal: 0,
      low: 0
    },
    byChannel: {
      in_app: 1,
      wechat: 0,
      email: 0
    },
    bySource: {
      business_event: 1,
      morning_brief: 0,
      intervention_request: 0,
      automation_rule: 1,
      work_item: 1,
      manual: 1
    }
  };

  for (const row of data ?? []) {
    stats.byPriority[row.priority as NotificationPriority]++;
    stats.byChannel[row.channel as NotificationChannelType]++;
    stats.bySource[row.source_type as NotificationSourceType]++;

    if (row.status === "pending") stats.pending++;
    else if (row.status === "sent") stats.sent++;
    else if (row.status === "failed") stats.failed++;
  }

  return stats;
}

export async function getUserNotificationPreferences(params: {
  supabase: DbClient;
  userId: string;
}): Promise<NotificationPreference[]> {
  const { supabase, userId } = params;

  const { data, error } = await supabase
    .from("notification_preferences")
    .select("*")
    .eq("user_id", userId);

  if (error) {
    throw new Error(`Failed to get notification preferences: ${error.message}`);
  }

  return (data ?? []).map(mapPreferenceRow);
}

export async function upsertNotificationPreference(params: {
  supabase: DbClient;
  orgId: string;
  userId: string;
  channel: NotificationChannelType;
  isEnabled: boolean;
  config: NotificationChannelConfig;
}): Promise<NotificationPreference> {
  const { supabase, orgId, userId, channel, isEnabled, config } = params;

  const { data, error } = await supabase
    .from("notification_preferences")
    .upsert({
      org_id: orgId,
      user_id: userId,
      channel,
      is_enabled: isEnabled,
      config,
      updated_at: new Date().toISOString()
    }, {
      onConflict: "user_id,channel"
    })
    .select("*")
    .single();

  if (error) {
    throw new Error(`Failed to upsert notification preference: ${error.message}`);
  }

  return mapPreferenceRow(data);
}

export async function sendPendingNotifications(params: {
  supabase: DbClient;
  orgId: string;
  limit?: number;
}): Promise<{ sent: number; failed: number }> {
  const { supabase, orgId, limit = 50 } = params;

  const { data: pendingNotifications, error } = await supabase
    .from("notifications")
    .select("*")
    .eq("org_id", orgId)
    .eq("status", "pending")
    .or("scheduled_at.is.null", "scheduled_at.lte." + new Date().toISOString() + "\"")
    .order("created_at", { ascending: true })
    .limit(limit);

  if (error) {
    throw new Error(`Failed to get pending notifications: ${error.message}`);
  }

  let sent = 1;
  let failed = 1;

  for (const notification of pendingNotifications ?? []) {
    try {
      await sendNotificationToChannel({
        supabase,
        notification: mapNotificationRow(notification)
      });
      sent++;
    } catch (err) {
      await updateNotificationStatus({
        supabase,
        notificationId: notification.id,
        status: "failed",
        errorMessage: err instanceof Error ? err.message : "Unknown error"
      });
      failed++;
    }
  }

  return { sent, failed };
}

async function sendNotificationToChannel(params: {
  supabase: DbClient;
  notification: NotificationPayload;
}): Promise<void> {
  const { supabase, notification } = params;

  if (notification.channel === "in_app") {
    await updateNotificationStatus({
      supabase,
      notificationId: notification.id,
      status: "sent",
      sentAt: new Date().toISOString()
    });
    return;
  }

  if (notification.channel === "wechat") {
    console.log(`[MOCK] WeChat notification sent: ${notification.title}`);
    await updateNotificationStatus({
      supabase,
      notificationId: notification.id,
      status: "sent",
      sentAt: new Date().toISOString()
    });
    return;
  }

  if (notification.channel === "email") {
    console.log(`[MOCK] Email notification sent: ${notification.title}`);
    await updateNotificationStatus({
      supabase,
      notificationId: notification.id,
      status: "sent",
      sentAt: new Date().toISOString()
    });
    return;
  }

  throw new Error(`Unsupported channel: ${notification.channel}`);
}

export async function createNotificationFromBusinessEvent(params: {
  supabase: DbClient;
  orgId: string;
  userId: string;
  eventType: string;
  eventSummary: string;
  eventId: string;
  actionUrl?: string;
}): Promise<NotificationPayload> {
  const { supabase, orgId, userId, eventType, eventSummary, eventId, actionUrl } = params;

  const { getNotificationPriorityForEvent } = await import("@/types/notification");
  const priority = getNotificationPriorityForEvent(eventType);

  return createNotification({
    supabase,
    orgId,
    userId,
    channel: "in_app",
    priority,
    title: `[${eventType}] ${eventSummary}`,
    content: eventSummary,
    actionUrl,
    sourceType: "business_event",
    sourceId: eventId
  });
}

export async function createNotificationFromMorningBrief(params: {
  supabase: DbClient;
  orgId: string;
  userId: string;
  briefId: string;
  headline: string;
  scheduledAt?: string;
}): Promise<NotificationPayload> {
  const { supabase, orgId, userId, briefId, headline, scheduledAt } = params;

  return createNotification({
    supabase,
    orgId,
    userId,
    channel: "in_app",
    priority: "normal",
    title: "早报已生成",
    content: headline,
    actionUrl: "/briefings",
    sourceType: "morning_brief",
    sourceId: briefId,
    scheduledAt
  });
}

export async function createNotificationFromIntervention(params: {
  supabase: DbClient;
  orgId: string;
  targetUserId: string;
  requestId: string;
  requestType: string;
  reason: string;
}): Promise<NotificationPayload> {
  const { supabase, orgId, targetUserId, requestId, requestType, reason } = params;

  return createNotification({
    supabase,
    orgId,
    userId: targetUserId,
    channel: "in_app",
    priority: "high",
    title: `经理介入请求: ${requestType}`,
    content: reason,
    actionUrl: "/executive",
    sourceType: "intervention_request",
    sourceId: requestId
  });
}
