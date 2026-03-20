"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";

import { useAuth } from "@/components/auth/auth-provider";
import { useAppData } from "@/components/shared/app-data-provider";
import { IndustryTemplateBanner } from "@/components/shared/industry-template-banner";
import { PageHeader } from "@/components/shared/page-header";
import { StatCard } from "@/components/shared/stat-card";
import { InstallPromptCard } from "@/components/mobile/InstallPromptCard";
import { OfflineDraftBanner } from "@/components/mobile/OfflineDraftBanner";
import { SwipeableTaskCard } from "@/components/mobile/SwipeableTaskCard";
import { SalesDeskQueue } from "@/components/sales-desk/sales-desk-queue";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useOutcomes } from "@/hooks/use-outcomes";
import { usePlaybooks } from "@/hooks/use-playbooks";
import { useTouchpoints } from "@/hooks/use-touchpoints";
import { useTodayPlan } from "@/hooks/use-today-plan";
import { useNetworkStatus } from "@/hooks/use-network-status";
import { formatDateTime } from "@/lib/format";
import { listLocalMobileDrafts } from "@/lib/mobile-local-drafts";
import { briefingHubClientService } from "@/services/briefing-hub-client-service";
import { contentDraftClientService } from "@/services/content-draft-client-service";
import { outcomeClientService } from "@/services/outcome-client-service";
import { prepClientService } from "@/services/prep-client-service";
import { suggestionAdoptionClientService } from "@/services/suggestion-adoption-client-service";
import { touchpointClientService } from "@/services/touchpoint-client-service";
import { workItemClientService } from "@/services/work-item-client-service";
import { dealRoomClientService } from "@/services/deal-room-client-service";
import { executiveClientService } from "@/services/executive-client-service";
import { valueMetricsClientService } from "@/services/value-metrics-client-service";
import type { TaskActionSuggestionResult } from "@/types/ai";
import type { BusinessEvent } from "@/types/automation";
import type { ContentDraft, PrepCard } from "@/types/preparation";
import type { WorkItem } from "@/types/work";
import type { TodayValueSummary, TodayPriorityAction } from "@/types/value-metrics";
import { ACTION_TYPE_LABELS, ACTION_TYPE_DESCRIPTIONS } from "@/types/value-metrics";
import { AlertTriangle, CalendarClock, CircleCheck, ListTodo, Mail, Sparkles, Target, TrendingUp, ShieldAlert } from "lucide-react";

const blockLabel: Record<string, string> = {
  early_morning: "Early Morning",
  morning: "Morning",
  noon: "Noon",
  afternoon: "Afternoon",
  evening: "Evening"
};

const bandTone: Record<string, "secondary" | "default" | "destructive" | "outline"> = {
  low: "secondary",
  medium: "outline",
  high: "default",
  critical: "destructive"
};

