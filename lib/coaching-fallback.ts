export interface CoachingFallbackInput {
  userName: string;
  periodStart: string;
  periodEnd: string;
  quality: {
    activityQualityScore: number;
    shallowActivityRatio: number;
    riskResponseScore: number;
    highRiskUnhandledCount: number;
    followupCompletenessScore: number;
  };
}

export interface CoachingFallbackResult {
  title: string;
  executive_summary: string;
  strengths: string[];
  weaknesses: string[];
  coaching_actions: string[];
  replicable_patterns: string[];
  risk_warnings: string[];
  content_markdown: string;
}

export function buildFallbackUserCoachingReport(input: CoachingFallbackInput): CoachingFallbackResult {
  return {
    title: `${input.userName} 辅导报告（规则回退）`,
    executive_summary: `周期 ${input.periodStart} ~ ${input.periodEnd} 内，活动质量得分 ${input.quality.activityQualityScore.toFixed(
      1
    )}，建议优先提升记录完整度与风险处理响应。`,
    strengths: [
      input.quality.activityQualityScore >= 70 ? "总体推进节奏较稳定" : "保持了基础跟进动作",
      input.quality.followupCompletenessScore >= 65 ? "跟进记录完整度较好" : "具备可提升的记录规范基础"
    ],
    weaknesses: [
      `浅层忙碌占比 ${(input.quality.shallowActivityRatio * 100).toFixed(0)}%`,
      `高风险未处理 ${input.quality.highRiskUnhandledCount} 个`
    ],
    coaching_actions: [
      "优先补齐关键客户的下一步动作和跟进时间",
      "将高风险客户响应时效控制在 48 小时内",
      "每周复盘 2 条高质量推进案例"
    ],
    replicable_patterns: [
      "保留有效沟通方式与节奏，固化成固定推进模板",
      "对常见异议采用分阶段报价与决策人同步策略"
    ],
    risk_warnings: input.quality.riskResponseScore < 60 ? ["高风险提醒处理偏慢，可能带来漏单风险"] : [],
    content_markdown: `# ${input.userName} 辅导报告（规则回退）\n\n- 周期：${input.periodStart} ~ ${input.periodEnd}\n- 活动质量得分：${input.quality.activityQualityScore.toFixed(
      1
    )}\n- 浅层忙碌占比：${(input.quality.shallowActivityRatio * 100).toFixed(0)}%\n- 高风险未处理：${input.quality.highRiskUnhandledCount}\n\n## 建议动作\n- 优先补齐关键客户的下一步动作和跟进时间\n- 将高风险客户响应时效控制在 48 小时内\n- 每周复盘 2 条高质量推进案例`
  };
}
