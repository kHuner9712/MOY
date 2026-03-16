"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { InstallPromptCard } from "@/components/mobile/InstallPromptCard";
import { MobileSectionTabs } from "@/components/mobile/MobileSectionTabs";
import { QuickActionCard } from "@/components/mobile/QuickActionCard";
import { IndustryTemplateBanner } from "@/components/shared/industry-template-banner";
import { PageHeader } from "@/components/shared/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useBriefings } from "@/hooks/use-briefings";
import { useTouchpoints } from "@/hooks/use-touchpoints";
import { formatDateTime } from "@/lib/format";
import { contentDraftClientService } from "@/services/content-draft-client-service";
import { executiveClientService } from "@/services/executive-client-service";
import { prepClientService } from "@/services/prep-client-service";
import type { ContentDraft, PrepCardType } from "@/types/preparation";
import type { ExecutiveBrief } from "@/types/automation";
import { Mail } from "lucide-react";

const cardTypeLabel: Record<PrepCardType, string> = {
  followup_prep: "Follow-up Prep",
  quote_prep: "Quote Prep",
  meeting_prep: "Meeting Prep",
  task_brief: "Task Brief",
  manager_attention: "Manager Attention"
};

export default function BriefingsPage(): JSX.Element {
  const { user } = useAuth();
  const { data, loading, error, message, reload, generateMorningBrief } = useBriefings();
  const [scopeFilter, setScopeFilter] = useState<"all" | "manager" | "sales" | "mine">("all");
  const [cardFilter, setCardFilter] = useState<PrepCardType | "all">("all");
  const [draftFilter, setDraftFilter] = useState<ContentDraft["draftType"] | "all">("all");
  const [feedbackMessage, setFeedbackMessage] = useState<string | null>(null);
  const [feedbackLoadingId, setFeedbackLoadingId] = useState<string | null>(null);
  const [executiveBriefs, setExecutiveBriefs] = useState<ExecutiveBrief[]>([]);
  const { hub: touchpointHub, summary: touchpointSummary } = useTouchpoints({
    ownerId: user?.role === "sales" ? user.id : undefined,
    limit: 80,
    enabled: Boolean(user)
  });

  const prepCards = useMemo(() => {
    const rows = data?.prepCards ?? [];
    const byScope = rows.filter((item) => {
      if (!user) return false;
      if (user.role !== "manager") return item.ownerId === user.id;
      if (scopeFilter === "all") return true;
      if (scopeFilter === "mine") return item.ownerId === user.id;
      if (scopeFilter === "manager") return item.cardType === "manager_attention" || item.ownerId === user.id;
      return item.cardType !== "manager_attention" && item.ownerId !== user.id;
    });
    if (cardFilter === "all") return byScope;
    return byScope.filter((item) => item.cardType === cardFilter);
  }, [data?.prepCards, cardFilter, scopeFilter, user]);

  const drafts = useMemo(() => {
    const rows = data?.contentDrafts ?? [];
    const byScope = rows.filter((item) => {
      if (!user) return false;
      if (user.role !== "manager") return item.ownerId === user.id;
      if (scopeFilter === "all") return true;
      if (scopeFilter === "mine" || scopeFilter === "manager") return item.ownerId === user.id;
      return item.ownerId !== user.id;
    });
    if (draftFilter === "all") return byScope;
    return byScope.filter((item) => item.draftType === draftFilter);
  }, [data?.contentDrafts, draftFilter, scopeFilter, user]);

  useEffect(() => {
    let cancelled = false;
    const loadExecutiveBriefs = async () => {
      if (user?.role !== "manager") return;
      try {
        const payload = await executiveClientService.getExecutiveBriefs({ limit: 6 });
        if (!cancelled) setExecutiveBriefs(payload.briefs);
      } catch {
        if (!cancelled) setExecutiveBriefs([]);
      }
    };
    void loadExecutiveBriefs();
    return () => {
      cancelled = true;
    };
  }, [user?.role, message]);

  const submitPrepFeedback = async (prepCardId: string, feedbackType: "useful" | "not_useful" | "adopted" | "inaccurate") => {
    setFeedbackLoadingId(prepCardId);
    setFeedbackMessage(null);
    try {
      await prepClientService.feedback(prepCardId, feedbackType);
      setFeedbackMessage("Prep card feedback saved.");
      await reload();
    } catch (cause) {
      setFeedbackMessage(cause instanceof Error ? cause.message : "Failed to save prep card feedback");
    } finally {
      setFeedbackLoadingId(null);
    }
  };

  const submitDraftFeedback = async (draftId: string, feedbackType: "useful" | "not_useful" | "adopted" | "inaccurate") => {
    setFeedbackLoadingId(draftId);
    setFeedbackMessage(null);
    try {
      await contentDraftClientService.feedback(draftId, feedbackType);
      setFeedbackMessage("Draft feedback saved.");
      await reload();
    } catch (cause) {
      setFeedbackMessage(cause instanceof Error ? cause.message : "Failed to save draft feedback");
    } finally {
      setFeedbackLoadingId(null);
    }
  };

  if (loading) return <div className="text-sm text-muted-foreground">Loading briefings...</div>;
  if (error) return <div className="text-sm text-rose-600">Failed to load briefings: {error}</div>;
  if (!user || !data) return <div className="text-sm text-muted-foreground">Missing context.</div>;

  return (
    <div>
      <PageHeader
        title="Briefings Hub"
        description="Morning brief + preparation cards + action drafts. Everything needed before execution."
        action={
          <div className="flex items-center gap-2">
            <Button
              onClick={() =>
                void generateMorningBrief({
                  briefType: user.role === "manager" ? "manager_morning" : "sales_morning"
                })
              }
            >
              Generate Morning Brief
            </Button>
            <Button variant="outline" onClick={() => void reload()}>
              Refresh
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

      {message ? <p className="mb-3 text-sm text-emerald-700">{message}</p> : null}
      {feedbackMessage ? <p className="mb-3 text-sm text-muted-foreground">{feedbackMessage}</p> : null}
      <InstallPromptCard />
      <IndustryTemplateBanner className="mb-3" compact />

      <section className="mb-4 space-y-3 lg:hidden">
        <QuickActionCard
          title={data.morningBrief?.headline ?? "今日晨报"}
          subtitle={data.morningBrief?.executiveSummary ?? "可一键生成晨报，查看今日优先动作。"}
          right={
            <Badge variant={data.morningBrief?.status === "completed" ? "default" : "secondary"}>
              {data.morningBrief?.status ?? "not_ready"}
            </Badge>
          }
        >
          <p className="text-xs text-slate-600">
            风险 {touchpointSummary.waitingReplyThreads} | 会议 {touchpointSummary.upcomingMeetings} | 文档 {touchpointSummary.documentUpdates}
          </p>
        </QuickActionCard>
      </section>

      {user.role === "manager" ? (
        <section className="mb-4">
          <Card>
            <CardHeader>
              <CardTitle>View Scope</CardTitle>
            </CardHeader>
            <CardContent>
              <Select value={scopeFilter} onValueChange={(value) => setScopeFilter(value as "all" | "manager" | "sales" | "mine")}>
                <SelectTrigger className="max-w-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All outputs</SelectItem>
                  <SelectItem value="manager">Manager view</SelectItem>
                  <SelectItem value="sales">Sales view</SelectItem>
                  <SelectItem value="mine">My outputs</SelectItem>
                </SelectContent>
              </Select>
            </CardContent>
          </Card>
        </section>
      ) : null}

      <section className="mb-4">
        <Card>
          <CardHeader>
            <CardTitle>Today&apos;s Morning Brief</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {!data.morningBrief ? <p className="text-sm text-muted-foreground">No morning brief yet. Generate one now.</p> : null}
            {data.morningBrief ? (
              <>
                <div className="flex items-center justify-between gap-2">
                  <p className="text-base font-semibold text-slate-900">{data.morningBrief.headline}</p>
                  <Badge variant={data.morningBrief.status === "completed" ? "default" : data.morningBrief.status === "failed" ? "destructive" : "secondary"}>
                    {data.morningBrief.status}
                  </Badge>
                </div>
                <p className="text-sm text-slate-700">{data.morningBrief.executiveSummary}</p>
                <pre className="overflow-auto rounded-md border bg-slate-50 p-3 text-xs text-slate-700">
                  {JSON.stringify(data.morningBrief.briefPayload, null, 2)}
                </pre>
                <p className="text-xs text-muted-foreground">Updated at {formatDateTime(data.morningBrief.updatedAt)}</p>
              </>
            ) : null}
          </CardContent>
        </Card>
      </section>

      {user.role === "manager" ? (
        <section className="mb-4">
          <Card>
            <CardHeader>
              <CardTitle>Executive Brief Snapshot</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {executiveBriefs.length === 0 ? <p className="text-sm text-muted-foreground">No executive brief yet.</p> : null}
              {executiveBriefs.map((item) => (
                <div key={item.id} className="rounded-md border p-2">
                  <div className="mb-1 flex items-center justify-between gap-2">
                    <p className="text-sm font-semibold text-slate-900">{item.headline ?? item.briefType}</p>
                    <Badge variant={item.status === "completed" ? "default" : item.status === "failed" ? "destructive" : "secondary"}>{item.status}</Badge>
                  </div>
                  <p className="text-xs text-slate-700">{item.summary ?? "-"}</p>
                </div>
              ))}
              <Button asChild size="sm" variant="outline">
                <Link href="/executive">Open Executive Cockpit</Link>
              </Button>
            </CardContent>
          </Card>
        </section>
      ) : null}

      <section className="mb-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>External Touchpoint Snapshot</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p>7-day events: {touchpointSummary.totalEvents}</p>
            <p>Waiting replies: {touchpointSummary.waitingReplyThreads}</p>
            <p>Upcoming meetings: {touchpointSummary.upcomingMeetings}</p>
            <p>Document updates: {touchpointSummary.documentUpdates}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Upcoming Meetings / Waiting Replies</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {touchpointHub.calendarEvents
              .filter((item) => item.meetingStatus === "scheduled")
              .slice(0, 3)
              .map((item) => (
                <p key={item.id} className="text-sm text-slate-700">
                  - [Meeting] {item.title} ({formatDateTime(item.startAt)})
                </p>
              ))}
            {touchpointHub.emailThreads
              .filter((item) => item.threadStatus === "waiting_reply")
              .slice(0, 3)
              .map((item) => (
                <p key={item.id} className="text-sm text-slate-700">
                  - [Waiting Reply] {item.subject}
                </p>
              ))}
            {touchpointHub.calendarEvents.filter((item) => item.meetingStatus === "scheduled").length === 0 &&
            touchpointHub.emailThreads.filter((item) => item.threadStatus === "waiting_reply").length === 0 ? (
              <p className="text-sm text-muted-foreground">No urgent external touchpoint item now.</p>
            ) : null}
          </CardContent>
        </Card>
      </section>

      <section className="mb-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Recent Prep Cards</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="lg:hidden">
              <MobileSectionTabs
                value={cardFilter}
                onChange={(value) => setCardFilter(value)}
                options={[
                  { value: "all", label: "全部" },
                  { value: "followup_prep", label: "跟进" },
                  { value: "quote_prep", label: "报价" },
                  { value: "meeting_prep", label: "会议" },
                  { value: "task_brief", label: "任务" },
                  { value: "manager_attention", label: "管理关注" }
                ]}
              />
            </div>
            <Select value={cardFilter} onValueChange={(value) => setCardFilter(value as PrepCardType | "all")}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All card types</SelectItem>
                <SelectItem value="followup_prep">Follow-up Prep</SelectItem>
                <SelectItem value="quote_prep">Quote Prep</SelectItem>
                <SelectItem value="meeting_prep">Meeting Prep</SelectItem>
                <SelectItem value="task_brief">Task Brief</SelectItem>
                <SelectItem value="manager_attention">Manager Attention</SelectItem>
              </SelectContent>
            </Select>

            {prepCards.length === 0 ? <p className="text-sm text-muted-foreground">No prep cards under current filter.</p> : null}
            {prepCards.slice(0, 18).map((item) => (
              <div key={item.id} className="rounded-lg border p-3">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <p className="text-sm font-semibold text-slate-900">{item.title}</p>
                  <div className="flex items-center gap-2">
                    <Badge variant="outline">{cardTypeLabel[item.cardType]}</Badge>
                    <Badge variant="secondary">{item.status}</Badge>
                  </div>
                </div>
                <p className="text-xs text-slate-700">{item.summary}</p>
                <p className="mt-1 text-xs text-muted-foreground">Updated at {formatDateTime(item.updatedAt)}</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={feedbackLoadingId === item.id}
                    onClick={() => void submitPrepFeedback(item.id, "useful")}
                  >
                    Useful
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={feedbackLoadingId === item.id}
                    onClick={() => void submitPrepFeedback(item.id, "not_useful")}
                  >
                    Not Useful
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={feedbackLoadingId === item.id}
                    onClick={() => void submitPrepFeedback(item.id, "inaccurate")}
                  >
                    Inaccurate
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={feedbackLoadingId === item.id}
                    onClick={() => void submitPrepFeedback(item.id, "adopted")}
                  >
                    Adopted
                  </Button>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Recent Action Drafts</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="lg:hidden">
              <MobileSectionTabs
                value={draftFilter}
                onChange={(value) => setDraftFilter(value)}
                options={[
                  { value: "all", label: "全部" },
                  { value: "followup_message", label: "跟进消息" },
                  { value: "quote_explanation", label: "报价说明" },
                  { value: "meeting_opening", label: "会议开场" },
                  { value: "meeting_summary", label: "会后总结" },
                  { value: "manager_checkin_note", label: "管理督导" },
                  { value: "internal_update", label: "内部更新" }
                ]}
              />
            </div>
            <Select value={draftFilter} onValueChange={(value) => setDraftFilter(value as ContentDraft["draftType"] | "all")}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All draft types</SelectItem>
                <SelectItem value="followup_message">Follow-up Message</SelectItem>
                <SelectItem value="quote_explanation">Quote Explanation</SelectItem>
                <SelectItem value="meeting_opening">Meeting Opening</SelectItem>
                <SelectItem value="meeting_summary">Meeting Summary</SelectItem>
                <SelectItem value="manager_checkin_note">Manager Check-in Note</SelectItem>
                <SelectItem value="internal_update">Internal Update</SelectItem>
              </SelectContent>
            </Select>

            {drafts.length === 0 ? <p className="text-sm text-muted-foreground">No drafts under current filter.</p> : null}
            {drafts.slice(0, 18).map((item) => (
              <div key={item.id} className="rounded-lg border p-3">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <p className="text-sm font-semibold text-slate-900">{item.title}</p>
                  <Badge variant="secondary">{item.status}</Badge>
                </div>
                <p className="line-clamp-3 text-xs text-slate-700">{item.contentText}</p>
                <p className="mt-1 text-xs text-muted-foreground">{item.draftType} | {formatDateTime(item.updatedAt)}</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={feedbackLoadingId === item.id}
                    onClick={() => void submitDraftFeedback(item.id, "useful")}
                  >
                    Useful
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={feedbackLoadingId === item.id}
                    onClick={() => void submitDraftFeedback(item.id, "not_useful")}
                  >
                    Not Useful
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={feedbackLoadingId === item.id}
                    onClick={() => void submitDraftFeedback(item.id, "inaccurate")}
                  >
                    Inaccurate
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={feedbackLoadingId === item.id}
                    onClick={() => void submitDraftFeedback(item.id, "adopted")}
                  >
                    Adopted
                  </Button>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
