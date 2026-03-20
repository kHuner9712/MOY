/**
 * v1.5 Manager Insights Trends Component
 * 轻量趋势展示，支持 weekly/monthly 切换 + backfill
 */

import { useState } from "react";
import { TrendingUp, TrendingDown, Minus, AlertTriangle, History } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useManagerInsightsTrends } from "@/hooks/use-manager-insights-trends";
import type { TruthBand } from "@/types/manager-desk";
import { TRUTH_BAND_LABELS } from "@/types/manager-desk";
import type { SnapshotType } from "@/types/manager-insights-snapshot";

const BAND_COLORS: Record<TruthBand, string> = {
  healthy: "text-emerald-600",
  watch: "text-amber-600",
  suspicious: "text-orange-600",
  stalled: "text-red-600",
};

function TrendArrow({ direction, band }: { direction: "up" | "down" | "stable"; band: TruthBand }) {
  if (direction === "stable") {
    return <Minus className="h-3 w-3 text-slate-400" />;
  }
  const isPositive = band === "healthy" ? direction === "up" : band === "stalled" || band === "suspicious" ? direction === "down" : direction === "up";
  return isPositive ? (
    <TrendingUp className="h-3 w-3 text-emerald-500" />
  ) : (
    <TrendingDown className="h-3 w-3 text-red-500" />
  );
}

function SignalQualityBadge({ quality, note }: { quality: string; note: string }) {
  if (quality === "sufficient") {
    return <Badge className="bg-emerald-100 text-emerald-700 text-xs">数据充足</Badge>;
  }
  if (quality === "early") {
    return <Badge className="bg-amber-100 text-amber-700 text-xs">早期信号</Badge>;
  }
  return <Badge variant="outline" className="text-xs">数据不足</Badge>;
}

function EarlySignalTag() {
  return (
    <span className="text-xs text-amber-600 bg-amber-50 px-1.5 py-0.5 rounded border border-amber-200">
      早期
    </span>
  );
}

