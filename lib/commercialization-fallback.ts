import type {
  GrowthPipelineSummaryResult,
  LeadQualificationAssistResult,
  TrialConversionReviewResult
} from "@/types/commercialization";

export function buildFallbackLeadQualification(params: {
  leadSource: string;
  industryHint: string | null;
  teamSizeHint: string | null;
  useCaseHint: string | null;
}): LeadQualificationAssistResult {
  const source = (params.leadSource ?? "").toLowerCase();
  const industry = (params.industryHint ?? "").toLowerCase();
  const useCase = params.useCaseHint ?? "";

  let fitScore = 50;
  if (source === "website_demo") fitScore += 12;
  if (source === "website_trial") fitScore += 16;
  if (useCase.length >= 24) fitScore += 12;
  if (industry.includes("software") || industry.includes("saas") || industry.includes("制造") || industry.includes("教育")) fitScore += 8;
  fitScore = Math.max(20, Math.min(95, fitScore));

  const riskFlags: string[] = [];
  if (!params.industryHint) riskFlags.push("industry_unknown");
  if (!params.teamSizeHint) riskFlags.push("team_size_unknown");
  if (!params.useCaseHint || params.useCaseHint.trim().length < 10) riskFlags.push("use_case_unclear");

  return {
    qualificationAssessment: fitScore >= 70 ? "线索质量较高，建议 24 小时内进入 Demo/Trial 跟进。" : "线索质量中等，建议先做资格确认再推进 Demo。",
    fitScore,
    likelyUseCase: useCase || "销售团队希望提升跟进效率与转化透明度。",
    suggestedOwnerType: fitScore >= 78 ? "manager" : "sales",
    suggestedNextActions: [
      "电话确认当前销售流程与主要痛点",
      "演示 Today/Capture/Deal Room 的闭环链路",
      "根据行业模板给出试用落地路径"
    ],
    riskFlags
  };
}

export function buildFallbackTrialConversionReview(params: {
  activationScore: number;
  engagementScore: number;
  readinessScore: number;
  riskFlags: string[];
}): TrialConversionReviewResult {
  const readinessAssessment =
    params.readinessScore >= 80
      ? "试用组织已进入高转正准备度区间，可推进转正会谈。"
      : params.readinessScore >= 60
      ? "试用组织具备转正潜力，建议补齐关键使用动作后推进商务沟通。"
      : "试用组织活跃不足，建议先完成 onboarding 与首个业务闭环。";

  const activationHealth =
    params.activationScore >= 70 ? "激活健康" : params.activationScore >= 45 ? "激活一般" : "激活偏弱";

  return {
    activationHealth,
    readinessAssessment,
    riskFactors: params.riskFlags.length > 0 ? params.riskFlags : ["none"],
    recommendedConversionActions: [
      "组织一次 30 分钟价值复盘会，确认试用收益与关键阻碍",
      "针对低活跃模块给出模板化落地动作清单",
      "锁定转正决策人与预计决策时间"
    ],
    recommendedOwnerFollowup: [
      "48 小时内发送试用总结与下一步建议",
      "约定下一次管理层对齐会议",
      "对高风险项设置一条 manager_checkin 任务"
    ]
  };
}

export function buildFallbackGrowthPipelineSummary(params: {
  leadsTotal: number;
  demoRequested: number;
  demoCompleted: number;
  trialRequested: number;
  trialActivated: number;
  convertedCount: number;
}): GrowthPipelineSummaryResult {
  const demoRate = params.demoRequested === 0 ? 0 : Math.round((params.demoCompleted / params.demoRequested) * 100);
  const trialRate = params.trialRequested === 0 ? 0 : Math.round((params.trialActivated / params.trialRequested) * 100);

  return {
    funnelSummary: `近周期线索 ${params.leadsTotal}，Demo 完成率 ${demoRate}% ，Trial 激活率 ${trialRate}% ，已转正 ${params.convertedCount}。`,
    bestChannels: ["website_trial", "website_demo"],
    weakPoints: demoRate < 45 ? ["demo completion low"] : ["trial to conversion lag"],
    highPotentialSegments: ["已完成 onboarding 且已创建 deal room 的试用组织"],
    nextBestActions: [
      "优先跟进 readiness >= 70 的试用组织",
      "对 demo no-show 线索补一次 24h 内回访",
      "按行业模板输出差异化试用激活动作"
    ]
  };
}
