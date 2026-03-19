"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { CheckCircle2, ChevronRight, Clock, Loader2, Rocket, Sparkles, X } from "lucide-react";

import { useAuth } from "@/components/auth/auth-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { settingsClientService } from "@/services/settings-client-service";
import type { OnboardingChecklist } from "@/types/productization";

interface OnboardingQuickStartProps {
  className?: string;
  compact?: boolean;
}

interface QuickStartStep {
  key: string;
  title: string;
  description: string;
  href?: string;
  action?: string;
  priority: "high" | "medium" | "low";
  estimatedMinutes: number;
}

const QUICK_START_STEPS: QuickStartStep[] = [
  {
    key: "industry_template",
    title: "选择行业模板",
    description: "根据您的业务类型选择预设模板，快速配置阶段、预警规则和话术库",
    href: "/settings/templates",
    priority: "high",
    estimatedMinutes: 3
  },
  {
    key: "org_profile",
    title: "完善组织信息",
    description: "设置公司名称、时区、默认客户阶段等基础配置",
    href: "/settings/org",
    priority: "high",
    estimatedMinutes: 2
  },
  {
    key: "ai_setup",
    title: "配置 AI 服务",
    description: "确保 DeepSeek API 密钥已配置，启用智能分析和自动规划功能",
    href: "/settings/ai",
    priority: "high",
    estimatedMinutes: 2
  },
  {
    key: "team_invite",
    title: "邀请团队成员",
    description: "添加销售和管理人员，开始团队协作",
    href: "/settings/team",
    priority: "medium",
    estimatedMinutes: 5
  },
  {
    key: "first_data",
    title: "导入首批客户",
    description: "通过导入中心批量导入客户数据，或手动创建首批客户",
    href: "/imports",
    priority: "high",
    estimatedMinutes: 10
  }
];

