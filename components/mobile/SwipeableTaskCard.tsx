"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { QuickActionCard } from "@/components/mobile/QuickActionCard";

export function SwipeableTaskCard(props: {
  title: string;
  subtitle?: string;
  status: string;
  priorityBand: string;
  onStart?: () => void;
  onDone?: () => void;
  onSnooze?: () => void;
}): JSX.Element {
  return (
    <QuickActionCard
      title={props.title}
      subtitle={props.subtitle}
      right={
        <div className="flex items-center gap-1">
          <Badge variant="outline">{props.priorityBand}</Badge>
          <Badge variant="secondary">{props.status}</Badge>
        </div>
      }
    >
      <div className="flex flex-wrap gap-2">
        {props.onStart ? (
          <Button size="sm" variant="outline" onClick={props.onStart}>
            开始
          </Button>
        ) : null}
        {props.onDone ? (
          <Button size="sm" variant="outline" onClick={props.onDone}>
            完成
          </Button>
        ) : null}
        {props.onSnooze ? (
          <Button size="sm" variant="ghost" onClick={props.onSnooze}>
            延后
          </Button>
        ) : null}
      </div>
    </QuickActionCard>
  );
}
