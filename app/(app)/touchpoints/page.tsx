"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { InstallPromptCard } from "@/components/mobile/InstallPromptCard";
import { MobileSectionTabs } from "@/components/mobile/MobileSectionTabs";
import { QuickActionCard } from "@/components/mobile/QuickActionCard";
import { useAppData } from "@/components/shared/app-data-provider";
import { PageHeader } from "@/components/shared/page-header";
import { StatCard } from "@/components/shared/stat-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useDeals } from "@/hooks/use-deals";
import { useTouchpoints } from "@/hooks/use-touchpoints";
import { formatDateTime } from "@/lib/format";
import { touchpointClientService } from "@/services/touchpoint-client-service";
import type { CalendarEventType, DocumentAssetType, EmailMessageDirection } from "@/types/touchpoint";
import { CalendarClock, FileText, Mail, RefreshCw } from "lucide-react";

type TouchpointTypeFilter = "all" | "email" | "meeting" | "document";

function toIso(localDateTime: string): string {
  return new Date(localDateTime).toISOString();
}

function defaultDateTime(hoursDelta: number): string {
  const date = new Date(Date.now() + hoursDelta * 60 * 60 * 1000);
  const year = date.getFullYear();
  const month = `${date.getMonth() + 1}`.padStart(2, "0");
  const day = `${date.getDate()}`.padStart(2, "0");
  const hour = `${date.getHours()}`.padStart(2, "0");
  const minute = `${date.getMinutes()}`.padStart(2, "0");
  return `${year}-${month}-${day}T${hour}:${minute}`;
}

