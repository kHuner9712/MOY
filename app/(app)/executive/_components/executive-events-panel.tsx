"use client";

import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDateTime } from "@/lib/format";
import type { BusinessEvent } from "@/types/automation";
import { ArrowUpRight } from "lucide-react";

type EventAction = "ack" | "resolve" | "ignore";

export function ExecutiveEventsPanel(props: {
  events: BusinessEvent[];
  busyEventId: string | null;
  onEventAction: (eventId: string, action: EventAction) => void;
}): JSX.Element {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Open Business Events</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {props.events.length === 0 ? <p className="text-sm text-muted-foreground">No open business events.</p> : null}
        {props.events.slice(0, 20).map((event) => (
          <div key={event.id} className="rounded-md border p-2">
            <div className="mb-1 flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <Badge variant="outline">{event.eventType}</Badge>
                <Badge variant={event.severity === "critical" ? "destructive" : event.severity === "warning" ? "default" : "secondary"}>
                  {event.severity}
                </Badge>
                <Badge variant={event.status === "open" ? "destructive" : event.status === "acknowledged" ? "default" : "secondary"}>
                  {event.status}
                </Badge>
              </div>
              <p className="text-xs text-muted-foreground">{formatDateTime(event.createdAt)}</p>
            </div>
            <p className="text-sm text-slate-700">{event.eventSummary}</p>
            <div className="mt-2 flex flex-wrap gap-2">
              <Button size="sm" variant="outline" disabled={props.busyEventId === event.id} onClick={() => props.onEventAction(event.id, "ack")}>
                Ack
              </Button>
              <Button size="sm" variant="outline" disabled={props.busyEventId === event.id} onClick={() => props.onEventAction(event.id, "resolve")}>
                Resolve
              </Button>
              <Button size="sm" variant="ghost" disabled={props.busyEventId === event.id} onClick={() => props.onEventAction(event.id, "ignore")}>
                Ignore
              </Button>
              {event.entityType === "deal_room" ? (
                <Button asChild size="sm" variant="outline">
                  <Link href={`/deals/${event.entityId}`}>
                    Open Deal
                    <ArrowUpRight className="ml-1 h-3 w-3" />
                  </Link>
                </Button>
              ) : null}
              {event.entityType === "customer" ? (
                <Button asChild size="sm" variant="outline">
                  <Link href={`/customers/${event.entityId}`}>
                    Open Customer
                    <ArrowUpRight className="ml-1 h-3 w-3" />
                  </Link>
                </Button>
              ) : null}
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
