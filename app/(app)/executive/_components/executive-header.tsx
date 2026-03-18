"use client";

import Link from "next/link";

import { PageHeader } from "@/components/shared/page-header";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { ExecutiveBriefType } from "@/types/automation";
import { RefreshCw } from "lucide-react";

export function ExecutiveHeader(props: {
  briefType: ExecutiveBriefType;
  generatingBrief: boolean;
  onBriefTypeChange: (value: ExecutiveBriefType) => void;
  onRefresh: () => void;
  onGenerateBrief: () => void;
}): JSX.Element {
  return (
    <PageHeader
      title="Executive Cockpit"
      description="Unified operations cockpit for risks, opportunities, rule hits and actionable management moves."
      action={
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={props.onRefresh}>
            <RefreshCw className="mr-1 h-4 w-4" />
            Refresh Signals
          </Button>
          <Select value={props.briefType} onValueChange={(value) => props.onBriefTypeChange(value as ExecutiveBriefType)}>
            <SelectTrigger className="w-[180px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="executive_daily">executive_daily</SelectItem>
              <SelectItem value="executive_weekly">executive_weekly</SelectItem>
              <SelectItem value="retention_watch">retention_watch</SelectItem>
              <SelectItem value="trial_watch">trial_watch</SelectItem>
              <SelectItem value="deal_watch">deal_watch</SelectItem>
            </SelectContent>
          </Select>
          <Button onClick={props.onGenerateBrief} disabled={props.generatingBrief}>
            {props.generatingBrief ? "Generating..." : "Generate Brief"}
          </Button>
          <Button asChild variant="outline">
            <Link href="/settings/automation">Automation Rules</Link>
          </Button>
        </div>
      }
    />
  );
}
