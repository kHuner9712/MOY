"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";

import { useAuth } from "@/components/auth/auth-provider";
import { InstallPromptCard } from "@/components/mobile/InstallPromptCard";
import { OfflineDraftBanner } from "@/components/mobile/OfflineDraftBanner";
import { MobileSectionTabs } from "@/components/mobile/MobileSectionTabs";
import { StickyBottomComposer } from "@/components/mobile/StickyBottomComposer";
import { useAppData } from "@/components/shared/app-data-provider";
import { IndustryTemplateBanner } from "@/components/shared/industry-template-banner";
import { PageHeader } from "@/components/shared/page-header";
import { useUserMemory } from "@/hooks/use-user-memory";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useNetworkStatus } from "@/hooks/use-network-status";
import { salesDeskClientService } from "@/services/sales-desk-client-service";
import type { CommunicationReuseResult } from "@/types/sales-desk";
import { formatDateTime } from "@/lib/format";
import {
  listLocalMobileDrafts,
  markLocalMobileDraftFailed,
  markLocalMobileDraftSynced,
  saveLocalMobileDraft
} from "@/lib/mobile-local-drafts";
import { mobileClientService } from "@/services/mobile-client-service";
import { customerService } from "@/services/customer-service";
import type { CaptureExtractResult, CommunicationSourceType } from "@/types/communication";
import { Sparkles } from "lucide-react";

const modeOptions: Array<{ value: CommunicationSourceType; label: string; hint: string }> = [
  { value: "manual_note", label: "快速纪要", hint: "输入自然语言纪要，自动提取结构化字段" },
  { value: "pasted_chat", label: "聊天粘贴", hint: "粘贴微信/邮件/IM 文本并提炼重点" },
  { value: "meeting_note", label: "会议纪要", hint: "导入会议纪要并拆分推进信息" },
  { value: "voice_transcript", label: "语音转写", hint: "粘贴转写文本，后续可接 ASR" },
  { value: "call_summary", label: "电话总结", hint: "快速记录电话沟通核心事实" },
  { value: "imported_text", label: "外部文本", hint: "导入任何长文本后结构化" }
];

function statusTone(status: string): "secondary" | "default" | "destructive" {
  if (status === "completed") return "default";
  if (status === "failed") return "destructive";
  return "secondary";
}

