'use client';

import { ArrowLeft, ExternalLink, Play, Power, ShieldCheck, Trash2, Upload, Wrench } from 'lucide-react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import * as React from 'react';

import { CapabilityList } from '@/components/plugin/capability-list';
import { GrantDrawer, type GrantDrawerMode } from '@/components/plugin/grant-drawer';
import { ReviewForm } from '@/components/plugin/review-form';
import { ReviewList } from '@/components/plugin/review-list';
import { VersionHistory } from '@/components/plugin/version-history';
import { StatusBadge } from '@/components/common/status-badge';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/feedback';
import { Field, Input } from '@/components/ui/input';
import { Pagination } from '@/components/ui/pagination';
import { Rating } from '@/components/ui/rating';
import { Select } from '@/components/ui/select';
import { toast } from '@/components/ui/toast';
import { Tooltip } from '@/components/ui/tooltip';
import { QUERY_KEYS, useApiMutation, useApiQuery } from '@/hooks/use-api';
import { BusinessError, pluginsApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import { formatDate, formatDateTime, orPlaceholder } from '@/lib/format';
import type { PluginDetail, PluginSettingValue } from '@/lib/types';

/** 评价分页大小（与后端默认一致） */
const REVIEW_PAGE_SIZE = 10;

/** 插件已下架的错误码（16002），前端据此给「该插件已下架」而非普通错误 */
const PLUGIN_NOT_FOUND_CODE = 16002;

/**
 * 插件详情页（P-10）。
 *
 * 数据源：PL2（声明）+ PL4（授权与企业开关）+ PL11（评价）。
 * 四个主按钮态：未安装→添加 / 已安装未启用→启用 / 已启用→打开工作台 / 有更新→更新。
 *
 * @returns 插件详情页元素
 */
export default function PluginDetailPage(): React.JSX.Element {
  const params = useParams<{ pluginId: string }>();
  const pluginId = params.pluginId;
  const [autoInstall, setAutoInstall] = React.useState<boolean>(false);

  const [grantOpen, setGrantOpen] = React.useState<boolean>(false);

  // 地址栏参数在挂载后读取：避免 useSearchParams 在静态渲染阶段要求 Suspense 边界
  React.useEffect(() => {
    if (typeof window === 'undefined') return;
    const params = new URLSearchParams(window.location.search);
    setAutoInstall(params.get('action') === 'install');
  }, []);
  const [grantMode, setGrantMode] = React.useState<GrantDrawerMode>('install');
  const [uninstallOpen, setUninstallOpen] = React.useState<boolean>(false);
  const [settingsDraft, setSettingsDraft] = React.useState<Record<string, PluginSettingValue>>({});
  const [reviewPage, setReviewPage] = React.useState<number>(1);
  const [reviewRating, setReviewRating] = React.useState<number>(0);
  const [reviewContent, setReviewContent] = React.useState<string>('');
  const [reviewAnonymous, setReviewAnonymous] = React.useState<boolean>(false);

  const detailQuery = useApiQuery(QUERY_KEYS.plugin(pluginId), () => pluginsApi.detail(pluginId), {
    enabled: Boolean(pluginId),
  });
  const capabilitiesQuery = useApiQuery(
    QUERY_KEYS.pluginCapabilities(pluginId),
    () => pluginsApi.capabilities(pluginId),
    { enabled: Boolean(pluginId) },
  );
  const reviewsQuery = useApiQuery(
    QUERY_KEYS.pluginReviews(pluginId, reviewPage),
    () => pluginsApi.reviews(pluginId, { page: reviewPage, page_size: REVIEW_PAGE_SIZE }),
    { enabled: Boolean(pluginId) },
  );

  const plugin = detailQuery.data;
  const capabilitiesData = capabilitiesQuery.data;
  const granted = capabilitiesData?.granted_capabilities ?? [];

  // 详情拿到后初始化配置草稿（只填未填过的键，避免覆盖用户输入）
  React.useEffect(() => {
    if (!plugin) return;
    setSettingsDraft((prev) => {
      const next: Record<string, PluginSettingValue> = { ...prev };
      for (const spec of plugin.settings_schema) {
        if (next[spec.key] === undefined) {
          next[spec.key] = spec.type === 'select' ? (spec.options?.[0] ?? '') : '';
        }
      }
      return next;
    });
  }, [plugin]);

  // 从市场点「添加」跳转过来时自动打开授权抽屉（有已授权数据后）
  React.useEffect(() => {
    if (!autoInstall || !plugin || grantOpen) return;
    if (plugin.installed_status !== 'none') return;
    setGrantMode('install');
    setGrantOpen(true);
  }, [autoInstall, plugin, grantOpen]);

  /** 所有写操作统一失效 `['plugins']` 前缀 */
  const invalidatePlugins = [['plugins']];

  const installMutation = useApiMutation(
    (payload: { grants: Record<string, boolean>; settings?: Record<string, PluginSettingValue> }) =>
      pluginsApi.install(pluginId, payload),
    {
      invalidateKeys: invalidatePlugins,
      onSuccess: () => {
        toast.success('已添加，可以打开工作台试试');
        setGrantOpen(false);
      },
      onError: (error) => toast.error(toBusinessMessage(error)),
    },
  );

  const updateGrantsMutation = useApiMutation(
    (payload: { grants: Record<string, boolean> }) =>
      pluginsApi.updateGrants(pluginId, { grants: payload.grants }),
    {
      invalidateKeys: invalidatePlugins,
      onSuccess: () => {
        toast.success('授权已更新');
        setGrantOpen(false);
      },
      onError: (error) => toast.error(toBusinessMessage(error)),
    },
  );

  const stateMutation = useApiMutation(
    (next: 'enabled' | 'disabled') => pluginsApi.setState(pluginId, next),
    {
      invalidateKeys: invalidatePlugins,
      onSuccess: (data) => {
        const suffix =
          data.running_task_count > 0
            ? `（${data.running_task_count} 个在跑任务会继续完成）`
            : '';
        toast.success(data.status === 'enabled' ? `已启用${suffix}` : `已停用${suffix}`);
      },
      onError: (error) => toast.error(toBusinessMessage(error)),
    },
  );

  const upgradeMutation = useApiMutation((version: string) => pluginsApi.upgrade(pluginId, version), {
    invalidateKeys: invalidatePlugins,
    onSuccess: () => toast.success('已更新到该版本'),
    onError: (error) => toast.error(toBusinessMessage(error)),
  });

  const rollbackMutation = useApiMutation(() => pluginsApi.rollback(pluginId), {
    invalidateKeys: invalidatePlugins,
    onSuccess: () => toast.success('已回退到上一版本'),
    onError: (error) => toast.error(toBusinessMessage(error)),
  });

  const uninstallMutation = useApiMutation(() => pluginsApi.uninstall(pluginId), {
    invalidateKeys: invalidatePlugins,
    onSuccess: (data) => {
      toast.success(
        data.kept_task_count > 0
          ? `已卸载，${data.kept_task_count} 个历史任务记录已保留`
          : '已卸载',
      );
      setUninstallOpen(false);
    },
    onError: (error) => toast.error(toBusinessMessage(error)),
  });

  const settingsMutation = useApiMutation(
    (settings: Record<string, PluginSettingValue>) => pluginsApi.setSettings(pluginId, settings),
    {
      invalidateKeys: invalidatePlugins,
      onSuccess: () => toast.success('配置已保存'),
      onError: (error) => toast.error(toBusinessMessage(error)),
    },
  );

  const reviewMutation = useApiMutation(
    (payload: { rating: number; content: string; anonymous: boolean }) =>
      pluginsApi.submitReview(pluginId, payload),
    {
      invalidateKeys: invalidatePlugins,
      onSuccess: () => {
        toast.success('评价已提交');
        setReviewContent('');
        setReviewRating(0);
        setReviewAnonymous(false);
      },
      onError: (error) => toast.error(toBusinessMessage(error)),
    },
  );

  // ---------------------------------------------------------------- 三态
  if (detailQuery.isLoading) {
    return (
      <div className="mx-auto max-w-5xl">
        <LoadingState label="正在加载插件信息…" />
      </div>
    );
  }

  if (detailQuery.error) {
    const notFound =
      detailQuery.error instanceof BusinessError && detailQuery.error.code === PLUGIN_NOT_FOUND_CODE;
    return (
      <div className="mx-auto max-w-5xl space-y-3">
        <Button asChild variant="ghost" size="sm">
          <Link href="/plugins">
            <ArrowLeft className="h-4 w-4" />
            返回插件市场
          </Link>
        </Button>
        {notFound ? (
          <EmptyState
            title="该插件已下架"
            description="它可能已停止维护或被平台下线，去市场看看其他插件"
            action={
              <Button asChild>
                <Link href="/plugins">去市场看看</Link>
              </Button>
            }
          />
        ) : (
          <ErrorState
            message={toBusinessMessage(detailQuery.error)}
            onRetry={() => void detailQuery.refetch()}
          />
        )}
      </div>
    );
  }

  if (!plugin) {
    return (
      <div className="mx-auto max-w-5xl">
        <EmptyState
          title="没有找到这个插件"
          description="链接可能已失效，去市场重新找一个适合你的插件"
          action={
            <Button asChild>
              <Link href="/plugins">去市场看看</Link>
            </Button>
          }
        />
      </div>
    );
  }

  const enabled = plugin.installed_status === 'enabled';
  const installed = plugin.installed_status !== 'none';
  const canOperate = plugin.installable || installed;
  const reviews = reviewsQuery.data?.items ?? [];
  const reviewTotal = reviewsQuery.data?.total ?? 0;
  /**
   * 有实际说明内容的版本。
   *
   * 后端 PL2 没有顶层 `changelog`，版本说明的唯一来源是 `versions[].changelog`；
   * 这里用 `filter` 而不是 `some`，否则列表里会出现空行。
   */
  const documentedVersions = plugin.versions.filter((item) => item.changelog.trim() !== '');

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div className="space-y-3">
        <Button asChild variant="ghost" size="sm">
          <Link href="/plugins">
            <ArrowLeft className="h-4 w-4" />
            返回插件市场
          </Link>
        </Button>

        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-xl font-semibold">{plugin.name}</h2>
              <Badge variant="outline">{orPlaceholder(plugin.category)}</Badge>
              <StatusBadge
                status={plugin.installed_status}
                label={
                  plugin.installed_status === 'enabled'
                    ? '已添加'
                    : plugin.installed_status === 'disabled'
                      ? '已停用'
                      : '未添加'
                }
              />
              {plugin.has_update ? <Badge variant="warning">有新版本</Badge> : null}
            </div>
            <p className="max-w-2xl text-sm text-muted-foreground">
              {orPlaceholder(plugin.summary, '暂无简介')}
            </p>
            <div className="flex flex-wrap items-center gap-3">
              <Rating
                value={plugin.rating_avg}
                count={plugin.rating_count}
                readOnly
                size="sm"
                label={`${plugin.name} 评分`}
              />
              <span className="text-xs text-muted-foreground">
                版本 {orPlaceholder(plugin.latest_version)}
              </span>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {plugin.has_update && installed ? (
              <Button
                type="button"
                disabled={!canOperate || upgradeMutation.isPending}
                onClick={() => upgradeMutation.mutate(plugin.latest_version)}
              >
                <Upload className="h-4 w-4" />
                {upgradeMutation.isPending ? '更新中…' : '更新到最新版本'}
              </Button>
            ) : null}

            {!installed ? (
              <Tooltip content="当前套餐不含插件能力，升级后即可添加" disabled={plugin.installable}>
                <span>
                  <Button
                    type="button"
                    disabled={!plugin.installable}
                    onClick={() => {
                      setGrantMode('install');
                      setGrantOpen(true);
                    }}
                  >
                    <Wrench className="h-4 w-4" />
                    添加插件
                  </Button>
                </span>
              </Tooltip>
            ) : enabled ? (
              <Button asChild>
                <Link href={`/workbench/${plugin.plugin_id}`}>
                  <Play className="h-4 w-4" />
                  打开工作台
                </Link>
              </Button>
            ) : (
              <Button
                type="button"
                disabled={stateMutation.isPending}
                onClick={() => stateMutation.mutate('enabled')}
              >
                <Power className="h-4 w-4" />
                启用
              </Button>
            )}

            {installed ? (
              <>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => {
                    setGrantMode('edit');
                    setGrantOpen(true);
                  }}
                >
                  <ShieldCheck className="h-4 w-4" />
                  调整授权
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  disabled={stateMutation.isPending}
                  onClick={() => stateMutation.mutate(enabled ? 'disabled' : 'enabled')}
                >
                  {enabled ? '停用' : '启用'}
                </Button>
                <Button type="button" variant="ghost" onClick={() => setUninstallOpen(true)}>
                  <Trash2 className="h-4 w-4" />
                  卸载
                </Button>
              </>
            ) : null}
          </div>
        </div>

        {!plugin.installable && !installed ? (
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-300 bg-amber-50 px-4 py-2 text-sm text-amber-900">
            <span>当前套餐不含插件能力，升级套餐后即可添加并使用工作台。</span>
            <Button asChild size="sm" variant="outline">
              <Link href="/billing">
                查看套餐
                <ExternalLink className="h-3.5 w-3.5" />
              </Link>
            </Button>
          </div>
        ) : null}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>插件介绍</CardTitle>
          <CardDescription>由插件提供方维护的说明</CardDescription>
        </CardHeader>
        <CardContent>
          <p className="whitespace-pre-wrap text-sm leading-relaxed">
            {orPlaceholder(plugin.description, '插件未提供详细介绍')}
          </p>
          {documentedVersions.length > 0 ? (
            <div className="mt-4 space-y-1">
              <p className="text-xs font-medium text-muted-foreground">版本说明</p>
              <ul className="list-inside list-disc space-y-0.5 text-sm text-muted-foreground">
                {documentedVersions.map((item) => (
                  <li key={item.version}>
                    <span className="font-medium tabular-nums">{`v${item.version}`}</span>
                    <span className="text-xs">{`（${formatDate(item.published_at)}）`}</span>
                    {item.changelog}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>需要哪些权限</CardTitle>
          <CardDescription>
            {capabilitiesQuery.isLoading
              ? '正在加载授权状态…'
              : `共 ${plugin.capabilities.length} 项，与插件声明一一对应`}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {capabilitiesQuery.error ? (
            <ErrorState
              message={toBusinessMessage(capabilitiesQuery.error)}
              onRetry={() => void capabilitiesQuery.refetch()}
            />
          ) : (
            <CapabilityList
              items={capabilitiesData?.items ?? plugin.capabilities}
              granted={installed ? granted : undefined}
            />
          )}
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>执行步骤</CardTitle>
            <CardDescription>添加后按以下顺序执行，可在关键步骤前人工确认</CardDescription>
          </CardHeader>
          <CardContent>
            {plugin.step_titles.length === 0 ? (
              <EmptyState title="还没有步骤说明" description="插件未提供执行步骤" />
            ) : (
              <ol className="space-y-2">
                {plugin.step_titles.map((title, index) => (
                  <li key={title} className="flex items-start gap-2 text-sm">
                    <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-medium text-primary tabular-nums">
                      {index + 1}
                    </span>
                    <span>{title}</span>
                  </li>
                ))}
              </ol>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>会产出什么</CardTitle>
            <CardDescription>每条产物的类型决定结果区的操作方式</CardDescription>
          </CardHeader>
          <CardContent>
            {plugin.outputs.length === 0 ? (
              <EmptyState title="还没有产物说明" description="插件未声明产物类型" />
            ) : (
              <ul className="space-y-2 text-sm">
                {plugin.outputs.map((output) => (
                  <li key={output.key} className="flex items-center justify-between gap-2">
                    <span>{output.label || output.key}</span>
                    <Badge variant="outline">
                      {output.type === 'file'
                        ? '文件'
                        : output.type === 'link'
                          ? '链接'
                          : output.type === 'table'
                            ? '表格'
                            : '文本'}
                    </Badge>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>

      {installed && plugin.settings_schema.length > 0 ? (
        <Card>
          <CardHeader>
            <CardTitle>插件配置</CardTitle>
            <CardDescription>配置项来自插件声明，保存后对新发起的任务生效</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {plugin.settings_schema.map((spec) => (
              <Field key={spec.key} label={spec.label} htmlFor={`plugin-setting-${spec.key}`}>
                {spec.type === 'select' ? (
                  <Select
                    id={`plugin-setting-${spec.key}`}
                    value={String(settingsDraft[spec.key] ?? '')}
                    onChange={(event) =>
                      setSettingsDraft((prev) => ({ ...prev, [spec.key]: event.target.value }))
                    }
                    options={(spec.options ?? []).map((option) => ({ value: option, label: option }))}
                  />
                ) : (
                  <Input
                    id={`plugin-setting-${spec.key}`}
                    value={String(settingsDraft[spec.key] ?? '')}
                    onChange={(event) =>
                      setSettingsDraft((prev) => ({ ...prev, [spec.key]: event.target.value }))
                    }
                    placeholder={`填写${spec.label}`}
                  />
                )}
              </Field>
            ))}
            <Button
              type="button"
              size="sm"
              disabled={settingsMutation.isPending}
              onClick={() => settingsMutation.mutate(settingsDraft)}
            >
              {settingsMutation.isPending ? '保存中…' : '保存配置'}
            </Button>
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>版本历史</CardTitle>
          <CardDescription>只保留最近两个版本，可升级或回退一步</CardDescription>
        </CardHeader>
        <CardContent>
          <VersionHistory
            versions={plugin.versions}
            installedVersion={plugin.installed_version}
            hasUpdate={plugin.has_update}
            upgrading={upgradeMutation.isPending}
            rollingBack={rollbackMutation.isPending}
            canManage={installed}
            onUpgrade={(version) => upgradeMutation.mutate(version)}
            onRollback={() => rollbackMutation.mutate(undefined)}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <CardTitle>用户评价</CardTitle>
            <Rating
              value={reviewsQuery.data?.rating_avg ?? plugin.rating_avg}
              count={reviewsQuery.data?.rating_count ?? plugin.rating_count}
              readOnly
              size="sm"
              label="整体评分"
            />
          </div>
          <CardDescription>同一成员重复提交会覆盖之前那条评价</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {reviewsQuery.isLoading ? (
            <LoadingState label="正在加载评价…" />
          ) : reviewsQuery.error ? (
            <ErrorState
              message={toBusinessMessage(reviewsQuery.error)}
              onRetry={() => void reviewsQuery.refetch()}
            />
          ) : (
            <>
              <ReviewList items={reviews} total={reviewTotal} />
              {reviewTotal > REVIEW_PAGE_SIZE ? (
                <Pagination
                  page={reviewPage}
                  pageSize={REVIEW_PAGE_SIZE}
                  total={reviewTotal}
                  onPageChange={setReviewPage}
                />
              ) : null}
            </>
          )}

          <div className="rounded-md border p-3">
            <h3 className="mb-2 text-sm font-semibold">写评价</h3>
            <ReviewForm
              rating={reviewRating}
              onRatingChange={setReviewRating}
              content={reviewContent}
              onContentChange={setReviewContent}
              anonymous={reviewAnonymous}
              onAnonymousChange={setReviewAnonymous}
              submitting={reviewMutation.isPending}
              canReview={installed}
              blockedReason="使用过这个插件才能评价"
              onSubmit={() => {
                if (reviewRating < 1) {
                  toast.error('请先选择 1-5 星');
                  return;
                }
                reviewMutation.mutate({
                  rating: reviewRating,
                  content: reviewContent.trim(),
                  anonymous: reviewAnonymous,
                });
              }}
            />
          </div>
        </CardContent>
      </Card>

      <GrantDrawer
        open={grantOpen}
        onOpenChange={setGrantOpen}
        plugin={plugin}
        granted={granted}
        mode={grantMode}
        submitting={installMutation.isPending || updateGrantsMutation.isPending}
        onSubmit={async (payload) => {
          if (grantMode === 'install') {
            await installMutation.mutateAsync(payload);
            return;
          }
          await updateGrantsMutation.mutateAsync({ grants: payload.grants });
        }}
      />

      <ConfirmDialog
        open={uninstallOpen}
        onOpenChange={setUninstallOpen}
        variant="danger"
        title="卸载该插件？"
        description="卸载后无法再从工作台发起任务；历史任务记录与产物会保留，180 天后统一清理。"
        confirmLabel="卸载"
        loading={uninstallMutation.isPending}
        onConfirm={async () => {
          await uninstallMutation.mutateAsync(undefined);
        }}
      >
        <p>{plugin.name}</p>
      </ConfirmDialog>
    </div>
  );
}
