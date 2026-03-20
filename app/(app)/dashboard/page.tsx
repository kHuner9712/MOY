"use client";

import Link from "next/link";
import { useMemo } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { ManagerDashboard } from "@/components/dashboard/manager-dashboard";
import { SalesDashboard } from "@/components/dashboard/sales-dashboard";
import { NewCustomerOnboardingBanner } from "@/components/onboarding/new-customer-onboarding-banner";
import { FirstValueSummaryBlock } from "@/components/onboarding/first-value-summary-block";
import { IndustryTemplateBanner } from "@/components/shared/industry-template-banner";
import { useAppData } from "@/components/shared/app-data-provider";
import { PageHeader } from "@/components/shared/page-header";
import { StatCard } from "@/components/shared/stat-card";
import { useUserMemory } from "@/hooks/use-user-memory";
import { useTodayPlan } from "@/hooks/use-today-plan";
import { useManagerRhythm } from "@/hooks/use-manager-rhythm";
import { useManagerOutcomes } from "@/hooks/use-manager-outcomes";
import { useOutcomes } from "@/hooks/use-outcomes";
import { useBriefings } from "@/hooks/use-briefings";
import { useTouchpoints } from "@/hooks/use-touchpoints";
import { useOrgProductizationSummary } from "@/hooks/use-org-productization-summary";
import { useImportJobs } from "@/hooks/use-import-jobs";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDateTime } from "@/lib/format";
import { getManagerDashboardData, getSalesDashboardData } from "@/lib/metrics";
import { Activity, AlertTriangle, BookText, CalendarCheck2, CircleCheck, FileText, Mail, PenSquare, Sparkles, TrendingUp, UserPlus, Users } from "lucide-react";

function isToday(isoText: string): boolean {
  const date = new Date(isoText);
  const now = new Date();
  return date.getFullYear() === now.getFullYear() && date.getMonth() === now.getMonth() && date.getDate() === now.getDate();
}

