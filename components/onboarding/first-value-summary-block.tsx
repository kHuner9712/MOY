"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  AlertTriangle,
  ArrowUpRight,
  Clock,
  Loader2,
  ShieldAlert,
  Target,
  TrendingUp,
  Users
} from "lucide-react";

import { useAuth } from "@/components/auth/auth-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { executiveClientService } from "@/services/executive-client-service";
import { valueMetricsClientService } from "@/services/value-metrics-client-service";

interface RiskCustomer {
  id: string;
  customerId: string;
  customerName: string | null;
  healthBand: string;
  overallHealthScore: number;
  summary: string | null;
}

interface StalledDeal {
  id: string;
  customerId: string;
  customerName: string | null;
  opportunityTitle: string | null;
  stalledDays: number;
  lastTouchpointAt: string | null;
}

interface PriorityAction {
  type: string;
  title: string;
  customerName: string | null;
  reason: string;
  impact: string;
  href: string;
}

interface FirstValueSummaryData {
  riskCustomers: RiskCustomer[];
  stalledDeals: StalledDeal[];
  priorityActions: PriorityAction[];
  weeklyAttentionPoints: string[];
  hasData: boolean;
}

export function FirstValueSummaryBlock({
  className = ""
}: {
  className?: string;
}): JSX.Element {
  const { user } = useAuth();
  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<FirstValueSummaryData | null>(null);

  useEffect(() => {
    if (user?.role !== "manager") {
      setLoading(false);
      return;
    }

    const loadData = async () => {
      setLoading(true);
      try {
        const [healthPayload, valuePayload] = await Promise.all([
          executiveClientService.getExecutiveHealth(),
          valueMetricsClientService.getSummary({})
        ]);

        const riskCustomers = (healthPayload?.healthSnapshots ?? [])
          .filter((item) => item.healthBand === "critical" || item.healthBand === "at_risk")
          .slice(0, 5)
          .map((item) => ({
            id: item.id,
            customerId: item.customerId,
            customerName: item.customerName ?? null,
            healthBand: item.healthBand,
            overallHealthScore: item.overallHealthScore,
            summary: item.summary ?? null
          }));

        const stalledDeals: StalledDeal[] = [];
        const priorityActions: PriorityAction[] = [];

        const weeklyAttentionPoints = (valuePayload?.summary?.highlights ?? []).slice(0, 5);

        const hasData = riskCustomers.length > 0 || stalledDeals.length > 0 || priorityActions.length > 0;

        setData({
          riskCustomers,
          stalledDeals,
          priorityActions,
          weeklyAttentionPoints,
          hasData
        });
      } catch {
        setData({
          riskCustomers: [],
          stalledDeals: [],
          priorityActions: [],
          weeklyAttentionPoints: [],
          hasData: false
        });
      } finally {
        setLoading(false);
      }
    };

    void loadData();
  }, [user?.role]);

  if (user?.role !== "manager") {
    return (
      <Card className={className}>
        <CardContent className="p-4">
          <p className="text-sm text-muted-foreground">
            此视图仅对管理员/经理可见。请联系您的管理员获取访问权限。
          </p>
        </CardContent>
      </Card>
    );
  }

  if (loading) {
    return (
      <Card className={className}>
        <CardContent className="p-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            正在分析您的业务数据...
          </div>
        </CardContent>
      </Card>
    );
  }

  if (!data) {
    return (
      <Card className={className}>
        <CardContent className="p-4">
          <p className="text-sm text-muted-foreground">暂无数据</p>
        </CardContent>
      </Card>
    );
  }

  if (!data.hasData) {
    return (
      <Card className={`border-dashed ${className}`}>
        <CardContent className="p-6 text-center">
          <Target className="mx-auto mb-3 h-8 w-8 text-slate-400" />
          <p className="text-sm font-medium text-slate-700">暂无需要关注的风险信号</p>
          <p className="mt-1 text-xs text-muted-foreground">
            导入客户数据后，系统将自动识别风险客户和停滞推进
          </p>
          <Button asChild className="mt-3" size="sm">
            <Link href="/imports">
              导入客户数据
              <ArrowUpRight className="ml-1 h-3 w-3" />
            </Link>
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className={`space-y-4 ${className}`}>
      <div className="flex items-center justify-between">
        <h3 className="flex items-center gap-2 text-base font-semibold text-slate-800">
          <Target className="h-5 w-5 text-indigo-600" />
          首次价值扫描
        </h3>
        <Button asChild size="sm" variant="outline">
          <Link href="/executive">
            打开经营驾驶舱
            <ArrowUpRight className="ml-1 h-3 w-3" />
          </Link>
        </Button>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="border-rose-100">
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-sm">
              <ShieldAlert className="h-4 w-4 text-rose-600" />
              风险客户 ({data.riskCustomers.length})
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {data.riskCustomers.length === 0 ? (
              <p className="text-sm text-muted-foreground">暂无风险客户</p>
            ) : (
              data.riskCustomers.map((customer) => (
                <div
                  key={customer.id}
                  className="rounded-lg border border-rose-100 bg-rose-50/30 p-2"
                >
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-sm font-medium text-slate-800">
                      {customer.customerName ?? "未知客户"}
                    </p>
                    <Badge
                      variant={
                        customer.healthBand === "critical"
                          ? "destructive"
                          : "default"
                      }
                    >
                      {customer.healthBand === "critical"
                        ? "严重"
                        : customer.healthBand === "at_risk"
                          ? "风险"
                          : customer.healthBand}
                    </Badge>
                  </div>
                  {customer.summary && (
                    <p className="mt-1 text-xs text-slate-600">{customer.summary}</p>
                  )}
                  <Button
                    asChild
                    size="sm"
                    variant="ghost"
                    className="mt-1 h-auto p-0 text-xs text-indigo-600"
                  >
                    <Link href={`/customers/${customer.customerId}`}>
                      查看详情
                      <ArrowUpRight className="ml-1 h-3 w-3" />
                    </Link>
                  </Button>
                </div>
              ))
            )}
          </CardContent>
        </Card>

        <Card className="border-amber-100">
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-sm">
              <Clock className="h-4 w-4 text-amber-600" />
              停滞推进
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {data.stalledDeals.length === 0 ? (
              <div className="rounded-lg border border-dashed p-3 text-center">
                <p className="text-sm text-muted-foreground">
                  暂无停滞推进记录
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  系统将在导入数据后自动识别停滞商机
                </p>
              </div>
            ) : (
              data.stalledDeals.map((deal) => (
                <div
                  key={deal.id}
                  className="rounded-lg border border-amber-100 bg-amber-50/30 p-2"
                >
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-sm font-medium text-slate-800">
                      {deal.customerName ?? "未知客户"}
                    </p>
                    <Badge variant="outline">
                      停滞 {deal.stalledDays} 天
                    </Badge>
                  </div>
                  {deal.opportunityTitle && (
                    <p className="mt-1 text-xs text-slate-600">
                      商机: {deal.opportunityTitle}
                    </p>
                  )}
                </div>
              ))
            )}
          </CardContent>
        </Card>
      </div>

      <Card className="border-indigo-100">
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-sm">
            <TrendingUp className="h-4 w-4 text-indigo-600" />
            本周值得关注
          </CardTitle>
        </CardHeader>
        <CardContent>
          {data.weeklyAttentionPoints.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              暂无特别关注事项。完成更多跟进后系统将生成洞察。
            </p>
          ) : (
            <ul className="space-y-2">
              {data.weeklyAttentionPoints.map((point, index) => (
                <li
                  key={index}
                  className="flex items-start gap-2 text-sm text-slate-700"
                >
                  <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-amber-500" />
                  {point}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card className="border-sky-100">
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-sm">
            <Users className="h-4 w-4 text-sky-600" />
            建议优先动作
          </CardTitle>
        </CardHeader>
        <CardContent>
          {data.priorityActions.length === 0 ? (
            <div className="space-y-2">
              <p className="text-sm text-muted-foreground">
                基于您的业务数据，建议以下优先动作：
              </p>
              <div className="grid gap-2 md:grid-cols-2">
                <Button asChild variant="outline" className="justify-start">
                  <Link href="/today">
                    <Target className="mr-2 h-4 w-4" />
                    生成今日任务计划
                  </Link>
                </Button>
                <Button asChild variant="outline" className="justify-start">
                  <Link href="/briefings">
                    <TrendingUp className="mr-2 h-4 w-4" />
                    查看晨报与准备卡
                  </Link>
                </Button>
                <Button asChild variant="outline" className="justify-start">
                  <Link href="/manager">
                    <ShieldAlert className="mr-2 h-4 w-4" />
                    运行漏单扫描
                  </Link>
                </Button>
                <Button asChild variant="outline" className="justify-start">
                  <Link href="/settings/automation">
                    <Clock className="mr-2 h-4 w-4" />
                    配置自动化规则
                  </Link>
                </Button>
              </div>
            </div>
          ) : (
            <div className="space-y-2">
              {data.priorityActions.map((action, index) => (
                <div
                  key={index}
                  className="flex items-center justify-between rounded-lg border border-sky-100 bg-sky-50/30 p-2"
                >
                  <div>
                    <p className="text-sm font-medium text-slate-800">
                      {action.title}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {action.reason} · 预期效果: {action.impact}
                    </p>
                  </div>
                  <Button asChild size="sm" variant="outline">
                    <Link href={action.href}>
                      去处理
                      <ArrowUpRight className="ml-1 h-3 w-3" />
                    </Link>
                  </Button>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
