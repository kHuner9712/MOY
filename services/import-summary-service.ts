import type { ServerSupabaseClient } from "@/lib/supabase/types";
import { getOrgSettings, patchOnboardingSteps } from "@/services/org-settings-service";
import { getOrgFeatureFlagMap } from "@/services/org-feature-service";
import { getOrgAiControlStatus } from "@/services/org-ai-settings-service";
import { getAiProvider, isRuleFallbackEnabled } from "@/lib/ai/provider";
import { getActivePromptVersion } from "@/services/ai-prompt-service";
import { createAiRun, updateAiRunStatus } from "@/services/ai-run-service";
import { z } from "zod";

type DbClient = ServerSupabaseClient;

function nowIso(): string {
  return new Date().toISOString();
}

const importSummaryResultSchema = z.object({
  health_distribution: z.object({
    healthy: z.number(),
    stable: z.number(),
    at_risk: z.number(),
    critical: z.number()
  }),
  stalled_count: z.number(),
  priority_items: z.array(z.object({
    type: z.enum(["risk_customer", "stalled_deal", "missing_followup", "high_value_opportunity"]),
    title: z.string(),
    customer_name: z.string().optional(),
    reason: z.string(),
    suggested_action: z.string()
  })),
  recommended_rules: z.array(z.object({
    rule_name: z.string(),
    reason: z.string(),
    priority: z.enum(["high", "medium", "low"])
  })),
  manager_attention_points: z.array(z.string()),
  quick_wins: z.array(z.string())
});

export type ImportSummaryResult = z.infer<typeof importSummaryResultSchema>;

export interface ImportBusinessSummary {
  summary: ImportSummaryResult;
  usedFallback: boolean;
  fallbackReason: string | null;
  generatedAt: string;
}

