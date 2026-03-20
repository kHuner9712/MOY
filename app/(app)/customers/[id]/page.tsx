"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { useAuth } from "@/components/auth/auth-provider";
import { InstallPromptCard } from "@/components/mobile/InstallPromptCard";
import { QuickActionCard } from "@/components/mobile/QuickActionCard";
import { IndustryTemplateBanner } from "@/components/shared/industry-template-banner";
import { CustomerAiPanel } from "@/components/customers/customer-ai-panel";
import { FollowupDrawer } from "@/components/customers/followup-drawer";
import { FollowupTimeline } from "@/components/customers/followup-timeline";
import { CustomerUnifiedTimeline } from "@/components/customers/customer-unified-timeline";
import { useAppData } from "@/components/shared/app-data-provider";
import { PageHeader } from "@/components/shared/page-header";
import { useOutcomes } from "@/hooks/use-outcomes";
import { usePlaybooks } from "@/hooks/use-playbooks";
import { useTouchpoints } from "@/hooks/use-touchpoints";
import { useIndustryTemplate } from "@/hooks/use-industry-template";
import { useUserMemory } from "@/hooks/use-user-memory";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { customerStageLabel, opportunityStageLabel, riskTone } from "@/lib/constants";
import { formatCurrency, formatDateTime } from "@/lib/format";
import { aiClientService } from "@/services/ai-client-service";
import { briefingHubClientService } from "@/services/briefing-hub-client-service";
import { contentDraftClientService } from "@/services/content-draft-client-service";
import { outcomeClientService } from "@/services/outcome-client-service";
import { prepClientService } from "@/services/prep-client-service";
import { touchpointClientService } from "@/services/touchpoint-client-service";
import { workItemClientService } from "@/services/work-item-client-service";
import { dealRoomClientService } from "@/services/deal-room-client-service";
import { executiveClientService, type CustomerHealthDetailPayload } from "@/services/executive-client-service";
import type { AiRun } from "@/types/ai";
import type { ContentDraft, PrepCard } from "@/types/preparation";
import type { WorkItem } from "@/types/work";
import type { DealRoom } from "@/types/deal";
import { ArrowLeft, CircleAlert, Mail, PlusCircle } from "lucide-react";

