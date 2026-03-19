"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { AttributionChain, AttributionSummary } from "@/services/attribution-service";
import { formatDateTime } from "@/lib/format";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle,
  Clock,
  TrendingUp,
  Users,
  Zap,
  XCircle
} from "lucide-react";

interface AttributionResultViewProps {
  summary: AttributionSummary;
  chains: AttributionChain[];
  loading?: boolean;
}

function getStatusBadge(status: string) {
  switch (status) {
    case "resolved":
      return <Badge className="bg-emerald-100 text-emerald-800">已解决</Badge>;
    case "acknowledged":
      return <Badge className="bg-blue-100 text-blue-800">已确认</Badge>;
    case "ignored":
      return <Badge className="bg-slate-100 text-slate-800">已忽略</Badge>;
    default:
      return <Badge className="bg-rose-100 text-rose-800">待处理</Badge>;
  }
}

function getResultBadge(resultStatus: string) {
  switch (resultStatus) {
    case "positive_progress":
      return <Badge className="bg-emerald-100 text-emerald-800">正向推进</Badge>;
    case "closed_won":
      return <Badge className="bg-emerald-100 text-emerald-800">成交</Badge>;
    case "risk_increased":
      return <Badge className="bg-rose-100 text-rose-800">风险增加</Badge>;
    case "closed_lost":
      return <Badge className="bg-rose-100 text-rose-800">流失</Badge>;
    default:
      return <Badge className="bg-slate-100 text-slate-800">无变化</Badge>;
  }
}