export default function TodayPage(): JSX.Element {
  const { user } = useAuth();
  const { followups, alerts } = useAppData();
  const { planView, loading, error, message, generate, load } = useTodayPlan();
  const { data: recentOutcomes, reload: reloadOutcomes } = useOutcomes({
    ownerId: user?.id,
    limit: 8
  });
  const { data: personalPlaybooks } = usePlaybooks({
    ownerUserId: user?.id,
    scopeType: "user",
    includeEntries: true,
    limit: 20
  });
  const { online } = useNetworkStatus();
  const { hub: touchpointHub, summary: touchpointSummary, reload: reloadTouchpoints } = useTouchpoints({
    ownerId: user?.id,
    limit: 80,
    enabled: Boolean(user)
  });
  const [actionLoadingId, setActionLoadingId] = useState<string | null>(null);
  const [pageMessage, setPageMessage] = useState<string | null>(null);
  const [suggestions, setSuggestions] = useState<Record<string, TaskActionSuggestionResult>>({});
  const [prepByWorkItemId, setPrepByWorkItemId] = useState<Record<string, PrepCard>>({});
  const [draftByWorkItemId, setDraftByWorkItemId] = useState<Record<string, ContentDraft[]>>({});
  const [adoptionIdsByWorkItemId, setAdoptionIdsByWorkItemId] = useState<Record<string, string[]>>({});
  const [outcomeWorkItem, setOutcomeWorkItem] = useState<WorkItem | null>(null);
  const [outcomeStatus, setOutcomeStatus] = useState<"positive_progress" | "neutral" | "stalled" | "risk_increased">("neutral");
  const [outcomeUsefulness, setOutcomeUsefulness] = useState<"helpful" | "somewhat_helpful" | "not_helpful" | "unknown">("unknown");
  const [outcomeSummary, setOutcomeSummary] = useState("");
  const [outcomeNextStep, setOutcomeNextStep] = useState("");
  const [capturingOutcome, setCapturingOutcome] = useState(false);
  const [dealByCustomerId, setDealByCustomerId] = useState<Record<string, string>>({});
  const [localPendingDrafts, setLocalPendingDrafts] = useState(0);
  const [localFailedDrafts, setLocalFailedDrafts] = useState(0);
  const [syncingLocalDrafts, setSyncingLocalDrafts] = useState(false);
  const [opsEvents, setOpsEvents] = useState<BusinessEvent[]>([]);
  const [todayValueSummary, setTodayValueSummary] = useState<TodayValueSummary | null>(null);
  const [todayValueLoading, setTodayValueLoading] = useState(true);

  const workItemMap = useMemo(() => {
    const map = new Map<string, WorkItem>();
    for (const item of planView?.workItems ?? []) map.set(item.id, item);
    return map;
  }, [planView?.workItems]);

  const mustDoSet = useMemo(() => {
    const set = new Set<string>();
    const items = planView?.planItems ?? [];
    for (const item of items) {
      if (item.mustDo) set.add(item.workItemId);
    }
    return set;
  }, [planView?.planItems]);

  const doneCount = (planView?.workItems ?? []).filter((item) => item.status === "done").length;
  const overdueCount = (planView?.workItems ?? []).filter((item) => item.status !== "done" && item.dueAt && new Date(item.dueAt).getTime() < Date.now()).length;
  const pendingDraftCount = followups.filter((item) => item.draftStatus === "draft" && item.ownerId === user?.id).length;
  const riskOpenCount = alerts.filter((item) => item.ownerId === user?.id && item.status !== "resolved" && item.level !== "info").length;
  const waitingReplyCount = touchpointSummary.waitingReplyThreads;
  const upcomingMeetingCount = touchpointSummary.upcomingMeetings;
  const planItemIdsKey = (planView?.planItems ?? []).map((item) => item.workItemId).join(",");

  const loadCoverage = async (): Promise<void> => {
    const ids = (planView?.planItems ?? []).map((item) => item.workItemId);
    if (ids.length === 0) {
      setPrepByWorkItemId({});
      setDraftByWorkItemId({});
      return;
    }
    try {
      const coverage = await briefingHubClientService.getWorkItemCoverage(ids);
      setPrepByWorkItemId(coverage.prepByWorkItemId);
      setDraftByWorkItemId(coverage.draftByWorkItemId);
    } catch {
      setPrepByWorkItemId({});
      setDraftByWorkItemId({});
    }
  };

  useEffect(() => {
    void loadCoverage();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [planItemIdsKey]);

  useEffect(() => {
    let cancelled = false;
    const loadDeals = async () => {
      try {
        const rooms = await dealRoomClientService.list({
          statuses: ["active", "watchlist", "escalated", "blocked"],
          limit: 200
        });
        if (cancelled) return;
        const map: Record<string, string> = {};
        for (const room of rooms) {
          if (!map[room.customerId]) map[room.customerId] = room.id;
        }
        setDealByCustomerId(map);
      } catch {
        if (!cancelled) setDealByCustomerId({});
      }
    };
    void loadDeals();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const loadOpsSignals = async () => {
      if (user?.role !== "manager") return;
      try {
        const payload = await executiveClientService.getExecutiveEvents({
          status: ["open", "acknowledged"],
          limit: 50
        });
        if (!cancelled) setOpsEvents(payload.events.slice(0, 8));
      } catch {
        if (!cancelled) setOpsEvents([]);
      }
    };
    void loadOpsSignals();
    return () => {
      cancelled = true;
    };
  }, [user?.role, planItemIdsKey]);

  useEffect(() => {
    let cancelled = false;
    const loadTodayValue = async () => {
      setTodayValueLoading(true);
      try {
        const workItems = planView?.workItems ?? [];
        const completedTasks = workItems.filter((w) => w.status === "done").length;
        const aiAssistedTasks = workItems.filter((w) => w.status === "done" && w.aiGenerated === true).length;

        const riskActionsPending = workItems.filter(
          (w) => w.status !== "done" && (w.priorityBand === "critical" || w.priorityBand === "high")
        ).length;

        const progressionActionsPending = workItems.filter(
          (w) => w.status !== "done" && w.workType === "revive_stalled_deal"
        ).length;

        const priorityActions: TodayPriorityAction[] = workItems
          .filter((w) => w.status !== "done")
          .slice(0, 5)
          .map((w) => {
            let actionType: TodayPriorityAction["actionType"] = "reduce_risk";
            let reason = "";
            let expectedImpact = "";

            if (w.priorityBand === "critical" || w.priorityBand === "high") {
              actionType = "reduce_risk";
              reason = w.rationale || "高优先级任务，需要尽快处理";
              expectedImpact = "降低客户流失风险";
            } else if (w.workType === "revive_stalled_deal") {
              actionType = "recover_progression";
              reason = "客户推进停滞，需要主动跟进";
              expectedImpact = "恢复客户推进";
            } else if (w.workType === "resolve_alert") {
              actionType = "prevent_loss";
              reason = "存在风险预警需要处理";
              expectedImpact = "防止客户流失";
            } else {
              actionType = "capture_opportunity";
              reason = w.rationale || "推进销售机会";
              expectedImpact = "增加成交可能性";
            }

            return {
              workItemId: w.id,
              title: w.title,
              customerName: undefined,
              priority: w.priorityBand as TodayPriorityAction["priority"],
              actionType,
              reason,
              expectedImpact,
              dueAt: w.dueAt ?? undefined
            };
          });

        const summary: TodayValueSummary = {
          completedTasks,
          aiAssistedTasks,
          riskActionsPending,
          progressionActionsPending,
          estimatedTimeSavedMinutes: aiAssistedTasks * 15,
          priorityActions
        };

        if (!cancelled) setTodayValueSummary(summary);
      } catch {
        if (!cancelled) {
          setTodayValueSummary({
            completedTasks: 0,
            aiAssistedTasks: 0,
            riskActionsPending: 0,
            progressionActionsPending: 0,
            estimatedTimeSavedMinutes: 0,
            priorityActions: []
          });
        }
      } finally {
        if (!cancelled) setTodayValueLoading(false);
      }
    };

    if (planView?.workItems) {
      void loadTodayValue();
    }
    return () => {
      cancelled = true;
    };
  }, [planView?.workItems]);

  const refreshLocalDraftCounters = (): void => {
    if (typeof window === "undefined") return;
    const all = listLocalMobileDrafts();
    setLocalPendingDrafts(all.filter((item) => item.syncStatus === "pending").length);
    setLocalFailedDrafts(all.filter((item) => item.syncStatus === "failed").length);
  };

  useEffect(() => {
    refreshLocalDraftCounters();
    const onFocus = () => refreshLocalDraftCounters();
    window.addEventListener("focus", onFocus);
    return () => {
      window.removeEventListener("focus", onFocus);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const syncLocalDrafts = async (): Promise<void> => {
    if (typeof window === "undefined") return;
    const pending = listLocalMobileDrafts().filter((item) => item.syncStatus === "pending" || item.syncStatus === "failed");
    if (pending.length === 0) return;

    setSyncingLocalDrafts(true);
    try {
      for (const draft of pending) {
        await fetch("/api/mobile/drafts/sync", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            localDraftId: draft.localDraftId,
            draftType: draft.draftType,
            summary: draft.summary,
            payload: draft.payload
          })
        });
      }
      refreshLocalDraftCounters();
      await load();
    } finally {
      setSyncingLocalDrafts(false);
    }
  };

  const trackAdoption = async (params: {
    targetType: "prep_card" | "content_draft" | "task_action_suggestion";
    targetId: string;
    adoptionType: "viewed" | "copied" | "edited" | "adopted" | "dismissed" | "partially_used";
    adoptionContext: "before_followup" | "before_quote" | "before_meeting" | "during_task_execution" | "after_review";
    workItemId: string;
  }): Promise<void> => {
    try {
      const record = await suggestionAdoptionClientService.track({
        targetType: params.targetType,
        targetId: params.targetId,
        adoptionType: params.adoptionType,
        adoptionContext: params.adoptionContext
      });
      setAdoptionIdsByWorkItemId((prev) => {
        const list = prev[params.workItemId] ?? [];
        if (list.includes(record.id)) return prev;
        return {
          ...prev,
          [params.workItemId]: [...list, record.id]
        };
      });
    } catch {
      // Adoption tracking is non-blocking in MVP.
    }
  };

  const handleAction = async (workItemId: string, action: "start" | "complete" | "snooze" | "cancel") => {
    setActionLoadingId(workItemId);
    setPageMessage(null);
    try {
      if (action === "start") await workItemClientService.start(workItemId);
      if (action === "complete") {
        const updated = await workItemClientService.complete(workItemId);
        setOutcomeWorkItem(updated);
        setOutcomeStatus("neutral");
        setOutcomeUsefulness("unknown");
        setOutcomeSummary(`${updated.title} executed.`);
        setOutcomeNextStep("");
      }
      if (action === "snooze") {
        const oneDayLater = new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString();
        await workItemClientService.snooze(workItemId, oneDayLater);
      }
      if (action === "cancel") await workItemClientService.cancel(workItemId);
      setPageMessage(action === "complete" ? "Task completed. Please capture lightweight outcome." : "Task status updated");
      await load();
    } catch (cause) {
      setPageMessage(cause instanceof Error ? cause.message : "Task operation failed");
    } finally {
      setActionLoadingId(null);
    }
  };

  const handleConvertToFollowup = async (workItemId: string) => {
    setActionLoadingId(workItemId);
    setPageMessage(null);
    try {
      const result = await workItemClientService.completeToFollowup(workItemId);
      setPageMessage(result.followupId ? `Work item converted to followup ${result.followupId.slice(0, 8)}...` : "Work item converted");
      await load();
    } catch (cause) {
      setPageMessage(cause instanceof Error ? cause.message : "Failed to convert work item");
    } finally {
      setActionLoadingId(null);
    }
  };

  const loadSuggestion = async (workItemId: string) => {
    setActionLoadingId(workItemId);
    try {
      const result = await workItemClientService.getTaskSuggestion(workItemId);
      setSuggestions((prev) => ({
        ...prev,
        [workItemId]: result.result
      }));
      if (result.usedFallback) {
        setPageMessage("Action suggestion generated by fallback rules");
      }
      await trackAdoption({
        targetType: "task_action_suggestion",
        targetId: workItemId,
        adoptionType: "viewed",
        adoptionContext: "during_task_execution",
        workItemId
      });
    } catch (cause) {
      setPageMessage(cause instanceof Error ? cause.message : "Failed to generate task suggestion");
    } finally {
      setActionLoadingId(null);
    }
  };

  const handleGeneratePrepCard = async (workItemId: string) => {
    setActionLoadingId(workItemId);
    setPageMessage(null);
    try {
      const result = await prepClientService.generateTaskBrief({
        workItemId
      });
      setPageMessage(result.usedFallback ? "Prep card generated with fallback rules." : "Prep card generated.");
      await trackAdoption({
        targetType: "prep_card",
        targetId: result.prepCard.id,
        adoptionType: "viewed",
        adoptionContext: "during_task_execution",
        workItemId
      });
      await loadCoverage();
    } catch (cause) {
      setPageMessage(cause instanceof Error ? cause.message : "Failed to generate prep card");
    } finally {
      setActionLoadingId(null);
    }
  };

  const handleGenerateDraft = async (workItemId: string) => {
    setActionLoadingId(workItemId);
    setPageMessage(null);
    try {
      const work = workItemMap.get(workItemId);
      const result = await contentDraftClientService.generate({
        draftType: "followup_message",
        workItemId,
        customerId: work?.customerId ?? undefined,
        title: work ? `${work.title} | Follow-up Draft` : undefined
      });
      setPageMessage(result.usedFallback ? "Action draft generated by fallback template." : "Action draft generated.");
      await trackAdoption({
        targetType: "content_draft",
        targetId: result.draft.id,
        adoptionType: "viewed",
        adoptionContext: "during_task_execution",
        workItemId
      });
      await loadCoverage();
    } catch (cause) {
      setPageMessage(cause instanceof Error ? cause.message : "Failed to generate action draft");
    } finally {
      setActionLoadingId(null);
    }
  };

  const handleGenerateEmailDraft = async (workItemId: string) => {
    setActionLoadingId(workItemId);
    setPageMessage(null);
    try {
      const work = workItemMap.get(workItemId);
      if (!work?.customerId) {
        setPageMessage("This task has no customer context for email draft generation.");
        return;
      }
      const contextType =
        work.workType === "send_quote" || work.workType === "prepare_proposal"
          ? "quote"
          : work.workType === "manager_checkin"
            ? "manager_support"
            : "followup";
      const result = await touchpointClientService.generateEmailDraft({
        contextType,
        customerId: work.customerId,
        opportunityId: work.opportunityId ?? undefined
      });
      setPageMessage(result.usedFallback ? "Email draft generated by fallback template." : "Email draft generated.");
      await reloadTouchpoints();
    } catch (cause) {
      setPageMessage(cause instanceof Error ? cause.message : "Failed to generate email draft");
    } finally {
      setActionLoadingId(null);
    }
  };

  const handleOpenDealRoom = async (workItemId: string): Promise<void> => {
    const work = workItemMap.get(workItemId);
    if (!work?.customerId) return;
    const existing = dealByCustomerId[work.customerId];
    if (existing) {
      window.location.href = `/deals/${existing}`;
      return;
    }
    setActionLoadingId(workItemId);
    setPageMessage(null);
    try {
      const result = await dealRoomClientService.create({
        customerId: work.customerId,
        opportunityId: work.opportunityId ?? undefined
      });
      setPageMessage(result.created ? "Deal room created and linked." : "Opened existing deal room.");
      window.location.href = `/deals/${result.room.id}`;
    } catch (cause) {
      setPageMessage(cause instanceof Error ? cause.message : "Failed to open deal room");
    } finally {
      setActionLoadingId(null);
    }
  };

  const submitOutcomeCapture = async (): Promise<void> => {
    if (!outcomeWorkItem) return;
    setCapturingOutcome(true);
    setPageMessage(null);
    try {
      const linkedAdoptionIds = adoptionIdsByWorkItemId[outcomeWorkItem.id] ?? [];
      const outcome = await outcomeClientService.capture({
        workItemId: outcomeWorkItem.id,
        customerId: outcomeWorkItem.customerId ?? undefined,
        outcomeType: "task_result",
        resultStatus: outcomeStatus,
        keyOutcomeSummary: outcomeSummary,
        nextStepDefined: Boolean(outcomeNextStep.trim()),
        nextStepText: outcomeNextStep.trim() ? outcomeNextStep.trim() : undefined,
        usedPrepCard: Boolean(prepByWorkItemId[outcomeWorkItem.id]),
        usedDraft: (draftByWorkItemId[outcomeWorkItem.id]?.length ?? 0) > 0,
        usefulnessRating: outcomeUsefulness,
        autoInfer: true,
        linkAdoptionIds: linkedAdoptionIds.length > 0 ? linkedAdoptionIds : undefined
      });

      setPageMessage(
        outcome.usedFallback
          ? "Outcome captured with fallback inference. Closed-loop learning updated."
          : "Outcome captured. Closed-loop learning updated."
      );
      setOutcomeWorkItem(null);
      setOutcomeSummary("");
      setOutcomeNextStep("");
      await reloadOutcomes();
    } catch (cause) {
      setPageMessage(cause instanceof Error ? cause.message : "Failed to capture outcome");
    } finally {
      setCapturingOutcome(false);
    }
  };

  if (!user) return <div className="text-sm text-muted-foreground">Missing user context.</div>;
  if (loading) return <div className="text-sm text-muted-foreground">Loading today plan...</div>;
  if (error) return <div className="text-sm text-rose-600">Failed to load today plan: {error}</div>;

  return (
    <div>
      <PageHeader
        title="Today Task Agent"
        description="See what to do first, why it matters, and execute tasks with clear state transitions."
        action={
          <div className="flex items-center gap-2">
            <Button onClick={() => void generate()}>Generate Today Plan</Button>
            <Button asChild variant="outline">
              <Link href="/capture">Quick Capture</Link>
            </Button>
            <Button asChild variant="outline">
              <Link href="/touchpoints">Touchpoints</Link>
            </Button>
          </div>
        }
      />

      {message ? <p className="mb-3 text-sm text-emerald-700">{message}</p> : null}
      {pageMessage ? <p className="mb-3 text-sm text-muted-foreground">{pageMessage}</p> : null}
      <InstallPromptCard />
      <IndustryTemplateBanner className="mb-3" compact />
      <OfflineDraftBanner
        online={online}
        pendingCount={localPendingDrafts}
        failedCount={localFailedDrafts}
        syncing={syncingLocalDrafts}
        onSync={() => void syncLocalDrafts()}
      />

      <section className="mb-4 space-y-3 lg:hidden">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">今日焦点</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <p className="text-sm text-slate-800">{planView?.plan.focusTheme ?? "先处理关键任务，再推进高价值客户。"}</p>
            <p className="text-xs text-muted-foreground">{planView?.plan.summary ?? "生成今日计划后可查看节奏建议。"}</p>
            <div className="flex flex-wrap gap-2">
              <Badge variant="outline">Must-do: {(planView?.planItems ?? []).filter((item) => item.mustDo).length}</Badge>
              <Badge variant="secondary">Waiting reply: {waitingReplyCount}</Badge>
              <Badge variant="secondary">Meetings: {upcomingMeetingCount}</Badge>
            </div>
          </CardContent>
        </Card>

        {(planView?.planItems ?? [])
          .slice(0, 5)
          .map((planItem) => {
            const work = workItemMap.get(planItem.workItemId);
            if (!work) return null;
            return (
              <SwipeableTaskCard
                key={work.id}
                title={work.title}
                subtitle={planItem.recommendationReason}
                status={work.status}
                priorityBand={work.priorityBand}
                onStart={() => void handleAction(work.id, "start")}
                onDone={() => void handleAction(work.id, "complete")}
                onSnooze={() => void handleAction(work.id, "snooze")}
              />
            );
          })}
      </section>

      <section className="mb-4 grid gap-4 xl:grid-cols-4">
        <StatCard title="Today Tasks" value={planView?.plan.totalItems ?? 0} icon={<ListTodo className="h-4 w-4 text-sky-700" />} />
        <StatCard title="Completed" value={doneCount} icon={<CircleCheck className="h-4 w-4 text-emerald-600" />} />
        <StatCard title="Overdue" value={overdueCount} icon={<CalendarClock className="h-4 w-4 text-amber-600" />} />
        <StatCard title="Open Risks" value={riskOpenCount} icon={<AlertTriangle className="h-4 w-4 text-rose-600" />} />
        <StatCard title="Waiting Replies" value={waitingReplyCount} icon={<Mail className="h-4 w-4 text-amber-700" />} />
        <StatCard title="Upcoming Meetings" value={upcomingMeetingCount} icon={<CalendarClock className="h-4 w-4 text-indigo-700" />} />
      </section>

      <SalesDeskQueue />

      <section className="mb-4">
        <Card className="border-indigo-100 bg-gradient-to-br from-indigo-50/30 to-white">
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle className="flex items-center gap-2 text-base">
                <Target className="h-5 w-5 text-indigo-600" />
                今日优先价值动作
              </CardTitle>
              {todayValueSummary && (
                <div className="flex items-center gap-3 text-xs text-slate-500">
                  <span>已完成 {todayValueSummary.completedTasks} 项</span>
                  <span>AI 辅助 {todayValueSummary.aiAssistedTasks} 项</span>
                  <span>节省 {todayValueSummary.estimatedTimeSavedMinutes} 分钟</span>
                </div>
              )}
            </div>
          </CardHeader>
          <CardContent>
            {todayValueLoading ? (
              <p className="text-sm text-muted-foreground">正在分析今日优先动作...</p>
            ) : todayValueSummary && todayValueSummary.priorityActions.length > 0 ? (
              <div className="space-y-3">
                <div className="mb-3 flex flex-wrap gap-2">
                  <Badge variant="outline" className="gap-1">
                    <ShieldAlert className="h-3 w-3 text-rose-500" />
                    风险处理: {todayValueSummary.riskActionsPending}
                  </Badge>
                  <Badge variant="outline" className="gap-1">
                    <TrendingUp className="h-3 w-3 text-emerald-500" />
                    推进恢复: {todayValueSummary.progressionActionsPending}
                  </Badge>
                </div>
                <div className="grid gap-2 md:grid-cols-2 lg:grid-cols-3">
                  {todayValueSummary.priorityActions.map((action, index) => (
                    <div
                      key={action.workItemId}
                      className="rounded-lg border border-slate-200 bg-white p-3"
                    >
                      <div className="mb-1 flex items-center justify-between gap-2">
                        <span className="text-xs font-medium text-indigo-600">
                          #{index + 1} {ACTION_TYPE_LABELS[action.actionType]}
                        </span>
                        <Badge
                          variant={
                            action.priority === "critical"
                              ? "destructive"
                              : action.priority === "high"
                                ? "default"
                                : "secondary"
                          }
                          className="text-xs"
                        >
                          {action.priority}
                        </Badge>
                      </div>
                      <p className="text-sm font-medium text-slate-800">{action.title}</p>
                      <p className="mt-1 text-xs text-slate-500">{action.reason}</p>
                      <p className="mt-1 text-xs text-emerald-600">
                        预期效果: {action.expectedImpact}
                      </p>
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                暂无优先动作。生成今日计划后可查看推荐。
              </p>
            )}
          </CardContent>
        </Card>
      </section>

      {user.role === "manager" ? (
        <section className="mb-4">
          <Card>
            <CardHeader>
              <CardTitle>Operating Event Signals</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {opsEvents.length === 0 ? <p className="text-sm text-muted-foreground">No open operating event signal.</p> : null}
              {opsEvents.map((event) => (
                <div key={event.id} className="rounded-md border p-2">
                  <div className="mb-1 flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <Badge variant="outline">{event.eventType}</Badge>
                      <Badge variant={event.severity === "critical" ? "destructive" : event.severity === "warning" ? "default" : "secondary"}>
                        {event.severity}
                      </Badge>
                    </div>
                    <p className="text-xs text-muted-foreground">{formatDateTime(event.createdAt)}</p>
                  </div>
                  <p className="text-sm text-slate-700">{event.eventSummary}</p>
                </div>
              ))}
              <Button asChild size="sm" variant="outline">
                <Link href="/executive">Open Executive Cockpit</Link>
              </Button>
            </CardContent>
          </Card>
        </section>
      ) : null}

      <section className="mb-4 grid gap-4 xl:grid-cols-[1.1fr_1.9fr]">
        <Card>
          <CardHeader>
            <CardTitle>Focus Theme</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-sm text-slate-700">{planView?.plan.focusTheme ?? "No plan yet. Generate your today plan."}</p>
            <div className="rounded-lg border bg-slate-50 p-3 text-xs text-slate-600">{planView?.plan.summary ?? "AI/Rule planner will summarize your execution rhythm here."}</div>
            <div className="flex items-center gap-2">
              <Badge variant="outline">Pending capture drafts: {pendingDraftCount}</Badge>
              <Badge variant="secondary">Plan date: {planView?.plan.planDate ?? new Date().toISOString().slice(0, 10)}</Badge>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Must-Do Items</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {(planView?.planItems ?? [])
              .filter((item) => item.mustDo)
              .slice(0, 4)
              .map((item) => {
                const work = workItemMap.get(item.workItemId);
                if (!work) return null;
                return (
                  <div key={item.id} className="rounded-lg border p-3">
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-sm font-semibold text-slate-900">{work.title}</p>
                      <Badge variant={bandTone[work.priorityBand]}>{work.priorityBand}</Badge>
                    </div>
                    <p className="mt-1 text-xs text-slate-700">{item.recommendationReason}</p>
                    <p className="mt-1 text-xs text-muted-foreground">Time block: {item.plannedTimeBlock ? blockLabel[item.plannedTimeBlock] : "Flexible"}</p>
                  </div>
                );
              })}
            {(planView?.planItems ?? []).filter((item) => item.mustDo).length === 0 ? (
              <p className="text-sm text-muted-foreground">No must-do items yet.</p>
            ) : null}
          </CardContent>
        </Card>
      </section>

      {outcomeWorkItem ? (
        <section className="mb-4">
          <Card className="border-amber-200">
            <CardHeader>
              <CardTitle>Capture Action Outcome</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-3 md:grid-cols-2">
              <div className="md:col-span-2 rounded-md border bg-amber-50/70 p-3 text-xs text-slate-700">
                Task {outcomeWorkItem.title} is done. Confirm a lightweight outcome so MOY can learn what really works.
              </div>
              <div>
                <p className="mb-1 text-xs text-muted-foreground">Result status</p>
                <Select value={outcomeStatus} onValueChange={(value) => setOutcomeStatus(value as typeof outcomeStatus)}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="positive_progress">Positive progress</SelectItem>
                    <SelectItem value="neutral">Neutral</SelectItem>
                    <SelectItem value="stalled">Stalled</SelectItem>
                    <SelectItem value="risk_increased">Risk increased</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div>
                <p className="mb-1 text-xs text-muted-foreground">Prep/draft usefulness</p>
                <Select value={outcomeUsefulness} onValueChange={(value) => setOutcomeUsefulness(value as typeof outcomeUsefulness)}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="helpful">Helpful</SelectItem>
                    <SelectItem value="somewhat_helpful">Somewhat helpful</SelectItem>
                    <SelectItem value="not_helpful">Not helpful</SelectItem>
                    <SelectItem value="unknown">Unknown</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="md:col-span-2">
                <p className="mb-1 text-xs text-muted-foreground">Outcome summary</p>
                <Textarea
                  value={outcomeSummary}
                  onChange={(event) => setOutcomeSummary(event.target.value)}
                  placeholder="What changed after this action?"
                />
              </div>
              <div className="md:col-span-2">
                <p className="mb-1 text-xs text-muted-foreground">Next step (optional)</p>
                <Textarea
                  value={outcomeNextStep}
                  onChange={(event) => setOutcomeNextStep(event.target.value)}
                  placeholder="What is the next concrete step?"
                />
              </div>
              <div className="md:col-span-2 flex flex-wrap gap-2">
                <Button onClick={() => void submitOutcomeCapture()} disabled={capturingOutcome}>
                  {capturingOutcome ? "Saving..." : "Save Outcome"}
                </Button>
                <Button
                  variant="outline"
                  onClick={() => {
                    setOutcomeWorkItem(null);
                    setOutcomeSummary("");
                    setOutcomeNextStep("");
                  }}
                  disabled={capturingOutcome}
                >
                  Skip for now
                </Button>
              </div>
            </CardContent>
          </Card>
        </section>
      ) : null}

      <section className="mb-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>External Actions Today</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {touchpointHub.emailThreads.filter((item) => item.threadStatus === "waiting_reply").slice(0, 5).map((item) => (
              <div key={item.id} className="rounded border p-2">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-sm font-semibold text-slate-900">{item.subject}</p>
                  <Badge variant="destructive">waiting_reply</Badge>
                </div>
                <p className="text-xs text-muted-foreground">
                  {item.customerName ?? "Unknown customer"} | last touchpoint {item.latestMessageAt ? formatDateTime(item.latestMessageAt) : "-"}
                </p>
              </div>
            ))}
            {touchpointHub.emailThreads.filter((item) => item.threadStatus === "waiting_reply").length === 0 ? (
              <p className="text-sm text-muted-foreground">No waiting-reply thread in your scope.</p>
            ) : null}
            <Button asChild size="sm" variant="outline">
              <Link href="/touchpoints">Open Touchpoints</Link>
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Upcoming Meeting Prep</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {touchpointHub.calendarEvents
              .filter((item) => item.meetingStatus === "scheduled")
              .slice(0, 5)
              .map((item) => (
                <div key={item.id} className="rounded border p-2">
                  <p className="text-sm font-semibold text-slate-900">{item.title}</p>
                  <p className="text-xs text-muted-foreground">{formatDateTime(item.startAt)} | {item.customerName ?? "Internal"}</p>
                </div>
              ))}
            {touchpointHub.calendarEvents.filter((item) => item.meetingStatus === "scheduled").length === 0 ? (
              <p className="text-sm text-muted-foreground">No scheduled meeting to prepare now.</p>
            ) : null}
          </CardContent>
        </Card>
      </section>

      <section className="mb-4">
        <Card>
          <CardHeader>
            <CardTitle>Recent Outcome Loop</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {recentOutcomes.length === 0 ? <p className="text-sm text-muted-foreground">No captured outcomes yet.</p> : null}
            {recentOutcomes.slice(0, 6).map((item) => (
              <div key={item.id} className="rounded-lg border p-3">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <p className="text-sm font-semibold text-slate-900">{item.keyOutcomeSummary}</p>
                  <Badge variant={item.resultStatus === "positive_progress" ? "default" : item.resultStatus === "risk_increased" ? "destructive" : "secondary"}>
                    {item.resultStatus}
                  </Badge>
                </div>
                <p className="text-xs text-muted-foreground">
                  {item.outcomeType} | useful: {item.usefulnessRating} | {formatDateTime(item.createdAt)}
                </p>
                {item.nextStepText ? <p className="mt-1 text-xs text-slate-700">Next step: {item.nextStepText}</p> : null}
              </div>
            ))}
          </CardContent>
        </Card>
      </section>

      <Card className="hidden lg:block">
        <CardHeader>
          <CardTitle>Recommended Sequence</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {(planView?.planItems ?? []).map((planItem) => {
            const work = workItemMap.get(planItem.workItemId);
            if (!work) return null;
            const suggestion = suggestions[work.id];
            const prepCard = prepByWorkItemId[work.id];
            const drafts = draftByWorkItemId[work.id] ?? [];
            const relatedPlaybooks = personalPlaybooks.filter((item) => {
              if (work.workType === "send_quote" || work.workType === "prepare_proposal") return item.playbook.playbookType === "quote_strategy";
              if (work.workType === "schedule_demo") return item.playbook.playbookType === "meeting_strategy";
              if (work.workType === "resolve_alert" || work.workType === "revive_stalled_deal") return item.playbook.playbookType === "risk_recovery";
              if (work.workType === "followup_call" || work.workType === "review_customer") return item.playbook.playbookType === "followup_rhythm";
              return item.playbook.playbookType === "objection_handling";
            });
            return (
              <div key={planItem.id} className="rounded-lg border p-3">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <p className="text-sm font-semibold text-slate-900">
                    #{planItem.sequenceNo} {work.title}
                  </p>
                  <div className="flex items-center gap-2">
                    {mustDoSet.has(work.id) ? <Badge variant="destructive">Must Do</Badge> : null}
                    <Badge variant={bandTone[work.priorityBand]}>{work.priorityBand}</Badge>
                    <Badge variant="secondary">{work.status}</Badge>
                  </div>
                </div>

                <p className="text-xs text-slate-700">Why now: {work.rationale || planItem.recommendationReason}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  Not doing this risks: {work.priorityBand === "critical" || work.priorityBand === "high" ? "high conversion leakage" : "slower pipeline momentum"}
                </p>
                {work.dueAt ? <p className="mt-1 text-xs text-muted-foreground">Due: {formatDateTime(work.dueAt)}</p> : null}
                <p className="mt-1 text-xs text-muted-foreground">Time block: {planItem.plannedTimeBlock ? blockLabel[planItem.plannedTimeBlock] : "Flexible"}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  Prep: {prepCard ? `${prepCard.cardType} (${prepCard.status})` : "Not generated"} | Drafts: {drafts.length}
                </p>
                {relatedPlaybooks[0] ? (
                  <p className="mt-1 text-xs text-indigo-700">
                    Playbook hint: {relatedPlaybooks[0].playbook.title}
                  </p>
                ) : null}

                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <Button size="sm" variant="outline" disabled={actionLoadingId === work.id} onClick={() => void handleAction(work.id, "start")}>
                    Start
                  </Button>
                  <Button size="sm" variant="outline" disabled={actionLoadingId === work.id} onClick={() => void handleAction(work.id, "complete")}>
                    Complete
                  </Button>
                  <Button size="sm" variant="outline" disabled={actionLoadingId === work.id} onClick={() => void handleAction(work.id, "snooze")}>
                    Snooze 1 day
                  </Button>
                  <Button size="sm" variant="ghost" disabled={actionLoadingId === work.id} onClick={() => void handleAction(work.id, "cancel")}>
                    Cancel
                  </Button>
                  <Button size="sm" variant="secondary" disabled={actionLoadingId === work.id} onClick={() => void loadSuggestion(work.id)}>
                    <Sparkles className="mr-1 h-3 w-3" />
                    Action Suggestion
                  </Button>
                  <Button size="sm" variant="secondary" disabled={actionLoadingId === work.id} onClick={() => void handleGenerateDraft(work.id)}>
                    Generate Talk Draft
                  </Button>
                  <Button size="sm" variant="secondary" disabled={actionLoadingId === work.id} onClick={() => void handleGenerateEmailDraft(work.id)}>
                    Generate Email Draft
                  </Button>
                  {prepCard ? (
                    <Button size="sm" variant="outline" asChild>
                      <Link href="/briefings">View Prep Card</Link>
                    </Button>
                  ) : (
                    <Button size="sm" variant="outline" disabled={actionLoadingId === work.id} onClick={() => void handleGeneratePrepCard(work.id)}>
                      Generate Prep Card
                    </Button>
                  )}
                  {work.customerId && work.status !== "done" ? (
                    <Button size="sm" variant="secondary" disabled={actionLoadingId === work.id} onClick={() => void handleConvertToFollowup(work.id)}>
                      Convert To Followup
                    </Button>
                  ) : null}
                  {work.customerId ? (
                    <Button size="sm" variant="outline" disabled={actionLoadingId === work.id} onClick={() => void handleOpenDealRoom(work.id)}>
                      Open Deal Room
                    </Button>
                  ) : null}
                </div>

                {suggestion ? (
                  <div className="mt-3 rounded-md border bg-slate-50 p-2 text-xs text-slate-700">
                    <p>
                      <span className="font-semibold">Suggested action:</span> {suggestion.suggested_action}
                    </p>
                    <p className="mt-1">
                      <span className="font-semibold">Success signal:</span> {suggestion.success_signal}
                    </p>
                    <p className="mt-1">
                      <span className="font-semibold">Risk if delayed:</span> {suggestion.risk_if_delayed}
                    </p>
                  </div>
                ) : null}
              </div>
            );
          })}

          {(planView?.planItems ?? []).length === 0 ? (
            <div className="rounded-lg border border-dashed p-6 text-center">
              <p className="text-sm text-muted-foreground">No plan items for today yet.</p>
              <Button className="mt-3" onClick={() => void generate()}>
                Generate Today Plan
              </Button>
            </div>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