export async function generateImportBusinessSummary(params: {
  supabase: DbClient;
  orgId: string;
  actorUserId: string;
  importJobId: string;
}): Promise<ImportBusinessSummary> {
  const { supabase, orgId, actorUserId, importJobId } = params;

  const [settings, aiStatus, featureFlags, importJob] = await Promise.all([
    getOrgSettings({ supabase, orgId }),
    getOrgAiControlStatus({ supabase, orgId }),
    getOrgFeatureFlagMap({ supabase, orgId }),
    supabase.from("import_jobs").select("*").eq("id", importJobId).single()
  ]);

  const [customerCountRes, healthSnapshotsRes, opportunitiesRes, workItemsRes, alertsRes] = await Promise.all([
    supabase.from("customers").select("id", { count: "exact", head: true }).eq("org_id", orgId),
    supabase.from("customer_health_snapshots").select("health_band, overall_health_score, customer_id").eq("org_id", orgId).order("computed_at", { ascending: false }),
    supabase.from("opportunities").select("id, title, stage, expected_close_date, customer_id, customers(name)").eq("org_id", orgId).eq("status", "active"),
    supabase.from("work_items").select("id, work_type, priority_band, status, customer_id").eq("org_id", orgId).neq("status", "done"),
    supabase.from("alerts").select("id, alert_type, level, status, customer_id").eq("org_id", orgId).neq("status", "resolved")
  ]);

  const healthDistribution = {
    healthy: 0,
    stable: 0,
    at_risk: 0,
    critical: 0
  };

  const seenCustomers = new Set<string>();
  for (const snapshot of (healthSnapshotsRes.data ?? [])) {
    if (seenCustomers.has(snapshot.customer_id)) continue;
    seenCustomers.add(snapshot.customer_id);
    const band = snapshot.health_band as keyof typeof healthDistribution;
    if (band in healthDistribution) {
      healthDistribution[band]++;
    }
  }

  const stalledOpportunities = ((opportunitiesRes.data ?? []) as any[]).filter((opp) => {
    if (!opp.expected_close_date) return false;
    const closeDate = new Date(opp.expected_close_date);
    const now = new Date();
    return closeDate < now && opp.stage !== "closed_won" && opp.stage !== "closed_lost";
  });

  const criticalAlerts = ((alertsRes.data ?? []) as any[]).filter((a) => a.level === "critical");
  const highPriorityWorkItems = ((workItemsRes.data ?? []) as any[]).filter((w) => w.priority_band === "critical" || w.priority_band === "high");

  const priorityItems: ImportSummaryResult["priority_items"] = [];

  for (const alert of criticalAlerts.slice(0, 3)) {
    priorityItems.push({
      type: "risk_customer",
      title: `风险预警: ${alert.alert_type}`,
      reason: "客户存在严重风险信号，需要立即关注",
      suggested_action: "查看客户详情并制定挽回计划"
    });
  }

  for (const opp of stalledOpportunities.slice(0, 3)) {
    const customerName = (opp.customers as { name: string } | null)?.name;
    priorityItems.push({
      type: "stalled_deal",
      title: opp.title ?? "未命名商机",
      customer_name: customerName,
      reason: "商机已超过预期关闭日期但未成交",
      suggested_action: "联系客户确认当前状态，更新预期关闭日期"
    });
  }

  for (const work of highPriorityWorkItems.slice(0, 3)) {
    priorityItems.push({
      type: "missing_followup",
      title: `高优先级任务: ${work.work_type}`,
      reason: "存在需要立即处理的跟进任务",
      suggested_action: "尽快完成此任务以降低风险"
    });
  }

  const recommendedRules: ImportSummaryResult["recommended_rules"] = [
    {
      rule_name: "客户健康度下降预警",
      reason: "自动监测客户健康度变化，及时发现流失风险",
      priority: "high"
    },
    {
      rule_name: "商机停滞预警",
      reason: "监测商机推进停滞情况，避免漏单",
      priority: "high"
    },
    {
      rule_name: "跟进超期提醒",
      reason: "确保客户得到及时跟进，提升客户满意度",
      priority: "medium"
    }
  ];

  const managerAttentionPoints: string[] = [];
  if (healthDistribution.critical > 0) {
    managerAttentionPoints.push(`有 ${healthDistribution.critical} 个客户处于严重风险状态，建议优先处理`);
  }
  if (healthDistribution.at_risk > 0) {
    managerAttentionPoints.push(`有 ${healthDistribution.at_risk} 个客户存在风险信号，需要关注`);
  }
  if (stalledOpportunities.length > 0) {
    managerAttentionPoints.push(`有 ${stalledOpportunities.length} 个商机已超过预期关闭日期`);
  }
  if (criticalAlerts.length > 0) {
    managerAttentionPoints.push(`有 ${criticalAlerts.length} 条严重预警需要处理`);
  }

  const quickWins: string[] = [
    "生成今日任务计划，开始执行优先动作",
    "查看经营驾驶舱，了解当前业务全貌",
    "配置自动化规则，让系统自动发现问题"
  ];

  const ruleBasedSummary: ImportSummaryResult = {
    health_distribution: healthDistribution,
    stalled_count: stalledOpportunities.length,
    priority_items: priorityItems,
    recommended_rules: recommendedRules,
    manager_attention_points: managerAttentionPoints,
    quick_wins: quickWins
  };

  const provider = getAiProvider();
  if (!provider.isConfigured() || !aiStatus.providerConfigured) {
    return {
      summary: ruleBasedSummary,
      usedFallback: true,
      fallbackReason: "ai_provider_not_configured",
      generatedAt: nowIso()
    };
  }

  try {
    const scenario = "import_business_summary";
    const model = provider.getDefaultModel({ reasoning: false });
    const prompt = await getActivePromptVersion({
      supabase,
      orgId,
      scenario,
      providerId: provider.id
    });

    const run = await createAiRun({
      supabase,
      orgId,
      triggerSource: "import_complete",
      scenario,
      provider: provider.id,
      model,
      promptVersion: prompt.version,
      triggeredByUserId: actorUserId,
      inputSnapshot: {
        import_job_id: importJobId,
        health_distribution: healthDistribution,
        stalled_count: stalledOpportunities.length,
        priority_items_count: priorityItems.length
      }
    });

    await updateAiRunStatus({
      supabase,
      runId: run.id,
      status: "running",
      startedAt: nowIso()
    });

    const response = await provider.chatCompletion({
      scenario,
      model,
      systemPrompt: prompt.systemPrompt,
      developerPrompt: `${prompt.developerPrompt}\n\nOutput schema:\n${JSON.stringify(prompt.outputSchema)}`,
      userPrompt: JSON.stringify({
        org_settings: settings,
        health_distribution: healthDistribution,
        stalled_opportunities: stalledOpportunities.slice(0, 10),
        priority_items: priorityItems,
        critical_alerts_count: criticalAlerts.length,
        high_priority_work_items_count: highPriorityWorkItems.length
      }),
      jsonMode: true,
      strictMode: true
    });

    const parsed = importSummaryResultSchema.safeParse(
      response.parsedJson ?? (response.rawText ? JSON.parse(response.rawText) : null)
    );

    if (!parsed.success) {
      throw new Error("import_summary_schema_invalid");
    }

    await updateAiRunStatus({
      supabase,
      runId: run.id,
      status: "completed",
      provider: response.provider,
      model: response.model,
      outputSnapshot: response.rawResponse,
      parsedResult: parsed.data,
      latencyMs: response.latencyMs,
      resultSource: "provider",
      completedAt: nowIso()
    });

    return {
      summary: parsed.data,
      usedFallback: false,
      fallbackReason: null,
      generatedAt: nowIso()
    };
  } catch (error) {
    if (!isRuleFallbackEnabled()) {
      return {
        summary: ruleBasedSummary,
        usedFallback: true,
        fallbackReason: error instanceof Error ? error.message : "ai_generation_failed",
        generatedAt: nowIso()
      };
    }

    return {
      summary: ruleBasedSummary,
      usedFallback: true,
      fallbackReason: error instanceof Error ? error.message : "ai_generation_failed",
      generatedAt: nowIso()
    };
  }
}

export async function saveImportSummaryToOrgSettings(params: {
  supabase: DbClient;
  orgId: string;
  summary: ImportBusinessSummary;
}): Promise<void> {
  const { supabase, orgId, summary } = params;

  await supabase.from("org_settings").update({
    import_summary_snapshot: summary as unknown as Record<string, unknown>,
    import_summary_generated_at: summary.generatedAt
  }).eq("org_id", orgId);

  await patchOnboardingSteps({
    supabase,
    orgId,
    steps: {
      post_import_bootstrap: true
    },
    completeIfThreshold: true
  }).catch(() => null);
}

export async function getImportSummaryFromOrgSettings(params: {
  supabase: DbClient;
  orgId: string;
}): Promise<ImportBusinessSummary | null> {
  const { supabase, orgId } = params;

  const res = await supabase
    .from("org_settings")
    .select("import_summary_snapshot, import_summary_generated_at")
    .eq("org_id", orgId)
    .maybeSingle();

  if (res.error || !res.data) {
    return null;
  }

  const row = res.data as {
    import_summary_snapshot: Record<string, unknown> | null;
    import_summary_generated_at: string | null;
  };

  if (!row.import_summary_snapshot) {
    return null;
  }

  return {
    ...(row.import_summary_snapshot as unknown as ImportBusinessSummary),
    generatedAt: row.import_summary_generated_at ?? nowIso()
  };
}
