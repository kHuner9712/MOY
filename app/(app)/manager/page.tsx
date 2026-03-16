"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { FileText, Mail, Users2 } from "lucide-react";

import { useAuth } from "@/components/auth/auth-provider";
import { ManagerDashboard } from "@/components/dashboard/manager-dashboard";
import { SalesDetailTable } from "@/components/manager/sales-detail-table";
import { useAppData } from "@/components/shared/app-data-provider";
import { PageHeader } from "@/components/shared/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useManagerQuality } from "@/hooks/use-manager-quality";
import { useManagerOutcomes } from "@/hooks/use-manager-outcomes";
import { useManagerRhythm } from "@/hooks/use-manager-rhythm";
import { useTouchpoints } from "@/hooks/use-touchpoints";
import { formatDateTime } from "@/lib/format";
import { getManagerDashboardData } from "@/lib/metrics";

function isToday(isoText: string): boolean {
  const date = new Date(isoText);
  const now = new Date();
  return date.getFullYear() === now.getFullYear() && date.getMonth() === now.getMonth() && date.getDate() === now.getDate();
}

export default function ManagerPage(): JSX.Element {
  const { user } = useAuth();
  const { customers, followups, opportunities, alerts, communicationInputs, reports, runAlertScan, loading, error } = useAppData();
  const { data: qualityData } = useManagerQuality("weekly");
  const { data: rhythmData } = useManagerRhythm("daily");
  const { data: outcomeData } = useManagerOutcomes("weekly");
  const { summary: touchpointSummary, hub: touchpointHub } = useTouchpoints({
    enabled: user?.role === "manager"
  });
  const searchParams = useSearchParams();
  const selectedSalesId = searchParams.get("sales");
  const [scanLoading, setScanLoading] = useState(false);
  const [scanMessage, setScanMessage] = useState<string | null>(null);

  const managerData = useMemo(
    () =>
      getManagerDashboardData({
        customers,
        followups,
        opportunities,
        alerts
      }),
    [customers, followups, opportunities, alerts]
  );

  const aiFocusCustomers = useMemo(() => {
    const riskCustomers = customers.filter((item) => item.riskLevel === "high" && item.stage !== "won" && item.stage !== "lost");
    const byAlert = new Set(alerts.filter((item) => item.source !== "rule" && item.status !== "resolved").map((item) => item.customerId));
    return riskCustomers.filter((item) => byAlert.has(item.id) || item.aiRiskJudgement.includes("风险")).slice(0, 8);
  }, [customers, alerts]);

  const todayInputCount = communicationInputs.filter((item) => isToday(item.createdAt)).length;
  const pendingDraftCount = followups.filter((item) => item.draftStatus === "draft").length;
  const recentTeamReports = reports.filter((item) => item.reportType.startsWith("manager_")).slice(0, 5);

  if (loading) {
    return <div className="text-sm text-muted-foreground">Loading manager workspace...</div>;
  }

  if (error) {
    return <div className="text-sm text-rose-600">Manager workspace load failed: {error}</div>;
  }

  if (user?.role !== "manager") {
    return (
      <div className="rounded-xl border bg-white p-8 text-center">
        <p className="text-sm text-muted-foreground">Only manager role can access this page.</p>
      </div>
    );
  }

  const selectedSalesName = customers.find((item) => item.ownerId === selectedSalesId)?.ownerName;
  const selectedSalesCustomers = selectedSalesId ? customers.filter((item) => item.ownerId === selectedSalesId) : [];

  const runScan = async (): Promise<void> => {
    setScanLoading(true);
    setScanMessage(null);
    try {
      const result = await runAlertScan();
      setScanMessage(`Scan completed: +${result.createdAlertCount} new, ${result.dedupedAlertCount} deduped, ${result.resolvedAlertCount} resolved.`);
    } catch (cause) {
      setScanMessage(cause instanceof Error ? cause.message : "Leak scan failed");
    } finally {
      setScanLoading(false);
    }
  };

  return (
    <div>
      <PageHeader
        title="Manager 工作台"
        description="聚焦团队真实推进：沟通输入活跃、草稿确认、风险扫描与团队报告。"
        action={
          <div className="flex items-center gap-2">
            <Button onClick={() => void runScan()} disabled={scanLoading}>
              {scanLoading ? "扫描中..." : "运行漏单扫描"}
            </Button>
            <Button asChild variant="outline">
              <Link href="/reports">团队报告中心</Link>
            </Button>
            <Button asChild variant="outline">
              <Link href="/manager/quality">经营质量视图</Link>
            </Button>
            <Button asChild variant="outline">
              <Link href="/manager/rhythm">执行节奏视图</Link>
            </Button>
            <Button asChild variant="outline">
              <Link href="/manager/outcomes">结果洞察视图</Link>
            </Button>
            <Button asChild variant="outline">
              <Link href="/manager/conversion">商业化转化视图</Link>
            </Button>
            <Button asChild variant="outline">
              <Link href="/executive">经营驾驶舱</Link>
            </Button>
            <Button asChild variant="outline">
              <Link href="/growth">Growth 漏斗工作区</Link>
            </Button>
            <Button asChild variant="outline">
              <Link href="/deals">Deal Command View</Link>
            </Button>
            <Button asChild variant="outline">
              <Link href="/touchpoints">
                <Mail className="mr-1 h-4 w-4" />
                Touchpoints
              </Link>
            </Button>
          </div>
        }
      />

      {scanMessage ? <p className="mb-3 text-sm text-muted-foreground">{scanMessage}</p> : null}

      <section className="mb-4 grid gap-4 xl:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>今日团队输入</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-3xl font-semibold text-slate-900">{todayInputCount}</p>
            <p className="mt-1 text-xs text-muted-foreground">反映一线真实沟通活跃度</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>团队待确认草稿</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-3xl font-semibold text-slate-900">{pendingDraftCount}</p>
            <p className="mt-1 text-xs text-muted-foreground">草稿过多通常意味着推进未闭环</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle>团队报告快捷入口</CardTitle>
            <FileText className="h-4 w-4 text-slate-500" />
          </CardHeader>
          <CardContent className="space-y-2">
            <Button asChild className="w-full justify-start" size="sm">
              <Link href="/reports">生成团队日报</Link>
            </Button>
            <Button asChild className="w-full justify-start" size="sm" variant="outline">
              <Link href="/reports">生成团队周报</Link>
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>团队执行节奏摘要</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1">
            <p className="text-2xl font-semibold text-slate-900">{rhythmData?.teamTotals.totalTasks ?? 0}</p>
            <p className="text-xs text-muted-foreground">今日任务总数</p>
            <p className="text-xs text-muted-foreground">超期：{rhythmData?.teamTotals.overdueTasks ?? 0}</p>
            <p className="text-xs text-muted-foreground">关键未完成：{rhythmData?.teamTotals.criticalOpenTasks ?? 0}</p>
            <Button asChild size="sm" variant="outline" className="mt-2 w-full justify-start">
              <Link href="/manager/rhythm">打开执行节奏视图</Link>
            </Button>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>结果闭环摘要</CardTitle>
            </CardHeader>
            <CardContent className="space-y-1">
              <p className="text-2xl font-semibold text-slate-900">{outcomeData?.summary.totalOutcomes ?? 0}</p>
              <p className="text-xs text-muted-foreground">周期内 outcome 数</p>
              <p className="text-xs text-muted-foreground">正向推进率：{(((outcomeData?.summary.positiveProgressRate ?? 0) * 100)).toFixed(0)}%</p>
              <p className="text-xs text-muted-foreground">建议采用率：{(((outcomeData?.summary.adoptionRate ?? 0) * 100)).toFixed(0)}%</p>
              <Button asChild size="sm" variant="outline" className="mt-2 w-full justify-start">
                <Link href="/manager/outcomes">打开结果洞察</Link>
              </Button>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>外部触点推进摘要</CardTitle>
            </CardHeader>
            <CardContent className="space-y-1">
              <p className="text-2xl font-semibold text-slate-900">{touchpointSummary.totalEvents}</p>
              <p className="text-xs text-muted-foreground">近 7 日外部触点事件</p>
              <p className="text-xs text-muted-foreground">待客户回复：{touchpointSummary.waitingReplyThreads}</p>
              <p className="text-xs text-muted-foreground">即将会议：{touchpointSummary.upcomingMeetings}</p>
              <p className="text-xs text-muted-foreground">文档更新：{touchpointSummary.documentUpdates}</p>
              <Button asChild size="sm" variant="outline" className="mt-2 w-full justify-start">
                <Link href="/touchpoints">打开触点中心</Link>
              </Button>
            </CardContent>
          </Card>
      </section>

      <section className="mb-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle>按销售查看客户明细</CardTitle>
            <Users2 className="h-4 w-4 text-slate-500" />
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            {Array.from(new Map(customers.map((item) => [item.ownerId, item.ownerName])).entries()).map(([id, name]) => (
              <Button key={id} size="sm" variant={selectedSalesId === id ? "default" : "outline"} asChild>
                <Link href={`/manager?sales=${id}`}>{name}</Link>
              </Button>
            ))}
          </CardContent>
        </Card>
      </section>

      <section className="mb-4">
        <ManagerDashboard
          salesWorkload={managerData.salesWorkload}
          opportunityStageDist={managerData.opportunityStageDist}
          highRiskCustomers={managerData.highRiskCustomers}
          longTimeNoFollowups={managerData.longTimeNoFollowups}
          highRiskOpportunities={managerData.highRiskOpportunities}
        />
      </section>

      <section className="mb-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>最近团队报告</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {recentTeamReports.length === 0 ? <p className="text-sm text-muted-foreground">暂无团队报告。</p> : null}
            {recentTeamReports.map((item) => (
              <div key={item.id} className="rounded-lg border p-3">
                <p className="text-sm font-semibold text-slate-900">{item.title}</p>
                <p className="mt-1 text-xs text-muted-foreground">{item.periodStart} ~ {item.periodEnd}</p>
                <p className="mt-1 text-xs text-muted-foreground">{formatDateTime(item.createdAt)}</p>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>AI 关注客户</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-2 md:grid-cols-2">
            {aiFocusCustomers.length === 0 ? (
              <p className="text-sm text-muted-foreground">当前暂无 AI 重点关注客户。</p>
            ) : (
              aiFocusCustomers.map((item) => (
                <Link key={item.id} href={`/customers/${item.id}`} className="rounded-lg border bg-slate-50 p-3 text-sm hover:bg-slate-100">
                  <p className="font-semibold text-slate-900">{item.companyName}</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {item.ownerName} | 概率 {item.winProbability}% | 风险 {item.riskLevel}
                  </p>
                </Link>
              ))
            )}
          </CardContent>
        </Card>
      </section>

      <section className="mb-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>可复制打法</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {(qualityData?.aiInsight.replicable_patterns ?? []).slice(0, 4).map((item) => (
              <p key={item} className="text-sm text-slate-700">
                - {item}
              </p>
            ))}
            {!qualityData?.aiInsight.replicable_patterns?.length ? <p className="text-sm text-muted-foreground">暂无可复制打法洞察。</p> : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>需要辅导的销售</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {(qualityData?.aiInsight.needs_coaching ?? []).slice(0, 4).map((item) => (
              <div key={item.user_id} className="rounded-lg border p-2">
                <p className="text-sm font-semibold text-slate-900">{item.user_name}</p>
                <p className="text-xs text-slate-700">{item.reason}</p>
              </div>
            ))}
            {!qualityData?.aiInsight.needs_coaching?.length ? <p className="text-sm text-muted-foreground">当前暂无重点辅导对象。</p> : null}
          </CardContent>
        </Card>
      </section>

      <section className="mb-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>关键会议后未形成下一步</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {touchpointHub.calendarEvents
              .filter((item) => item.meetingStatus === "completed" && !item.notesSummary)
              .slice(0, 6)
              .map((item) => (
                <p key={item.id} className="text-sm text-slate-700">
                  - {item.title} | {item.customerName ?? "Unknown customer"}
                </p>
              ))}
            {touchpointHub.calendarEvents.filter((item) => item.meetingStatus === "completed" && !item.notesSummary).length === 0 ? (
              <p className="text-sm text-muted-foreground">暂无“会后无下一步”风险。</p>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>报价/合同文档待跟进</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {touchpointHub.documentAssets
              .filter((item) => item.documentType === "quote" || item.documentType === "contract_draft")
              .slice(0, 6)
              .map((item) => (
                <p key={item.id} className="text-sm text-slate-700">
                  - {item.title} ({item.documentType}) | {item.customerName ?? "Unknown customer"}
                </p>
              ))}
            {touchpointHub.documentAssets.filter((item) => item.documentType === "quote" || item.documentType === "contract_draft").length === 0 ? (
              <p className="text-sm text-muted-foreground">暂无报价/合同文档待跟进项。</p>
            ) : null}
          </CardContent>
        </Card>
      </section>

      {selectedSalesId && selectedSalesName ? (
        <SalesDetailTable salesName={selectedSalesName} customers={selectedSalesCustomers} />
      ) : (
        <Card>
          <CardContent className="p-6 text-sm text-muted-foreground">选择上方销售成员查看其客户明细。</CardContent>
        </Card>
      )}
    </div>
  );
}
