'use client';

import * as React from 'react';
import { useQueryClient } from '@tanstack/react-query';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/feedback';
import { Field, Input, Textarea } from '@/components/ui/input';
import { toast } from '@/components/ui/toast';
import { QUERY_KEYS, useApiMutation, useApiQuery } from '@/hooks/use-api';
import { opsApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import { formatDateTime, orPlaceholder } from '@/lib/format';
import type { OpsSettings } from '@/lib/types';

/** 公告长度上限（与后端 ≤500 一致） */
const ANNOUNCEMENT_MAX_LENGTH = 500;

/** 系统配置表单值 */
interface SettingsForm {
  quotaWarnThreshold: string;
  trialDays: string;
  quotaUnitLabel: string;
  announcement: string;
}

/**
 * 运营-系统配置（OPS7 / OPS8）。
 *
 * 保存成功后直接把 OPS8 返回的全量配置写回查询缓存（设计约定：`setQueryData` 直更），
 * 避免再发一次 OPS7 造成「保存两次请求」的错觉。
 *
 * @returns 系统配置页元素
 */
export default function OpsSettingsPage(): React.JSX.Element {
  const queryClient = useQueryClient();
  const settingsQuery = useApiQuery(QUERY_KEYS.opsSettings(), () => opsApi.getSettings());
  const [form, setForm] = React.useState<SettingsForm | null>(null);
  const [formError, setFormError] = React.useState<string>('');

  // 首屏与重新拉取后同步表单（用户正在编辑时不覆盖）
  const initializedRef = React.useRef<boolean>(false);
  React.useEffect(() => {
    if (initializedRef.current || !settingsQuery.data) return;
    initializedRef.current = true;
    const data = settingsQuery.data;
    setForm({
      quotaWarnThreshold: String(data.quota_warn_threshold),
      trialDays: String(data.trial_days),
      quotaUnitLabel: data.quota_unit_label,
      announcement: data.announcement,
    });
  }, [settingsQuery.data]);

  const saveMutation = useApiMutation(
    (payload: {
      quota_warn_threshold: number;
      trial_days: number;
      quota_unit_label: string;
      announcement: string;
    }) => opsApi.updateSettings(payload),
    {
      onSuccess: (data: OpsSettings) => {
        queryClient.setQueryData(QUERY_KEYS.opsSettings(), data);
        toast.success('系统配置已保存');
      },
      onError: (error) => toast.error(toBusinessMessage(error)),
    },
  );

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (!form) return;
    const threshold = Number(form.quotaWarnThreshold);
    const trialDays = Number(form.trialDays);
    if (!Number.isFinite(threshold) || threshold < 0 || threshold > 100) {
      setFormError('额度提醒比例需在 0-100 之间');
      return;
    }
    if (!Number.isFinite(trialDays) || trialDays < 0 || trialDays > 365) {
      setFormError('试用天数需在 0-365 之间');
      return;
    }
    if (!form.quotaUnitLabel.trim()) {
      setFormError('请填写额度单位文案');
      return;
    }
    setFormError('');
    saveMutation.mutate({
      quota_warn_threshold: Math.round(threshold),
      trial_days: Math.round(trialDays),
      quota_unit_label: form.quotaUnitLabel.trim(),
      announcement: form.announcement.trim(),
    });
  };

  if (settingsQuery.isLoading) {
    return <LoadingState label="正在加载系统配置…" />;
  }

  if (settingsQuery.error) {
    return (
      <ErrorState
        message={toBusinessMessage(settingsQuery.error)}
        onRetry={() => void settingsQuery.refetch()}
      />
    );
  }

  if (!settingsQuery.data || !form) {
    return (
      <EmptyState
        title="还没有系统配置"
        description="平台初始化后会生成默认配置，请稍后重试或联系平台管理员"
      />
    );
  }

  const settings = settingsQuery.data;
  const planEntries = Object.entries(settings.plan_status);

  return (
    <div className="grid gap-4 lg:grid-cols-[1.2fr_1fr]">
      <Card>
        <CardHeader>
          <CardTitle>平台参数</CardTitle>
          <CardDescription>保存后立即对全部企业生效，请谨慎修改</CardDescription>
        </CardHeader>
        <CardContent>
          <form className="space-y-4" onSubmit={handleSubmit}>
            <Field
              label="额度提醒比例（%）"
              htmlFor="quota-warn-threshold"
              hint="达到该比例后在企业端展示提醒，默认 80"
            >
              <Input
                id="quota-warn-threshold"
                type="number"
                min="0"
                max="100"
                value={form.quotaWarnThreshold}
                onChange={(event) =>
                  setForm({ ...form, quotaWarnThreshold: event.target.value })
                }
              />
            </Field>

            <Field label="默认试用天数" htmlFor="trial-days" hint="0 表示不提供试用">
              <Input
                id="trial-days"
                type="number"
                min="0"
                max="365"
                value={form.trialDays}
                onChange={(event) => setForm({ ...form, trialDays: event.target.value })}
              />
            </Field>

            <Field
              label="额度单位文案"
              htmlFor="quota-unit-label"
              hint="展示给用户的计量单位，如「字数额度」"
            >
              <Input
                id="quota-unit-label"
                value={form.quotaUnitLabel}
                maxLength={32}
                onChange={(event) => setForm({ ...form, quotaUnitLabel: event.target.value })}
              />
            </Field>

            <Field
              label="平台公告"
              htmlFor="announcement"
              hint={`最多 ${ANNOUNCEMENT_MAX_LENGTH} 字，留空表示不展示公告`}
            >
              <Textarea
                id="announcement"
                value={form.announcement}
                rows={4}
                maxLength={ANNOUNCEMENT_MAX_LENGTH}
                onChange={(event) => setForm({ ...form, announcement: event.target.value })}
              />
            </Field>

            {formError ? <p className="text-sm text-destructive">{formError}</p> : null}

            <div className="flex items-center gap-2">
              <Button type="submit" disabled={saveMutation.isPending}>
                {saveMutation.isPending ? '保存中…' : '保存配置'}
              </Button>
              <span className="text-xs text-muted-foreground">
                最近修改：{orPlaceholder(settings.updated_by)} · {formatDateTime(settings.updated_at)}
              </span>
            </div>
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>套餐上下架</CardTitle>
          <CardDescription>上下架在套餐目录页由产品配置，这里只读展示</CardDescription>
        </CardHeader>
        <CardContent>
          {planEntries.length === 0 ? (
            <EmptyState
              title="还没有套餐状态"
              description="套餐目录初始化后会展示各档位的上下架状态"
            />
          ) : (
            <ul className="divide-y">
              {planEntries.map(([planCode, status]) => (
                <li key={planCode} className="flex items-center justify-between py-2.5 text-sm">
                  <span>{planCode}</span>
                  <span
                    className={
                      status === 'available' ? 'text-emerald-700' : 'text-muted-foreground'
                    }
                  >
                    {status === 'available' ? '已在架' : '已下架'}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
