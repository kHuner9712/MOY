"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { ValueMetricsSummary, TrendResult, KeyNumberItem } from "@/types/value-metrics";
import { ArrowDown, ArrowUp, Minus, TrendingUp, TrendingDown, Activity } from "lucide-react";

interface ValueOverviewBlockProps {
  summary: ValueMetricsSummary;
  trend: TrendResult;
  loading?: boolean;
}

function TrendIcon({ direction }: { direction: TrendResult["direction"] }) {
  if (direction === "up") return <TrendingUp className="h-4 w-4 text-emerald-600" />;
  if (direction === "down") return <TrendingDown className="h-4 w-4 text-rose-600" />;
  return <Minus className="h-4 w-4 text-slate-400" />;
}

function KeyNumberCard({ item }: { item: KeyNumberItem }) {
  const trendColor =
    item.trend === "up"
      ? "text-emerald-600"
      : item.trend === "down"
        ? "text-rose-600"
        : "text-slate-500";

  return (
    <div className="rounded-lg border bg-slate-50/50 p-3">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-xs text-slate-500">{item.label}</span>
        {item.trend === "up" ? (
          <ArrowUp className="h-3 w-3 text-emerald-500" />
        ) : item.trend === "down" ? (
          <ArrowDown className="h-3 w-3 text-rose-500" />
        ) : (
          <Minus className="h-3 w-3 text-slate-400" />
        )}
      </div>
      <div className={`text-xl font-bold ${trendColor}`}>{item.value}</div>
      {item.change !== 0 && (
        <div className="mt-1 text-xs text-slate-400">
          {item.change > 0 ? "+" : ""}
          {item.change} vs 上周
        </div>
      )}
    </div>
  );
}

export function ValueOverviewBlock({ summary, trend, loading }: ValueOverviewBlockProps) {
  if (loading) {
    return (
      <Card className="border-indigo-100 bg-gradient-to-br from-indigo-50/30 to-white">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Activity className="h-5 w-5 text-indigo-600" />
            本周价值总览
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-sm text-muted-foreground">正在加载价值数据...</div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="border-indigo-100 bg-gradient-to-br from-indigo-50/30 to-white">
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-base">
            <Activity className="h-5 w-5 text-indigo-600" />
            本周价值总览
          </CardTitle>
          <div className="flex items-center gap-1.5">
            <TrendIcon direction={trend.direction} />
            <span
              className={
                trend.direction === "up"
                  ? "text-sm font-medium text-emerald-600"
                  : trend.direction === "down"
                    ? "text-sm font-medium text-rose-600"
                    : "text-sm text-slate-500"
              }
            >
              {trend.description}
            </span>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="rounded-lg border border-indigo-100 bg-white p-3">
          <p className="text-sm font-medium text-slate-800">{summary.headline}</p>
        </div>

        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
          {summary.keyNumbers.map((item, index) => (
            <KeyNumberCard key={index} item={item} />
          ))}
        </div>

        {summary.highlights.length > 0 && (
          <div className="space-y-1.5">
            <p className="text-xs font-medium uppercase tracking-wide text-slate-500">关键成果</p>
            <ul className="space-y-1">
              {summary.highlights.slice(0, 4).map((highlight, index) => (
                <li key={index} className="flex items-start gap-2 text-sm text-slate-700">
                  <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-indigo-400" />
                  {highlight}
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="rounded-lg border border-amber-100 bg-amber-50/50 p-3">
          <p className="text-xs font-medium text-amber-800">建议下一步</p>
          <p className="mt-1 text-sm text-amber-900">{summary.recommendation}</p>
        </div>
      </CardContent>
    </Card>
  );
}

interface ValueMetricsCompactProps {
  summary: ValueMetricsSummary;
  trend: TrendResult;
}

export function ValueMetricsCompact({ summary, trend }: ValueMetricsCompactProps) {
  return (
    <div className="rounded-lg border border-indigo-100 bg-indigo-50/30 p-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs font-medium text-indigo-700">本周价值</span>
        <Badge
          variant={trend.direction === "up" ? "default" : trend.direction === "down" ? "destructive" : "secondary"}
          className="text-xs"
        >
          {trend.direction === "up" ? "提升" : trend.direction === "down" ? "下降" : "持平"}
        </Badge>
      </div>
      <p className="text-sm text-slate-700">{summary.headline}</p>
      <div className="mt-2 flex flex-wrap gap-2">
        {summary.keyNumbers.slice(0, 3).map((item, index) => (
          <div key={index} className="rounded bg-white px-2 py-1 text-xs">
            <span className="text-slate-500">{item.label}:</span>{" "}
            <span className="font-medium text-slate-800">{item.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