export default function TouchpointsPage(): JSX.Element {
  const { user } = useAuth();
  const { customers } = useAppData();
  const { data: deals } = useDeals();

  const [scopeType, setScopeType] = useState<TouchpointTypeFilter>("all");
  const [ownerFilter, setOwnerFilter] = useState<string>("all");
  const [customerFilter, setCustomerFilter] = useState<string>("all");
  const [dealFilter, setDealFilter] = useState<string>("all");
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const [emailSubject, setEmailSubject] = useState("");
  const [emailBody, setEmailBody] = useState("");
  const [emailDirection, setEmailDirection] = useState<EmailMessageDirection>("outbound");

  const [meetingTitle, setMeetingTitle] = useState("");
  const [meetingDesc, setMeetingDesc] = useState("");
  const [meetingType, setMeetingType] = useState<CalendarEventType>("customer_meeting");
  const [meetingStart, setMeetingStart] = useState(defaultDateTime(4));
  const [meetingEnd, setMeetingEnd] = useState(defaultDateTime(5));

  const [docTitle, setDocTitle] = useState("");
  const [docFileName, setDocFileName] = useState("");
  const [docType, setDocType] = useState<DocumentAssetType>("other");
  const [docText, setDocText] = useState("");

  const ownerOptions = useMemo(() => {
    const map = new Map<string, string>();
    for (const customer of customers) {
      if (!map.has(customer.ownerId)) map.set(customer.ownerId, customer.ownerName);
    }
    return Array.from(map.entries()).map(([id, name]) => ({ id, name }));
  }, [customers]);

  const {
    hub,
    summary,
    review,
    loading,
    reviewLoading,
    error,
    reload,
    generateReview
  } = useTouchpoints({
    ownerId: user?.role === "sales" ? user.id : ownerFilter === "all" ? undefined : ownerFilter,
    customerId: customerFilter === "all" ? undefined : customerFilter,
    dealRoomId: dealFilter === "all" ? undefined : dealFilter,
    type: scopeType === "all" ? undefined : scopeType,
    limit: 120,
    enabled: Boolean(user)
  });

  if (!user) return <div className="text-sm text-muted-foreground">Missing user context.</div>;

  const runAction = async (key: string, action: () => Promise<void>) => {
    setActionLoading(key);
    setActionMessage(null);
    try {
      await action();
      await reload();
    } catch (cause) {
      setActionMessage(cause instanceof Error ? cause.message : "Touchpoint action failed");
    } finally {
      setActionLoading(null);
    }
  };

  return (
    <div>
      <PageHeader
        title="External Touchpoints"
        description="Unify email, meetings, documents, and event feedback loop into deal execution."
        action={
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={() => void reload()} disabled={loading}>
              <RefreshCw className="mr-1 h-4 w-4" />
              Refresh
            </Button>
            <Button onClick={() => void generateReview()} disabled={reviewLoading}>
              {reviewLoading ? "Reviewing..." : "Generate Touchpoint Review"}
            </Button>
            <Button asChild variant="outline">
              <Link href="/deals">Deal Rooms</Link>
            </Button>
          </div>
        }
      />

      {error ? <p className="mb-3 text-sm text-rose-600">{error}</p> : null}
      {actionMessage ? <p className="mb-3 text-sm text-muted-foreground">{actionMessage}</p> : null}
      <InstallPromptCard />

      <section className="mb-4 space-y-3 lg:hidden">
        <QuickActionCard
          title="外部触点速览"
          subtitle="移动端优先查看等待回复、即将会议和新文档。"
        >
          <div className="flex flex-wrap gap-2">
            <Badge variant="outline">等待回复 {summary.waitingReplyThreads}</Badge>
            <Badge variant="outline">即将会议 {summary.upcomingMeetings}</Badge>
            <Badge variant="outline">文档更新 {summary.documentUpdates}</Badge>
          </div>
          <MobileSectionTabs
            value={scopeType}
            onChange={setScopeType}
            options={[
              { value: "all", label: "全部" },
              { value: "email", label: "邮件" },
              { value: "meeting", label: "会议" },
              { value: "document", label: "文档" }
            ]}
          />
        </QuickActionCard>
      </section>

      <section className="mb-4 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatCard title="7d External Events" value={summary.totalEvents} icon={<Mail className="h-4 w-4 text-sky-700" />} />
        <StatCard title="Waiting Reply Threads" value={summary.waitingReplyThreads} icon={<Mail className="h-4 w-4 text-amber-600" />} />
        <StatCard title="Upcoming Meetings" value={summary.upcomingMeetings} icon={<CalendarClock className="h-4 w-4 text-indigo-600" />} />
        <StatCard title="Document Updates" value={summary.documentUpdates} icon={<FileText className="h-4 w-4 text-emerald-600" />} />
      </section>

      <section className="mb-4 grid gap-4 xl:grid-cols-[1.2fr_1.8fr]">
        <Card>
          <CardHeader>
            <CardTitle>Filters</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="grid gap-1">
              <Label>Touchpoint Type</Label>
              <Select value={scopeType} onValueChange={(value) => setScopeType(value as TouchpointTypeFilter)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All</SelectItem>
                  <SelectItem value="email">Email</SelectItem>
                  <SelectItem value="meeting">Meeting</SelectItem>
                  <SelectItem value="document">Document</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {user.role === "manager" ? (
              <div className="grid gap-1">
                <Label>Owner</Label>
                <Select value={ownerFilter} onValueChange={setOwnerFilter}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All owners</SelectItem>
                    {ownerOptions.map((item) => (
                      <SelectItem key={item.id} value={item.id}>
                        {item.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            ) : null}

            <div className="grid gap-1">
              <Label>Customer</Label>
              <Select value={customerFilter} onValueChange={setCustomerFilter}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All customers</SelectItem>
                  {customers
                    .filter((item) => (user.role === "manager" ? true : item.ownerId === user.id))
                    .map((item) => (
                      <SelectItem key={item.id} value={item.id}>
                        {item.companyName}
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
            </div>

            <div className="grid gap-1">
              <Label>Deal Room</Label>
              <Select value={dealFilter} onValueChange={setDealFilter}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All deal rooms</SelectItem>
                  {deals.map((item) => (
                    <SelectItem key={item.id} value={item.id}>
                      {item.title}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Touchpoint Review</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {review.result ? (
              <>
                <p className="text-sm text-slate-700">{review.result.externalProgressAssessment}</p>
                <div>
                  <p className="mb-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Stalled</p>
                  {(review.result.stalledTouchpoints ?? []).map((item) => (
                    <p key={item} className="text-sm text-slate-700">
                      - {item}
                    </p>
                  ))}
                </div>
                <div>
                  <p className="mb-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Missing</p>
                  {(review.result.missingTouchpoints ?? []).map((item) => (
                    <p key={item} className="text-sm text-slate-700">
                      - {item}
                    </p>
                  ))}
                </div>
                <div>
                  <p className="mb-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Next moves</p>
                  {(review.result.recommendedNextMoves ?? []).map((item) => (
                    <p key={item} className="text-sm text-slate-700">
                      - {item}
                    </p>
                  ))}
                </div>
                <div className="flex items-center gap-2">
                  {review.usedFallback ? <Badge variant="secondary">Fallback</Badge> : <Badge variant="default">AI</Badge>}
                  <span className="text-xs text-muted-foreground">run: {review.runId?.slice(0, 10) ?? "-"}</span>
                </div>
              </>
            ) : (
              <p className="text-sm text-muted-foreground">Generate touchpoint review to inspect external progression quality.</p>
            )}
          </CardContent>
        </Card>
      </section>

      <section className="mb-4 grid gap-4 xl:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>Quick Email</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <Input value={emailSubject} onChange={(event) => setEmailSubject(event.target.value)} placeholder="Email subject" />
            <Textarea value={emailBody} onChange={(event) => setEmailBody(event.target.value)} placeholder="Body text or pasted email content" />
            <Select value={emailDirection} onValueChange={(value) => setEmailDirection(value as EmailMessageDirection)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="outbound">outbound</SelectItem>
                <SelectItem value="inbound">inbound</SelectItem>
                <SelectItem value="draft">draft</SelectItem>
              </SelectContent>
            </Select>
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                disabled={actionLoading === "email-create"}
                onClick={() =>
                  void runAction("email-create", async () => {
                    const created = await touchpointClientService.createEmailThread({
                      customerId: customerFilter === "all" ? undefined : customerFilter,
                      dealRoomId: dealFilter === "all" ? undefined : dealFilter,
                      subject: emailSubject || "Customer communication",
                      summary: emailBody.slice(0, 220),
                      direction: emailDirection,
                      messageSubject: emailSubject || "Customer communication",
                      messageBodyText: emailBody,
                      messageBodyMarkdown: emailBody,
                      status: emailDirection === "draft" ? "draft" : emailDirection === "inbound" ? "received" : "sent"
                    });
                    setActionMessage(`Email touchpoint saved (thread ${created.threadId.slice(0, 8)}...).`);
                    setEmailBody("");
                  })
                }
              >
                Save Thread/Message
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={actionLoading === "email-draft"}
                onClick={() =>
                  void runAction("email-draft", async () => {
                    const result = await touchpointClientService.generateEmailDraft({
                      contextType: "followup",
                      customerId: customerFilter === "all" ? undefined : customerFilter,
                      dealRoomId: dealFilter === "all" ? undefined : dealFilter
                    });
                    setActionMessage(result.usedFallback ? "Email draft generated (fallback)." : "Email draft generated.");
                  })
                }
              >
                Generate Draft
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Quick Meeting</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <Input value={meetingTitle} onChange={(event) => setMeetingTitle(event.target.value)} placeholder="Meeting title" />
            <Textarea value={meetingDesc} onChange={(event) => setMeetingDesc(event.target.value)} placeholder="Meeting notes or purpose" />
            <Select value={meetingType} onValueChange={(value) => setMeetingType(value as CalendarEventType)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="customer_meeting">customer_meeting</SelectItem>
                <SelectItem value="demo">demo</SelectItem>
                <SelectItem value="proposal_review">proposal_review</SelectItem>
                <SelectItem value="internal_strategy">internal_strategy</SelectItem>
                <SelectItem value="manager_intervention">manager_intervention</SelectItem>
              </SelectContent>
            </Select>
            <Input type="datetime-local" value={meetingStart} onChange={(event) => setMeetingStart(event.target.value)} />
            <Input type="datetime-local" value={meetingEnd} onChange={(event) => setMeetingEnd(event.target.value)} />
            <Button
              size="sm"
              disabled={actionLoading === "meeting-create"}
              onClick={() =>
                void runAction("meeting-create", async () => {
                  await touchpointClientService.createCalendarEvent({
                    customerId: customerFilter === "all" ? undefined : customerFilter,
                    dealRoomId: dealFilter === "all" ? undefined : dealFilter,
                    eventType: meetingType,
                    title: meetingTitle || "Customer meeting",
                    description: meetingDesc,
                    startAt: toIso(meetingStart),
                    endAt: toIso(meetingEnd),
                    autoGeneratePrep: true,
                    autoGenerateAgenda: true
                  });
                  setActionMessage("Meeting created and prep/agenda triggered.");
                })
              }
            >
              Save Meeting
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Quick Document</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <Input value={docTitle} onChange={(event) => setDocTitle(event.target.value)} placeholder="Document title" />
            <Input value={docFileName} onChange={(event) => setDocFileName(event.target.value)} placeholder="File name, e.g. quote-v2.md" />
            <Select value={docType} onValueChange={(value) => setDocType(value as DocumentAssetType)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="proposal">proposal</SelectItem>
                <SelectItem value="quote">quote</SelectItem>
                <SelectItem value="contract_draft">contract_draft</SelectItem>
                <SelectItem value="meeting_note">meeting_note</SelectItem>
                <SelectItem value="case_study">case_study</SelectItem>
                <SelectItem value="product_material">product_material</SelectItem>
                <SelectItem value="other">other</SelectItem>
              </SelectContent>
            </Select>
            <Textarea value={docText} onChange={(event) => setDocText(event.target.value)} placeholder="Paste extracted text or summary for AI analysis" />
            <Button
              size="sm"
              disabled={actionLoading === "doc-upload"}
              onClick={() =>
                void runAction("doc-upload", async () => {
                  await touchpointClientService.uploadDocument({
                    customerId: customerFilter === "all" ? undefined : customerFilter,
                    dealRoomId: dealFilter === "all" ? undefined : dealFilter,
                    title: docTitle || "Business document",
                    fileName: docFileName || "document.txt",
                    documentType: docType,
                    extractedText: docText,
                    autoSummarize: true
                  });
                  setActionMessage("Document uploaded and summarized.");
                })
              }
            >
              Upload & Summarize
            </Button>
          </CardContent>
        </Card>
      </section>

      <section className="mb-4 grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader>
            <CardTitle>Email Threads</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {hub.emailThreads.length === 0 ? <p className="text-sm text-muted-foreground">No email thread in current scope.</p> : null}
            {hub.emailThreads.slice(0, 12).map((item) => (
              <div key={item.id} className="rounded-lg border p-3">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <p className="text-sm font-semibold text-slate-900">{item.subject}</p>
                  <div className="flex items-center gap-2">
                    <Badge variant={item.threadStatus === "waiting_reply" ? "destructive" : "secondary"}>{item.threadStatus}</Badge>
                    <Badge variant="outline">{item.sentimentHint}</Badge>
                  </div>
                </div>
                <p className="text-xs text-muted-foreground">
                  customer: {item.customerName ?? "-"} | latest: {item.latestMessageAt ? formatDateTime(item.latestMessageAt) : "-"}
                </p>
                <p className="mt-1 text-xs text-slate-700">{item.summary}</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={actionLoading === `email-draft-${item.id}`}
                    onClick={() =>
                      void runAction(`email-draft-${item.id}`, async () => {
                        const result = await touchpointClientService.generateEmailDraft({
                          contextType: "followup",
                          customerId: item.customerId ?? undefined,
                          opportunityId: item.opportunityId ?? undefined,
                          dealRoomId: item.dealRoomId ?? undefined,
                          threadId: item.id
                        });
                        setActionMessage(result.usedFallback ? "Email draft generated (fallback)." : "Email draft generated.");
                      })
                    }
                  >
                    Generate Follow-up Draft
                  </Button>
                  {dealFilter !== "all" && !item.dealRoomId ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={actionLoading === `email-link-${item.id}`}
                      onClick={() =>
                        void runAction(`email-link-${item.id}`, async () => {
                          await touchpointClientService.linkToDeal({
                            targetType: "email_thread",
                            targetId: item.id,
                            customerId: customerFilter === "all" ? undefined : customerFilter,
                            dealRoomId: dealFilter
                          });
                          setActionMessage("Email thread linked to current deal.");
                        })
                      }
                    >
                      Link To Deal
                    </Button>
                  ) : null}
                </div>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Recent Events</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {hub.events.length === 0 ? <p className="text-sm text-muted-foreground">No touchpoint events yet.</p> : null}
            {hub.events.slice(0, 16).map((item) => (
              <div key={item.id} className="rounded border px-2 py-1">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-xs font-semibold text-slate-900">{item.eventType}</p>
                  <Badge variant="outline">{item.touchpointType}</Badge>
                </div>
                <p className="text-xs text-slate-700">{item.eventSummary}</p>
                <p className="text-[11px] text-muted-foreground">{formatDateTime(item.createdAt)}</p>
              </div>
            ))}
          </CardContent>
        </Card>
      </section>

      <section className="mb-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Calendar Events</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {hub.calendarEvents.length === 0 ? <p className="text-sm text-muted-foreground">No meeting event in current scope.</p> : null}
            {hub.calendarEvents.slice(0, 12).map((item) => (
              <div key={item.id} className="rounded-lg border p-3">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <p className="text-sm font-semibold text-slate-900">{item.title}</p>
                  <Badge variant={item.meetingStatus === "completed" ? "default" : item.meetingStatus === "cancelled" ? "secondary" : "outline"}>
                    {item.meetingStatus}
                  </Badge>
                </div>
                <p className="text-xs text-muted-foreground">
                  {formatDateTime(item.startAt)} - {formatDateTime(item.endAt)}
                </p>
                <p className="mt-1 text-xs text-slate-700">{item.agendaSummary || item.description || "-"}</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {item.meetingStatus !== "completed" ? (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={actionLoading === `meeting-complete-${item.id}`}
                      onClick={() =>
                        void runAction(`meeting-complete-${item.id}`, async () => {
                          const result = await touchpointClientService.completeMeetingFollowup({
                            eventId: item.id,
                            captureOutcome: true
                          });
                          setActionMessage(result.usedFallback ? "Meeting followup completed (fallback)." : "Meeting followup completed.");
                        })
                      }
                    >
                      Complete & Followup
                    </Button>
                  ) : null}
                  {dealFilter !== "all" && !item.dealRoomId ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={actionLoading === `meeting-link-${item.id}`}
                      onClick={() =>
                        void runAction(`meeting-link-${item.id}`, async () => {
                          await touchpointClientService.linkToDeal({
                            targetType: "calendar_event",
                            targetId: item.id,
                            customerId: customerFilter === "all" ? undefined : customerFilter,
                            dealRoomId: dealFilter
                          });
                          setActionMessage("Calendar event linked to current deal.");
                        })
                      }
                    >
                      Link To Deal
                    </Button>
                  ) : null}
                </div>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Document Assets</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {hub.documentAssets.length === 0 ? <p className="text-sm text-muted-foreground">No document in current scope.</p> : null}
            {hub.documentAssets.slice(0, 12).map((item) => (
              <div key={item.id} className="rounded-lg border p-3">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <p className="text-sm font-semibold text-slate-900">{item.title}</p>
                  <Badge variant="outline">{item.documentType}</Badge>
                </div>
                <p className="text-xs text-muted-foreground">
                  {item.fileName} | {formatDateTime(item.updatedAt)}
                </p>
                <p className="mt-1 line-clamp-3 text-xs text-slate-700">{item.summary || item.extractedText || "-"}</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={actionLoading === `doc-summarize-${item.id}`}
                    onClick={() =>
                      void runAction(`doc-summarize-${item.id}`, async () => {
                        const result = await touchpointClientService.summarizeDocument(item.id);
                        setActionMessage(result.usedFallback ? "Document summarized (fallback)." : "Document summarized.");
                      })
                    }
                  >
                    Re-summarize
                  </Button>
                  {dealFilter !== "all" && !item.dealRoomId ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={actionLoading === `doc-link-${item.id}`}
                      onClick={() =>
                        void runAction(`doc-link-${item.id}`, async () => {
                          await touchpointClientService.linkToDeal({
                            targetType: "document_asset",
                            targetId: item.id,
                            customerId: customerFilter === "all" ? undefined : customerFilter,
                            dealRoomId: dealFilter
                          });
                          setActionMessage("Document linked to current deal.");
                        })
                      }
                    >
                      Link To Deal
                    </Button>
                  ) : null}
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