export function NewCustomerOnboardingBanner({
  className = "",
  compact = false
}: OnboardingQuickStartProps): JSX.Element | null {
  const { user } = useAuth();
  const [loading, setLoading] = useState(true);
  const [dismissed, setDismissed] = useState(false);
  const [checklist, setChecklist] = useState<OnboardingChecklist | null>(null);
  const [onboardingCompleted, setOnboardingCompleted] = useState(false);

  useEffect(() => {
    if (user?.role !== "manager") {
      setLoading(false);
      return;
    }

    const loadChecklist = async () => {
      try {
        const payload = await settingsClientService.getOnboardingSettings();
        setChecklist(payload.checklist);
        setOnboardingCompleted(payload.checklist.completed);
      } catch {
        setChecklist(null);
      } finally {
        setLoading(false);
      }
    };

    void loadChecklist();
  }, [user?.role]);

  useEffect(() => {
    if (typeof window !== "undefined") {
      const dismissedAt = sessionStorage.getItem("moy_onboarding_banner_dismissed");
      if (dismissedAt) {
        setDismissed(true);
      }
    }
  }, []);

  const handleDismiss = () => {
    if (typeof window !== "undefined") {
      sessionStorage.setItem("moy_onboarding_banner_dismissed", new Date().toISOString());
    }
    setDismissed(true);
  };

  if (loading) {
    return (
      <Card className={`border-indigo-100 bg-gradient-to-r from-indigo-50/50 to-sky-50/50 ${className}`}>
        <CardContent className="p-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            正在检查初始化进度...
          </div>
        </CardContent>
      </Card>
    );
  }

  if (onboardingCompleted || dismissed) {
    return null;
  }

  if (!checklist) {
    return null;
  }

  const incompleteSteps = QUICK_START_STEPS.filter((step) => {
    const checklistItem = checklist.items.find((item) => item.key === step.key);
    return !checklistItem?.completed;
  });

  const completedCount = QUICK_START_STEPS.length - incompleteSteps.length;
  const progressPercent = Math.round((completedCount / QUICK_START_STEPS.length) * 100);

  if (compact) {
    return (
      <Card className={`border-indigo-100 bg-gradient-to-r from-indigo-50/50 to-sky-50/50 ${className}`}>
        <CardContent className="p-3">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Rocket className="h-4 w-4 text-indigo-600" />
              <span className="text-sm font-medium text-slate-800">
                初始化进度 {progressPercent}%
              </span>
              <Progress value={progressPercent} className="h-2 w-24" />
            </div>
            <div className="flex items-center gap-2">
              {incompleteSteps[0] && (
                <Button asChild size="sm" variant="outline">
                  <Link href={incompleteSteps[0].href || "#"}>
                    {incompleteSteps[0].title}
                    <ChevronRight className="ml-1 h-3 w-3" />
                  </Link>
                </Button>
              )}
              <Button size="sm" variant="ghost" onClick={handleDismiss}>
                <X className="h-3 w-3" />
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className={`border-indigo-100 bg-gradient-to-r from-indigo-50/50 to-sky-50/50 ${className}`}>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-base">
            <Rocket className="h-5 w-5 text-indigo-600" />
            欢迎使用 MOY！完成以下步骤快速上手
          </CardTitle>
          <Button size="sm" variant="ghost" onClick={handleDismiss}>
            <X className="h-4 w-4" />
          </Button>
        </div>
        <div className="flex items-center gap-3 text-sm text-muted-foreground">
          <span>已完成 {completedCount}/{QUICK_START_STEPS.length} 项</span>
          <Progress value={progressPercent} className="h-2 w-32" />
          <Badge variant={progressPercent >= 80 ? "default" : "secondary"}>{progressPercent}%</Badge>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
          {QUICK_START_STEPS.map((step) => {
            const checklistItem = checklist.items.find((item) => item.key === step.key);
            const isCompleted = checklistItem?.completed ?? false;

            return (
              <div
                key={step.key}
                className={`rounded-lg border p-3 ${
                  isCompleted
                    ? "border-emerald-200 bg-emerald-50/50"
                    : step.priority === "high"
                      ? "border-indigo-200 bg-white"
                      : "border-slate-200 bg-white"
                }`}
              >
                <div className="flex items-start gap-2">
                  <CheckCircle2
                    className={`mt-0.5 h-4 w-4 ${
                      isCompleted ? "text-emerald-600" : "text-slate-300"
                    }`}
                  />
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <p className={`text-sm font-medium ${isCompleted ? "text-slate-500 line-through" : "text-slate-800"}`}>
                        {step.title}
                      </p>
                      {step.priority === "high" && !isCompleted && (
                        <Badge variant="destructive" className="text-xs">必做</Badge>
                      )}
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">{step.description}</p>
                    <div className="mt-2 flex items-center gap-2">
                      {!isCompleted && step.href && (
                        <Button asChild size="sm" variant="outline">
                          <Link href={step.href}>
                            去完成
                            <ChevronRight className="ml-1 h-3 w-3" />
                          </Link>
                        </Button>
                      )}
                      <span className="flex items-center gap-1 text-xs text-muted-foreground">
                        <Clock className="h-3 w-3" />
                        约 {step.estimatedMinutes} 分钟
                      </span>
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        {progressPercent >= 60 && progressPercent < 100 && (
          <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50/50 p-3">
            <div className="flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-amber-600" />
              <p className="text-sm text-amber-800">
                您已完成大部分初始化步骤！建议尽快完成剩余项目以获得最佳体验。
              </p>
            </div>
          </div>
        )}

        {progressPercent >= 100 && (
          <div className="mt-4 rounded-lg border border-emerald-200 bg-emerald-50/50 p-3">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="h-4 w-4 text-emerald-600" />
              <p className="text-sm text-emerald-800">
                恭喜！您已完成所有初始化步骤。现在可以开始使用 MOY 的完整功能了。
              </p>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
