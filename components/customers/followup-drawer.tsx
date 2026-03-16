"use client";

import { useMemo, useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { useAppData } from "@/components/shared/app-data-provider";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import type { FollowupMethod } from "@/types/followup";

const methodOptions: Array<{ value: FollowupMethod; label: string }> = [
  { value: "phone", label: "电话" },
  { value: "wechat", label: "微信" },
  { value: "email", label: "邮件" },
  { value: "meeting", label: "面谈" },
  { value: "other", label: "其他" }
];

function toLocalInputValue(source: Date): string {
  const date = new Date(source.getTime() - source.getTimezoneOffset() * 60 * 1000);
  return date.toISOString().slice(0, 16);
}

interface FollowupDrawerProps {
  customerId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function FollowupDrawer({ customerId, open, onOpenChange }: FollowupDrawerProps): JSX.Element {
  const { user } = useAuth();
  const { addFollowup } = useAppData();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const defaultNextTime = useMemo(() => toLocalInputValue(new Date(Date.now() + 2 * 24 * 60 * 60 * 1000)), []);

  const [method, setMethod] = useState<FollowupMethod>("phone");
  const [summary, setSummary] = useState("");
  const [customerNeeds, setCustomerNeeds] = useState("");
  const [objections, setObjections] = useState("");
  const [nextPlan, setNextPlan] = useState("");
  const [nextFollowupAt, setNextFollowupAt] = useState(defaultNextTime);
  const [needsAiAnalysis, setNeedsAiAnalysis] = useState(true);

  const resetForm = (): void => {
    setMethod("phone");
    setSummary("");
    setCustomerNeeds("");
    setObjections("");
    setNextPlan("");
    setNextFollowupAt(defaultNextTime);
    setNeedsAiAnalysis(true);
    setError(null);
    setNotice(null);
  };

  const handleSubmit = async (): Promise<void> => {
    if (!user) return;
    if (!summary.trim() || !customerNeeds.trim() || !nextPlan.trim()) {
      setError("请填写沟通摘要、客户需求和下一步计划。");
      return;
    }

    setSubmitting(true);
    setError(null);
    setNotice(null);
    try {
      const result = await addFollowup({
        customerId,
        ownerId: user.id,
        ownerName: user.name,
        method,
        summary: summary.trim(),
        customerNeeds: customerNeeds.trim(),
        objections: objections.trim(),
        nextPlan: nextPlan.trim(),
        nextFollowupAt: new Date(nextFollowupAt).toISOString(),
        needsAiAnalysis
      });

      if (result.analysisStatus === "failed") {
        setNotice(result.analysisMessage);
        return;
      }

      setNotice(result.analysisMessage);
      onOpenChange(false);
      resetForm();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "提交失败，请稍后重试。");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="overflow-y-auto">
        <SheetHeader>
          <SheetTitle>新增跟进记录</SheetTitle>
          <SheetDescription>录入本次沟通信息，系统会自动更新时间线并可触发 AI 分析。</SheetDescription>
        </SheetHeader>

        <div className="mt-6 space-y-4">
          <div>
            <Label>沟通方式</Label>
            <Select value={method} onValueChange={(value: FollowupMethod) => setMethod(value)}>
              <SelectTrigger className="mt-1">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {methodOptions.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div>
            <Label htmlFor="summary">沟通摘要</Label>
            <Textarea
              id="summary"
              className="mt-1"
              value={summary}
              onChange={(e) => setSummary(e.target.value)}
              placeholder="本次沟通发生了什么，客户反馈如何？"
            />
          </div>

          <div>
            <Label htmlFor="needs">客户需求</Label>
            <Textarea id="needs" className="mt-1" value={customerNeeds} onChange={(e) => setCustomerNeeds(e.target.value)} placeholder="客户当前最关注的需求点" />
          </div>

          <div>
            <Label htmlFor="objections">异议 / 阻碍</Label>
            <Textarea id="objections" className="mt-1" value={objections} onChange={(e) => setObjections(e.target.value)} placeholder="预算、决策链、竞品等阻碍因素" />
          </div>

          <div>
            <Label htmlFor="next-plan">下一步计划</Label>
            <Textarea id="next-plan" className="mt-1" value={nextPlan} onChange={(e) => setNextPlan(e.target.value)} placeholder="你打算如何推进下一步行动？" />
          </div>

          <div>
            <Label htmlFor="next-time">下次跟进时间</Label>
            <Input id="next-time" className="mt-1" type="datetime-local" value={nextFollowupAt} onChange={(e) => setNextFollowupAt(e.target.value)} />
          </div>

          <div className="rounded-lg border bg-slate-50 p-3">
            <div className="flex items-center justify-between">
              <div>
                <Label htmlFor="ai-analysis">提交后自动分析</Label>
                <p className="text-xs text-muted-foreground">勾选后触发 AI 结构化分析，并自动更新风险提醒。</p>
              </div>
              <Switch id="ai-analysis" checked={needsAiAnalysis} onCheckedChange={setNeedsAiAnalysis} />
            </div>
          </div>
        </div>

        {error ? <p className="mt-3 text-sm text-rose-600">{error}</p> : null}
        {notice ? <p className="mt-3 text-sm text-slate-600">{notice}</p> : null}

        <SheetFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={() => void handleSubmit()} disabled={submitting}>
            {submitting ? "提交中..." : "保存跟进记录"}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
