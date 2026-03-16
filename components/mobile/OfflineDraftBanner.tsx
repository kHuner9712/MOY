"use client";

import { AlertTriangle, CloudOff, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function OfflineDraftBanner(props: {
  online: boolean;
  pendingCount: number;
  failedCount?: number;
  syncing?: boolean;
  onSync?: () => void;
  className?: string;
}): JSX.Element | null {
  if (props.online && props.pendingCount === 0 && (props.failedCount ?? 0) === 0) return null;

  return (
    <div className={cn("mb-3 rounded-lg border p-3 text-xs", props.className)}>
      <div className="flex items-center gap-2">
        {props.online ? <AlertTriangle className="h-4 w-4 text-amber-600" /> : <CloudOff className="h-4 w-4 text-amber-700" />}
        <span className="font-medium text-slate-800">
          {props.online ? "有草稿待同步" : "当前离线，草稿已本地保存"}
        </span>
      </div>
      <p className="mt-1 text-muted-foreground">
        待同步 {props.pendingCount} 条{(props.failedCount ?? 0) > 0 ? `，失败 ${props.failedCount} 条` : ""}。
      </p>
      {props.online && props.onSync ? (
        <Button size="sm" variant="outline" className="mt-2" onClick={props.onSync} disabled={props.syncing}>
          <RefreshCw className="mr-1 h-3 w-3" />
          {props.syncing ? "同步中..." : "立即同步"}
        </Button>
      ) : null}
    </div>
  );
}