export default function CustomerDetailPage({ params }: { params: { id: string } }): JSX.Element {
  const { user } = useAuth();
  const router = useRouter();
  const { customers, opportunities, alerts, getFollowupsByCustomerId, getCommunicationInputsByCustomerId, loading, error, refreshAll } = useAppData();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [aiRuns, setAiRuns] = useState<AiRun[]>([]);
  const [aiLoading, setAiLoading] = useState(false);
  const [aiMessage, setAiMessage] = useState<string | null>(null);
  const [customerWorkItems, setCustomerWorkItems] = useState<WorkItem[]>([]);
  const [workItemMessage, setWorkItemMessage] = useState<string | null>(null);
  const [prepCards, setPrepCards] = useState<PrepCard[]>([]);
  const [contentDrafts, setContentDrafts] = useState<ContentDraft[]>([]);
  const [outcomeWorkItemId, setOutcomeWorkItemId] = useState<string | null>(null);
  const [outcomeStatus, setOutcomeStatus] = useState<"positive_progress" | "neutral" | "stalled" | "risk_increased">("neutral");
  const [outcomeUsefulness, setOutcomeUsefulness] = useState<"helpful" | "somewhat_helpful" | "not_helpful" | "unknown">("unknown");
  const [outcomeSummary, setOutcomeSummary] = useState("");
  const [outcomeNextStep, setOutcomeNextStep] = useState("");
  const [capturingOutcome, setCapturingOutcome] = useState(false);
  const [dealActionLoading, setDealActionLoading] = useState(false);
  const [dealRoomSummary, setDealRoomSummary] = useState<DealRoom | null>(null);
  const [healthDetail, setHealthDetail] = useState<CustomerHealthDetailPayload | null>(null);

  const customer = customers.find((item) => item.id === params.id);
  const { hub: touchpointHub, summary: touchpointSummary, reload: reloadTouchpoints } = useTouchpoints({
    ownerId: user?.role === "sales" ? user.id : undefined,
    customerId: customer?.id,
    enabled: Boolean(user && customer)
  });
  const { data: outcomes, reload: reloadOutcomes } = useOutcomes({
    customerId: customer?.id,
    limit: 12
  });
  const memoryTargetUserId = user?.role === "manager" ? customer?.ownerId : user?.id;
  const { profile: memoryProfile } = useUserMemory(memoryTargetUserId);
  const { data: templateContext } = useIndustryTemplate(true);
  const { data: relatedPlaybooks } = usePlaybooks({
    ownerUserId: memoryTargetUserId,
    scopeType: "user",
    includeEntries: true,
    limit: 12
  });

  const followups = useMemo(() => (customer ? getFollowupsByCustomerId(customer.id) : []), [customer, getFollowupsByCustomerId]);
  const communicationInputs = useMemo(
    () => (customer ? getCommunicationInputsByCustomerId(customer.id) : []),
    [customer, getCommunicationInputsByCustomerId]
  );
  const pendingDrafts = useMemo(() => followups.filter((item) => item.draftStatus === "draft"), [followups]);
  const relatedOpportunities = useMemo(
    () => (customer ? opportunities.filter((item) => item.customerId === customer.id) : []),
    [customer, opportunities]
  );
  const relatedAlerts = useMemo(
    () => (customer ? alerts.filter((item) => item.customerId === customer.id && item.status !== "resolved") : []),
    [alerts, customer]
  );
  const isTodayPriorityCustomer = useMemo(
    () => customerWorkItems.some((item) => item.priorityBand === "critical" && item.status !== "done" && item.status !== "cancelled"),
    [customerWorkItems]
  );

  const latestAiRun = aiRuns[0] ?? null;
  const leakAlert = useMemo(
    () =>
      relatedAlerts
        .filter((item) => item.ruleType === "high_probability_stalled" || item.ruleType === "ai_detected" || item.ruleType === "quoted_but_stalled")
        .sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime())[0] ?? null,
    [relatedAlerts]
  );

  const loadAiRuns = useCallback(async (): Promise<void> => {
    if (!customer) return;
    try {
      const runs = await aiClientService.listCustomerRuns(customer.id, 8);
      setAiRuns(runs);
    } catch (cause) {
      setAiMessage(cause instanceof Error ? cause.message : "Failed to load AI run history");
    }
  }, [customer]);

  const loadCustomerWorkItems = useCallback(async (): Promise<void> => {
    if (!customer) return;
    try {
      const workItems = await workItemClientService.getByCustomer(customer.id);
      setCustomerWorkItems(workItems);
    } catch {
      setCustomerWorkItems([]);
    }
  }, [customer]);

  const loadCustomerBriefings = useCallback(async (): Promise<void> => {
    if (!customer) return;
    try {
      const result = await briefingHubClientService.getCustomer(customer.id);
      setPrepCards(result.prepCards);
      setContentDrafts(result.contentDrafts);
    } catch {
      setPrepCards([]);
      setContentDrafts([]);
    }
  }, [customer]);

  useEffect(() => {
    void loadAiRuns();
  }, [loadAiRuns]);

  useEffect(() => {
    void loadCustomerWorkItems();
  }, [loadCustomerWorkItems]);

  useEffect(() => {
    void loadCustomerBriefings();
  }, [loadCustomerBriefings]);

  useEffect(() => {
    let cancelled = false;
    const loadDealRoom = async () => {
      if (!customer) return;
      try {
        const rooms = await dealRoomClientService.list({
          statuses: ["active", "watchlist", "escalated", "blocked", "won", "lost"],
          limit: 200
        });
        if (cancelled) return;
        const matched = rooms.find((item) => item.customerId === customer.id) ?? null;
        setDealRoomSummary(matched);
      } catch {
        if (!cancelled) setDealRoomSummary(null);
      }
    };
    void loadDealRoom();
    return () => {
      cancelled = true;
    };
  }, [customer]);

  useEffect(() => {
    let cancelled = false;
    const loadHealthDetail = async () => {
      if (!customer) return;
      try {
        const payload = await executiveClientService.getCustomerHealthDetail(customer.id);
        if (!cancelled) setHealthDetail(payload);
      } catch {
        if (!cancelled) setHealthDetail(null);
      }
    };
    void loadHealthDetail();
    return () => {
      cancelled = true;
    };
  }, [customer]);

  if (loading) {
    return <div className="text-sm text-muted-foreground">Loading customer detail...</div>;
  }

  if (error) {
    return <div className="text-sm text-rose-600">Failed to load customer detail: {error}</div>;
  }

  if (!customer) {
    return (
      <div className="rounded-xl border bg-white p-8 text-center">
        <p className="text-sm text-muted-foreground">Customer not found.</p>
        <Link href="/customers" className="mt-3 inline-flex text-sm text-sky-700">
          Back to customer list
        </Link>
      </div>
    );
  }

  if (user?.role === "sales" && customer.ownerId !== user.id) {
    return (
      <div className="rounded-xl border bg-white p-8 text-center">
        <p className="text-sm text-muted-foreground">No permission to view this customer.</p>
      </div>
    );
  }

  const handleRerunAnalysis = async (): Promise<void> => {
    setAiLoading(true);
    setAiMessage("Analysis submitted. Please wait...");
    try {
      await aiClientService.runCustomerAnalysis(customer.id);
      await refreshAll();
      await loadAiRuns();
      setAiMessage("AI analysis completed.");
    } catch (cause) {
      setAiMessage(cause instanceof Error ? cause.message : "AI analysis failed");
    } finally {
      setAiLoading(false);
    }
  };

  const handleWorkItemAction = async (workItemId: string, action: "start" | "complete"): Promise<void> => {
    try {
      if (action === "start") {
        await workItemClientService.start(workItemId);
      } else {
        const updated = await workItemClientService.complete(workItemId);
        setOutcomeWorkItemId(updated.id);
        setOutcomeStatus("neutral");
        setOutcomeUsefulness("unknown");
        setOutcomeSummary(`${updated.title} finished.`);
        setOutcomeNextStep("");
      }
      await loadCustomerWorkItems();
      setWorkItemMessage(action === "complete" ? "Task completed." : "Task started.");
    } catch (cause) {
      setWorkItemMessage(cause instanceof Error ? cause.message : "Task operation failed");
    }
  };

  const handleGeneratePrep = async (type: "followup" | "quote" | "meeting"): Promise<void> => {
    try {
      if (type === "followup") {
        await prepClientService.generateFollowup({
          customerId: customer.id
        });
      } else if (type === "quote") {
        await prepClientService.generateQuote({
          customerId: customer.id,
          opportunityId: relatedOpportunities[0]?.id
        });
      } else {
        await prepClientService.generateMeeting({
          customerId: customer.id,
          opportunityId: relatedOpportunities[0]?.id,
          meetingPurpose: "Demo and decision alignment"
        });
      }
      setWorkItemMessage("Preparation card generated.");
      await loadCustomerBriefings();
    } catch (cause) {
      setWorkItemMessage(cause instanceof Error ? cause.message : "Failed to generate preparation card");
    }
  };

  const handleGenerateDraft = async (draftType: ContentDraft["draftType"]): Promise<void> => {
    try {
      await contentDraftClientService.generate({
        draftType,
        customerId: customer.id,
        opportunityId: relatedOpportunities[0]?.id
      });
      setWorkItemMessage("Action draft generated.");
      await loadCustomerBriefings();
    } catch (cause) {
      setWorkItemMessage(cause instanceof Error ? cause.message : "Failed to generate action draft");
    }
  };

  const handleGenerateTaskBrief = async (workItemId: string): Promise<void> => {
    try {
      const result = await prepClientService.generateTaskBrief({
        workItemId
      });
      setWorkItemMessage(result.usedFallback ? "Task brief generated with fallback." : "Task brief generated.");
      await loadCustomerBriefings();
    } catch (cause) {
      setWorkItemMessage(cause instanceof Error ? cause.message : "Failed to generate task brief");
    }
  };

  const handleGenerateEmailDraft = async (
    contextType: "followup" | "quote" | "meeting_confirm" | "meeting_followup" | "manager_support"
  ): Promise<void> => {
    try {
      const result = await touchpointClientService.generateEmailDraft({
        contextType,
        customerId: customer.id,
        opportunityId: relatedOpportunities[0]?.id,
        dealRoomId: dealRoomSummary?.id ?? undefined
      });
      setWorkItemMessage(result.usedFallback ? "Email draft generated with fallback template." : "Email draft generated.");
      await reloadTouchpoints();
    } catch (cause) {
      setWorkItemMessage(cause instanceof Error ? cause.message : "Failed to generate email draft");
    }
  };

  const handleCreateMeetingTouchpoint = async (): Promise<void> => {
    try {
      const startAt = new Date(Date.now() + 24 * 60 * 60 * 1000);
      const endAt = new Date(startAt.getTime() + 45 * 60 * 1000);
      await touchpointClientService.createCalendarEvent({
        customerId: customer.id,
        opportunityId: relatedOpportunities[0]?.id,
        dealRoomId: dealRoomSummary?.id ?? undefined,
        eventType: "customer_meeting",
        title: `${customer.companyName} | Follow-up Meeting`,
        description: "Created from customer detail quick action.",
        startAt: startAt.toISOString(),
        endAt: endAt.toISOString(),
        autoGeneratePrep: true,
        autoGenerateAgenda: true
      });
      setWorkItemMessage("Meeting touchpoint created and prep/agenda generated.");
      await reloadTouchpoints();
    } catch (cause) {
      setWorkItemMessage(cause instanceof Error ? cause.message : "Failed to create meeting touchpoint");
    }
  };

  const handleUploadQuickDocument = async (): Promise<void> => {
    try {
      await touchpointClientService.uploadDocument({
        customerId: customer.id,
        opportunityId: relatedOpportunities[0]?.id,
        dealRoomId: dealRoomSummary?.id ?? undefined,
        title: `${customer.companyName} | Meeting Note`,
        fileName: `${customer.companyName.replace(/\s+/g, "-").toLowerCase()}-note.txt`,
        documentType: "meeting_note",
        extractedText: followups[0]?.summary ?? customer.aiSummary ?? "Customer note from detail quick action.",
        autoSummarize: true
      });
      setWorkItemMessage("Document touchpoint uploaded and summarized.");
      await reloadTouchpoints();
    } catch (cause) {
      setWorkItemMessage(cause instanceof Error ? cause.message : "Failed to upload quick document");
    }
  };

  const handleCaptureOutcome = async (): Promise<void> => {
    if (!outcomeWorkItemId || !customer) return;
    setCapturingOutcome(true);
    setWorkItemMessage(null);
    try {
      await outcomeClientService.capture({
        workItemId: outcomeWorkItemId,
        customerId: customer.id,
        outcomeType: "task_result",
        resultStatus: outcomeStatus,
        keyOutcomeSummary: outcomeSummary,
        nextStepDefined: Boolean(outcomeNextStep.trim()),
        nextStepText: outcomeNextStep.trim() ? outcomeNextStep.trim() : undefined,
        usefulnessRating: outcomeUsefulness,
        autoInfer: true
      });
      setWorkItemMessage("Outcome captured and linked to this customer.");
      setOutcomeWorkItemId(null);
      setOutcomeSummary("");
      setOutcomeNextStep("");
      await reloadOutcomes();
    } catch (cause) {
      setWorkItemMessage(cause instanceof Error ? cause.message : "Failed to capture outcome");
    } finally {
      setCapturingOutcome(false);
    }
  };

  const handleOpenDealRoom = async (): Promise<void> => {
    if (!customer) return;
    setDealActionLoading(true);
    setWorkItemMessage(null);
    try {
      const room = await dealRoomClientService.create({
        customerId: customer.id,
        opportunityId: relatedOpportunities[0]?.id
      });
      router.push(`/deals/${room.room.id}`);
    } catch (cause) {
      setWorkItemMessage(cause instanceof Error ? cause.message : "Failed to open deal room");
    } finally {
      setDealActionLoading(false);
    }
  };

  return (
    <div>
      <PageHeader
        title={`${customer.companyName} | Customer Detail`}
        description={`Contact: ${customer.contactName} | Owner: ${customer.ownerName}`}
        action={
          <div className="flex items-center gap-2">
            {isTodayPriorityCustomer ? <Badge variant="destructive">Today Priority</Badge> : null}
            <Button asChild variant="outline">
              <Link href="/customers">
                <ArrowLeft className="mr-1 h-4 w-4" />
                Back
              </Link>
            </Button>
            <Button onClick={() => setDrawerOpen(true)}>
              <PlusCircle className="mr-1 h-4 w-4" />
              New Followup
            </Button>
            <Button asChild variant="outline">
              <Link href="/briefings">Briefings Hub</Link>
            </Button>
            <Button asChild variant="outline">
              <Link href="/touchpoints">Touchpoints</Link>
            </Button>
            <Button variant="outline" onClick={() => void handleOpenDealRoom()} disabled={dealActionLoading}>
              {dealActionLoading ? "Opening..." : "Open Deal Room"}
            </Button>
          </div>
        }
      />
      <InstallPromptCard />
      <IndustryTemplateBanner className="mb-3" compact />

      <section className="mb-4 space-y-3 lg:hidden">
        <QuickActionCard
          title={customer.companyName}
          subtitle={`阶段：${customerStageLabel[customer.stage]} | 负责人：${customer.ownerName}`}
          right={<Badge variant={riskTone[customer.riskLevel]}>{customer.riskLevel}</Badge>}
        >
          <p className="text-xs text-slate-700">下次跟进：{formatDateTime(customer.nextFollowupAt)}</p>
          <p className="text-xs text-slate-700">最近沟通：{followups[0]?.summary ?? "暂无"}</p>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" onClick={() => setDrawerOpen(true)}>
              快速补录
            </Button>
            <Button size="sm" variant="outline" onClick={() => void handleGeneratePrep("followup")}>
              生成准备卡
            </Button>
            <Button size="sm" variant="outline" onClick={() => void handleOpenDealRoom()} disabled={dealActionLoading}>
              查看 Deal
            </Button>
          </div>
        </QuickActionCard>
      </section>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader>
            <CardTitle>Basic Information</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 md:grid-cols-2">
            <Info label="Customer Name" value={customer.customerName} />
            <Info label="Company" value={customer.companyName} />
            <Info label="Contact" value={customer.contactName} />
            <Info label="Phone" value={customer.phone} />
            <Info label="Email" value={customer.email} />
            <Info label="Source" value={customer.sourceChannel} />
            <Info label="Owner" value={customer.ownerName} />
            <Info label="Stage" value={customerStageLabel[customer.stage]} />
            <Info label="Last Followup" value={formatDateTime(customer.lastFollowupAt)} />
            <Info label="Next Followup" value={formatDateTime(customer.nextFollowupAt)} />
            <Info label="Win Probability" value={`${customer.winProbability}%`} />
            <Info
              label="Risk Level"
              value={
                <Badge variant={riskTone[customer.riskLevel]}>
                  {customer.riskLevel === "high" ? "High" : customer.riskLevel === "medium" ? "Medium" : "Low"}
                </Badge>
              }
            />
            <div className="md:col-span-2">
              <p className="text-xs text-muted-foreground">Tags</p>
              <div className="mt-1 flex flex-wrap gap-2">
                {customer.tags.map((tag) => (
                  <Badge key={tag} variant="outline">
                    {tag}
                  </Badge>
                ))}
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Risk Alerts</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {relatedAlerts.length === 0 ? (
              <p className="text-sm text-muted-foreground">No unresolved risk alert.</p>
            ) : (
              relatedAlerts.map((alert) => (
                <div key={alert.id} className="rounded-lg border border-rose-100 bg-rose-50/70 p-3">
                  <p className="flex items-center gap-2 text-sm font-semibold text-rose-900">
                    <CircleAlert className="h-4 w-4" />
                    {alert.level === "critical" ? "Critical Risk" : "Warning"}
                  </p>
                  <p className="mt-2 text-xs leading-5 text-rose-700">{alert.message}</p>
                  {alert.evidence.length > 0 ? (
                    <p className="mt-1 text-xs text-rose-700">Evidence: {alert.evidence.slice(0, 2).join("; ")}</p>
                  ) : null}
                </div>
              ))
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Health & Retention Signals</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            {!healthDetail?.snapshot ? <p className="text-muted-foreground">No health snapshot yet.</p> : null}
            {healthDetail?.snapshot ? (
              <>
                <div className="flex items-center justify-between gap-2">
                  <p className="font-semibold text-slate-900">{healthDetail.snapshot.summary ?? "Health snapshot ready."}</p>
                  <Badge variant={healthDetail.snapshot.healthBand === "critical" ? "destructive" : healthDetail.snapshot.healthBand === "at_risk" ? "default" : "secondary"}>
                    {healthDetail.snapshot.healthBand} / {healthDetail.snapshot.overallHealthScore}
                  </Badge>
                </div>
                <p className="text-xs text-muted-foreground">
                  activity={healthDetail.snapshot.activityScore} · engagement={healthDetail.snapshot.engagementScore} · progression=
                  {healthDetail.snapshot.progressionScore}
                </p>
              </>
            ) : null}
            {healthDetail?.renewalWatch ? (
              <div className="rounded-md border bg-slate-50 p-2">
                <p className="text-xs text-muted-foreground">renewal_watch</p>
                <p className="text-sm text-slate-700">
                  {healthDetail.renewalWatch.renewalStatus} {healthDetail.renewalWatch.recommendationSummary ? `· ${healthDetail.renewalWatch.recommendationSummary}` : ""}
                </p>
              </div>
            ) : null}
            {(healthDetail?.relatedEvents ?? []).slice(0, 3).map((event) => (
              <div key={event.id} className="rounded border p-2">
                <div className="mb-1 flex items-center gap-2">
                  <Badge variant="outline">{event.eventType}</Badge>
                  <Badge variant={event.severity === "critical" ? "destructive" : event.severity === "warning" ? "default" : "secondary"}>{event.severity}</Badge>
                </div>
                <p className="text-xs text-slate-700">{event.eventSummary}</p>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card className="xl:col-span-3">
          <CardHeader>
            <CardTitle>Related Work Items</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {workItemMessage ? <p className="text-xs text-muted-foreground">{workItemMessage}</p> : null}
            {customerWorkItems.length === 0 ? <p className="text-sm text-muted-foreground">No linked task for this customer.</p> : null}
            {customerWorkItems.slice(0, 10).map((item) => (
              <div key={item.id} className="rounded-lg border p-3">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-sm font-semibold text-slate-900">{item.title}</p>
                  <div className="flex items-center gap-2">
                    <Badge variant="outline">{item.priorityBand}</Badge>
                    <Badge variant="secondary">{item.status}</Badge>
                  </div>
                </div>
                <p className="mt-1 text-xs text-slate-700">{item.rationale}</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  <Button size="sm" variant="outline" onClick={() => void handleWorkItemAction(item.id, "start")}>
                    Start
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => void handleWorkItemAction(item.id, "complete")}>
                    Complete
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      setOutcomeWorkItemId(item.id);
                      setOutcomeStatus("neutral");
                      setOutcomeUsefulness("unknown");
                      setOutcomeSummary(`${item.title} finished.`);
                      setOutcomeNextStep("");
                    }}
                  >
                    Capture Outcome
                  </Button>
                  <Button size="sm" variant="secondary" onClick={() => void handleGenerateTaskBrief(item.id)}>
                    Generate Task Brief
                  </Button>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>

        {outcomeWorkItemId ? (
          <Card className="xl:col-span-3 border-amber-200">
            <CardHeader>
              <CardTitle>Capture Action Outcome</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-3 md:grid-cols-2">
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
                <p className="mb-1 text-xs text-muted-foreground">Prep/draft helpfulness</p>
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
                <Textarea value={outcomeSummary} onChange={(event) => setOutcomeSummary(event.target.value)} placeholder="What changed after this action?" />
              </div>
              <div className="md:col-span-2">
                <p className="mb-1 text-xs text-muted-foreground">Next step</p>
                <Textarea value={outcomeNextStep} onChange={(event) => setOutcomeNextStep(event.target.value)} placeholder="What should happen next?" />
              </div>
              <div className="md:col-span-2 flex flex-wrap gap-2">
                <Button onClick={() => void handleCaptureOutcome()} disabled={capturingOutcome}>
                  {capturingOutcome ? "Saving..." : "Save Outcome"}
                </Button>
                <Button
                  variant="outline"
                  disabled={capturingOutcome}
                  onClick={() => {
                    setOutcomeWorkItemId(null);
                    setOutcomeSummary("");
                    setOutcomeNextStep("");
                  }}
                >
                  Cancel
                </Button>
              </div>
            </CardContent>
          </Card>
        ) : null}

        <Card className="xl:col-span-3">
          <CardHeader>
            <CardTitle>Recent Outcome Loop</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {outcomes.length === 0 ? <p className="text-sm text-muted-foreground">No outcomes captured for this customer.</p> : null}
            {outcomes.slice(0, 8).map((item) => (
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
                {item.nextStepText ? <p className="mt-1 text-xs text-slate-700">Next: {item.nextStepText}</p> : null}
              </div>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Deal Room Status</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            {dealRoomSummary ? (
              <>
                <p className="font-semibold text-slate-900">{dealRoomSummary.title}</p>
                <p className="text-slate-700">{dealRoomSummary.commandSummary || dealRoomSummary.currentGoal || "No command summary yet."}</p>
                <div className="flex gap-2">
                  <Badge variant="secondary">{dealRoomSummary.roomStatus}</Badge>
                  <Badge variant={dealRoomSummary.priorityBand === "critical" ? "destructive" : "outline"}>{dealRoomSummary.priorityBand}</Badge>
                </div>
                <Button asChild size="sm" variant="outline">
                  <Link href={`/deals/${dealRoomSummary.id}`}>Open Deal Room</Link>
                </Button>
              </>
            ) : (
              <p className="text-muted-foreground">No deal room linked yet.</p>
            )}
          </CardContent>
        </Card>

        <Card className="xl:col-span-3">
          <CardHeader>
            <CardTitle>External Touchpoints</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="grid gap-3 md:grid-cols-4">
              <div className="rounded-lg border p-3">
                <p className="text-xs text-muted-foreground">7d events</p>
                <p className="text-xl font-semibold text-slate-900">{touchpointSummary.totalEvents}</p>
              </div>
              <div className="rounded-lg border p-3">
                <p className="text-xs text-muted-foreground">waiting reply</p>
                <p className="text-xl font-semibold text-slate-900">{touchpointSummary.waitingReplyThreads}</p>
              </div>
              <div className="rounded-lg border p-3">
                <p className="text-xs text-muted-foreground">upcoming meetings</p>
                <p className="text-xl font-semibold text-slate-900">{touchpointSummary.upcomingMeetings}</p>
              </div>
              <div className="rounded-lg border p-3">
                <p className="text-xs text-muted-foreground">document updates</p>
                <p className="text-xl font-semibold text-slate-900">{touchpointSummary.documentUpdates}</p>
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" onClick={() => void handleGenerateEmailDraft("followup")}>
                <Mail className="mr-1 h-4 w-4" />
                Generate Follow-up Email
              </Button>
              <Button variant="outline" onClick={() => void handleGenerateEmailDraft("quote")}>
                Generate Quote Email
              </Button>
              <Button variant="outline" onClick={() => void handleCreateMeetingTouchpoint()}>
                Create Meeting Touchpoint
              </Button>
              <Button variant="outline" onClick={() => void handleUploadQuickDocument()}>
                Upload Quick Document
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card className="xl:col-span-3">
          <CardHeader>
            <CardTitle>Preparation Actions</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => void handleGeneratePrep("followup")}>
              Generate Follow-up Prep
            </Button>
            <Button variant="outline" onClick={() => void handleGeneratePrep("quote")}>
              Generate Quote Prep
            </Button>
            <Button variant="outline" onClick={() => void handleGeneratePrep("meeting")}>
              Generate Meeting Prep
            </Button>
            <Button variant="secondary" onClick={() => void handleGenerateDraft("followup_message")}>
              Generate Follow-up Draft
            </Button>
            <Button variant="secondary" onClick={() => void handleGenerateDraft("quote_explanation")}>
              Generate Quote Draft
            </Button>
            <Button variant="secondary" onClick={() => void handleGenerateDraft("meeting_opening")}>
              Generate Meeting Opening Draft
            </Button>
          </CardContent>
        </Card>

        <Card className="xl:col-span-3">
          <CardHeader>
            <CardTitle>Recent Prep Cards</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {prepCards.length === 0 ? <p className="text-sm text-muted-foreground">No prep card yet.</p> : null}
            {prepCards.slice(0, 9).map((item) => (
              <div key={item.id} className="rounded-lg border p-3">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <p className="text-sm font-semibold text-slate-900">{item.title}</p>
                  <Badge variant="secondary">{item.status}</Badge>
                </div>
                <p className="line-clamp-3 text-xs text-slate-700">{item.summary}</p>
                <p className="mt-1 text-xs text-muted-foreground">{formatDateTime(item.updatedAt)}</p>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card className="xl:col-span-3">
          <CardHeader>
            <CardTitle>Recent Action Drafts</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {contentDrafts.length === 0 ? <p className="text-sm text-muted-foreground">No draft yet.</p> : null}
            {contentDrafts.slice(0, 9).map((item) => (
              <div key={item.id} className="rounded-lg border p-3">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <p className="text-sm font-semibold text-slate-900">{item.title}</p>
                  <Badge variant="outline">{item.draftType}</Badge>
                </div>
                <p className="line-clamp-3 text-xs text-slate-700">{item.contentText}</p>
                <p className="mt-1 text-xs text-muted-foreground">{formatDateTime(item.updatedAt)}</p>
              </div>
            ))}
          </CardContent>
        </Card>

        <div className="xl:col-span-2">
          <CustomerUnifiedTimeline customerId={customer.id} />
        </div>

        <CustomerAiPanel
          customer={customer}
          latestRun={latestAiRun}
          runs={aiRuns}
          onRerun={handleRerunAnalysis}
          running={aiLoading}
          rerunMessage={aiMessage}
          leakAlert={leakAlert}
        />

        <Card>
          <CardHeader>
            <CardTitle>Memory Enhanced Suggestions</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p className="text-slate-700">Suggested playbook: {memoryProfile?.effectiveTactics?.[0] ?? "Start from decision-maker alignment and clear next step."}</p>
            <p className="text-slate-700">Objection handling: {memoryProfile?.commonObjections?.[0] ?? "Clarify concern source and propose phased plan."}</p>
            <p className="text-slate-700">Blind-spot reminder: {memoryProfile?.riskBlindSpots?.[0] ?? "Avoid high-probability customer stagnation."}</p>
          </CardContent>
        </Card>

        {templateContext?.template ? (
          <Card>
            <CardHeader>
              <CardTitle>Industry Path Hint</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <p>
                Template: <Badge variant="secondary">{templateContext.template.displayName}</Badge>
              </p>
              <p>
                Suggested checkpoints:{" "}
                {((templateContext.template.templatePayload.suggested_checkpoints as string[] | undefined) ?? []).slice(0, 4).join(" / ") || "-"}
              </p>
              <p>
                Manager signals:{" "}
                {((templateContext.template.templatePayload.manager_attention_signals as string[] | undefined) ?? []).slice(0, 3).join(" / ") || "-"}
              </p>
            </CardContent>
          </Card>
        ) : null}

        <Card>
          <CardHeader>
            <CardTitle>Relevant Playbooks</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            {relatedPlaybooks.length === 0 ? <p className="text-muted-foreground">No related playbook yet.</p> : null}
            {relatedPlaybooks.slice(0, 4).map((item) => (
              <div key={item.playbook.id} className="rounded-lg border p-2">
                <p className="font-semibold text-slate-900">{item.playbook.title}</p>
                <p className="text-xs text-slate-700">{item.playbook.summary}</p>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card className="xl:col-span-3">
          <CardHeader>
            <CardTitle>Raw Communication Inputs</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {communicationInputs.length === 0 ? (
              <p className="text-sm text-muted-foreground">No raw communication input linked.</p>
            ) : (
              communicationInputs.map((item) => (
                <div key={item.id} className="rounded-lg border bg-slate-50 p-3">
                  <div className="mb-1 flex items-center justify-between gap-2">
                    <p className="text-sm font-semibold text-slate-900">{item.title}</p>
                    <Badge variant={item.extractionStatus === "completed" ? "default" : item.extractionStatus === "failed" ? "destructive" : "secondary"}>
                      {item.extractionStatus}
                    </Badge>
                  </div>
                  <p className="text-xs text-muted-foreground">{formatDateTime(item.createdAt)}</p>
                  <p className="mt-2 line-clamp-3 text-xs text-slate-700">{item.rawContent}</p>
                  {item.extractedFollowupId ? <p className="mt-1 text-xs text-muted-foreground">Followup: {item.extractedFollowupId}</p> : null}
                </div>
              ))
            )}
          </CardContent>
        </Card>

        <Card className="xl:col-span-3">
          <CardHeader>
            <CardTitle>Email / Meeting / Document Touchpoints</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 md:grid-cols-3">
            <div className="space-y-2">
              <p className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Email threads</p>
              {touchpointHub.emailThreads.length === 0 ? <p className="text-sm text-muted-foreground">No email thread yet.</p> : null}
              {touchpointHub.emailThreads.slice(0, 4).map((item) => (
                <div key={item.id} className="rounded border p-2">
                  <p className="text-sm font-semibold text-slate-900">{item.subject}</p>
                  <p className="text-xs text-muted-foreground">{item.threadStatus} | {item.latestMessageAt ? formatDateTime(item.latestMessageAt) : "-"}</p>
                </div>
              ))}
            </div>

            <div className="space-y-2">
              <p className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Meetings</p>
              {touchpointHub.calendarEvents.length === 0 ? <p className="text-sm text-muted-foreground">No meeting event yet.</p> : null}
              {touchpointHub.calendarEvents.slice(0, 4).map((item) => (
                <div key={item.id} className="rounded border p-2">
                  <p className="text-sm font-semibold text-slate-900">{item.title}</p>
                  <p className="text-xs text-muted-foreground">{item.meetingStatus} | {formatDateTime(item.startAt)}</p>
                </div>
              ))}
            </div>

            <div className="space-y-2">
              <p className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Documents</p>
              {touchpointHub.documentAssets.length === 0 ? <p className="text-sm text-muted-foreground">No document asset yet.</p> : null}
              {touchpointHub.documentAssets.slice(0, 4).map((item) => (
                <div key={item.id} className="rounded border p-2">
                  <p className="text-sm font-semibold text-slate-900">{item.title}</p>
                  <p className="text-xs text-muted-foreground">{item.documentType} | {formatDateTime(item.updatedAt)}</p>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        <Card className="xl:col-span-3">
          <CardHeader>
            <CardTitle>Pending Drafts</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {pendingDrafts.length === 0 ? (
              <p className="text-sm text-muted-foreground">No pending draft.</p>
            ) : (
              pendingDrafts.map((item) => (
                <div key={item.id} className="rounded-lg border p-3">
                  <p className="text-sm font-semibold text-slate-900">{item.summary}</p>
                  <p className="mt-1 text-xs text-muted-foreground">Next step: {item.nextPlan}</p>
                  <p className="mt-1 text-xs text-muted-foreground">Source input: {item.sourceInputId ?? "N/A"}</p>
                </div>
              ))
            )}
          </CardContent>
        </Card>

        <Card className="xl:col-span-3">
          <CardHeader>
            <CardTitle>Opportunity Overview</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {relatedOpportunities.length === 0 ? (
              <p className="text-sm text-muted-foreground">No opportunity linked.</p>
            ) : (
              relatedOpportunities.map((item) => (
                <div key={item.id} className="rounded-lg border p-3">
                  <p className="text-sm font-semibold text-slate-900">{item.name}</p>
                  <p className="mt-1 text-xs text-muted-foreground">Last progress: {formatDateTime(item.lastProgressAt)}</p>
                  <div className="mt-2 flex items-center justify-between">
                    <Badge variant="outline">{opportunityStageLabel[item.stage]}</Badge>
                    <span className="text-sm font-semibold text-slate-900">{formatCurrency(item.expectedAmount)}</span>
                  </div>
                </div>
              ))
            )}
          </CardContent>
        </Card>
      </div>

      <FollowupDrawer customerId={customer.id} open={drawerOpen} onOpenChange={setDrawerOpen} />
    </div>
  );
}

function Info({ label, value }: { label: string; value: React.ReactNode }): JSX.Element {
  return (
    <div>
      <p className="text-xs text-muted-foreground">{label}</p>
      <div className="mt-1 text-sm font-medium text-slate-900">{value}</div>
    </div>
  );
}
