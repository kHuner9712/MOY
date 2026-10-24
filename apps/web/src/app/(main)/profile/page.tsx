'use client';

import * as React from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/feedback';
import { QuotaProgress } from '@/components/ui/progress';
import { toast } from '@/components/ui/toast';
import { QUERY_KEYS, useApiQuery } from '@/hooks/use-api';
import { useSession } from '@/hooks/use-session';
import { modelApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';
import { formatNumber, formatUnits, orPlaceholder } from '@/lib/format';

/** 套餐名称展示映射（业务语言，不暴露内部标识） */
const PLAN_LABELS: Readonly<Record<string, string>> = {
  free: '免费版',
  professional: '专业版',
  enterprise: '企业版',
};

/** 组织角色展示映射 */
const ROLE_LABELS: Readonly<Record<string, string>> = {
  owner: '所有者',
  org_admin: '企业管理员',
  member: '成员',
  guest: '访客',
};

/**
 * 个人中心页（P-08 深化）。
 *
 * 只展示「我是谁、我在哪个企业、我这个月用了多少」，
 * 不展示任何内部计费细节与技术口径；两段数据（会话资料 / 用量）各自独立三态。
 *
 * 会话资料不再由本页判定：布局层的会话门保证「能渲染到这里 = 已就绪」，
 * 因此本页没有「加载中 / 未登录 / 资料出错」三个分支（它们的语义已上提）。
 *
 * @returns 个人中心页元素
 */
export default function ProfilePage(): React.JSX.Element {
  const session = useSession();
  const usageQuery = useApiQuery(QUERY_KEYS.myUsage('month'), () => modelApi.usage('month'), {
    enabled: Boolean(session.user),
  });

  const user = session.user;
  if (user === null) {
    // 门已保证就绪，这里只是让类型收敛的兜底；刻意不渲染任何「未登录」文案，
    // 否则「门的判据」与「页面的判据」会变成两套说法。
    return <LoadingState label="正在加载账户信息…" />;
  }

  const usage = usageQuery.data;
  const quota = usage?.quota;

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>账户信息</CardTitle>
          <CardDescription>你当前所在的企业与角色</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <dl className="grid gap-3 sm:grid-cols-2">
            <div>
              <dt className="text-xs text-muted-foreground">姓名</dt>
              <dd className="text-sm font-medium">{orPlaceholder(user.display_name)}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">邮箱</dt>
              <dd className="text-sm font-medium">{orPlaceholder(user.email)}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">所属企业</dt>
              <dd className="text-sm font-medium">{orPlaceholder(user.tenant.name)}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">套餐</dt>
              <dd>
                <Badge variant="secondary">
                  {PLAN_LABELS[user.tenant.plan] ?? orPlaceholder(user.tenant.plan)}
                </Badge>
              </dd>
            </div>
          </dl>

          {user.organizations.length > 0 ? (
            <div>
              <p className="mb-2 text-xs text-muted-foreground">我加入的组织</p>
              <ul className="divide-y">
                {user.organizations.map((org) => (
                  <li key={org.org_id} className="flex items-center justify-between py-2 text-sm">
                    <span>{org.name}</span>
                    <Badge variant="outline">{ROLE_LABELS[org.role] ?? org.role}</Badge>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">
              还没有加入任何组织；联系企业管理员邀请后即可协作。
            </p>
          )}

          <Button
            variant="outline"
            onClick={() => {
              session.logout();
              toast.success('已退出登录');
            }}
          >
            退出登录
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-2">
            <CardTitle>本月用量</CardTitle>
            <Button
              variant="ghost"
              size="sm"
              disabled={usageQuery.isFetching}
              onClick={() => void usageQuery.refetch()}
            >
              {usageQuery.isFetching ? '刷新中…' : '刷新'}
            </Button>
          </div>
          <CardDescription>只展示次数与额度，口径与「套餐与账单」一致</CardDescription>
        </CardHeader>
        <CardContent>
          {usageQuery.isLoading ? (
            <LoadingState label="正在加载用量…" />
          ) : usageQuery.error ? (
            <ErrorState
              message={toBusinessMessage(usageQuery.error)}
              onRetry={() => void usageQuery.refetch()}
            />
          ) : usage ? (
            <div className="space-y-4">
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="rounded-md border p-3">
                  <p className="text-xs text-muted-foreground">对话次数</p>
                  <p className="text-2xl font-semibold tabular-nums">
                    {formatNumber(usage.conversation_count)}
                  </p>
                </div>
                <div className="rounded-md border p-3">
                  <p className="text-xs text-muted-foreground">文档问答次数</p>
                  <p className="text-2xl font-semibold tabular-nums">
                    {formatNumber(usage.document_qa_count)}
                  </p>
                </div>
              </div>

              {quota && quota.total_units > 0 ? (
                <QuotaProgress
                  used={quota.used_units}
                  total={quota.total_units}
                  unit="字"
                  label={`${orPlaceholder(quota.plan_name, PLAN_LABELS[user.tenant.plan] ?? '当前套餐')}额度`}
                  hint={`剩余 ${formatUnits(quota.remaining_units)}，可在「套餐与账单」中升级`}
                />
              ) : (
                <p className="text-sm text-muted-foreground">
                  当前套餐未设置额度上限，可放心使用。
                </p>
              )}
            </div>
          ) : (
            <EmptyState
              title="还没有用量记录"
              description="发起一次对话或文档问答后，这里会显示本月用量"
            />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
