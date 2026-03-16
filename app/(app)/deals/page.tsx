"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { useAppData } from "@/components/shared/app-data-provider";
import { PageHeader } from "@/components/shared/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useDeals } from "@/hooks/use-deals";
import { formatDateTime } from "@/lib/format";
import { dealRoomClientService } from "@/services/deal-room-client-service";
import type { DealRoomStatus } from "@/types/deal";

const statusOptions: Array<{ value: DealRoomStatus | "all"; label: string }> = [
  { value: "all", label: "All Status" },
  { value: "active", label: "Active" },
  { value: "watchlist", label: "Watchlist" },
  { value: "escalated", label: "Escalated" },
  { value: "blocked", label: "Blocked" },
  { value: "won", label: "Won" },
  { value: "lost", label: "Lost" },
  { value: "archived", label: "Archived" }
];

const priorityOptions: Array<{ value: "all" | "normal" | "important" | "strategic" | "critical"; label: string }> = [
  { value: "all", label: "All Priority" },
  { value: "normal", label: "Normal" },
  { value: "important", label: "Important" },
  { value: "strategic", label: "Strategic" },
  { value: "critical", label: "Critical" }
];

export default function DealsPage(): JSX.Element {
  const { user } = useAuth();
  const { customers, opportunities } = useAppData();
  const [status, setStatus] = useState<DealRoomStatus | "all">("all");
  const [priorityBand, setPriorityBand] = useState<"all" | "normal" | "important" | "strategic" | "critical">("all");
  const [attentionOnly, setAttentionOnly] = useState(false);
  const [ownerFilter, setOwnerFilter] = useState<string>("all");
  const [message, setMessage] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newCustomerId, setNewCustomerId] = useState<string>("");
  const [newOpportunityId, setNewOpportunityId] = useState<string>("");
  const [newPriority, setNewPriority] = useState<"normal" | "important" | "strategic" | "critical">("important");
  const [newTitle, setNewTitle] = useState("");
  const [newGoal, setNewGoal] = useState("");

  const ownerOptions = useMemo(() => {
    if (user?.role !== "manager") return [];
    const map = new Map<string, string>();
    for (const customer of customers) {
      if (!map.has(customer.ownerId)) {
        map.set(customer.ownerId, customer.ownerName);
      }
    }
    return Array.from(map.entries()).map(([id, name]) => ({ id, name }));
  }, [customers, user]);

  const { data, loading, error, reload } = useDeals({
    statuses: status === "all" ? undefined : [status],
    priorityBands: priorityBand === "all" ? undefined : [priorityBand],
    ownerId: user?.role === "manager" ? (ownerFilter === "all" ? undefined : ownerFilter) : user?.id,
    managerAttentionNeeded: attentionOnly ? true : undefined
  });

  const scopedCustomers = useMemo(() => {
    if (!user) return [];
    if (user.role === "manager") return customers;
    return customers.filter((item) => item.ownerId === user.id);
  }, [customers, user]);

  const scopedOpportunities = useMemo(() => {
    if (!newCustomerId) return [];
    return opportunities.filter((item) => item.customerId === newCustomerId);
  }, [newCustomerId, opportunities]);

  const createRoom = async (): Promise<void> => {
    if (!newCustomerId) {
      setMessage("Please choose a customer first.");
      return;
    }
    setCreating(true);
    setMessage(null);
    try {
      const result = await dealRoomClientService.create({
        customerId: newCustomerId,
        opportunityId: newOpportunityId || undefined,
        title: newTitle || undefined,
        priorityBand: newPriority,
        currentGoal: newGoal || undefined
      });
      setMessage(result.created ? "Deal room created." : "Existing active deal room found and reused.");
      if (result.created) {
        setNewTitle("");
        setNewGoal("");
      }
      await reload();
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "Failed to create deal room");
    } finally {
      setCreating(false);
    }
  };

  return (
    <div>
      <PageHeader
        title="Deal Rooms"
        description="Collaboration and command center for key opportunities and manager intervention."
        action={
          <div className="flex items-center gap-2">
            <Button variant={attentionOnly ? "default" : "outline"} onClick={() => setAttentionOnly((prev) => !prev)}>
              Attention Only
            </Button>
            <Button asChild variant="outline">
              <Link href="/manager/rhythm">Manager Rhythm</Link>
            </Button>
            <Button asChild variant="outline">
              <Link href="/manager/outcomes">Outcome Intelligence</Link>
            </Button>
          </div>
        }
      />

      {message ? <p className="mb-3 text-sm text-muted-foreground">{message}</p> : null}
      {error ? <p className="mb-3 text-sm text-rose-600">{error}</p> : null}

      <section className="mb-4 grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Create / Join Deal Room</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3">
            <div className="grid gap-1">
              <Label>Customer</Label>
              <Select value={newCustomerId} onValueChange={setNewCustomerId}>
                <SelectTrigger>
                  <SelectValue placeholder="Select customer" />
                </SelectTrigger>
                <SelectContent>
                  {scopedCustomers.map((item) => (
                    <SelectItem key={item.id} value={item.id}>
                      {item.companyName} · {item.ownerName}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1">
              <Label>Opportunity (Optional)</Label>
              <Select value={newOpportunityId} onValueChange={setNewOpportunityId}>
                <SelectTrigger>
                  <SelectValue placeholder="Select opportunity" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="">No opportunity</SelectItem>
                  {scopedOpportunities.map((item) => (
                    <SelectItem key={item.id} value={item.id}>
                      {item.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1">
              <Label>Priority</Label>
              <Select value={newPriority} onValueChange={(value) => setNewPriority(value as typeof newPriority)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="normal">Normal</SelectItem>
                  <SelectItem value="important">Important</SelectItem>
                  <SelectItem value="strategic">Strategic</SelectItem>
                  <SelectItem value="critical">Critical</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1">
              <Label>Title (Optional)</Label>
              <Input value={newTitle} onChange={(event) => setNewTitle(event.target.value)} placeholder="Deal room title" />
            </div>
            <div className="grid gap-1">
              <Label>Current Goal (Optional)</Label>
              <Input value={newGoal} onChange={(event) => setNewGoal(event.target.value)} placeholder="What is the current push goal?" />
            </div>
            <Button onClick={() => void createRoom()} disabled={creating}>
              {creating ? "Creating..." : "Create or Reuse Deal Room"}
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Filters</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3">
            <div className="grid gap-1">
              <Label>Status</Label>
              <Select value={status} onValueChange={(value) => setStatus(value as DealRoomStatus | "all")}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {statusOptions.map((item) => (
                    <SelectItem key={item.value} value={item.value}>
                      {item.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1">
              <Label>Priority</Label>
              <Select value={priorityBand} onValueChange={(value) => setPriorityBand(value as typeof priorityBand)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {priorityOptions.map((item) => (
                    <SelectItem key={item.value} value={item.value}>
                      {item.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {user?.role === "manager" ? (
              <div className="grid gap-1">
                <Label>Owner</Label>
                <Select value={ownerFilter} onValueChange={setOwnerFilter}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Owners</SelectItem>
                    {ownerOptions.map((item) => (
                      <SelectItem key={item.id} value={item.id}>
                        {item.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            ) : null}
            <Button variant="outline" onClick={() => void reload()}>
              Refresh
            </Button>
          </CardContent>
        </Card>
      </section>

      <Card>
        <CardHeader>
          <CardTitle>Deal Room List</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {loading ? <p className="text-sm text-muted-foreground">Loading deal rooms...</p> : null}
          {!loading && data.length === 0 ? <p className="text-sm text-muted-foreground">No deal rooms yet.</p> : null}
          {data.map((item) => (
            <Link key={item.id} href={`/deals/${item.id}`} className="block rounded-lg border p-3 transition hover:bg-slate-50">
              <div className="mb-1 flex items-center justify-between gap-2">
                <p className="text-sm font-semibold text-slate-900">{item.title}</p>
                <div className="flex items-center gap-2">
                  <Badge variant={item.priorityBand === "critical" ? "destructive" : "outline"}>{item.priorityBand}</Badge>
                  <Badge variant="secondary">{item.roomStatus}</Badge>
                  {item.managerAttentionNeeded ? <Badge variant="destructive">Manager Attention</Badge> : null}
                </div>
              </div>
              <p className="text-xs text-muted-foreground">
                Customer: {item.customerName} · Owner: {item.ownerName} · Updated: {formatDateTime(item.updatedAt)}
              </p>
              <p className="mt-1 line-clamp-2 text-xs text-slate-700">{item.commandSummary || item.currentGoal || "No command summary yet."}</p>
              {item.nextMilestone ? <p className="mt-1 text-xs text-slate-700">Next milestone: {item.nextMilestone}</p> : null}
            </Link>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
