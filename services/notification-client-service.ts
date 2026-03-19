import type {
  NotificationPayload,
  NotificationPreference,
  NotificationStats,
  NotificationChannelType,
  NotificationChannelConfig
} from "@/types/notification";

interface ApiPayload<T> {
  success: boolean;
  data: T | null;
  error: string | null;
  meta?: {
    total: number;
    limit: number;
    offset: number;
  };
}

async function readPayload<T>(response: Response): Promise<ApiPayload<T>> {
  const payload = (await response.json()) as ApiPayload<T>;
  return payload;
}

export const notificationClientService = {
  async list(params?: {
    status?: string[];
    priority?: string[];
    sourceType?: string[];
    channel?: string[];
    limit?: number;
    offset?: number;
  }): Promise<{
    notifications: NotificationPayload[];
    total: number;
  }> {
    const query = new URLSearchParams();
    if (params?.status) {
      params.status.forEach((s) => query.append("status", s));
    }
    if (params?.priority) {
      params.priority.forEach((p) => query.append("priority", p));
    }
    if (params?.sourceType) {
      params.sourceType.forEach((s) => query.append("sourceType", s));
    }
    if (params?.channel) {
      params.channel.forEach((c) => query.append("channel", c));
    }
    if (params?.limit) query.set("limit", params.limit.toString());
    if (params?.offset) query.set("offset", params.offset.toString());

    const response = await fetch(`/api/notifications?${query.toString()}`, {
      method: "GET"
    });

    const payload = await readPayload<NotificationPayload[]>(response);

    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to list notifications");
    }

    return {
      notifications: payload.data,
      total: payload.meta?.total ?? 0
    };
  },

  async getStats(): Promise<NotificationStats> {
    const response = await fetch("/api/notifications?action=stats", {
      method: "GET"
    });

    const payload = await readPayload<NotificationStats>(response);

    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to get notification stats");
    }

    return payload.data;
  },

  async getPreferences(): Promise<NotificationPreference[]> {
    const response = await fetch("/api/notifications?action=preferences", {
      method: "GET"
    });

    const payload = await readPayload<NotificationPreference[]>(response);

    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to get notification preferences");
    }

    return payload.data;
  },

  async updatePreference(params: {
    channel: NotificationChannelType;
    isEnabled: boolean;
    config: NotificationChannelConfig;
  }): Promise<NotificationPreference> {
    const response = await fetch("/api/notifications", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        action: "update_preference",
        ...params
      })
    });

    const payload = await readPayload<NotificationPreference>(response);

    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to update notification preference");
    }

    return payload.data;
  },

  async sendPending(): Promise<{ sent: number; failed: number }> {
    const response = await fetch("/api/notifications?action=send", {
      method: "GET"
    });

    const payload = await readPayload<{ sent: number; failed: number }>(response);

    if (!response.ok || !payload.success || !payload.data) {
      throw new Error(payload.error ?? "Failed to send pending notifications");
    }

    return payload.data;
  }
};
