"use client";

import { useMemo, useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { useAppData } from "@/components/shared/app-data-provider";
import { PageHeader } from "@/components/shared/page-header";
import { useCoachingReports } from "@/hooks/use-coaching-reports";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { formatDateTime } from "@/lib/format";
import { canViewManagerWorkspace } from "@/lib/role-capability";
import type { GeneratedReport, ReportType } from "@/types/report";

function todayDateString(): string {
  return new Date().toISOString().slice(0, 10);
}

function sevenDaysAgoString(): string {
  const date = new Date();
  date.setDate(date.getDate() - 6);
  return date.toISOString().slice(0, 10);
}

const reportLabels: Record<ReportType, string> = {
  sales_daily: "销售日报",
  sales_weekly: "销售周报",
  manager_daily: "团队日报",
  manager_weekly: "团队周报"
};

export default function ReportsPage(): JSX.Element {
  const { user } = useAuth();
  const canViewTeamReports = canViewManagerWorkspace(user);
  const { reports, customers, generateReport, getReportById, loading, error } = useAppData();
  const coachingScope = canViewTeamReports ? undefined : ("user" as const);
  const {
    items: coachingReports,
    generate: generateCoachingReport,
    loading: coachingLoading
  } = useCoachingReports({
    scope: coachingScope,
    targetUserId: canViewTeamReports ? undefined : user?.id
  });

  const [reportType, setReportType] = useState<ReportType>(canViewTeamReports ? "manager_daily" : "sales_daily");
  const [periodStart, setPeriodStart] = useState(todayDateString());
  const [periodEnd, setPeriodEnd] = useState(todayDateString());
  const [generating, setGenerating] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [selectedReport, setSelectedReport] = useState<GeneratedReport | null>(null);
  const [coachingGenerating, setCoachingGenerating] = useState(false);
  const [coachingPeriod, setCoachingPeriod] = useState<"weekly" | "monthly">("weekly");
  const [coachingTargetUserId, setCoachingTargetUserId] = useState<string>(user?.id ?? "");

  const availableTypes = canViewTeamReports
    ? ["sales_daily", "sales_weekly", "manager_daily", "manager_weekly"]
    : ["sales_daily", "sales_weekly"];

  const salesOptions = useMemo(() => {
    const unique = new Map<string, string>();
    for (const item of customers) {
      if (!unique.has(item.ownerId)) unique.set(item.ownerId, item.ownerName);
    }
    return Array.from(unique.entries()).map(([id, name]) => ({ id, name }));
  }, [customers]);

  const scopedReports = useMemo(() => {
    if (!user) return [];
    if (canViewTeamReports) return reports;
    return reports.filter((item) => item.targetUserId === user.id || item.generatedBy === user.id);
  }, [canViewTeamReports, reports, user]);

  const filteredReports = useMemo(() => {
    return scopedReports.filter((item) => item.reportType === reportType);
  }, [scopedReports, reportType]);

  const applyPreset = (preset: "today" | "week"): void => {
    if (preset === "today") {
      const today = todayDateString();
      setPeriodStart(today);
      setPeriodEnd(today);
      if (canViewTeamReports) setReportType("manager_daily");
      else setReportType("sales_daily");
      return;
    }

    setPeriodStart(sevenDaysAgoString());
    setPeriodEnd(todayDateString());
    if (canViewTeamReports) setReportType("manager_weekly");
    else setReportType("sales_weekly");
  };

  const handleGenerate = async (): Promise<void> => {
    setGenerating(true);
    setMessage(null);
    try {
      const generated = await generateReport({
        reportType,
        periodStart,
        periodEnd,
        scopeType: canViewTeamReports ? "team" : "self",
        targetUserId: canViewTeamReports ? null : user?.id
      });
      setSelectedReport(generated);
      setMessage("报告已生成。你可以在右侧查看详情。");
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "生成报告失败。");
    } finally {
      setGenerating(false);
    }
  };

  const openReport = async (report: GeneratedReport): Promise<void> => {
    try {
      const full = await getReportById(report.id);
      setSelectedReport(full);
    } catch {
      setSelectedReport(report);
    }
  };

  const handleGenerateCoaching = async (): Promise<void> => {
    setCoachingGenerating(true);
    setMessage(null);
    try {
      const generated = await generateCoachingReport({
        scope: canViewTeamReports ? "team" : "user",
        periodType: coachingPeriod,
        targetUserId: canViewTeamReports ? coachingTargetUserId : user?.id
      });
      setMessage(`辅导报告已生成：${generated.title}`);
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "生成辅导报告失败。");
    } finally {
      setCoachingGenerating(false);
    }
  };

  if (loading) return <div className="text-sm text-muted-foreground">Loading reports...</div>;
  if (error) return <div className="text-sm text-rose-600">Failed to load reports: {error}</div>;

  return (
    <div>
      <PageHeader title="日报 / 周报" description="销售和管理者都可以基于真实业务数据一键生成可审计报告。" />

      <div className="mb-4 grid gap-4 xl:grid-cols-[1.1fr_1.9fr]">
        <Card>
          <CardHeader>
            <CardTitle>生成报告</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="outline" onClick={() => applyPreset("today")}>今日模板</Button>
              <Button size="sm" variant="outline" onClick={() => applyPreset("week")}>本周模板</Button>
            </div>

            <div>
              <Label>报告类型</Label>
              <Select value={reportType} onValueChange={(value: ReportType) => setReportType(value)}>
                <SelectTrigger className="mt-1">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {availableTypes.map((item) => (
                    <SelectItem key={item} value={item}>
                      {reportLabels[item as ReportType]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <Label>开始日期</Label>
                <Input className="mt-1" type="date" value={periodStart} onChange={(event) => setPeriodStart(event.target.value)} />
              </div>
              <div>
                <Label>结束日期</Label>
                <Input className="mt-1" type="date" value={periodEnd} onChange={(event) => setPeriodEnd(event.target.value)} />
              </div>
            </div>

            <Button onClick={() => void handleGenerate()} disabled={generating}>
              {generating ? "生成中..." : "生成报告"}
            </Button>

            {message ? <p className="text-sm text-muted-foreground">{message}</p> : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>报告列表</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {filteredReports.length === 0 ? <p className="text-sm text-muted-foreground">当前筛选下暂无报告。</p> : null}
            {filteredReports.map((report) => (
              <button
                key={report.id}
                type="button"
                onClick={() => void openReport(report)}
                className="w-full rounded-lg border p-3 text-left hover:bg-slate-50"
              >
                <div className="mb-1 flex items-center justify-between gap-2">
                  <p className="text-sm font-semibold text-slate-900">{report.title}</p>
                  <Badge variant={report.status === "completed" ? "default" : report.status === "failed" ? "destructive" : "secondary"}>
                    {report.status}
                  </Badge>
                </div>
                <p className="text-xs text-muted-foreground">
                  {reportLabels[report.reportType]} | {report.periodStart} ~ {report.periodEnd}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">{formatDateTime(report.createdAt)}</p>
              </button>
            ))}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>报告详情</CardTitle>
        </CardHeader>
        <CardContent>
          {!selectedReport ? <p className="text-sm text-muted-foreground">请选择一份报告查看详情。</p> : null}
          {selectedReport ? (
            <div className="space-y-3">
              <div>
                <p className="text-lg font-semibold text-slate-900">{selectedReport.title}</p>
                <p className="text-sm text-muted-foreground">{selectedReport.summary}</p>
              </div>

              <div className="rounded-lg border bg-slate-50 p-3">
                <p className="mb-2 text-sm font-semibold text-slate-900">指标概览</p>
                <pre className="whitespace-pre-wrap text-xs text-slate-700">{JSON.stringify(selectedReport.metricsSnapshot, null, 2)}</pre>
              </div>

              <div className="rounded-lg border p-3">
                <p className="mb-2 text-sm font-semibold text-slate-900">Markdown 内容</p>
                <pre className="whitespace-pre-wrap text-sm leading-6 text-slate-700">{selectedReport.contentMarkdown}</pre>
              </div>
            </div>
          ) : null}
        </CardContent>
      </Card>

      <div className="mt-4 grid gap-4 xl:grid-cols-[1.1fr_1.9fr]">
        <Card>
          <CardHeader>
            <CardTitle>辅导报告</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-center gap-2">
              <Button size="sm" variant={coachingPeriod === "weekly" ? "default" : "outline"} onClick={() => setCoachingPeriod("weekly")}>
                周
              </Button>
              <Button size="sm" variant={coachingPeriod === "monthly" ? "default" : "outline"} onClick={() => setCoachingPeriod("monthly")}>
                月
              </Button>
            </div>

            {canViewTeamReports ? (
              <div>
                <Label>个人辅导目标（可选）</Label>
                <Select value={coachingTargetUserId} onValueChange={setCoachingTargetUserId}>
                  <SelectTrigger className="mt-1">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {salesOptions.map((item) => (
                      <SelectItem key={item.id} value={item.id}>
                        {item.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            ) : null}

            <Button onClick={() => void handleGenerateCoaching()} disabled={coachingGenerating}>
              {coachingGenerating ? "生成中..." : canViewTeamReports ? "生成团队辅导报告" : "生成我的辅导报告"}
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>最近辅导报告</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {coachingLoading ? <p className="text-sm text-muted-foreground">加载中...</p> : null}
            {!coachingLoading && coachingReports.length === 0 ? <p className="text-sm text-muted-foreground">暂无辅导报告。</p> : null}
            {coachingReports.slice(0, 8).map((item) => (
              <div key={item.id} className="rounded-lg border p-3">
                <p className="text-sm font-semibold text-slate-900">{item.title}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {item.reportScope} | {item.periodStart} ~ {item.periodEnd}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">{item.executiveSummary}</p>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
