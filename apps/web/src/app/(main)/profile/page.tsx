'use client';

import { useCallback, useEffect, useState } from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { ErrorState, LoadingState } from '@/components/ui/feedback';
import { useSession } from '@/hooks/use-session';
import { modelApi } from '@/lib/api-client';
import { toBusinessMessage } from '@/lib/business-errors';

/** 套餐名称展示映射（业务语言，不暴露内部标识） */
const PLAN_LABELS: Record<string, string> = {
  free: '免费版',
  professional: '专业版',
  enterprise: '企业版',
};

/** 个人中心页：账户信息 + 套餐与用量。 */
export default function ProfilePage(): JSX.Element {
  const session = useSession();
  const [usage, setUsage] = useState<{
    period: string;
    conversation_count: number;
    document_qa_count: number;
    quota: {
      plan_name: string;
      total_units: number;
      used_units: number;
      remaining_units: number;
    };
  } | null>(null);
  const [usageError, setUsageError] = useState('');

  /** 加载用量统计（业务化口径：次数/额度，不展示 token 精确值） */
  const loadUsage = useCallback(async (): Promise<void> => {
    setUsageError('');
    try {
      setUsage(await modelApi.usage('month'));
    } catch (error) {
      setUsageError(toBusinessMessage(error));
    }
  }, []);

  useEffect(() => {
    void loadUsage();
  }, [loadUsage]);

  if (session.loading) {
    return <LoadingState />;
  }

  if (session.errorMessage && !session.user) {
    return <ErrorState message={session.errorMessage} onRetry={() => void session.reload()} />;
  }

  const user = session.user;
  const quota = usage?.quota;
  const usedPercent =
    quota && quota.total_units > 0
      ? Math.min(Math.round((quota.used_units / quota.total_units) * 100), 100)
      : 0;

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>账户信息</CardTitle>
          <CardDescription>你当前使用的团队与角色</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <dl className="grid gap-3 sm:grid-cols-2">
            <div>
              <dt className="text-xs text-muted-foreground">姓名</dt>
              <dd className="text-sm font-medium">{user?.display_name || '—'}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">邮箱</dt>
              <dd className="text-sm font-medium">{user?.email || '—'}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">所属团队</dt>
              <dd className="text-sm font-medium">{user?.tenant.name || '—'}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">套餐</dt>
              <dd className="text-sm font-medium">
                <Badge variant="secondary">
                  {PLAN_LABELS[user?.tenant.plan ?? ''] ?? '—'}
                </Badge>
              </dd>
            </div>
          </dl>

          {user && user.organizations.length > 0 ? (
            <div>
              <p className="mb-2 text-xs text-muted-foreground">我加入的组织</p>
              <ul className="space-y-1">
                {user.organizations.map((org) => (
                  <li key={org.org_id} className="flex items-center justify-between text-sm">
                    <span>{org.name}</span>
                    <Badge variant="outline">{org.role}</Badge>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <Button variant="outline" onClick={session.logout}>
            退出登录
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>本月用量</CardTitle>
            <Button variant="ghost" size="sm" onClick={() => void loadUsage()}>
              刷新
            </Button>
          </div>
          <CardDescription>仅展示次数与额度，不展示内部计费细节</CardDescription>
        </CardHeader>
        <CardContent>
          {usageError ? (
            <ErrorState message={usageError} onRetry={() => void loadUsage()} />
          ) : usage ? (
            <div className="space-y-4">
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="rounded-md border p-3">
                  <p className="text-xs text-muted-foreground">对话次数</p>
                  <p className="text-2xl font-semibold">{usage.conversation_count}</p>
                </div>
                <div className="rounded-md border p-3">
                  <p className="text-xs text-muted-foreground">文档问答次数</p>
                  <p className="text-2xl font-semibold">{usage.document_qa_count}</p>
                </div>
              </div>
              {quota && quota.total_units > 0 ? (
                <div className="space-y-2">
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-muted-foreground">
                      {quota.plan_name || PLAN_LABELS[user?.tenant.plan ?? ''] || '当前套餐'}额度
                    </span>
                    <span>{usedPercent}%</span>
                  </div>
                  <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full bg-primary transition-all"
                      style={{ width: `${usedPercent}%` }}
                    />
                  </div>
                </div>
              ) : null}
            </div>
          ) : (
            <LoadingState />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
