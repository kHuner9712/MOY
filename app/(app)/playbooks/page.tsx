"use client";

import { useMemo, useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { IndustryTemplateBanner } from "@/components/shared/industry-template-banner";
import { PageHeader } from "@/components/shared/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { usePlaybooks } from "@/hooks/use-playbooks";
import { formatDateTime } from "@/lib/format";
import type { PlaybookFeedbackType, PlaybookType } from "@/types/playbook";

const playbookTypeLabel: Record<PlaybookType, string> = {
  objection_handling: "Objection Handling",
  customer_segment: "Customer Segment",
  quote_strategy: "Quote Strategy",
  meeting_strategy: "Meeting Strategy",
  followup_rhythm: "Followup Rhythm",
  risk_recovery: "Risk Recovery"
};

export default function PlaybooksPage(): JSX.Element {
  const { user } = useAuth();
  const [scopeType, setScopeType] = useState<"org" | "team" | "user">(user?.role === "manager" ? "team" : "user");
  const [playbookType, setPlaybookType] = useState<PlaybookType | "all">("all");
  const [feedbackLoadingId, setFeedbackLoadingId] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const { data, loading, error, compile, feedback, reload, message: serviceMessage } = usePlaybooks({
    scopeType,
    playbookType: playbookType === "all" ? undefined : playbookType,
    includeEntries: true,
    limit: 60
  });

  const rows = useMemo(() => data, [data]);

  const handleCompile = async () => {
    setMessage(null);
    try {
      const result = await compile({
        scopeType: user?.role === "manager" ? scopeType : "user"
      });
      setMessage(result.usedFallback ? "Playbook compiled with fallback rules." : "Playbook compiled successfully.");
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "Playbook compile failed");
    }
  };

  const handleFeedback = async (playbookId: string, feedbackType: PlaybookFeedbackType, playbookEntryId?: string) => {
    setFeedbackLoadingId(`${playbookId}:${playbookEntryId ?? "root"}`);
    setMessage(null);
    try {
      await feedback(playbookId, {
        playbookEntryId,
        feedbackType
      });
      setMessage("Playbook feedback saved.");
      await reload();
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "Failed to save playbook feedback");
    } finally {
      setFeedbackLoadingId(null);
    }
  };

  if (!user) return <div className="text-sm text-muted-foreground">Missing user context.</div>;
  if (loading) return <div className="text-sm text-muted-foreground">Loading playbooks...</div>;
  if (error) return <div className="text-sm text-rose-600">Failed to load playbooks: {error}</div>;

  return (
    <div>
      <PageHeader
        title="Playbooks"
        description="Closed-loop patterns from real outcomes. Promote what works and flag what only looks busy."
        action={
          <div className="flex items-center gap-2">
            {user.role === "manager" ? (
              <Select value={scopeType} onValueChange={(value) => setScopeType(value as "org" | "team" | "user") }>
                <SelectTrigger className="w-36">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="team">Team Scope</SelectItem>
                  <SelectItem value="org">Org Scope</SelectItem>
                  <SelectItem value="user">User Scope</SelectItem>
                </SelectContent>
              </Select>
            ) : null}
            <Button onClick={() => void handleCompile()}>Compile Playbook</Button>
            <Button variant="outline" onClick={() => void reload()}>Refresh</Button>
          </div>
        }
      />

      {serviceMessage ? <p className="mb-3 text-sm text-emerald-700">{serviceMessage}</p> : null}
      {message ? <p className="mb-3 text-sm text-muted-foreground">{message}</p> : null}
      <IndustryTemplateBanner className="mb-4" compact />

      <section className="mb-4 max-w-sm">
        <Select value={playbookType} onValueChange={(value) => setPlaybookType(value as PlaybookType | "all") }>
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All types</SelectItem>
            <SelectItem value="objection_handling">Objection Handling</SelectItem>
            <SelectItem value="customer_segment">Customer Segment</SelectItem>
            <SelectItem value="quote_strategy">Quote Strategy</SelectItem>
            <SelectItem value="meeting_strategy">Meeting Strategy</SelectItem>
            <SelectItem value="followup_rhythm">Followup Rhythm</SelectItem>
            <SelectItem value="risk_recovery">Risk Recovery</SelectItem>
          </SelectContent>
        </Select>
      </section>

      <section className="space-y-4">
        {rows.length === 0 ? (
          <Card>
            <CardContent className="p-6 text-sm text-muted-foreground">No playbooks yet. Compile one from recent outcomes.</CardContent>
          </Card>
        ) : null}

        {rows.map((item) => (
          <Card key={item.playbook.id}>
            <CardHeader>
              <CardTitle className="flex items-center justify-between gap-2">
                <span>{item.playbook.title}</span>
                <div className="flex items-center gap-2">
                  <Badge variant="outline">{playbookTypeLabel[item.playbook.playbookType]}</Badge>
                  <Badge variant="secondary">{item.playbook.scopeType}</Badge>
                  {(item.playbook.sourceSnapshot.seeded_from_template as boolean | undefined) ? <Badge>template seed</Badge> : null}
                  <Badge variant="secondary">{Math.round(item.playbook.confidenceScore * 100)}%</Badge>
                </div>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-sm text-slate-700">{item.playbook.summary}</p>
              <p className="text-xs text-muted-foreground">Applicability: {item.playbook.applicabilityNotes}</p>
              <p className="text-xs text-muted-foreground">Updated at {formatDateTime(item.playbook.updatedAt)}</p>

              <div className="grid gap-3 md:grid-cols-2">
                {item.entries.map((entry) => (
                  <div key={entry.id} className="rounded-lg border p-3">
                    <p className="text-sm font-semibold text-slate-900">{entry.entryTitle}</p>
                    <p className="mt-1 text-xs text-slate-700">{entry.entrySummary}</p>
                    <p className="mt-1 text-xs text-muted-foreground">Confidence {Math.round(entry.confidenceScore * 100)}%</p>
                    {entry.recommendedActions.slice(0, 3).map((action) => (
                      <p key={action} className="mt-1 text-xs text-slate-700">- {action}</p>
                    ))}
                    {entry.cautionNotes.slice(0, 2).map((note) => (
                      <p key={note} className="mt-1 text-xs text-amber-700">! {note}</p>
                    ))}
                    <div className="mt-2 flex flex-wrap gap-2">
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={feedbackLoadingId === `${item.playbook.id}:${entry.id}`}
                        onClick={() => void handleFeedback(item.playbook.id, "useful", entry.id)}
                      >
                        Useful
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={feedbackLoadingId === `${item.playbook.id}:${entry.id}`}
                        onClick={() => void handleFeedback(item.playbook.id, "outdated", entry.id)}
                      >
                        Outdated
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={feedbackLoadingId === `${item.playbook.id}:${entry.id}`}
                        onClick={() => void handleFeedback(item.playbook.id, "inaccurate", entry.id)}
                      >
                        Inaccurate
                      </Button>
                    </div>
                  </div>
                ))}
              </div>

              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={feedbackLoadingId === `${item.playbook.id}:root`}
                  onClick={() => void handleFeedback(item.playbook.id, "adopted")}
                >
                  Mark Adopted
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={feedbackLoadingId === `${item.playbook.id}:root`}
                  onClick={() => void handleFeedback(item.playbook.id, "not_useful")}
                >
                  Not Useful
                </Button>
              </div>
            </CardContent>
          </Card>
        ))}
      </section>
    </div>
  );
}