export default function CapturePage(): JSX.Element {
  const { user } = useAuth();
  const { online } = useNetworkStatus();
  const { profile: memoryProfile, items: memoryItems } = useUserMemory(user?.id);
  const {
    customers,
    communicationInputs,
    extractCommunicationInput,
    confirmCommunicationInput,
    loading,
    error
  } = useAppData();

  const scopedCustomers = useMemo(() => {
    if (!user) return [];
    return user.role === "manager" ? customers : customers.filter((item) => item.ownerId === user.id);
  }, [customers, user]);

  const [sourceType, setSourceType] = useState<CommunicationSourceType>("manual_note");
  const [title, setTitle] = useState("");
  const [customerId, setCustomerId] = useState<string>("none");
  const [rawContent, setRawContent] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [latestResult, setLatestResult] = useState<CaptureExtractResult | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [newCustomerName, setNewCustomerName] = useState("");
  const [creatingCustomer, setCreatingCustomer] = useState(false);
  const [localPendingDrafts, setLocalPendingDrafts] = useState(0);
  const [localFailedDrafts, setLocalFailedDrafts] = useState(0);
  const [syncingLocalDrafts, setSyncingLocalDrafts] = useState(false);
  const [refineHint, setRefineHint] = useState<string | null>(null);
  const [reuseResult, setReuseResult] = useState<CommunicationReuseResult | null>(null);
  const [generatingReuse, setGeneratingReuse] = useState(false);
  const [reuseActions, setReuseActions] = useState<Record<string, "idle" | "copied" | "adopted">>({});

  const selectedMode = modeOptions.find((item) => item.value === sourceType) ?? modeOptions[0];
  const recentInputs = useMemo(() => {
    if (!user) return [];
    if (user.role === "manager") return communicationInputs.slice(0, 12);
    return communicationInputs.filter((item) => item.ownerId === user.id).slice(0, 12);
  }, [communicationInputs, user]);

  const refreshLocalDraftCounters = (): void => {
    if (typeof window === "undefined") return;
    const drafts = listLocalMobileDrafts();
    setLocalPendingDrafts(drafts.filter((item) => item.syncStatus === "pending").length);
    setLocalFailedDrafts(drafts.filter((item) => item.syncStatus === "failed").length);
  };

  const saveAsLocalDraft = (): void => {
    const payload = {
      sourceType,
      title,
      customerId: customerId === "none" ? null : customerId,
      rawContent
    };
    saveLocalMobileDraft({
      draftType: "capture",
      summary: title || rawContent.slice(0, 48) || "Mobile capture draft",
      payload
    });
    refreshLocalDraftCounters();
    setMessage("已保存本地草稿，联网后可一键同步。");
  };

  const syncLocalDrafts = async (): Promise<void> => {
    if (typeof window === "undefined") return;
    const drafts = listLocalMobileDrafts().filter((item) => item.draftType === "capture" && item.syncStatus !== "synced");
    if (drafts.length === 0) {
      setMessage("没有待同步的 capture 草稿。");
      return;
    }
    setSyncingLocalDrafts(true);
    for (const draft of drafts) {
      try {
        await mobileClientService.syncDraft({
          localDraftId: draft.localDraftId,
          draftType: draft.draftType,
          summary: draft.summary,
          payload: draft.payload
        });
        markLocalMobileDraftSynced(draft.localDraftId);
      } catch (error) {
        markLocalMobileDraftFailed(draft.localDraftId, error instanceof Error ? error.message : "sync_failed");
      }
    }
    refreshLocalDraftCounters();
    setSyncingLocalDrafts(false);
    setMessage("本地草稿同步已执行，请检查结果。");
  };

  const runMobileRefine = async (): Promise<void> => {
    if (!rawContent.trim()) {
      setRefineHint("请先输入纪要内容。");
      return;
    }
    try {
      const response = await fetch("/api/mobile/capture/refine", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          rawInput: rawContent,
          customerId: customerId === "none" ? undefined : customerId
        })
      });
      const payload = (await response.json()) as {
        success: boolean;
        data: { result: { refined_summary: string; followup_hint: string; next_best_fields_to_fill: string[] } } | null;
        error: string | null;
      };
      if (!response.ok || !payload.success || !payload.data) {
        throw new Error(payload.error ?? "mobile refine failed");
      }
      setRefineHint(`${payload.data.result.followup_hint} 建议补充：${payload.data.result.next_best_fields_to_fill.slice(0, 2).join("、")}`);
    } catch (error) {
      setRefineHint(error instanceof Error ? error.message : "无法生成移动精简建议");
    }
  };

  useEffect(() => {
    refreshLocalDraftCounters();
    const onFocus = () => refreshLocalDraftCounters();
    window.addEventListener("focus", onFocus);
    return () => {
      window.removeEventListener("focus", onFocus);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const submitExtract = async (): Promise<void> => {
    if (!rawContent.trim()) {
      setMessage("请输入沟通内容后再提交。");
      return;
    }

    setSubmitting(true);
    setMessage(null);
    try {
      const result = await extractCommunicationInput({
        sourceType,
        title,
        rawContent,
        customerId: customerId === "none" ? null : customerId
      });

      setLatestResult(result);

      if (result.extractionStatus === "failed") {
        setMessage("抽取失败，原始输入已保存。你可以稍后手动补录跟进。");
      } else if (result.autoApplied) {
        setMessage("结构化完成，系统已自动落库并触发后续分析。");
      } else if (result.requiresConfirmation) {
        setMessage("结构化完成，请确认草稿后再落库。");
      } else {
        setMessage("结构化完成，未触发自动落库。");
      }

      setRawContent("");
      setTitle("");
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "提交失败，请稍后重试。");
    } finally {
      setSubmitting(false);
    }
  };

  const submitConfirm = async (): Promise<void> => {
    if (!latestResult) return;
    setConfirming(true);
    setReuseResult(null);
    try {
      const result = await confirmCommunicationInput({
        inputId: latestResult.inputId,
        customerId: customerId === "none" ? null : customerId
      });
      setMessage(result.message);
      setLatestResult((prev) => (prev ? { ...prev, requiresConfirmation: false, followupId: result.followupId } : prev));

      if (result.followupId || latestResult.inputId) {
        setGeneratingReuse(true);
        try {
          const reuse = await salesDeskClientService.generateCommunicationReuse({
            communicationInputId: latestResult.inputId,
            customerId: customerId === "none" ? undefined : customerId
          });
          setReuseResult(reuse);
          setMessage((prev) =>
            prev
              ? `${prev} | 已生成跟进建议`
              : '已生成跟进建议（AI 草稿，如需调整请查看详情）'
          );
        } catch {
          setMessage((prev) => (prev ? `${prev} | 五处复用生成失败（不影响落库）` : null));
        } finally {
          setGeneratingReuse(false);
        }
      }
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "确认落库失败。");
    } finally {
      setConfirming(false);
    }
  };

  const quickCreateCustomer = async (): Promise<void> => {
    if (!newCustomerName.trim()) {
      setMessage("请先输入公司名称再创建客户。");
      return;
    }

    setCreatingCustomer(true);
    try {
      const created = await customerService.createQuickFromCapture({
        companyName: newCustomerName.trim(),
        contactName: latestResult?.matchedCustomerName ?? undefined
      });
      setCustomerId(created.id);
      setMessage(`已创建客户：${created.companyName}，你可以继续确认落库。`);
      setNewCustomerName("");
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "创建客户失败。");
    } finally {
      setCreatingCustomer(false);
    }
  };

  if (loading) {
    return <div className="text-sm text-muted-foreground">Loading capture workspace...</div>;
  }

  if (error) {
    return <div className="text-sm text-rose-600">Failed to load capture data: {error}</div>;
  }

  return (
    <div>
      <PageHeader
        title="快速录入"
        description="像聊天一样记录沟通，系统自动结构化并生成可执行跟进。"
        action={
          <Button asChild variant="outline">
            <Link href="/followups/new">传统表单录入</Link>
          </Button>
        }
      />
      <InstallPromptCard />
      <IndustryTemplateBanner className="mb-3" compact />
      <OfflineDraftBanner
        online={online}
        pendingCount={localPendingDrafts}
        failedCount={localFailedDrafts}
        syncing={syncingLocalDrafts}
        onSync={() => void syncLocalDrafts()}
      />

      <div className="grid gap-4 xl:grid-cols-[1.5fr_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>沟通输入工作台</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <MobileSectionTabs
              value={sourceType}
              onChange={setSourceType}
              options={modeOptions.map((item) => ({ value: item.value, label: item.label }))}
            />

            <div className="grid gap-4 md:grid-cols-2">
              <div>
                <Label>输入模式</Label>
                <Select value={sourceType} onValueChange={(value: CommunicationSourceType) => setSourceType(value)}>
                  <SelectTrigger className="mt-1">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {modeOptions.map((item) => (
                      <SelectItem key={item.value} value={item.value}>
                        {item.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="mt-1 text-xs text-muted-foreground">{selectedMode.hint}</p>
              </div>

              <div>
                <Label>客户（可选）</Label>
                <Select value={customerId} onValueChange={setCustomerId}>
                  <SelectTrigger className="mt-1">
                    <SelectValue placeholder="先不指定客户" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">先不指定客户</SelectItem>
                    {scopedCustomers.map((item) => (
                      <SelectItem key={item.id} value={item.id}>
                        {item.companyName} | {item.ownerName}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div>
              <Label>标题（可选）</Label>
              <Input className="mt-1" value={title} onChange={(event) => setTitle(event.target.value)} placeholder="例如：与李总电话沟通纪要" />
            </div>

            <div>
              <Label>原始沟通内容</Label>
              <Textarea
                className="mt-1 min-h-[220px] text-base"
                value={rawContent}
                onChange={(event) => setRawContent(event.target.value)}
                placeholder="直接粘贴沟通纪要、聊天摘要、会议记录或语音转写文本..."
              />
            </div>

            <StickyBottomComposer>
              <div className="flex flex-wrap items-center gap-2">
                <Button onClick={() => void submitExtract()} disabled={submitting}>
                  {submitting ? "抽取中..." : "保存并结构化"}
                </Button>
                <Button variant="outline" onClick={saveAsLocalDraft}>
                  离线保存草稿
                </Button>
                <Button variant="outline" onClick={() => void runMobileRefine()}>
                  移动精简建议
                </Button>
                {latestResult?.requiresConfirmation ? (
                  <Button variant="outline" onClick={() => void submitConfirm()} disabled={confirming}>
                    {confirming ? "确认中..." : "确认落库"}
                  </Button>
                ) : null}
              </div>
            </StickyBottomComposer>

            {message ? <p className="text-sm text-muted-foreground">{message}</p> : null}
            {refineHint ? <p className="text-xs text-sky-700">{refineHint}</p> : null}

            {latestResult ? (
              <div className="rounded-lg border bg-slate-50 p-3">
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <Badge variant={statusTone(latestResult.extractionStatus)}>{latestResult.extractionStatus}</Badge>
                  {latestResult.autoApplied ? <Badge>自动落库</Badge> : null}
                  {latestResult.requiresConfirmation ? <Badge variant="secondary">待确认草稿</Badge> : null}
                </div>
                <p className="text-xs text-muted-foreground">
                  匹配客户：{latestResult.matchedCustomerName ?? "未命中"} | 置信度：
                  {typeof latestResult.confidenceOfMatch === "number" ? `${Math.round(latestResult.confidenceOfMatch * 100)}%` : "-"}
                </p>
                {latestResult.followupId ? <p className="mt-1 text-xs text-muted-foreground">关联跟进：{latestResult.followupId}</p> : null}
                {memoryProfile ? (
                  <div className="mt-2 rounded-md border bg-white p-2 text-xs text-muted-foreground">
                    <p>基于你的工作记忆：</p>
                    <p>- 跟进节奏建议：{memoryProfile.commonFollowupRhythm[0] ?? "建议 3 天内安排二次跟进"}</p>
                    <p>- 常用有效策略：{memoryProfile.effectiveTactics[0] ?? "分阶段推进并明确决策人"}</p>
                    <p>- 风险提醒：{memoryProfile.riskBlindSpots[0] ?? "注意高概率客户的停滞信号"}</p>
                  </div>
                ) : null}
                {latestResult.requiresConfirmation && !latestResult.matchedCustomerId ? (
                  <div className="mt-3 space-y-2 rounded-md border bg-white p-3">
                    <p className="text-xs text-muted-foreground">未命中客户，可先快速创建客户草稿：</p>
                    <div className="flex items-center gap-2">
                      <Input value={newCustomerName} onChange={(event) => setNewCustomerName(event.target.value)} placeholder="输入公司名称，例如：星河制造" />
                      <Button variant="outline" onClick={() => void quickCreateCustomer()} disabled={creatingCustomer}>
                        {creatingCustomer ? "创建中..." : "快速新建客户"}
                      </Button>
                    </div>
                  </div>
                ) : null}
                {generatingReuse ? (
                  <div className="mt-3 rounded-md border bg-blue-50 p-4 text-center">
                    <div className="animate-pulse">
                      <p className="text-sm text-blue-700">✨ 正在生成五处复用内容...</p>
                    </div>
                  </div>
                ) : reuseResult ? (
                  <div className="mt-3 space-y-3 rounded-md border bg-blue-50 p-4">
                    <div className="flex items-center gap-2 border-b border-blue-100 pb-2">
                      <Sparkles className="h-4 w-4 text-blue-600" />
                      <p className="text-sm font-medium text-blue-900">一次沟通，五处复用</p>
                      {reuseResult.usedFallback && (
                        <Badge variant="secondary" className="text-xs">规则生成</Badge>
                      )}
                    </div>
                    <div className="space-y-3">
                      {reuseResult.customerMessageDraft && (
                        <div className="rounded-lg bg-white p-3">
                          <div className="mb-2 flex items-center justify-between">
                            <div className="flex items-center gap-1.5">
                              <span className="text-base">💬</span>
                              <p className="text-xs font-medium text-blue-800">给客户消息</p>
                            </div>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-7 text-xs"
                              onClick={() => {
                                void navigator.clipboard.writeText(reuseResult.customerMessageDraft ?? "");
                                setReuseActions(prev => ({ ...prev, customerMessage: "copied" }));
                                setTimeout(() => setReuseActions(prev => ({ ...prev, customerMessage: "idle" })), 2000);
                              }}
                            >
                              {reuseActions.customerMessage === "copied" ? "已复制 ✓" : "复制"}
                            </Button>
                          </div>
                          <p className="text-sm text-gray-700 leading-relaxed">{reuseResult.customerMessageDraft}</p>
                        </div>
                      )}
                      {reuseResult.internalBrief && (
                        <div className="rounded-lg bg-white p-3">
                          <div className="mb-2 flex items-center justify-between">
                            <div className="flex items-center gap-1.5">
                              <span className="text-base">📋</span>
                              <p className="text-xs font-medium text-blue-800">给经理汇报</p>
                            </div>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-7 text-xs"
                              onClick={() => {
                                void navigator.clipboard.writeText(reuseResult.internalBrief ?? "");
                                setReuseActions(prev => ({ ...prev, internalBrief: "copied" }));
                                setTimeout(() => setReuseActions(prev => ({ ...prev, internalBrief: "idle" })), 2000);
                              }}
                            >
                              {reuseActions.internalBrief === "copied" ? "已复制 ✓" : "复制"}
                            </Button>
                          </div>
                          <p className="text-sm text-gray-700 leading-relaxed">{reuseResult.internalBrief}</p>
                        </div>
                      )}
                      {reuseResult.nextStepSuggestion && (
                        <div className="rounded-lg bg-white p-3">
                          <div className="mb-2 flex items-center justify-between">
                            <div className="flex items-center gap-1.5">
                              <span className="text-base">📌</span>
                              <p className="text-xs font-medium text-blue-800">下一步动作</p>
                            </div>
                            <Button
                              variant="outline"
                              size="sm"
                              className="h-7 text-xs"
                              onClick={() => {
                                setReuseActions(prev => ({ ...prev, nextStep: "adopted" }));
                              }}
                            >
                              {reuseActions.nextStep === "adopted" ? "已采用 ✓" : "采用"}
                            </Button>
                          </div>
                          <p className="text-sm text-gray-700 leading-relaxed">{reuseResult.nextStepSuggestion}</p>
                        </div>
                      )}
                      {!reuseResult.nextStepSuggestion && !reuseResult.internalBrief && !reuseResult.customerMessageDraft && (
                        <p className="text-xs text-blue-600">暂无更多建议，可直接进行跟进</p>
                      )}
                    </div>
                  </div>
                ) : null}
              </div>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>最近沟通输入</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {recentInputs.length === 0 ? <p className="text-sm text-muted-foreground">暂无输入记录。</p> : null}
            {recentInputs.map((item) => (
              <div key={item.id} className="rounded-lg border p-3">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <p className="text-sm font-semibold text-slate-900">{item.title}</p>
                  <Badge variant={statusTone(item.extractionStatus)}>{item.extractionStatus}</Badge>
                </div>
                <p className="text-xs text-muted-foreground">{item.customerName ?? "未关联客户"}</p>
                <p className="mt-1 text-xs text-muted-foreground">{formatDateTime(item.createdAt)}</p>
                {item.extractedFollowupId ? (
                  <p className="mt-1 text-xs">
                    <Link className="text-sky-700" href={`/customers/${item.customerId ?? ""}`}>
                      查看客户详情
                    </Link>
                  </p>
                ) : null}
              </div>
            ))}
          </CardContent>
        </Card>
      </div>

      <Card className="mt-4">
        <CardHeader>
          <CardTitle>记忆增强提示</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          {!memoryProfile ? <p className="text-muted-foreground">暂无个人工作记忆，请先前往“工作记忆”页面刷新。</p> : null}
          {memoryProfile ? (
            <>
              <p>- 你常见客户类型：{memoryProfile.preferredCustomerTypes.slice(0, 2).join("；") || "暂无"}</p>
              <p>- 你常见异议：{memoryProfile.commonObjections.slice(0, 2).join("；") || "暂无"}</p>
              <p>- 你更有效的推进方式：{memoryProfile.effectiveTactics.slice(0, 2).join("；") || "暂无"}</p>
              <p className="text-xs text-muted-foreground">最近更新条目：{memoryItems.slice(0, 3).length} 条</p>
            </>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
