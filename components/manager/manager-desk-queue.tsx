/**
 * v1.2 Manager Desk Component
 * 经理作战台 - 风险队列 + Pipeline Truth + 介入建议
 */

import { useState, useMemo, useCallback } from "react";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useManagerDesk } from "@/hooks/use-manager-desk";
import {
  TRUTH_BAND_LABELS,
  TRUTH_BAND_COLORS,
  RISK_LEVEL_LABELS,
  type ManagerRiskItem,
  type PipelineTruthScore,
  type ManagerIntervention,
  type TruthBand,
  type ManagerDeskInterventionStatus,
} from "@/types/manager-desk";
import {
  AlertTriangle,
  TrendingUp,
  Clock,
  MessageSquare,
  CheckCircle,
  ChevronDown,
  ChevronUp,
  Users,
  RefreshCw,
  ArrowRight,
  Plus,
  Check,
  X,
  Loader2,
} from "lucide-react";

function TruthBandBadge({ band }: { band: TruthBand }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium ${TRUTH_BAND_COLORS[band]}`}>
      {band === "healthy" && <CheckCircle className="h-3 w-3" />}
      {band === "stalled" && <AlertTriangle className="h-3 w-3" />}
      {TRUTH_BAND_LABELS[band]}
    </span>
  );
}

function RiskItemCard({ item }: { item: ManagerRiskItem }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className={`rounded-lg border p-3 ${item.riskLevel === "critical" ? "border-l-4 border-l-red-500" : ""}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-medium text-sm">{item.customerName}</span>
            {item.opportunityName && (
              <span className="text-xs text-muted-foreground truncate">{item.opportunityName}</span>
            )}
          </div>
          <p className="text-xs text-muted-foreground mt-1">{item.riskReason}</p>
        </div>
        <div className="flex items-center gap-1 flex-shrink-0">
          <Badge variant={item.riskLevel === "critical" ? "destructive" : item.riskLevel === "high" ? "destructive" : "secondary"} className="text-xs">
            {RISK_LEVEL_LABELS[item.riskLevel]}
          </Badge>
          {item.truthBand && <TruthBandBadge band={item.truthBand} />}
        </div>
      </div>
      <div className="flex items-center justify-between mt-2">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          {item.lastActivityAt && (
            <span className="flex items-center gap-1">
              <Clock className="h-3 w-3" />
              {new Date(item.lastActivityAt).toLocaleDateString("zh-CN")}
            </span>
          )}
          {item.ownerName && (
            <span className="flex items-center gap-1">
              <Users className="h-3 w-3" />
              {item.ownerName}
            </span>
          )}
        </div>
        <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={() => setExpanded(!expanded)}>
          {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
        </Button>
      </div>
      {expanded && (
        <div className="mt-3 pt-3 border-t space-y-2">
          <p className="text-xs text-muted-foreground">
            <span className="font-medium">建议动作：</span>{item.suggestedAction}
          </p>
          <div className="flex gap-2">
            {item.customerId && (
              <Button size="sm" variant="outline" className="h-7 text-xs" asChild>
                <Link href={`/customers/${item.customerId}`}>
                  查看客户 <ArrowRight className="ml-1 h-3 w-3" />
                </Link>
              </Button>
            )}
            {item.opportunityId && (
              <Button size="sm" variant="outline" className="h-7 text-xs" asChild>
                <Link href={`/deals/${item.opportunityId}`}>
                  查看商机 <ArrowRight className="ml-1 h-3 w-3" />
                </Link>
              </Button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function InterventionCard({ item, onAction, actionLoading }: {
  item: ManagerIntervention;
  onAction?: (action: "create" | "complete" | "dismiss") => void;
  actionLoading?: boolean;
}) {
  const statusLabel: Record<ManagerDeskInterventionStatus, string> = {
    new: "新建议",
    task_created: "任务已创建",
    in_progress: "进行中",
    completed: "已完成",
    dismissed: "已忽略"
  };
  const statusColor: Record<ManagerDeskInterventionStatus, string> = {
    new: "bg-slate-100 text-slate-700",
    task_created: "bg-blue-100 text-blue-700",
    in_progress: "bg-amber-100 text-amber-700",
    completed: "bg-emerald-100 text-emerald-700",
    dismissed: "bg-slate-100 text-slate-400"
  };

  return (
    <div className={`rounded-lg border p-3 ${item.priority === "critical" ? "border-l-4 border-l-red-500" : ""}`}>
      <div className="flex items-start justify-between gap-2 mb-2">
        <div>
          <p className="font-medium text-sm">{item.targetName}</p>
          <p className="text-xs text-muted-foreground">{item.ownerName}</p>
        </div>
        <div className="flex items-center gap-1 flex-wrap justify-end">
          {item.interventionStatus && (
            <span className={`text-xs px-2 py-0.5 rounded ${statusColor[item.interventionStatus]}`}>
              {statusLabel[item.interventionStatus]}
            </span>
          )}
          <Badge variant="outline" className="text-xs">
            {item.interventionType === "escalate" ? "升级处理" : item.interventionType === "coach" ? "需要辅导" : "跟进"}
          </Badge>
          {item.truthBand && <TruthBandBadge band={item.truthBand} />}
        </div>
      </div>
      <p className="text-xs text-muted-foreground mb-2">{item.reason}</p>
      <div className="bg-slate-50 rounded p-2 mb-2">
        <p className="text-xs font-medium mb-1">建议介入方式：</p>
        <ul className="text-xs text-muted-foreground space-y-1">
          {item.talkingPoints.map((point, i) => (
            <li key={i} className="flex items-start gap-1">
              <span className="text-blue-500">•</span>
              {point}
            </li>
          ))}
        </ul>
      </div>
      <div className="flex gap-2">
        {item.interventionStatus === "new" && onAction && (
          <Button
            size="sm"
            variant="default"
            className="h-7 text-xs flex-1"
            disabled={actionLoading}
            onClick={() => onAction("create")}
          >
            {actionLoading ? <Loader2 className="h-3 w-3 animate-spin" /> : <Plus className="h-3 w-3" />}
            <span className="ml-1">创建任务</span>
          </Button>
        )}
        {item.interventionStatus === "task_created" && onAction && (
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-xs flex-1"
            disabled={actionLoading}
            onClick={() => onAction("complete")}
          >
            {actionLoading ? <Loader2 className="h-3 w-3 animate-spin" /> : <Check className="h-3 w-3" />}
            <span className="ml-1">标记完成</span>
          </Button>
        )}
        {(item.interventionStatus === "new" || item.interventionStatus === "task_created") && onAction && (
          <Button
            size="sm"
            variant="ghost"
            className="h-7 text-xs"
            disabled={actionLoading}
            onClick={() => onAction("dismiss")}
          >
            {actionLoading ? <Loader2 className="h-3 w-3 animate-spin" /> : <X className="h-3 w-3" />}
          </Button>
        )}
        {item.interventionStatus === "completed" && (
          <span className="text-xs text-emerald-600 flex items-center gap-1 py-1">
            <Check className="h-3 w-3" /> 已完成
          </span>
        )}
        {item.interventionStatus === "dismissed" && (
          <span className="text-xs text-muted-foreground flex items-center gap-1 py-1">已忽略</span>
        )}
        {item.linkedWorkItemId && item.interventionStatus !== "completed" && item.interventionStatus !== "dismissed" && (
          <Button size="sm" variant="ghost" className="h-7 text-xs" asChild>
            <Link href={`/today?workItem=${item.linkedWorkItemId}`}>
              查看任务 <ArrowRight className="ml-1 h-3 w-3" />
            </Link>
          </Button>
        )}
      </div>
    </div>
  );
}

function TruthScoreCard({ item }: { item: PipelineTruthScore }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="flex items-center gap-3 p-3 rounded-lg border">
      <div className="flex-shrink-0">
        <TruthBandBadge band={item.truthBand} />
      </div>
      <div className="flex-1 min-w-0">
        <p className="font-medium text-sm truncate">{item.customerName}</p>
        {item.opportunityName && (
          <p className="text-xs text-muted-foreground truncate">{item.opportunityName}</p>
        )}
        <p className="text-xs text-muted-foreground mt-1">{item.reason}</p>
        {expanded && item.signals.length > 0 && (
          <div className="mt-2 pt-2 border-t space-y-1">
            {item.signals.map((signal, i) => (
              <div key={i} className="flex items-start gap-1.5">
                <span className={`text-xs ${signal.isPositive ? "text-emerald-600" : "text-red-500"} font-medium`}>
                  {signal.isPositive ? "✓" : "✗"}
                </span>
                <span className="text-xs text-muted-foreground">
                  <span className="font-medium">{signal.label}</span>
                  <span className="ml-1">{signal.description}</span>
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
      <div className="flex-shrink-0 text-right flex flex-col items-end gap-1">
        <div>
          <p className="text-lg font-semibold">{item.healthScore}</p>
          <p className="text-xs text-muted-foreground">健康分</p>
        </div>
        {item.signals.length > 0 && (
          <Button variant="ghost" size="sm" className="h-6 text-xs" onClick={() => setExpanded(!expanded)}>
            {expanded ? "收起" : "信号详情"}
          </Button>
        )}
      </div>
    </div>
  );
}

function LoadingSkeleton() {
  return (
    <div className="space-y-3">
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-24 w-full" />
    </div>
  );
}

interface ManagerDeskQueueProps {
  compact?: boolean;
}

export function ManagerDeskQueue({ compact = false }: ManagerDeskQueueProps) {
  const { data, loading, error, reload, createWorkItem, resolveIntervention, actionLoading, actionError } = useManagerDesk();
  const [activeTab, setActiveTab] = useState<"risks" | "truth" | "interventions">("risks");
  const [actionItemId, setActionItemId] = useState<string | null>(null);

  const handleInterventionAction = useCallback(async (item: ManagerIntervention, action: "create" | "complete" | "dismiss") => {
    setActionItemId(item.id);
    try {
      if (action === "create") {
        await createWorkItem(item);
      } else {
        await resolveIntervention({
          interventionKey: item.id,
          resolution: action === "complete" ? "completed" : "dismissed",
          intervention: item
        });
      }
    } finally {
      setActionItemId(null);
    }
  }, [createWorkItem, resolveIntervention]);

  const displayData = useMemo(() => {
    if (!data) return { risks: [], truth: [], interventions: [] };
    return {
      risks: data.riskQueue.slice(0, compact ? 5 : 15),
      truth: data.truthScores.slice(0, compact ? 5 : 10),
      interventions: data.interventions.slice(0, compact ? 3 : 8)
    };
  }, [data, compact]);

  if (loading) {
    return (
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-orange-600" />
            经理作战台
          </CardTitle>
        </CardHeader>
        <CardContent>
          <LoadingSkeleton />
        </CardContent>
      </Card>
    );
  }

  if (error || !data) {
    return (
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-orange-600" />
            经理作战台
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center justify-between">
            <p className="text-sm text-muted-foreground">加载失败</p>
            <Button variant="ghost" size="sm" onClick={() => void reload()}>
              <RefreshCw className="h-3 w-3" />
            </Button>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-orange-600" />
            经理作战台
          </CardTitle>
          <div className="flex items-center gap-2">
            {data.summary.criticalCount > 0 && (
              <Badge variant="destructive" className="text-xs">
                {data.summary.criticalCount} 紧急
              </Badge>
            )}
            {data.summary.needsInterventionCount > 0 && (
              <Badge variant="secondary" className="text-xs">
                {data.summary.needsInterventionCount} 待介入
              </Badge>
            )}
            <Button variant="ghost" size="sm" onClick={() => void reload()}>
              <RefreshCw className="h-3 w-3" />
            </Button>
          </div>
        </div>
        <div className="flex gap-1 pt-2">
          {(["risks", "truth", "interventions"] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`text-xs px-3 py-1 rounded ${activeTab === tab ? "bg-blue-100 text-blue-700" : "text-muted-foreground hover:bg-muted"}`}
            >
              {tab === "risks" && `风险队列 (${displayData.risks.length})`}
              {tab === "truth" && `Pipeline 真相 (${displayData.truth.length})`}
              {tab === "interventions" && `介入建议 (${displayData.interventions.length})`}
            </button>
          ))}
        </div>
      </CardHeader>
      <CardContent>
        {activeTab === "risks" && (
          <div className="space-y-2">
            {displayData.risks.length === 0 ? (
              <p className="text-sm text-muted-foreground py-4 text-center">暂无风险队列</p>
            ) : (
              displayData.risks.map((item) => <RiskItemCard key={item.id} item={item} />)
            )}
          </div>
        )}
        {activeTab === "truth" && (
          <div className="space-y-2">
            {displayData.truth.length === 0 ? (
              <p className="text-sm text-muted-foreground py-4 text-center">暂无 Pipeline 数据</p>
            ) : (
              <>
                <div className="flex gap-3 text-xs mb-2">
                  {(["healthy", "watch", "suspicious", "stalled"] as TruthBand[]).map((band) => {
                    const count = data.truthScores.filter(t => t.truthBand === band).length;
                    return (
                      <span key={band} className={`flex items-center gap-1 px-2 py-0.5 rounded ${TRUTH_BAND_COLORS[band]}`}>
                        <span className="font-medium">{count}</span>
                        <span>{TRUTH_BAND_LABELS[band]}</span>
                      </span>
                    );
                  })}
                </div>
                {displayData.truth.map((item) => <TruthScoreCard key={`${item.customerId}-${item.opportunityId}`} item={item} />)}
              </>
            )}
          </div>
        )}
        {activeTab === "interventions" && (
          <div className="space-y-2">
            {displayData.interventions.length === 0 ? (
              <p className="text-sm text-muted-foreground py-4 text-center">暂无待介入项</p>
            ) : (
              displayData.interventions.map((item) => (
                <InterventionCard
                  key={item.id}
                  item={item}
                  onAction={(action) => void handleInterventionAction(item, action)}
                  actionLoading={actionItemId === item.id}
                />
              ))
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
