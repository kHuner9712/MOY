"use client";

import { useEffect, useMemo, useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { useAppData } from "@/components/shared/app-data-provider";
import { PageHeader } from "@/components/shared/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useOutcomes } from "@/hooks/use-outcomes";
import { usePlaybooks } from "@/hooks/use-playbooks";
import { useUserMemory } from "@/hooks/use-user-memory";
import { formatDateTime } from "@/lib/format";

export default function MemoryPage(): JSX.Element {
  const { user } = useAuth();
  const { customers } = useAppData();

  const salesOptions = useMemo(() => {
    const map = new Map<string, string>();
    for (const item of customers) {
      if (!map.has(item.ownerId)) map.set(item.ownerId, item.ownerName);
    }
    return Array.from(map.entries()).map(([id, name]) => ({ id, name }));
  }, [customers]);

  const [selectedUserId, setSelectedUserId] = useState(user?.id ?? "");
  const targetUserId = user?.role === "manager" ? selectedUserId : user?.id;

  useEffect(() => {
    if (user?.role !== "manager") return;
    if (salesOptions.length === 0) return;
    const exists = salesOptions.some((item) => item.id === selectedUserId);
    if (!exists) {
      setSelectedUserId(salesOptions[0].id);
    }
  }, [user?.role, salesOptions, selectedUserId]);

  const { profile, items, loading, error, refresh, feedback } = useUserMemory(targetUserId);
  const { data: outcomes } = useOutcomes({
    ownerId: targetUserId,
    limit: 60
  });
  const { data: playbooks } = usePlaybooks({
    ownerUserId: targetUserId,
    scopeType: "user",
    includeEntries: true,
    limit: 20
  });

  const [message, setMessage] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [actionLoadingId, setActionLoadingId] = useState<string | null>(null);

  const canFeedback = Boolean(user?.id && user.id === targetUserId);

  const positiveOutcomes = outcomes.filter((item) => item.resultStatus === "positive_progress" || item.resultStatus === "closed_won").length;
  const positiveRate = outcomes.length > 0 ? Math.round((positiveOutcomes / outcomes.length) * 100) : 0;
  const stalledPatterns = outcomes
    .filter((item) => item.resultStatus === "stalled" || item.resultStatus === "risk_increased")
    .flatMap((item) => item.newRisks)
    .slice(0, 6);

  const onRefreshMemory = async () => {
    setRefreshing(true);
    setMessage(null);
    try {
      const result = await refresh(60);
      setMessage(result.usedFallback ? "Work memory refreshed with fallback rules." : "Work memory refreshed.");
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "Failed to refresh work memory.");
    } finally {
      setRefreshing(false);
    }
  };

  const onFeedback = async (
    memoryItemId: string,
    feedbackType: "accurate" | "inaccurate" | "outdated" | "useful" | "not_useful"
  ) => {
    setActionLoadingId(memoryItemId);
    setMessage(null);
    try {
      await feedback({
        memoryItemId,
        feedbackType
      });
      setMessage("Feedback saved. Future memory compilation will consider this correction.");
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "Failed to submit feedback.");
    } finally {
      setActionLoadingId(null);
    }
  };

  if (loading) return <div className="text-sm text-muted-foreground">Loading memory...</div>;
  if (error) return <div className="text-sm text-rose-600">Memory load failed: {error}</div>;

  return (
    <div>
      <PageHeader
        title="Work Memory"
        description="Controllable business memory only: explainable, editable, and correctable."
        action={
          <Button onClick={() => void onRefreshMemory()} disabled={refreshing}>
            {refreshing ? "Refreshing..." : "Refresh Work Memory"}
          </Button>
        }
      />

      {user?.role === "manager" ? (
        <Card className="mb-4">
          <CardHeader>
            <CardTitle>Select Sales View</CardTitle>
          </CardHeader>
          <CardContent className="max-w-md">
            <Label>Sales member</Label>
            <Select value={selectedUserId} onValueChange={setSelectedUserId}>
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
          </CardContent>
        </Card>
      ) : null}

      {message ? <p className="mb-3 text-sm text-muted-foreground">{message}</p> : null}

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader>
            <CardTitle>Memory Summary</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-sm leading-7 text-slate-700">
              {profile?.summary ?? "No work memory yet. Click refresh to compile memory from recent actions."}
            </p>
            <div className="grid gap-2 md:grid-cols-2">
              <InfoList title="Preferred customer types" values={profile?.preferredCustomerTypes ?? []} />
              <InfoList title="Communication patterns" values={profile?.preferredCommunicationStyles ?? []} />
              <InfoList title="Common objections" values={profile?.commonObjections ?? []} />
              <InfoList title="Effective tactics" values={profile?.effectiveTactics ?? []} />
              <InfoList title="Follow-up rhythm" values={profile?.commonFollowupRhythm ?? []} />
              <InfoList title="Quote style notes" values={profile?.quotingStyleNotes ?? []} />
              <InfoList title="Risk blind spots" values={profile?.riskBlindSpots ?? []} />
              <InfoList title="Coaching focus" values={profile?.managerCoachingFocus ?? []} />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Metadata</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p>Version: {profile?.memoryVersion ?? "-"}</p>
            <p>Confidence: {profile ? `${Math.round(profile.confidenceScore * 100)}%` : "-"}</p>
            <p>Source window: {profile?.sourceWindowDays ?? "-"} days</p>
            <p>Last compiled: {profile?.lastCompiledAt ? formatDateTime(profile.lastCompiledAt) : "-"}</p>
            <p>Last updated: {profile?.updatedAt ? formatDateTime(profile.updatedAt) : "-"}</p>
            <Badge variant="outline">Business Memory / Correctable</Badge>
          </CardContent>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Personal Effectiveness (Closed Loop)</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p>Total outcomes tracked: {outcomes.length}</p>
            <p>Positive progress rate: {positiveRate}%</p>
            <p>Linked personal playbooks: {playbooks.length}</p>
            <p className="text-xs text-muted-foreground">The system adjusts future suggestions based on adoption + outcome evidence.</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Ineffective Habit Signals</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1">
            {stalledPatterns.length === 0 ? <p className="text-sm text-muted-foreground">No repeated stalled signal in this period.</p> : null}
            {stalledPatterns.map((item, index) => (
              <p key={`${item}-${index}`} className="text-sm text-slate-700">
                - {item}
              </p>
            ))}
          </CardContent>
        </Card>
      </div>

      <Card className="mt-4">
        <CardHeader>
          <CardTitle>Recent Personal Playbooks</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {playbooks.length === 0 ? <p className="text-sm text-muted-foreground">No personal playbook compiled yet.</p> : null}
          {playbooks.slice(0, 6).map((item) => (
            <div key={item.playbook.id} className="rounded-lg border p-3">
              <div className="mb-1 flex items-center justify-between gap-2">
                <p className="text-sm font-semibold text-slate-900">{item.playbook.title}</p>
                <Badge variant="outline">{Math.round(item.playbook.confidenceScore * 100)}%</Badge>
              </div>
              <p className="text-xs text-slate-700">{item.playbook.summary}</p>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card className="mt-4">
        <CardHeader>
          <CardTitle>Memory Items</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {items.length === 0 ? <p className="text-sm text-muted-foreground">No memory item yet.</p> : null}
          {items.map((item) => (
            <div key={item.id} className="rounded-lg border p-3">
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <Badge variant="secondary">{item.memoryType}</Badge>
                <Badge variant="outline">{item.status}</Badge>
                <span className="text-xs text-muted-foreground">confidence {Math.round(item.confidenceScore * 100)}%</span>
                <span className="text-xs text-muted-foreground">sources {item.sourceCount}</span>
              </div>
              <p className="text-sm font-semibold text-slate-900">{item.title}</p>
              <p className="mt-1 text-sm text-slate-700">{item.description}</p>
              <p className="mt-1 text-xs text-muted-foreground">{formatDateTime(item.updatedAt)}</p>

              {canFeedback ? (
                <div className="mt-2 flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={actionLoadingId === item.id}
                    onClick={() => void onFeedback(item.id, "useful")}
                  >
                    Useful
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={actionLoadingId === item.id}
                    onClick={() => void onFeedback(item.id, "inaccurate")}
                  >
                    Inaccurate
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={actionLoadingId === item.id}
                    onClick={() => void onFeedback(item.id, "not_useful")}
                  >
                    Hide
                  </Button>
                </div>
              ) : null}
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}

function InfoList({ title, values }: { title: string; values: string[] }): JSX.Element {
  return (
    <div className="rounded-lg border bg-slate-50 p-3">
      <p className="text-xs font-medium text-slate-600">{title}</p>
      {values.length === 0 ? <p className="mt-1 text-xs text-muted-foreground">N/A</p> : null}
      {values.slice(0, 5).map((item) => (
        <p key={item} className="mt-1 text-xs text-slate-700">
          - {item}
        </p>
      ))}
    </div>
  );
}