function ChainCard({ chain }: { chain: AttributionChain }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="mb-3 flex items-start justify-between">
        <div>
          <p className="text-sm font-medium text-slate-800">{chain.event.eventType}</p>
          <p className="mt-1 text-xs text-slate-500">
            {formatDateTime(chain.event.createdAt)}
          </p>
        </div>
        {getStatusBadge(chain.event.status)}
      </div>

      <div className="space-y-2">
        <div className="flex items-center gap-2 text-xs">
          <AlertTriangle className="h-3.5 w-3.5 text-amber-500" />
          <span className="text-slate-600">问题发现</span>
          <ArrowRight className="h-3 w-3 text-slate-400" />
          <span className="text-slate-800">{chain.event.eventSummary}</span>
        </div>

        {chain.workItems.length > 0 && (
          <div className="flex items-center gap-2 text-xs">
            <Zap className="h-3.5 w-3.5 text-indigo-500" />
            <span className="text-slate-600">触发动作</span>
            <ArrowRight className="h-3 w-3 text-slate-400" />
            <span className="text-slate-800">
              {chain.workItems.length} 个任务
            </span>
          </div>
        )}

        {chain.outcomes.length > 0 && (
          <div className="flex items-center gap-2 text-xs">
            <CheckCircle className="h-3.5 w-3.5 text-emerald-500" />
            <span className="text-slate-600">处理结果</span>
            <ArrowRight className="h-3 w-3 text-slate-400" />
            <div className="flex gap-1">
              {chain.outcomes.slice(0, 2).map((o, i) => (
                <span key={i}>{getResultBadge(o.resultStatus)}</span>
              ))}
            </div>
          </div>
        )}

        {chain.customerImpact.stageChanged && (
          <div className="flex items-center gap-2 text-xs">
            <TrendingUp className="h-3.5 w-3.5 text-emerald-500" />
            <span className="text-slate-600">阶段变化</span>
            <ArrowRight className="h-3 w-3 text-slate-400" />
            <span className="text-slate-800">
              {chain.customerImpact.oldStage} → {chain.customerImpact.newStage}
            </span>
          </div>
        )}

        {chain.timeline.resolutionDurationHours !== null && (
          <div className="flex items-center gap-2 text-xs">
            <Clock className="h-3.5 w-3.5 text-blue-500" />
            <span className="text-slate-600">处理时长</span>
            <ArrowRight className="h-3 w-3 text-slate-400" />
            <span className="text-slate-800">
              {chain.timeline.resolutionDurationHours.toFixed(1)} 小时
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

export function AttributionResultView({ summary, chains, loading }: AttributionResultViewProps) {
  if (loading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">归因结果视图</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">正在加载归因数据...</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <Card className="border-indigo-100 bg-gradient-to-br from-indigo-50/30 to-white">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Zap className="h-5 w-5 text-indigo-600" />
            归因结果总览
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
            <div className="rounded-lg border border-slate-200 bg-white p-3">
              <p className="text-xs text-slate-500">识别风险</p>
              <p className="text-xl font-bold text-slate-800">{summary.totalEventsDetected}</p>
            </div>
            <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-3">
              <p className="text-xs text-slate-500">已处理</p>
              <p className="text-xl font-bold text-emerald-700">{summary.eventsHandled}</p>
            </div>
            <div className="rounded-lg border border-blue-200 bg-blue-50 p-3">
              <p className="text-xs text-slate-500">正向结果</p>
              <p className="text-xl font-bold text-blue-700">{summary.eventsWithPositiveOutcome}</p>
            </div>
            <div className="rounded-lg border border-rose-200 bg-rose-50 p-3">
              <p className="text-xs text-slate-500">待处理</p>
              <p className="text-xl font-bold text-rose-700">{summary.eventsPending}</p>
            </div>
          </div>

          {summary.averageResolutionHours !== null && (
            <div className="mb-4 rounded-lg border border-slate-200 bg-white p-3">
              <p className="text-xs text-slate-500">平均处理时长</p>
              <p className="text-lg font-medium text-slate-800">
                {summary.averageResolutionHours.toFixed(1)} 小时
              </p>
            </div>
          )}

          {summary.topEventTypes.length > 0 && (
            <div className="mb-4">
              <p className="mb-2 text-xs font-medium text-slate-500">主要风险类型</p>
              <div className="flex flex-wrap gap-2">
                {summary.topEventTypes.slice(0, 5).map((item, index) => (
                  <div
                    key={index}
                    className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs"
                  >
                    <span className="text-slate-600">{item.eventType}</span>
                    <span className="ml-1 font-medium text-slate-800">({item.count})</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {summary.topHandlers.length > 0 && (
            <div>
              <p className="mb-2 text-xs font-medium text-slate-500">处理效果最佳</p>
              <div className="flex flex-wrap gap-2">
                {summary.topHandlers.slice(0, 3).map((item, index) => (
                  <div
                    key={index}
                    className="flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs"
                  >
                    <Users className="h-3 w-3 text-emerald-600" />
                    <span className="text-slate-600">{item.handlerName}</span>
                    <span className="ml-1 font-medium text-emerald-700">
                      ({item.positiveOutcomeCount}/{item.handledCount})
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {chains.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">归因链路详情</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid gap-3 md:grid-cols-2">
              {chains.slice(0, 10).map((chain, index) => (
                <ChainCard key={chain.event.id} chain={chain} />
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {chains.length === 0 && summary.totalEventsDetected === 0 && (
        <Card>
          <CardContent className="py-8 text-center">
            <CheckCircle className="mx-auto mb-3 h-12 w-12 text-emerald-400" />
            <p className="text-sm text-slate-600">本周未检测到风险事件</p>
            <p className="mt-1 text-xs text-slate-400">系统将持续监控客户健康状态</p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

export function AttributionCompactView({
  summary,
  loading
}: {
  summary: AttributionSummary;
  loading?: boolean;
}) {
  if (loading) {
    return (
      <div className="rounded-lg border border-indigo-100 bg-indigo-50/30 p-3">
        <p className="text-sm text-muted-foreground">正在加载归因数据...</p>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-indigo-100 bg-indigo-50/30 p-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs font-medium text-indigo-700">归因结果</span>
        <div className="flex gap-1">
          <Badge variant="outline" className="text-xs">
            {summary.eventsHandled}/{summary.totalEventsDetected} 处理
          </Badge>
          {summary.eventsWithPositiveOutcome > 0 && (
            <Badge className="bg-emerald-100 text-xs text-emerald-800">
              {summary.eventsWithPositiveOutcome} 正向
            </Badge>
          )}
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        {summary.topEventTypes.slice(0, 3).map((item, index) => (
          <span key={index} className="rounded bg-white px-2 py-0.5 text-xs text-slate-600">
            {item.eventType}: {item.handledCount}/{item.count}
          </span>
        ))}
      </div>
    </div>
  );
}
