"use client";

import Link from "next/link";

import { useIndustryTemplate } from "@/hooks/use-industry-template";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

export function IndustryTemplateBanner({
  compact = false,
  showActions = true,
  className = ""
}: {
  compact?: boolean;
  showActions?: boolean;
  className?: string;
}): JSX.Element | null {
  const { data, loading } = useIndustryTemplate(true);

  if (loading) return null;
  if (!data?.template) return null;

  const payload = data.template.templatePayload;
  const checkpoints = Array.isArray(payload.suggested_checkpoints) ? (payload.suggested_checkpoints as string[]) : [];
  const managerSignals = Array.isArray(payload.manager_attention_signals) ? (payload.manager_attention_signals as string[]) : [];

  return (
    <Card className={`border-sky-100 bg-sky-50/50 ${className}`}>
      <CardContent className={compact ? "p-3 text-xs" : "p-4 text-sm"}>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="secondary">Industry Template</Badge>
          <Badge>{data.template.displayName}</Badge>
          <Badge variant="outline">{data.template.templateKey}</Badge>
          {showActions ? (
            <Button asChild size="sm" variant="outline" className="ml-auto">
              <Link href="/settings/templates">Manage Template</Link>
            </Button>
          ) : null}
        </div>
        <p className="mt-2 text-slate-700">{data.template.summary}</p>
        <p className="mt-1 text-muted-foreground">
          Checkpoints: {checkpoints.slice(0, 3).join(" / ") || "-"}
        </p>
        <p className="mt-1 text-muted-foreground">
          Manager signals: {managerSignals.slice(0, 3).join(" / ") || "-"}
        </p>
      </CardContent>
    </Card>
  );
}
