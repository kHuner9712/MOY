/**
 * v1.1 Customer Unified Timeline
 * 客户统一时间线 - 整合所有客户相关事件
 */

import { useState, useMemo } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useCustomerTimeline } from "@/hooks/use-sales-desk";
import { formatDateTime } from "@/lib/format";
import type { CustomerTimelineItem } from "@/types/sales-desk";
import { Mail, Phone, Calendar, FileText, MessageSquare, AlertTriangle, Sparkles, Clock, Filter } from "lucide-react";

const ITEM_TYPE_CONFIG: Record<string, { icon: React.ReactNode; color: string; label: string }> = {
  communication_input: { icon: <MessageSquare className="h-3 w-3" />, color: "text-blue-600", label: "沟通" },
  followup: { icon: <Phone className="h-3 w-3" />, color: "text-green-600", label: "跟进" },
  prep_card: { icon: <FileText className="h-3 w-3" />, color: "text-purple-600", label: "Prep" },
  content_draft: { icon: <FileText className="h-3 w-3" />, color: "text-indigo-600", label: "草稿" },
  outcome: { icon: <Sparkles className="h-3 w-3" />, color: "text-emerald-600", label: "结果" },
  business_event: { icon: <AlertTriangle className="h-3 w-3" />, color: "text-amber-600", label: "事件" },
  touchpoint: { icon: <Mail className="h-3 w-3" />, color: "text-sky-600", label: "触点" },
  briefing: { icon: <Calendar className="h-3 w-3" />, color: "text-rose-600", label: "简报" }
};

const IMPORTANCE_STYLES = {
  high: "border-l-4 border-l-red-500",
  medium: "border-l-4 border-l-amber-400",
  low: "border-l-4 border-l-slate-200"
};

function TimelineItem({ item }: { item: CustomerTimelineItem }) {
  const config = ITEM_TYPE_CONFIG[item.itemType] ?? { icon: <Clock className="h-3 w-3" />, color: "text-slate-500", label: item.itemType };

  return (
    <div className={`relative rounded-lg border bg-slate-50/70 p-4 ${IMPORTANCE_STYLES[item.importance]}`}>
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className={config.color}>{config.icon}</span>
          <Badge variant="outline" className={`text-xs ${config.color}`}>
            {config.label}
          </Badge>
          {item.isAiGenerated && (
            <Badge variant="secondary" className="text-xs">AI</Badge>
          )}
        </div>
        <p className="text-xs text-muted-foreground">
          {item.occurredAt ? formatDateTime(item.occurredAt) : "-"}
        </p>
      </div>
      <p className="text-sm font-medium text-slate-900">{item.title}</p>
      {item.subtitle && (
        <p className="mt-1 text-xs text-muted-foreground">{item.subtitle}</p>
      )}
    </div>
  );
}

function EmptyTimeline() {
  return (
    <div className="py-8 text-center">
      <Clock className="mx-auto h-8 w-8 text-slate-300" />
      <p className="mt-2 text-sm text-muted-foreground">暂无时间线记录</p>
      <p className="text-xs text-slate-400">开始录入沟通后将自动生成</p>
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

interface CustomerUnifiedTimelineProps {
  customerId: string;
  compact?: boolean;
}

export function CustomerUnifiedTimeline({ customerId, compact = false }: CustomerUnifiedTimelineProps) {
  const { items, loading, error } = useCustomerTimeline(customerId, compact ? 10 : 30);
  const [filterType, setFilterType] = useState<string>("all");

  const availableTypes = useMemo(() => {
    const types = new Set(items.map(i => i.itemType));
    return Array.from(types);
  }, [items]);

  const filteredItems = useMemo(() => {
    if (filterType === "all") return items;
    return items.filter(i => i.itemType === filterType);
  }, [items, filterType]);

  const uniqueTypes = useMemo(() => {
    const seen = new Set<string>();
    for (const item of items) {
      seen.add(item.itemType);
    }
    return Array.from(seen);
  }, [items]);

  if (loading) {
    return (
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">客户统一时间线</CardTitle>
        </CardHeader>
        <CardContent>
          <LoadingSkeleton />
        </CardContent>
      </Card>
    );
  }

  if (error) {
    return (
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">客户统一时间线</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="py-4 text-center text-sm text-muted-foreground">
            <p>加载失败</p>
            <p className="text-xs text-slate-400">{error}</p>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <CardTitle className="text-base">客户统一时间线</CardTitle>
          {filteredItems.length > 0 && (
            <span className="text-xs text-muted-foreground">{filteredItems.length} 条记录</span>
          )}
        </div>
        {uniqueTypes.length > 1 && (
          <div className="flex items-center gap-1 pt-2 flex-wrap">
            <Filter className="h-3 w-3 text-muted-foreground" />
            <button
              onClick={() => setFilterType("all")}
              className={`text-xs px-2 py-0.5 rounded ${filterType === "all" ? "bg-blue-100 text-blue-700" : "text-muted-foreground hover:bg-muted"}`}
            >
              全部 ({items.length})
            </button>
            {uniqueTypes.map(type => {
              const config = ITEM_TYPE_CONFIG[type];
              const count = items.filter(i => i.itemType === type).length;
              return (
                <button
                  key={type}
                  onClick={() => setFilterType(type)}
                  className={`text-xs px-2 py-0.5 rounded flex items-center gap-1 ${filterType === type ? "bg-blue-100 text-blue-700" : "text-muted-foreground hover:bg-muted"}`}
                >
                  {config?.icon}
                  {config?.label ?? type} ({count})
                </button>
              );
            })}
          </div>
        )}
      </CardHeader>
      <CardContent>
        {filteredItems.length === 0 ? (
          <EmptyTimeline />
        ) : (
          <div className={compact ? "space-y-2" : "space-y-4"}>
            {filteredItems.map((item) => (
              <TimelineItem key={item.id} item={item} />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
