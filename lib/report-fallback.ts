import type { ReportGenerationResult } from "@/types/ai";
import type { ReportType } from "@/types/report";

export function buildFallbackReport(params: {
  reportType: ReportType;
  periodStart: string;
  periodEnd: string;
  metricsSnapshot: Record<string, unknown>;
  sourceSnapshot: Record<string, unknown>;
}): ReportGenerationResult {
  const metrics = params.metricsSnapshot as Record<string, number | string>;
  const titleMap: Record<ReportType, string> = {
    sales_daily: "销售日报（规则回退）",
    sales_weekly: "销售周报（规则回退）",
    manager_daily: "团队日报（规则回退）",
    manager_weekly: "团队周报（规则回退）"
  };

  const keyMetrics = [
    { label: "新增客户", value: String(metrics.new_customers ?? 0) },
    { label: "跟进次数", value: String(metrics.followups_count ?? 0) },
    { label: "新增沟通输入", value: String(metrics.communication_inputs_count ?? 0) },
    { label: "高风险提醒", value: String(metrics.high_risk_alerts ?? 0) }
  ];

  const riskList = [
    `高风险客户：${String(metrics.high_risk_customers ?? 0)} 个`,
    `待确认草稿：${String(metrics.pending_drafts ?? 0)} 条`,
    `未解决提醒：${String(metrics.open_alerts ?? 0)} 条`
  ];

  const recommended = [
    "优先处理高风险客户并明确下一次跟进时间",
    "对待确认草稿进行人工确认或补充客户匹配",
    "对停滞客户安排管理介入或联合拜访"
  ];

  const summary = `本期（${params.periodStart} ~ ${params.periodEnd}）采用规则回退生成报告，建议聚焦高风险客户与待确认草稿。`;

  return {
    title: titleMap[params.reportType],
    summary,
    key_metrics: keyMetrics,
    risk_list: riskList,
    recommended_actions: recommended,
    content_markdown: [
      `# ${titleMap[params.reportType]}`,
      "",
      `- 统计周期：${params.periodStart} ~ ${params.periodEnd}`,
      `- 新增客户：${String(metrics.new_customers ?? 0)}`,
      `- 跟进次数：${String(metrics.followups_count ?? 0)}`,
      `- 沟通输入：${String(metrics.communication_inputs_count ?? 0)}`,
      `- 待确认草稿：${String(metrics.pending_drafts ?? 0)}`,
      "",
      "## 风险清单",
      ...riskList.map((item) => `- ${item}`),
      "",
      "## 建议动作",
      ...recommended.map((item) => `- ${item}`)
    ].join("\n")
  };
}
