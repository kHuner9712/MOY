/**
 * v1.3 Manager Insights Summary Component
 * 轻量介入效果 + Truth Band 分布 + 风险改善
 */

import Link from "next/link";
import { BarChart3, TrendingDown, CheckCircle, XCircle } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useManagerInsights } from "@/hooks/use-manager-insights";
import { TRUTH_BAND_LABELS } from "@/types/manager-desk";

const BAND_COLORS: Record<string, string> = {
  healthy: "bg-emerald-100 text-emerald-700",
  watch: "bg-amber-100 text-amber-700",
  suspicious: "bg-orange-100 text-orange-700",
  stalled: "bg-red-100 text-red-700"
};

const BAND_BAR_COLORS: Record<string, string> = {
  healthy: "bg-emerald-500",
  watch: "bg-amber-500",
  suspicious: "bg-orange-500",
  stalled: "bg-red-500"
};

export function ManagerInsightsSummary() {
  const { data, loading, error } = useManagerInsights(7);

  if (loading) {
    return (
      <Card>
        <CardHeader><CardTitle className="text-base">介入效果分析</CardTitle></CardHeader>
        <CardContent><p className="text-sm text-muted-foreground">加载中...</p></CardContent>
      </Card>
    );
  }

  if (error || !data) {
    return (
      <Card>
        <CardHeader><CardTitle className="text-base">介入效果分析</CardTitle></CardHeader>
        <CardContent><p className="text-sm text-muted-foreground">暂无可用数据</p></CardContent>
      </Card>
    );
  }

  const { truthBandDistribution, interventionAnalytics, riskImprovement, newRiskSignals } = data;
  const totalRooms = truthBandDistribution.reduce((sum, b) => sum + b.count, 0);

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-base">介入效果分析</CardTitle>
        <div className="flex gap-2">
          {newRiskSignals.newBlockedCount > 0 && (
            <Badge variant="destructive" className="text-xs">
              +{newRiskSignals.newBlockedCount} 新阻塞
            </Badge>
          )}
          {newRiskSignals.newCriticalCount > 0 && (
            <Badge variant="destructive" className="text-xs">
              +{newRiskSignals.newCriticalCount} 新危机
            </Badge>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div>
          <p className="text-xs text-muted-foreground mb-1">Pipeline 健康度分布（近7日）</p>
          <div className="flex h-2 rounded-full overflow-hidden bg-slate-100">
            {truthBandDistribution.map((band) => (
              <div
                key={band.band}
                className={BAND_BAR_COLORS[band.band]}
                style={{ width: band.percentage > 0 ? `${band.percentage * 100}%` : "2px" }}
                title={`${TRUTH_BAND_LABELS[band.band]}: ${band.count}`}
              />
            ))}
          </div>
          <div className="flex gap-3 mt-1 flex-wrap">
            {truthBandDistribution.map((band) => (
              <span key={band.band} className="flex items-center gap-1 text-xs">
                <span className={`inline-block w-2 h-2 rounded-full ${BAND_BAR_COLORS[band.band]}`} />
                {TRUTH_BAND_LABELS[band.band]} {band.count}
              </span>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-3 gap-2">
          <div className="text-center rounded bg-slate-50 p-2">
            <p className="text-lg font-semibold">{interventionAnalytics.totalCreated}</p>
            <p className="text-xs text-muted-foreground">创建任务</p>
          </div>
          <div className="text-center rounded bg-slate-50 p-2">
            <p className="text-lg font-semibold text-emerald-600">
              {interventionAnalytics.totalCompleted}
            </p>
            <p className="text-xs text-muted-foreground">已完成</p>
          </div>
          <div className="text-center rounded bg-slate-50 p-2">
            <p className="text-lg font-semibold text-slate-400">
              {interventionAnalytics.totalDismissed}
            </p>
            <p className="text-xs text-muted-foreground">已忽略</p>
          </div>
        </div>

        {interventionAnalytics.totalCreated > 0 && (
          <div className="flex gap-4 text-xs">
            <span className="flex items-center gap-1">
              <CheckCircle className="h-3 w-3 text-emerald-500" />
              完成率 {((interventionAnalytics.completionRate) * 100).toFixed(0)}%
            </span>
            <span className="flex items-center gap-1">
              <XCircle className="h-3 w-3 text-slate-400" />
              忽略率 {((interventionAnalytics.dismissRate) * 100).toFixed(0)}%
            </span>
          </div>
        )}

        {interventionAnalytics.byRiskReason.length > 0 && (
          <div>
            <p className="text-xs text-muted-foreground mb-1">高频风险原因（近7日）</p>
            <div className="space-y-1">
              {interventionAnalytics.byRiskReason.slice(0, 3).map((r) => (
                <div key={r.reason} className="flex justify-between items-center text-xs">
                  <span className="text-slate-600 truncate flex-1 mr-2">{r.reason}</span>
                  <Badge variant="outline" className="text-xs ml-auto">{r.count}</Badge>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="pt-1 border-t">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1 text-xs text-muted-foreground">
              <TrendingDown className="h-3 w-3" />
              <span>风险改善率</span>
              {riskImprovement.improvementRate > 0 && (
                <span className="text-emerald-600 font-medium">
                  {((riskImprovement.improvementRate) * 100).toFixed(0)}%
                </span>
              )}
              {riskImprovement.improvementRate === 0 && (
                <span>暂无数据</span>
              )}
            </div>
            <Button asChild size="sm" variant="ghost" className="h-6 text-xs">
              <Link href="/manager/outcomes">详情</Link>
            </Button>
          </div>
          <p className="text-xs text-muted-foreground mt-1">{riskImprovement.notes}</p>
        </div>
      </CardContent>
    </Card>
  );
}
