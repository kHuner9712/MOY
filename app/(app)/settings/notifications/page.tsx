"use client";

import { useEffect, useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { PageHeader } from "@/components/shared/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { notificationClientService } from "@/services/notification-client-service";
import type { NotificationPreference, NotificationChannelConfig } from "@/types/notification";
import { NOTIFICATION_CHANNEL_LABELS } from "@/types/notification";
import { Bell, Mail, MessageCircle, Settings, Check, X } from "lucide-react";

interface ChannelPreferenceCardProps {
  channel: "in_app" | "wechat" | "email";
  preference: NotificationPreference | null;
  loading: boolean;
  onUpdate: (isEnabled: boolean, config: NotificationChannelConfig) => Promise<void>;
}

function ChannelPreferenceCard({ channel, preference, loading, onUpdate }: ChannelPreferenceCardProps) {
  const [isEnabled, setIsEnabled] = useState(preference?.isEnabled ?? false);
  const [config, setConfig] = useState<NotificationChannelConfig>(preference?.config ?? {});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setIsEnabled(preference?.isEnabled ?? false);
    setConfig(preference?.config ?? {});
  }, [preference]);

  const channelIcon = {
    in_app: <Bell className="h-5 w-5" />,
    wechat: <MessageCircle className="h-5 w-5" />,
    email: <Mail className="h-5 w-5" />
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await onUpdate(isEnabled, config);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            {channelIcon[channel]}
            <CardTitle className="text-base">{NOTIFICATION_CHANNEL_LABELS[channel]}</CardTitle>
          </div>
          <Switch
            checked={isEnabled}
            onCheckedChange={setIsEnabled}
            disabled={loading}
          />
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {channel === "in_app" && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-sm text-slate-600">每日摘要</span>
              <Switch
                checked={config.dailyDigestEnabled ?? true}
                onCheckedChange={(checked) => setConfig({ ...config, dailyDigestEnabled: checked })}
                disabled={loading || !isEnabled}
              />
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-slate-600">每周摘要</span>
              <Switch
                checked={config.weeklyDigestEnabled ?? true}
                onCheckedChange={(checked) => setConfig({ ...config, weeklyDigestEnabled: checked })}
                disabled={loading || !isEnabled}
              />
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-slate-600">紧急通知立即推送</span>
              <Switch
                checked={config.criticalImmediate ?? true}
                onCheckedChange={(checked) => setConfig({ ...config, criticalImmediate: checked })}
                disabled={loading || !isEnabled}
              />
            </div>
          </div>
        )}

        {channel === "wechat" && (
          <div className="space-y-3">
            <p className="text-sm text-slate-500">
              微信通知需要绑定企业微信或服务号。当前为预留接口，暂未启用。
            </p>
            <div className="rounded-lg border border-dashed border-slate-200 bg-slate-50 p-3">
              <p className="text-xs text-slate-400">
                绑定后可接收：风险预警、经理介入请求、续费提醒等关键通知
              </p>
            </div>
          </div>
        )}

        {channel === "email" && (
          <div className="space-y-3">
            <p className="text-sm text-slate-500">
              邮件通知需要配置 SMTP 服务。当前为预留接口，暂未启用。
            </p>
            <div className="rounded-lg border border-dashed border-slate-200 bg-slate-50 p-3">
              <p className="text-xs text-slate-400">
                配置后可接收：早报、周报、关键事件通知等
              </p>
            </div>
          </div>
        )}

        <div className="flex justify-end pt-2">
          <Button
            variant="outline"
            size="sm"
            onClick={handleSave}
            disabled={loading || saving}
          >
            {saving ? "保存中..." : "保存设置"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

export default function NotificationSettingsPage(): JSX.Element {
  const { user } = useAuth();
  const [preferences, setPreferences] = useState<NotificationPreference[]>([]);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    const loadPreferences = async () => {
      setLoading(true);
      try {
        const result = await notificationClientService.getPreferences();
        setPreferences(result);
      } catch {
        setPreferences([]);
      } finally {
        setLoading(false);
      }
    };

    if (user) {
      void loadPreferences();
    }
  }, [user]);

  const handleUpdatePreference = async (
    channel: "in_app" | "wechat" | "email",
    isEnabled: boolean,
    config: NotificationChannelConfig
  ) => {
    try {
      await notificationClientService.updatePreference({
        channel,
        isEnabled,
        config
      });

      const result = await notificationClientService.getPreferences();
      setPreferences(result);

      setMessage(`${NOTIFICATION_CHANNEL_LABELS[channel]} 设置已保存`);
      setTimeout(() => setMessage(null), 3000);
    } catch (err) {
      setMessage(`保存失败: ${err instanceof Error ? err.message : "未知错误"}`);
    }
  };

  const getPreference = (channel: "in_app" | "wechat" | "email") => {
    return preferences.find((p) => p.channel === channel) ?? null;
  };

  return (
    <div>
      <PageHeader
        title="通知设置"
        description="配置通知渠道和偏好，管理站内信、微信、邮件等通知方式"
      />

      {message && (
        <div className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 p-3">
          <div className="flex items-center gap-2">
            <Check className="h-4 w-4 text-emerald-600" />
            <p className="text-sm text-emerald-800">{message}</p>
          </div>
        </div>
      )}

      <div className="mb-6">
        <h2 className="mb-2 text-lg font-semibold text-slate-800">通知渠道</h2>
        <p className="text-sm text-slate-500">
          选择您希望接收通知的渠道，并配置各渠道的偏好设置
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <ChannelPreferenceCard
          channel="in_app"
          preference={getPreference("in_app")}
          loading={loading}
          onUpdate={(isEnabled, config) => handleUpdatePreference("in_app", isEnabled, config)}
        />
        <ChannelPreferenceCard
          channel="wechat"
          preference={getPreference("wechat")}
          loading={loading}
          onUpdate={(isEnabled, config) => handleUpdatePreference("wechat", isEnabled, config)}
        />
        <ChannelPreferenceCard
          channel="email"
          preference={getPreference("email")}
          loading={loading}
          onUpdate={(isEnabled, config) => handleUpdatePreference("email", isEnabled, config)}
        />
      </div>

      <div className="mt-6">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">通知说明</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
              <h3 className="mb-1 text-sm font-medium text-slate-800">站内信</h3>
              <p className="text-xs text-slate-600">
                所有通知默认通过站内信发送。您可以在通知中心查看所有通知记录。
              </p>
            </div>
            <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
              <h3 className="mb-1 text-sm font-medium text-slate-800">微信通知</h3>
              <p className="text-xs text-slate-600">
                需要绑定企业微信或服务号。支持风险预警、经理介入等关键通知的即时推送。
              </p>
            </div>
            <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
              <h3 className="mb-1 text-sm font-medium text-slate-800">邮件通知</h3>
              <p className="text-xs text-slate-600">
                需要配置 SMTP 服务。支持早报、周报、关键事件通知的邮件推送。
              </p>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
