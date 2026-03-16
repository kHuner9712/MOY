import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDateTime } from "@/lib/format";
import type { AiRun } from "@/types/ai";
import type { AlertItem } from "@/types/alert";
import type { Customer } from "@/types/customer";

function statusTone(status: AiRun["status"]): "secondary" | "default" | "destructive" {
  if (status === "completed") return "default";
  if (status === "failed") return "destructive";
  return "secondary";
}

function statusLabel(status: AiRun["status"]): string {
  if (status === "completed") return "已完成";
  if (status === "failed") return "失败";
  if (status === "running") return "分析中";
  return "排队中";
}

function resultSourceLabel(source: AiRun["result_source"]): string {
  return source === "fallback" ? "回退结果" : "模型结果";
}

export function CustomerAiPanel({
  customer,
  latestRun,
  runs,
  onRerun,
  running,
  rerunMessage,
  leakAlert
}: {
  customer: Customer;
  latestRun: AiRun | null;
  runs: AiRun[];
  onRerun: () => Promise<void> | void;
  running: boolean;
  rerunMessage: string | null;
  leakAlert: AlertItem | null;
}): JSX.Element {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>AI 分析面板</CardTitle>
        <div className="flex items-center gap-2">
          {latestRun ? <Badge variant={statusTone(latestRun.status)}>{statusLabel(latestRun.status)}</Badge> : null}
          {latestRun?.result_source === "fallback" ? <Badge variant="secondary">已回退</Badge> : null}
          <Button size="sm" variant="outline" onClick={() => void onRerun()} disabled={running}>
            {running ? "分析中..." : "重新 AI 分析"}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <section className="rounded-lg border border-sky-100 bg-sky-50/70 p-3">
          <p className="text-xs font-semibold uppercase tracking-[0.15em] text-sky-700">客户状态总结</p>
          <p className="mt-1 text-sm leading-6 text-slate-700">{customer.aiSummary}</p>
        </section>

        <section className="rounded-lg border border-emerald-100 bg-emerald-50/70 p-3">
          <p className="text-xs font-semibold uppercase tracking-[0.15em] text-emerald-700">推荐下一步行动</p>
          <p className="mt-1 text-sm leading-6 text-slate-700">{customer.aiSuggestion}</p>
        </section>

        <section className="rounded-lg border border-rose-100 bg-rose-50/70 p-3">
          <p className="text-xs font-semibold uppercase tracking-[0.15em] text-rose-700">风险提醒</p>
          <p className="mt-1 text-sm leading-6 text-slate-700">{customer.aiRiskJudgement}</p>
          {leakAlert ? <p className="mt-2 text-xs text-rose-700">当前漏单证据：{leakAlert.evidence.slice(0, 3).join("；") || leakAlert.message}</p> : null}
        </section>

        <section className="rounded-lg border p-3">
          <p className="text-xs font-semibold uppercase tracking-[0.15em] text-slate-600">最近 AI 分析记录</p>
          {latestRun ? (
            <p className="mt-1 text-xs text-muted-foreground">
              最近一次：{formatDateTime(latestRun.created_at)} | 状态：{statusLabel(latestRun.status)}
            </p>
          ) : (
            <p className="mt-1 text-xs text-muted-foreground">暂无 AI 分析记录</p>
          )}

          <div className="mt-2 space-y-2">
            {runs.slice(0, 5).map((run) => (
              <div key={run.id} className="rounded-md border bg-slate-50 p-2 text-xs">
                <div className="flex items-center justify-between">
                  <span className="font-medium">{run.scenario}</span>
                  <Badge variant={statusTone(run.status)}>{statusLabel(run.status)}</Badge>
                </div>
                <p className="mt-1 text-muted-foreground">{formatDateTime(run.created_at)}</p>
                <p className="mt-1 text-muted-foreground">{resultSourceLabel(run.result_source)}</p>
                {run.fallback_reason ? <p className="mt-1 text-amber-700">回退原因：{run.fallback_reason}</p> : null}
                {run.error_message ? <p className="mt-1 text-rose-600">{run.error_message}</p> : null}
              </div>
            ))}
          </div>
        </section>

        {rerunMessage ? <p className="text-xs text-muted-foreground">{rerunMessage}</p> : null}
      </CardContent>
    </Card>
  );
}

