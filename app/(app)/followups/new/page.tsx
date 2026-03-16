"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { useAppData } from "@/components/shared/app-data-provider";
import { PageHeader } from "@/components/shared/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
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

function getDefaultDateTimeLocal(): string {
  const date = new Date(Date.now() + 24 * 60 * 60 * 1000);
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60 * 1000);
  return local.toISOString().slice(0, 16);
}

export default function NewFollowupPage(): JSX.Element {
  const { user } = useAuth();
  const { customers, addFollowup, loading, error } = useAppData();

  const scopedCustomers = useMemo(() => {
    if (!user) return [];
    return user.role === "manager" ? customers : customers.filter((item) => item.ownerId === user.id);
  }, [customers, user]);

  const [customerId, setCustomerId] = useState(scopedCustomers[0]?.id ?? "");
  const [method, setMethod] = useState<FollowupMethod>("phone");
  const [summary, setSummary] = useState("");
  const [customerNeeds, setCustomerNeeds] = useState("");
  const [objections, setObjections] = useState("");
  const [nextPlan, setNextPlan] = useState("");
  const [nextFollowupAt, setNextFollowupAt] = useState(getDefaultDateTimeLocal());
  const [needsAiAnalysis, setNeedsAiAnalysis] = useState(true);
  const [message, setMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!customerId && scopedCustomers.length > 0) {
      setCustomerId(scopedCustomers[0].id);
    }
  }, [customerId, scopedCustomers]);

  const handleSubmit = async (): Promise<void> => {
    if (!user || !customerId || !summary || !customerNeeds || !nextPlan) {
      setMessage("请完善必填字段后再提交。");
      return;
    }

    setSubmitting(true);
    setMessage("");
    try {
      const result = await addFollowup({
        customerId,
        ownerId: user.id,
        ownerName: user.name,
        method,
        summary,
        customerNeeds,
        objections,
        nextPlan,
        nextFollowupAt: new Date(nextFollowupAt).toISOString(),
        needsAiAnalysis
      });
      setSummary("");
      setCustomerNeeds("");
      setObjections("");
      setNextPlan("");
      setNeedsAiAnalysis(true);
      setMessage(result.analysisMessage);
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "提交失败，请稍后重试。");
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return <div className="text-sm text-muted-foreground">正在加载跟进录入数据...</div>;
  }

  if (error) {
    return <div className="text-sm text-rose-600">数据加载失败：{error}</div>;
  }

  return (
    <div>
      <PageHeader
        title="新增跟进记录"
        description="可作为独立录入页使用，和客户详情抽屉逻辑一致。"
        action={
          <Button asChild variant="outline">
            <Link href="/customers">回到客户列表</Link>
          </Button>
        }
      />
      <Card>
        <CardHeader>
          <CardTitle>沟通记录表单</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          <div className="md:col-span-2">
            <Label>客户</Label>
            <Select value={customerId} onValueChange={setCustomerId}>
              <SelectTrigger className="mt-1">
                <SelectValue placeholder="请选择客户" />
              </SelectTrigger>
              <SelectContent>
                {scopedCustomers.map((customer) => (
                  <SelectItem key={customer.id} value={customer.id}>
                    {customer.companyName} | {customer.ownerName}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div>
            <Label>沟通方式</Label>
            <Select value={method} onValueChange={(value: FollowupMethod) => setMethod(value)}>
              <SelectTrigger className="mt-1">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {methodOptions.map((item) => (
                  <SelectItem key={item.value} value={item.value}>
                    {item.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div>
            <Label>下次跟进时间</Label>
            <Input className="mt-1" type="datetime-local" value={nextFollowupAt} onChange={(e) => setNextFollowupAt(e.target.value)} />
          </div>

          <div className="md:col-span-2">
            <Label>沟通摘要</Label>
            <Textarea className="mt-1" value={summary} onChange={(e) => setSummary(e.target.value)} />
          </div>

          <div className="md:col-span-2">
            <Label>客户需求</Label>
            <Textarea className="mt-1" value={customerNeeds} onChange={(e) => setCustomerNeeds(e.target.value)} />
          </div>

          <div className="md:col-span-2">
            <Label>异议 / 阻碍</Label>
            <Textarea className="mt-1" value={objections} onChange={(e) => setObjections(e.target.value)} />
          </div>

          <div className="md:col-span-2">
            <Label>下一步计划</Label>
            <Textarea className="mt-1" value={nextPlan} onChange={(e) => setNextPlan(e.target.value)} />
          </div>

          <div className="md:col-span-2 flex items-center justify-between rounded-lg border bg-slate-50 p-3">
            <div>
              <p className="text-sm font-medium text-slate-900">提交后自动 AI 分析</p>
              <p className="text-xs text-muted-foreground">勾选后将触发服务端分析并自动回写客户摘要、建议与风险提醒。</p>
            </div>
            <Switch checked={needsAiAnalysis} onCheckedChange={setNeedsAiAnalysis} />
          </div>

          <div className="md:col-span-2 flex items-center gap-3">
            <Button onClick={() => void handleSubmit()} disabled={submitting}>
              {submitting ? "提交中..." : "提交跟进记录"}
            </Button>
            {message ? <p className="text-sm text-muted-foreground">{message}</p> : null}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
