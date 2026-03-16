"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { InstallPromptCard } from "@/components/mobile/InstallPromptCard";
import { QuickActionCard } from "@/components/mobile/QuickActionCard";
import { IndustryTemplateBanner } from "@/components/shared/industry-template-banner";
import { PageHeader } from "@/components/shared/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useTouchpoints } from "@/hooks/use-touchpoints";
import { useDealRoom } from "@/hooks/use-deal-room";
import { useIndustryTemplate } from "@/hooks/use-industry-template";
import { formatDateTime } from "@/lib/format";
import { dealRoomClientService } from "@/services/deal-room-client-service";
import { executiveClientService, type DealOpsEventsPayload } from "@/services/executive-client-service";
import { touchpointClientService } from "@/services/touchpoint-client-service";
import type { CalendarEventType, DocumentAssetType } from "@/types/touchpoint";

export default function DealRoomDetailPage({ params }: { params: { id: string } }): JSX.Element {
  const { user } = useAuth();
  const { data, loading, error, reload } = useDealRoom(params.id);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [threadId, setThreadId] = useState("");
  const [threadMessage, setThreadMessage] = useState("");
  const [newThreadTitle, setNewThreadTitle] = useState("");
  const [threadType, setThreadType] = useState<
    "strategy" | "blocker" | "quote_review" | "next_step" | "risk_discussion" | "manager_intervention" | "playbook_application"
  >("strategy");
  const [decisionTitle, setDecisionTitle] = useState("");
  const [interventionSummary, setInterventionSummary] = useState("");
  const [emailSubject, setEmailSubject] = useState("");
  const [emailBody, setEmailBody] = useState("");
  const [meetingTitle, setMeetingTitle] = useState("");
  const [meetingType, setMeetingType] = useState<CalendarEventType>("customer_meeting");
  const [documentTitle, setDocumentTitle] = useState("");
  const [documentType, setDocumentType] = useState<DocumentAssetType>("other");
  const [documentText, setDocumentText] = useState("");
  const [opsEvents, setOpsEvents] = useState<DealOpsEventsPayload | null>(null);

  const { hub: touchpointHub, summary: touchpointSummary, reload: reloadTouchpoints } = useTouchpoints({
    dealRoomId: params.id,
    enabled: true
  });
  const { data: templateContext } = useIndustryTemplate(true);

  useEffect(() => {
    let cancelled = false;
    const loadOpsEvents = async () => {
      try {
        const payload = await executiveClientService.getDealOpsEvents(params.id);
        if (!cancelled) setOpsEvents(payload);
      } catch {
        if (!cancelled) setOpsEvents(null);
      }
    };
    void loadOpsEvents();
    return () => {
      cancelled = true;
    };
  }, [params.id, data?.room.updatedAt]);

  const runAction = async (action: () => Promise<void>, successText: string): Promise<void> => {
    setBusy(true);
    setMessage(null);
    try {
      await action();
      setMessage(successText);
      await reload();
      await reloadTouchpoints();
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "Action failed");
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <div className="text-sm text-muted-foreground">Loading deal room...</div>;
  if (error) return <div className="text-sm text-rose-600">{error}</div>;
  if (!data) return <div className="text-sm text-muted-foreground">Deal room not found.</div>;

  return (
    <div>
      <PageHeader
        title={data.room.title}
        description={`Customer: ${data.room.customerName} · Owner: ${data.room.ownerName}`}
        action={
          <div className="flex items-center gap-2">
            <Button asChild variant="outline">
              <Link href="/deals">Back to deals</Link>
            </Button>
            <Button asChild variant="outline">
              <Link href="/touchpoints">Touchpoints</Link>
            </Button>
            <Button
              disabled={busy}
              onClick={() =>
                void runAction(
                  async () => {
                    await dealRoomClientService.refreshCommand(data.room.id);
                  },
                  "Command summary refreshed."
                )
              }
            >
              Refresh Command
            </Button>
          </div>
        }
      />

      {message ? <p className="mb-3 text-sm text-muted-foreground">{message}</p> : null}
      <InstallPromptCard />
      <IndustryTemplateBanner className="mb-3" compact />

      <section className="mb-4 space-y-3 lg:hidden">
        <QuickActionCard
          title={data.room.title}
          subtitle={data.room.commandSummary || "暂无 command summary"}
          right={<Badge variant={data.room.priorityBand === "critical" ? "destructive" : "outline"}>{data.room.priorityBand}</Badge>}
        >
          <p className="text-xs text-slate-700">目标：{data.room.currentGoal || "-"}</p>
          <p className="text-xs text-slate-700">下一里程碑：{data.room.nextMilestone || "-"}</p>
          <div className="flex flex-wrap gap-2">
            <Badge variant="secondary">状态 {data.room.roomStatus}</Badge>
            {data.room.managerAttentionNeeded ? <Badge variant="destructive">需管理介入</Badge> : null}
          </div>
        </QuickActionCard>
      </section>

      <section className="mb-4 grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader>
            <CardTitle>Command Summary</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <p className="text-sm text-slate-800">{data.room.commandSummary || "No command summary yet."}</p>
            <p className="text-xs text-muted-foreground">Current goal: {data.room.currentGoal || "-"}</p>
            <p className="text-xs text-muted-foreground">
              Next milestone: {data.room.nextMilestone || "-"} {data.room.nextMilestoneDueAt ? `(${formatDateTime(data.room.nextMilestoneDueAt)})` : ""}
            </p>
            <div className="flex flex-wrap gap-2">
              <Badge variant="secondary">{data.room.roomStatus}</Badge>
              <Badge variant={data.room.priorityBand === "critical" ? "destructive" : "outline"}>{data.room.priorityBand}</Badge>
              {data.room.managerAttentionNeeded ? <Badge variant="destructive">Manager attention</Badge> : null}
            </div>
            {data.room.currentBlockers.length > 0 ? (
              <div className="rounded-lg border bg-slate-50 p-2 text-xs">
                {data.room.currentBlockers.map((item) => (
                  <p key={item}>- {item}</p>
                ))}
              </div>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Participants</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {data.participants.map((item) => (
              <div key={item.id} className="rounded border px-2 py-1">
                <p className="text-sm font-medium text-slate-900">{item.userName}</p>
                <p className="text-xs text-muted-foreground">{item.roleInRoom}</p>
              </div>
            ))}
          </CardContent>
        </Card>
      </section>

      <section className="mb-4">
        <Card>
          <CardHeader>
            <CardTitle>Operating Signals</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 xl:grid-cols-2">
            <div className="space-y-2">
              <p className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Business events</p>
              {(opsEvents?.events ?? []).length === 0 ? <p className="text-sm text-muted-foreground">No linked operating event.</p> : null}
              {(opsEvents?.events ?? []).slice(0, 6).map((event) => (
                <div key={event.id} className="rounded border p-2">
                  <div className="mb-1 flex items-center gap-2">
                    <Badge variant="outline">{event.eventType}</Badge>
                    <Badge variant={event.severity === "critical" ? "destructive" : event.severity === "warning" ? "default" : "secondary"}>
                      {event.severity}
                    </Badge>
                  </div>
                  <p className="text-xs text-slate-700">{event.eventSummary}</p>
                </div>
              ))}
            </div>
            <div className="space-y-2">
              <p className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Recommended actions</p>
              {(opsEvents?.recommendedActions ?? []).length === 0 ? <p className="text-sm text-muted-foreground">No recommended action yet.</p> : null}
              {(opsEvents?.recommendedActions ?? []).map((item) => (
                <p key={item} className="text-sm text-slate-700">
                  - {item}
                </p>
              ))}
              <Button asChild size="sm" variant="outline">
                <Link href="/executive">Open Executive Cockpit</Link>
              </Button>
            </div>
          </CardContent>
        </Card>
      </section>

      {templateContext?.template ? (
        <section className="mb-4">
          <Card>
            <CardHeader>
              <CardTitle>Industry Suggested Path</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <p>
                Template: <Badge variant="secondary">{templateContext.template.displayName}</Badge>
              </p>
              <p>
                Suggested checkpoints:{" "}
                {((templateContext.template.templatePayload.suggested_checkpoints as string[] | undefined) ?? []).join(" / ") || "-"}
              </p>
              <p>
                Manager signals:{" "}
                {((templateContext.template.templatePayload.manager_attention_signals as string[] | undefined) ?? []).slice(0, 4).join(" / ") || "-"}
              </p>
              <p>
                Scenario hints: {templateContext.scenarioPacks.slice(0, 3).map((item) => item.title).join(" / ") || "-"}
              </p>
            </CardContent>
          </Card>
        </section>
      ) : null}

      <section className="mb-4 grid gap-4 xl:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>External Progress Snapshot</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p>7d touchpoint events: {touchpointSummary.totalEvents}</p>
            <p>waiting reply threads: {touchpointSummary.waitingReplyThreads}</p>
            <p>upcoming meetings: {touchpointSummary.upcomingMeetings}</p>
            <p>document updates: {touchpointSummary.documentUpdates}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Quick Email Touchpoint</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <Input value={emailSubject} onChange={(event) => setEmailSubject(event.target.value)} placeholder="Email subject" />
            <Textarea value={emailBody} onChange={(event) => setEmailBody(event.target.value)} placeholder="Email body or pasted message" />
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                disabled={busy}
                onClick={() =>
                  void runAction(
                    async () => {
                      await touchpointClientService.createEmailThread({
                        customerId: data.room.customerId,
                        opportunityId: data.room.opportunityId ?? undefined,
                        dealRoomId: data.room.id,
                        subject: emailSubject || `${data.room.customerName} follow-up`,
                        summary: emailBody.slice(0, 200),
                        direction: "outbound",
                        messageSubject: emailSubject || `${data.room.customerName} follow-up`,
                        messageBodyText: emailBody,
                        messageBodyMarkdown: emailBody,
                        status: "sent"
                      });
                      setEmailBody("");
                    },
                    "Email touchpoint saved."
                  )
                }
              >
                Save Email
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() =>
                  void runAction(
                    async () => {
                      await touchpointClientService.generateEmailDraft({
                        contextType: "followup",
                        customerId: data.room.customerId,
                        opportunityId: data.room.opportunityId ?? undefined,
                        dealRoomId: data.room.id
                      });
                    },
                    "Email draft generated."
                  )
                }
              >
                Generate Draft
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Quick Meeting / Document</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <Input value={meetingTitle} onChange={(event) => setMeetingTitle(event.target.value)} placeholder="Meeting title" />
            <Select value={meetingType} onValueChange={(value) => setMeetingType(value as CalendarEventType)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="customer_meeting">customer_meeting</SelectItem>
                <SelectItem value="demo">demo</SelectItem>
                <SelectItem value="proposal_review">proposal_review</SelectItem>
                <SelectItem value="manager_intervention">manager_intervention</SelectItem>
                <SelectItem value="internal_strategy">internal_strategy</SelectItem>
              </SelectContent>
            </Select>
            <Button
              size="sm"
              disabled={busy}
              onClick={() =>
                void runAction(
                  async () => {
                    const start = new Date(Date.now() + 24 * 60 * 60 * 1000);
                    const end = new Date(start.getTime() + 45 * 60 * 1000);
                    await touchpointClientService.createCalendarEvent({
                      customerId: data.room.customerId,
                      opportunityId: data.room.opportunityId ?? undefined,
                      dealRoomId: data.room.id,
                      eventType: meetingType,
                      title: meetingTitle || `${data.room.customerName} meeting`,
                      description: "Created from deal room quick action.",
                      startAt: start.toISOString(),
                      endAt: end.toISOString(),
                      autoGenerateAgenda: true,
                      autoGeneratePrep: true
                    });
                  },
                  "Meeting touchpoint created."
                )
              }
            >
              Create Meeting
            </Button>

            <Input value={documentTitle} onChange={(event) => setDocumentTitle(event.target.value)} placeholder="Document title" />
            <Select value={documentType} onValueChange={(value) => setDocumentType(value as DocumentAssetType)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="proposal">proposal</SelectItem>
                <SelectItem value="quote">quote</SelectItem>
                <SelectItem value="contract_draft">contract_draft</SelectItem>
                <SelectItem value="meeting_note">meeting_note</SelectItem>
                <SelectItem value="product_material">product_material</SelectItem>
                <SelectItem value="case_study">case_study</SelectItem>
                <SelectItem value="other">other</SelectItem>
              </SelectContent>
            </Select>
            <Textarea value={documentText} onChange={(event) => setDocumentText(event.target.value)} placeholder="Pasted document text" />
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() =>
                void runAction(
                  async () => {
                    await touchpointClientService.uploadDocument({
                      customerId: data.room.customerId,
                      opportunityId: data.room.opportunityId ?? undefined,
                      dealRoomId: data.room.id,
                      title: documentTitle || "Deal document",
                      fileName: `${(documentTitle || "deal-document").replace(/\s+/g, "-").toLowerCase()}.txt`,
                      documentType,
                      extractedText: documentText,
                      autoSummarize: true
                    });
                  },
                  "Document touchpoint uploaded."
                )
              }
            >
              Upload Document
            </Button>
          </CardContent>
        </Card>
      </section>

      <section className="mb-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Checkpoints</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {data.checkpoints.map((item) => (
              <div key={item.id} className="rounded border p-2">
                <div className="mb-1 flex items-center justify-between">
                  <p className="text-sm font-semibold text-slate-900">{item.title}</p>
                  <Badge variant={item.status === "blocked" ? "destructive" : item.status === "completed" ? "default" : "secondary"}>{item.status}</Badge>
                </div>
                <p className="text-xs text-muted-foreground">{item.description || "-"}</p>
                <div className="mt-2 flex gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() =>
                      void runAction(
                        async () => {
                          await dealRoomClientService.updateCheckpointStatus(data.room.id, {
                            checkpointId: item.id,
                            status: "completed"
                          });
                        },
                        "Checkpoint completed."
                      )
                    }
                  >
                    Complete
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() =>
                      void runAction(
                        async () => {
                          await dealRoomClientService.updateCheckpointStatus(data.room.id, {
                            checkpointId: item.id,
                            status: "blocked",
                            blockedReason: "Blocked and needs intervention"
                          });
                        },
                        "Checkpoint blocked and escalated."
                      )
                    }
                  >
                    Block
                  </Button>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Decision & Intervention</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="rounded-lg border border-dashed p-2">
              <p className="mb-1 text-sm font-semibold text-slate-900">Create decision</p>
              <Input value={decisionTitle} onChange={(event) => setDecisionTitle(event.target.value)} placeholder="Decision title" />
              <Button
                className="mt-2"
                size="sm"
                disabled={busy}
                onClick={() =>
                  void runAction(
                    async () => {
                      await dealRoomClientService.createDecision(data.room.id, {
                        decisionType: "quote_strategy",
                        title: decisionTitle || "Quote strategy decision",
                        contextSummary: data.room.currentGoal || "",
                        optionsConsidered: ["Option A", "Option B"],
                        status: user?.role === "manager" ? "approved" : "proposed",
                        includeDecisionSupport: true
                      });
                      setDecisionTitle("");
                    },
                    "Decision created."
                  )
                }
              >
                Save decision
              </Button>
            </div>

            <div className="rounded-lg border border-dashed p-2">
              <p className="mb-1 text-sm font-semibold text-slate-900">Create intervention request</p>
              <Textarea value={interventionSummary} onChange={(event) => setInterventionSummary(event.target.value)} placeholder="What manager/support action is needed?" />
              <Button
                className="mt-2"
                size="sm"
                disabled={busy}
                onClick={() =>
                  void runAction(
                    async () => {
                      await dealRoomClientService.createIntervention(data.room.id, {
                        requestType: "manager_join_call",
                        requestSummary: interventionSummary || "Need manager to join next key call.",
                        priorityBand: "high",
                        includeRecommendation: true
                      });
                      setInterventionSummary("");
                    },
                    "Intervention request created."
                  )
                }
              >
                Save intervention
              </Button>
            </div>
          </CardContent>
        </Card>
      </section>

      <section className="mb-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Collaboration Threads</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {data.threads.map((item) => (
              <div key={item.id} className="rounded border p-2">
                <div className="mb-1 flex items-center justify-between">
                  <p className="text-sm font-semibold text-slate-900">{item.title}</p>
                  <Badge variant="secondary">{item.threadType}</Badge>
                </div>
                <p className="text-xs text-muted-foreground">{item.summary || "No summary."}</p>
                <div className="mt-2 flex gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() =>
                      void runAction(
                        async () => {
                          await dealRoomClientService.summarizeThread(data.room.id, item.id);
                        },
                        "Thread summarized."
                      )
                    }
                  >
                    Summarize
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => setThreadId(item.id)}>
                    Reply
                  </Button>
                </div>
              </div>
            ))}

            <div className="rounded-lg border border-dashed p-2">
              <p className="mb-1 text-sm font-semibold text-slate-900">Create thread</p>
              <Select value={threadType} onValueChange={(value) => setThreadType(value as typeof threadType)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="strategy">strategy</SelectItem>
                  <SelectItem value="blocker">blocker</SelectItem>
                  <SelectItem value="quote_review">quote_review</SelectItem>
                  <SelectItem value="next_step">next_step</SelectItem>
                  <SelectItem value="risk_discussion">risk_discussion</SelectItem>
                  <SelectItem value="manager_intervention">manager_intervention</SelectItem>
                  <SelectItem value="playbook_application">playbook_application</SelectItem>
                </SelectContent>
              </Select>
              <Input className="mt-2" value={newThreadTitle} onChange={(event) => setNewThreadTitle(event.target.value)} placeholder="Thread title" />
              <Button
                className="mt-2"
                size="sm"
                disabled={busy}
                onClick={() =>
                  void runAction(
                    async () => {
                      await dealRoomClientService.createThread(data.room.id, {
                        threadType,
                        title: newThreadTitle || `Thread ${threadType}`
                      });
                      setNewThreadTitle("");
                    },
                    "Thread created."
                  )
                }
              >
                Save thread
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Messages & Related Signals</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="rounded-lg border border-dashed p-2">
              <p className="mb-1 text-sm font-semibold text-slate-900">Post message</p>
              <Select value={threadId} onValueChange={setThreadId}>
                <SelectTrigger>
                  <SelectValue placeholder="Select thread" />
                </SelectTrigger>
                <SelectContent>
                  {data.threads.map((item) => (
                    <SelectItem key={item.id} value={item.id}>
                      {item.title}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Textarea className="mt-2" value={threadMessage} onChange={(event) => setThreadMessage(event.target.value)} placeholder="Write collaboration message..." />
              <Button
                className="mt-2"
                size="sm"
                disabled={busy || !threadId || !threadMessage.trim()}
                onClick={() =>
                  void runAction(
                    async () => {
                      await dealRoomClientService.addMessage(data.room.id, {
                        threadId,
                        bodyMarkdown: threadMessage
                      });
                      setThreadMessage("");
                    },
                    "Message posted."
                  )
                }
              >
                Post
              </Button>
            </div>

            <div>
              <p className="mb-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Latest alerts</p>
              {data.related.alerts.slice(0, 5).map((item) => (
                <p key={item.id} className="text-sm text-slate-700">
                  - {item.level}: {item.title}
                </p>
              ))}
            </div>
            <div>
              <p className="mb-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Latest outcomes</p>
              {data.related.outcomes.slice(0, 5).map((item) => (
                <p key={item.id} className="text-sm text-slate-700">
                  - {item.resultStatus}: {item.keyOutcomeSummary}
                </p>
              ))}
            </div>
            <div>
              <p className="mb-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Email threads</p>
              {touchpointHub.emailThreads.slice(0, 4).map((item) => (
                <p key={item.id} className="text-sm text-slate-700">
                  - {item.threadStatus}: {item.subject}
                </p>
              ))}
              {touchpointHub.emailThreads.length === 0 ? <p className="text-sm text-muted-foreground">No email thread yet.</p> : null}
            </div>
            <div>
              <p className="mb-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Meetings</p>
              {touchpointHub.calendarEvents.slice(0, 4).map((item) => (
                <p key={item.id} className="text-sm text-slate-700">
                  - {item.meetingStatus}: {item.title}
                </p>
              ))}
              {touchpointHub.calendarEvents.length === 0 ? <p className="text-sm text-muted-foreground">No meeting touchpoint yet.</p> : null}
            </div>
            <div>
              <p className="mb-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Documents</p>
              {touchpointHub.documentAssets.slice(0, 4).map((item) => (
                <p key={item.id} className="text-sm text-slate-700">
                  - {item.documentType}: {item.title}
                </p>
              ))}
              {touchpointHub.documentAssets.length === 0 ? <p className="text-sm text-muted-foreground">No document touchpoint yet.</p> : null}
            </div>
            <div>
              <p className="mb-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Touchpoint timeline</p>
              {touchpointHub.events.slice(0, 6).map((item) => (
                <p key={item.id} className="text-sm text-slate-700">
                  - {item.eventType}: {item.eventSummary}
                </p>
              ))}
              {touchpointHub.events.length === 0 ? <p className="text-sm text-muted-foreground">No external event timeline yet.</p> : null}
            </div>
            <div className="flex flex-wrap gap-2">
              <Button asChild variant="outline" size="sm">
                <Link href={`/customers/${data.room.customerId}`}>Customer Detail</Link>
              </Button>
              <Button asChild variant="outline" size="sm">
                <Link href="/today">Today</Link>
              </Button>
              <Button asChild variant="outline" size="sm">
                <Link href="/playbooks">Playbooks</Link>
              </Button>
              <Button asChild variant="outline" size="sm">
                <Link href="/touchpoints">Touchpoints</Link>
              </Button>
            </div>
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