export default function DashboardPage(): JSX.Element {
  const { user } = useAuth();
  const { customers, followups, opportunities, alerts, communicationInputs, reports, loading, error } = useAppData();
  const { profile: memoryProfile, items: memoryItems } = useUserMemory(user?.id);
  const { data: personalOutcomes } = useOutcomes({
    ownerId: user?.id,
    limit: 40
  });
  const { planView: todayPlan } = useTodayPlan();
  const { data: rhythmData } = useManagerRhythm("daily", user?.role === "manager");
  const { data: managerOutcomeData } = useManagerOutcomes("weekly", user?.role === "manager");
  const { data: briefingData } = useBriefings();
  const { summary: touchpointSummary } = useTouchpoints({
    ownerId: user?.role === "sales" ? user.id : undefined,
    limit: 80,
    enabled: Boolean(user)
  });
  const { data: orgSummary } = useOrgProductizationSummary(user?.role === "manager");
  const { jobs: recentImportJobs } = useImportJobs(user?.role === "manager", 6);

  const salesData = useMemo(() => {
    if (!user) return null;
    return getSalesDashboardData({
      userId: user.id,
      customers,
      followups,
      alerts
    });
  }, [user, customers, followups, alerts]);

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

  if (loading) return <div className="text-sm text-muted-foreground">Loading dashboard...</div>;
  if (error) return <div className="text-sm text-rose-600">Dashboard load failed: {error}</div>;
  if (!user || !salesData) return <div className="text-sm text-muted-foreground">Missing user context.</div>;

  const isManager = user.role === "manager";

  const myRecentInputs = communicationInputs.filter((item) => (isManager ? true : item.ownerId === user.id)).slice(0, 6);
  const pendingDrafts = followups.filter((item) => item.draftStatus === "draft" && (isManager || item.ownerId === user.id));
  const todayInputCount = communicationInputs.filter((item) => isToday(item.createdAt)).length;
  const todayFollowupCount = followups.filter((item) => item.draftStatus === "confirmed" && isToday(item.createdAt)).length;

  const recentTeamReports = reports
    .filter((item) => (isManager ? true : item.targetUserId === user.id || item.generatedBy === user.id))
    .slice(0, 5);
  const todayPrepCount = (briefingData?.prepCards ?? []).filter((item) => isToday(item.createdAt)).length;
  const todayPreparedCustomers = new Set((briefingData?.prepCards ?? []).map((item) => item.customerId).filter(Boolean)).size;
  const recentDraftCount = (briefingData?.contentDrafts ?? []).length;
  const managerAttentionCount = (briefingData?.prepCards ?? []).filter((item) => item.cardType === "manager_attention" && item.status !== "archived").length;
  const personalPositiveOutcomeRate =
    personalOutcomes.length === 0
      ? 0
      : Math.round((personalOutcomes.filter((item) => item.resultStatus === "positive_progress" || item.resultStatus === "closed_won").length / personalOutcomes.length) * 100);

  return (
    <div>
      <PageHeader
        title={isManager ? "团队行动工作台" : "销售行动工作台"}
        description={isManager ? "先看行动，再看图表：报告、风险、草稿确认与团队推进一屏完成。" : "以最少录入完成最多推进：快速采集、确认草稿、执行今日动作。"}
        action={
          <div className="flex items-center gap-2">
            <Button asChild>
              <Link href="/capture">
                <PenSquare className="mr-1 h-4 w-4" />
                快速录入
              </Link>
            </Button>
            <Button asChild variant="outline">
              <Link href="/today">
                <CalendarCheck2 className="mr-1 h-4 w-4" />
                今日任务
              </Link>
            </Button>
            <Button asChild variant="outline">
              <Link href="/reports">
                <FileText className="mr-1 h-4 w-4" />
                报告中心
              </Link>
            </Button>
            <Button asChild variant="outline">
              <Link href="/briefings">
                <Sparkles className="mr-1 h-4 w-4" />
                准备与晨报
              </Link>
            </Button>
            <Button asChild variant="outline">
              <Link href="/deals">
                <BookText className="mr-1 h-4 w-4" />
                Deal Rooms
              </Link>
            </Button>
            <Button asChild variant="outline">
              <Link href="/growth">
                <TrendingUp className="mr-1 h-4 w-4" />
                Growth
              </Link>
            </Button>
            {isManager ? (
              <Button asChild variant="outline">
                <Link href="/executive">
                  <AlertTriangle className="mr-1 h-4 w-4" />
                  Executive
                </Link>
              </Button>
            ) : null}
            <Button asChild variant="outline">
              <Link href="/touchpoints">
                <Mail className="mr-1 h-4 w-4" />
                Touchpoints
              </Link>
            </Button>
          </div>
        }
      />

      {isManager && orgSummary ? (
        <section className="mb-4 grid gap-4 md:grid-cols-3">
          <Card className="border-sky-100 bg-sky-50/50">
            <CardHeader>
              <CardTitle className="text-sm">Organization Readiness</CardTitle>
            </CardHeader>
            <CardContent className="space-y-1 text-sm">
              <p>role={orgSummary.role}</p>
              <p>onboarding={orgSummary.onboardingCompleted ? "completed" : `${orgSummary.onboardingProgress}%`}</p>
              <p>plan={orgSummary.planTier}/{orgSummary.planStatus}</p>
              <p>
                template=
                {orgSummary.templateApplied
                  ? `${orgSummary.currentTemplateName ?? orgSummary.currentTemplateKey ?? "active"}`
                  : "not_applied"}
              </p>
            </CardContent>
          </Card>
          <Card className={orgSummary.aiProviderConfigured ? "" : "border-amber-200 bg-amber-50/70"}>
            <CardHeader>
              <CardTitle className="text-sm">AI Provider Status</CardTitle>
            </CardHeader>
            <CardContent className="space-y-1 text-sm">
              <p>{orgSummary.aiProviderConfigured ? "configured" : "not configured"}</p>
              <p className="text-xs text-muted-foreground">{orgSummary.aiProviderReason ?? "DeepSeek provider is ready."}</p>
            </CardContent>
          </Card>
          <Card className={orgSummary.quotaExceeded ? "border-rose-200 bg-rose-50/60" : orgSummary.quotaNearLimit ? "border-amber-200 bg-amber-50/60" : ""}>
            <CardHeader>
              <CardTitle className="text-sm">Quota Signal</CardTitle>
            </CardHeader>
            <CardContent className="space-y-1 text-sm">
              <p>AI runs {orgSummary.aiRunUsedMonthly}/{orgSummary.aiRunLimitMonthly}</p>
              <p>{orgSummary.quotaExceeded ? "exceeded" : orgSummary.quotaNearLimit ? "near limit" : "healthy"}</p>
              <Button asChild size="sm" variant="outline" className="mt-1">
                <Link href="/settings/usage">Open Usage</Link>
              </Button>
            </CardContent>
          </Card>
        </section>
      ) : null}

      <section className="mb-4">
        <NewCustomerOnboardingBanner />
      </section>

      <section className="mb-4">
        <IndustryTemplateBanner />
      </section>

      {isManager ? (
        <section className="mb-4">
          <FirstValueSummaryBlock />
        </section>
      ) : null}

      {isManager && orgSummary && (orgSummary.role === "owner" || orgSummary.role === "admin") ? (
        <section className="mb-4 grid gap-4 xl:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>Recent Import Jobs</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {recentImportJobs.length === 0 ? <p className="text-sm text-muted-foreground">No import jobs yet.</p> : null}
              {recentImportJobs.slice(0, 5).map((job) => (
                <div key={job.id} className="rounded-md border border-slate-200 p-2">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-sm font-semibold text-slate-900">{job.fileName}</p>
                    <Badge variant={job.jobStatus === "completed" ? "default" : job.jobStatus === "failed" ? "destructive" : "outline"}>{job.jobStatus}</Badge>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    type={job.importType} · imported={job.importedRows} · failed={job.errorRows}
                  </p>
                </div>
              ))}
              <Button asChild size="sm" variant="outline">
                <Link href="/imports">Open Import Center</Link>
              </Button>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Import Next Actions</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <p>1. Run one customers import for onboarding checklist.</p>
              <p>2. Confirm owner mapping and stage normalization.</p>
              <p>3. Resolve dedupe groups before execution.</p>
              <p>4. Run post-import today plan and manager view.</p>
              <Button asChild size="sm" variant="outline">
                <Link href="/settings/onboarding">Open Onboarding Checklist</Link>
              </Button>
            </CardContent>
          </Card>
        </section>
      ) : null}

      {isManager ? (
        <>
          <section className="mb-4 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <StatCard title="团队本周新增客户" value={managerData.weeklyNewCustomers} icon={<UserPlus className="h-4 w-4 text-sky-700" />} />
            <StatCard title="跟进完成率" value={`${managerData.followupCompletionRate}%`} icon={<Activity className="h-4 w-4 text-sky-700" />} />
            <StatCard title="团队今日关键任务" value={rhythmData?.teamTotals.criticalOpenTasks ?? 0} icon={<AlertTriangle className="h-4 w-4 text-rose-600" />} />
            <StatCard title="团队超期任务" value={rhythmData?.teamTotals.overdueTasks ?? 0} icon={<BookText className="h-4 w-4 text-amber-600" />} />
          </section>

          <section className="mb-4 grid gap-4 md:grid-cols-2 xl:grid-cols-5">
            <StatCard title="管理关注准备卡" value={managerAttentionCount} icon={<Sparkles className="h-4 w-4 text-indigo-600" />} />
            <StatCard title="关键客户未准备" value={rhythmData?.teamTotals.highValueWithoutPrepCount ?? 0} icon={<AlertTriangle className="h-4 w-4 text-rose-600" />} />
            <StatCard title="团队准备覆盖率" value={`${(((rhythmData?.teamTotals.prepCoverageRate ?? 0) * 100)).toFixed(0)}%`} icon={<Activity className="h-4 w-4 text-emerald-600" />} />
            <StatCard title="建议采用率" value={`${(((managerOutcomeData?.summary.adoptionRate ?? 0) * 100)).toFixed(0)}%`} icon={<TrendingUp className="h-4 w-4 text-violet-600" />} />
            <StatCard title="7日外部触点事件" value={touchpointSummary.totalEvents} icon={<Mail className="h-4 w-4 text-sky-700" />} />
            <StatCard title="待客户回复邮件" value={touchpointSummary.waitingReplyThreads} icon={<AlertTriangle className="h-4 w-4 text-amber-600" />} />
            <StatCard title="即将会议" value={touchpointSummary.upcomingMeetings} icon={<CalendarCheck2 className="h-4 w-4 text-indigo-700" />} />
            <StatCard title="文档更新" value={touchpointSummary.documentUpdates} icon={<FileText className="h-4 w-4 text-emerald-700" />} />
          </section>

          <section className="mb-4 grid gap-4 xl:grid-cols-[1.2fr_1.8fr]">
            <Card>
              <CardHeader>
                <CardTitle>管理动作</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                <Button asChild className="w-full justify-start">
                  <Link href="/reports">生成团队日报 / 周报</Link>
                </Button>
                <Button asChild variant="outline" className="w-full justify-start">
                  <Link href="/manager">运行漏单扫描并复核风险</Link>
                </Button>
                <Button asChild variant="outline" className="w-full justify-start">
                  <Link href="/capture">查看团队最近沟通输入</Link>
                </Button>
                <Button asChild variant="outline" className="w-full justify-start">
                  <Link href="/manager/quality">查看经营质量视图</Link>
                </Button>
                <Button asChild variant="outline" className="w-full justify-start">
                  <Link href="/manager/rhythm">查看执行节奏视图</Link>
                </Button>
                <Button asChild variant="outline" className="w-full justify-start">
                  <Link href="/manager/outcomes">查看结果洞察视图</Link>
                </Button>
                <Button asChild variant="outline" className="w-full justify-start">
                  <Link href="/executive">打开经营驾驶舱</Link>
                </Button>
                <Button asChild variant="outline" className="w-full justify-start">
                  <Link href="/briefings">打开晨报与准备卡中心</Link>
                </Button>
                <Button asChild variant="outline" className="w-full justify-start">
                  <Link href="/touchpoints">外部触点推进看板</Link>
                </Button>
              </CardContent>
            </Card>

            <ManagerDashboard
              salesWorkload={managerData.salesWorkload}
              opportunityStageDist={managerData.opportunityStageDist}
              highRiskCustomers={managerData.highRiskCustomers}
              longTimeNoFollowups={managerData.longTimeNoFollowups}
              highRiskOpportunities={managerData.highRiskOpportunities}
            />
          </section>

          <section className="grid gap-4 xl:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>最近团队报告</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {recentTeamReports.length === 0 ? <p className="text-sm text-muted-foreground">暂无报告。</p> : null}
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
                <CardTitle>最近沟通输入活跃</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {myRecentInputs.length === 0 ? <p className="text-sm text-muted-foreground">暂无沟通输入。</p> : null}
                {myRecentInputs.map((item) => (
                  <div key={item.id} className="rounded-lg border p-3">
                    <div className="mb-1 flex items-center justify-between">
                      <p className="text-sm font-semibold text-slate-900">{item.title}</p>
                      <Badge variant={item.extractionStatus === "completed" ? "default" : item.extractionStatus === "failed" ? "destructive" : "secondary"}>
                        {item.extractionStatus}
                      </Badge>
                    </div>
                    <p className="text-xs text-muted-foreground">{item.customerName ?? "未匹配客户"}</p>
                  </div>
                ))}
              </CardContent>
            </Card>
          </section>
        </>
      ) : (
        <>
          <section className="mb-4 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <StatCard title="今日待跟进客户" value={salesData.todayPendingFollowups} icon={<Users className="h-4 w-4 text-sky-700" />} />
            <StatCard title="本周新增客户" value={salesData.weeklyNewCustomers} icon={<UserPlus className="h-4 w-4 text-sky-700" />} />
            <StatCard title="今日任务数" value={todayPlan?.plan.totalItems ?? 0} icon={<CalendarCheck2 className="h-4 w-4 text-indigo-600" />} />
            <StatCard title="今日已完成动作" value={`${todayInputCount + todayFollowupCount}`} hint={`输入 ${todayInputCount} + 跟进 ${todayFollowupCount}`} icon={<CircleCheck className="h-4 w-4 text-emerald-600" />} />
            <StatCard title="待回复邮件线程" value={touchpointSummary.waitingReplyThreads} icon={<Mail className="h-4 w-4 text-amber-600" />} />
            <StatCard title="即将客户会议" value={touchpointSummary.upcomingMeetings} icon={<CalendarCheck2 className="h-4 w-4 text-indigo-700" />} />
            <StatCard title="7日外部触点事件" value={touchpointSummary.totalEvents} icon={<Activity className="h-4 w-4 text-slate-700" />} />
          </section>

          <section className="mb-4 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <StatCard title="今日待准备客户" value={todayPreparedCustomers} icon={<Sparkles className="h-4 w-4 text-amber-600" />} />
            <StatCard title="今日准备卡生成" value={todayPrepCount} icon={<BookText className="h-4 w-4 text-indigo-600" />} />
            <StatCard title="最近草稿数" value={recentDraftCount} icon={<FileText className="h-4 w-4 text-slate-700" />} />
            <StatCard
              title="准备完成率"
              value={`${Math.min(100, Math.round(((todayPrepCount || 0) / Math.max(1, todayPlan?.plan.totalItems ?? 1)) * 100))}%`}
              icon={<Activity className="h-4 w-4 text-emerald-600" />}
            />
            <StatCard title="建议后正向推进率" value={`${personalPositiveOutcomeRate}%`} icon={<TrendingUp className="h-4 w-4 text-violet-600" />} />
          </section>

          <section className="mb-4 grid gap-4 xl:grid-cols-[1.2fr_1.8fr]">
            <Card>
              <CardHeader>
                <CardTitle>今日行动</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                <Button asChild className="w-full justify-start">
                  <Link href="/capture">1. 快速录入今日沟通</Link>
                </Button>
                <Button asChild variant="outline" className="w-full justify-start">
                  <Link href="/customers">2. 处理待确认草稿</Link>
                </Button>
                <Button asChild variant="outline" className="w-full justify-start">
                  <Link href="/today">3. 执行今日任务计划</Link>
                </Button>
                <Button asChild variant="outline" className="w-full justify-start">
                  <Link href="/reports">4. 生成今日日报</Link>
                </Button>
                <Button asChild variant="outline" className="w-full justify-start">
                  <Link href="/briefings">5. 查看准备卡与晨报</Link>
                </Button>
                <Button asChild variant="outline" className="w-full justify-start">
                  <Link href="/touchpoints">6. 处理外部触点动作</Link>
                </Button>
                <div className="rounded-lg border bg-slate-50 p-3 text-xs text-muted-foreground">
                  AI 建议：{salesData.aiSuggestions[0] ?? "先完成今日首条有效跟进。"}
                </div>
                <div className="rounded-lg border bg-slate-50 p-3 text-xs text-muted-foreground">
                  基于我的习惯建议：{memoryProfile?.effectiveTactics?.[0] ?? "先完成一条高质量跟进并明确下一步动作。"}
                </div>
              </CardContent>
            </Card>

            <SalesDashboard
              keyCustomers={salesData.keyCustomers}
              recentFollowups={salesData.recentFollowups}
              aiSuggestions={salesData.aiSuggestions}
              riskAlerts={salesData.riskAlerts}
            />
          </section>

          <section className="grid gap-4 xl:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>最近输入的沟通纪要</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {myRecentInputs.length === 0 ? <p className="text-sm text-muted-foreground">暂无沟通输入。</p> : null}
                {myRecentInputs.map((item) => (
                  <div key={item.id} className="rounded-lg border p-3">
                    <div className="mb-1 flex items-center justify-between">
                      <p className="text-sm font-semibold text-slate-900">{item.title}</p>
                      <Badge variant={item.extractionStatus === "completed" ? "default" : item.extractionStatus === "failed" ? "destructive" : "secondary"}>
                        {item.extractionStatus}
                      </Badge>
                    </div>
                    <p className="text-xs text-muted-foreground">{item.customerName ?? "未匹配客户"}</p>
                    <p className="mt-1 text-xs text-muted-foreground">{formatDateTime(item.createdAt)}</p>
                  </div>
                ))}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>待确认草稿</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {pendingDrafts.length === 0 ? <p className="text-sm text-muted-foreground">暂无待确认草稿。</p> : null}
                {pendingDrafts.slice(0, 6).map((item) => (
                  <div key={item.id} className="rounded-lg border p-3">
                    <p className="text-sm font-semibold text-slate-900">{item.summary}</p>
                    <p className="mt-1 text-xs text-muted-foreground">下一步：{item.nextPlan}</p>
                    <p className="mt-1 text-xs text-muted-foreground">来源输入：{item.sourceInputId ?? "无"}</p>
                  </div>
                ))}
              </CardContent>
            </Card>
          </section>

          <section className="mt-4 grid gap-4 xl:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>我的工作记忆摘要</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                <p className="text-sm text-slate-700">{memoryProfile?.summary ?? "暂无工作记忆，请前往工作记忆页刷新。"} </p>
                <p className="text-xs text-muted-foreground">常见异议：{memoryProfile?.commonObjections?.slice(0, 2).join("；") || "暂无"}</p>
                <Button asChild size="sm" variant="outline">
                  <Link href="/memory">打开工作记忆页</Link>
                </Button>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>本周纠偏建议</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                {(memoryProfile?.riskBlindSpots ?? []).slice(0, 3).map((item) => (
                  <p key={item} className="text-sm text-slate-700">
                    - {item}
                  </p>
                ))}
                {memoryProfile?.riskBlindSpots?.length ? null : <p className="text-sm text-muted-foreground">暂无纠偏建议。</p>}
                <p className="text-xs text-muted-foreground">最近新增记忆条目：{memoryItems.slice(0, 3).length} 条</p>
              </CardContent>
            </Card>
          </section>
        </>
      )}
    </div>
  );
}