export function ManagerInsightsTrends() {
  const [snapshotType, setSnapshotType] = useState<SnapshotType>("weekly");
  const { data, loading, error, generateSnapshot, generating, backfillSnapshots, backfilling } = useManagerInsightsTrends(snapshotType, 8);

  if (loading) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">趋势分析</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">加载中...</p>
        </CardContent>
      </Card>
    );
  }

  if (error || !data) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">趋势分析</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">暂无可用数据</p>
        </CardContent>
      </Card>
    );
  }

  const { truthBandTrends, interventionTrends, improvementTrend, signalQuality, signalQualityNote, snapshots } = data;

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <div className="flex items-center gap-2">
          <CardTitle className="text-base">趋势分析</CardTitle>
          <div className="flex items-center rounded border">
            <button
              className={`px-2 py-0.5 text-xs rounded-l ${snapshotType === "weekly" ? "bg-slate-100 font-medium" : "text-muted-foreground hover:bg-slate-50"}`}
              onClick={() => setSnapshotType("weekly")}
            >
              周
            </button>
            <button
              className={`px-2 py-0.5 text-xs rounded-r ${snapshotType === "monthly" ? "bg-slate-100 font-medium" : "text-muted-foreground hover:bg-slate-50"}`}
              onClick={() => setSnapshotType("monthly")}
            >
              月
            </button>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <SignalQualityBadge quality={signalQuality} note={signalQualityNote} />
          {signalQuality !== "sufficient" && (
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              disabled={backfilling}
              onClick={() => void backfillSnapshots(snapshotType === "weekly" ? 8 : 6)}
            >
              <History className="h-3 w-3 mr-1" />
              {backfilling ? "回填中..." : "回填历史"}
            </Button>
          )}
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-xs"
            disabled={generating}
            onClick={() => void generateSnapshot(snapshotType === "monthly" ? 30 : 7)}
          >
            {generating ? "生成中..." : "生成快照"}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {signalQuality === "insufficient" && (
          <div className="flex items-start gap-2 p-2 rounded bg-amber-50 border border-amber-200">
            <AlertTriangle className="h-4 w-4 text-amber-500 mt-0.5 flex-shrink-0" />
            <p className="text-xs text-amber-700">{signalQualityNote}</p>
          </div>
        )}

        <div>
          <div className="flex items-center gap-2 mb-2">
            <p className="text-xs text-muted-foreground">Truth Band 变化（最近两期对比）</p>
            {truthBandTrends[0]?.isEarlySignal && <EarlySignalTag />}
          </div>
          <div className="space-y-1">
            {truthBandTrends.map((trend) => (
              <div key={trend.band} className="flex items-center justify-between text-xs">
                <span className={`flex items-center gap-1 ${BAND_COLORS[trend.band]}`}>
                  <TrendArrow direction={trend.direction} band={trend.band} />
                  {trend.label}
                </span>
                <div className="flex items-center gap-2 text-muted-foreground">
                  <span>
                    {trend.changeCount >= 0 ? "+" : ""}{trend.changeCount} ({trend.changePercentage.toFixed(0)}%)
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="pt-2 border-t">
          <div className="flex items-center gap-2 mb-2">
            <p className="text-xs text-muted-foreground">介入效果趋势</p>
            {interventionTrends.isEarlySignal && <EarlySignalTag />}
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div className="flex items-center justify-between text-xs">
              <span className="text-muted-foreground">任务创建</span>
              <div className="flex items-center gap-1">
                {interventionTrends.createdTrend === "up" && <TrendingUp className="h-3 w-3 text-emerald-500" />}
                {interventionTrends.createdTrend === "down" && <TrendingDown className="h-3 w-3 text-red-500" />}
                {interventionTrends.createdTrend === "stable" && <Minus className="h-3 w-3 text-slate-400" />}
                <span>{interventionTrends.createdTrend}</span>
              </div>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-muted-foreground">任务完成</span>
              <div className="flex items-center gap-1">
                {interventionTrends.completedTrend === "up" && <TrendingUp className="h-3 w-3 text-emerald-500" />}
                {interventionTrends.completedTrend === "down" && <TrendingDown className="h-3 w-3 text-red-500" />}
                {interventionTrends.completedTrend === "stable" && <Minus className="h-3 w-3 text-slate-400" />}
                <span>{interventionTrends.completedTrend}</span>
              </div>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-muted-foreground">完成率趋势</span>
              <div className="flex items-center gap-1">
                {interventionTrends.completionRateTrend === "up" && <TrendingUp className="h-3 w-3 text-emerald-500" />}
                {interventionTrends.completionRateTrend === "down" && <TrendingDown className="h-3 w-3 text-red-500" />}
                {interventionTrends.completionRateTrend === "stable" && <Minus className="h-3 w-3 text-slate-400" />}
                <span>{interventionTrends.completionRateTrend}</span>
              </div>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-muted-foreground">忽略率趋势</span>
              <div className="flex items-center gap-1">
                {interventionTrends.dismissRateTrend === "up" && <TrendingDown className="h-3 w-3 text-red-500" />}
                {interventionTrends.dismissRateTrend === "down" && <TrendingUp className="h-3 w-3 text-emerald-500" />}
                {interventionTrends.dismissRateTrend === "stable" && <Minus className="h-3 w-3 text-slate-400" />}
                <span>{interventionTrends.dismissRateTrend}</span>
              </div>
            </div>
          </div>
        </div>

        <div className="pt-2 border-t">
          <div className="flex items-center justify-between text-xs">
            <div className="flex items-center gap-2">
              <span className="text-muted-foreground">风险改善率趋势</span>
              {improvementTrend.isEarlySignal && <EarlySignalTag />}
            </div>
            <div className="flex items-center gap-1">
              {improvementTrend.direction === "up" && <TrendingUp className="h-3 w-3 text-emerald-500" />}
              {improvementTrend.direction === "down" && <TrendingDown className="h-3 w-3 text-red-500" />}
              {improvementTrend.direction === "stable" && <Minus className="h-3 w-3 text-slate-400" />}
              <span className={improvementTrend.direction === "up" ? "text-emerald-600" : improvementTrend.direction === "down" ? "text-red-600" : "text-muted-foreground"}>
                {improvementTrend.changeRate >= 0 ? "+" : ""}{improvementTrend.changeRate.toFixed(1)}%
              </span>
            </div>
          </div>
          <p className="text-xs text-muted-foreground mt-1">风险改善率为近似口径，非因果归因</p>
        </div>
      </CardContent>
    </Card>
  );
}
