/**
 * v1.1 Sales Desk Queue Component
 * 今日作战台 - 联系人队列组件
 */

import Link from "next/link";
import { useState, useMemo } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useSalesDeskQueue } from "@/hooks/use-sales-desk";
import { RHYTHM_ALERT_LABELS, QUEUE_TYPE_LABELS } from "@/types/sales-desk";
import type { SalesDeskQueueItem } from "@/types/sales-desk";
import { AlertTriangle, Phone, Clock, RefreshCw, ArrowRight, Filter, ArrowUpDown } from "lucide-react";

function QueueItemCard({ item }: { item: SalesDeskQueueItem }) {
  const queueLabel = QUEUE_TYPE_LABELS[item.queueType] ?? item.queueType;
  const alertLabel = item.alertReason ? RHYTHM_ALERT_LABELS[item.alertReason] : null;
  const isHighPriority = item.priorityScore >= 80;

  return (
    <div className={`flex items-start gap-3 p-3 rounded-lg border bg-card hover:bg-muted/50 transition-colors ${isHighPriority ? "border-l-4 border-l-red-400" : ""}`}>
      <div className="flex-shrink-0 mt-1">
        {item.queueType === "rhythm_breach" || item.queueType === "high_intent_silent" ? (
          <AlertTriangle className="h-4 w-4 text-destructive" />
        ) : (
          <Clock className="h-4 w-4 text-muted-foreground" />
        )}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1 flex-wrap">
          <span className="font-medium truncate">{item.customerName}</span>
          <Badge variant={alertLabel ? "destructive" : "secondary"} className="text-xs">
            {queueLabel}
          </Badge>
        </div>
        <p className="text-sm text-muted-foreground truncate">{item.reason}</p>
        {item.hoursSinceContact && (
          <p className="text-xs text-muted-foreground mt-1">
            {item.hoursSinceContact}小时未推进
          </p>
        )}
      </div>
      <div className="flex-shrink-0 flex flex-col items-end gap-1">
        {item.customerId && (
          <Button variant="ghost" size="sm" asChild>
            <Link href={`/customers/${item.customerId}`}>
              查看
              <ArrowRight className="ml-1 h-3 w-3" />
            </Link>
          </Button>
        )}
      </div>
    </div>
  );
}

function QueueSection({ title, items, icon }: { title: string; items: SalesDeskQueueItem[]; icon: React.ReactNode }) {
  if (items.length === 0) return null;

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        {icon}
        <h4 className="font-medium text-sm">{title}</h4>
        <Badge variant="secondary">{items.length}</Badge>
      </div>
      <div className="space-y-2">
        {items.slice(0, 5).map((item) => (
          <QueueItemCard key={`${item.queueType}-${item.customerId}`} item={item} />
        ))}
      </div>
    </div>
  );
}

export function SalesDeskQueue() {
  const { queue, loading, error, reload, totalAlerts } = useSalesDeskQueue();
  const [filterType, setFilterType] = useState<"all" | "rhythm_breach" | "high_intent_silent" | "quote_waiting">("all");
  const [sortBy, setSortBy] = useState<"priority" | "time">("priority");

  const allItems = useMemo(() => {
    if (!queue) return [];
    const items: SalesDeskQueueItem[] = [];
    if (filterType === "all" || filterType === "rhythm_breach") items.push(...queue.queues.rhythmBreach);
    if (filterType === "all" || filterType === "high_intent_silent") items.push(...queue.queues.highIntentSilent);
    if (filterType === "all" || filterType === "quote_waiting") items.push(...queue.queues.quoteWaiting);
    return items;
  }, [queue, filterType]);

  const sortedItems = useMemo(() => {
    return [...allItems].sort((a, b) => {
      if (sortBy === "priority") return b.priorityScore - a.priorityScore;
      const aTime = a.lastContactAt ? new Date(a.lastContactAt).getTime() : 0;
      const bTime = b.lastContactAt ? new Date(b.lastContactAt).getTime() : 0;
      return aTime - bTime;
    });
  }, [allItems, sortBy]);

  if (loading) {
    return (
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <Phone className="h-4 w-4" />
            今日作战台
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-20 w-full" />
          </div>
        </CardContent>
      </Card>
    );
  }

  if (error) {
    return (
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            <Phone className="h-4 w-4" />
            今日作战台
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center justify-between text-sm text-muted-foreground">
            <span>加载失败</span>
            <Button variant="ghost" size="sm" onClick={() => void reload()}>
              <RefreshCw className="h-3 w-3 mr-1" />
              重试
            </Button>
          </div>
        </CardContent>
      </Card>
    );
  }

  const hasAnyItems =
    queue &&
    (queue.queues.rhythmBreach.length > 0 ||
      queue.queues.highIntentSilent.length > 0 ||
      queue.queues.quoteWaiting.length > 0 ||
      queue.queues.mustContactToday.length > 0);

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <CardTitle className="text-base flex items-center gap-2">
            <Phone className="h-4 w-4" />
            今日作战台
          </CardTitle>
          {totalAlerts > 0 && (
            <Badge variant="destructive" className="text-xs">
              {totalAlerts} 个预警
            </Badge>
          )}
        </div>
        {hasAnyItems && (
          <div className="flex items-center gap-2 pt-2">
            <div className="flex items-center gap-1">
              <Filter className="h-3 w-3 text-muted-foreground" />
              <select
                value={filterType}
                onChange={e => setFilterType(e.target.value as typeof filterType)}
                className="text-xs border rounded px-1 py-0.5"
              >
                <option value="all">全部</option>
                <option value="rhythm_breach">节奏中断</option>
                <option value="high_intent_silent">高意向沉默</option>
                <option value="quote_waiting">报价等待</option>
              </select>
            </div>
            <div className="flex items-center gap-1">
              <ArrowUpDown className="h-3 w-3 text-muted-foreground" />
              <select
                value={sortBy}
                onChange={e => setSortBy(e.target.value as typeof sortBy)}
                className="text-xs border rounded px-1 py-0.5"
              >
                <option value="priority">按优先级</option>
                <option value="time">按最近动作</option>
              </select>
            </div>
          </div>
        )}
      </CardHeader>
      <CardContent>
        {!hasAnyItems || sortedItems.length === 0 ? (
          <p className="text-sm text-muted-foreground py-4 text-center">
            {filterType !== "all" ? "当前筛选条件下暂无数据" : "暂无紧急待处理客户"}
          </p>
        ) : (
          <div className="space-y-2">
            {sortedItems.slice(0, 10).map((item) => (
              <QueueItemCard key={`${item.queueType}-${item.customerId}`} item={item} />
            ))}
            {sortedItems.length > 10 && (
              <p className="text-xs text-center text-muted-foreground pt-2">
                还有 {sortedItems.length - 10} 项未显示
              </p>
            )}
          </div>
        )}
        <div className="mt-4 pt-3 border-t">
          <Button variant="ghost" size="sm" onClick={() => void reload()} className="w-full">
            <RefreshCw className="h-3 w-3 mr-1" />
            刷新
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
