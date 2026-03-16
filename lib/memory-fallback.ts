export interface MemoryFallbackInput {
  stageTop: string[];
  sourceTypeTop: string[];
  communicationTop: string[];
  objectionTop: string[];
  tacticsTop: string[];
  rhythmTop: string[];
  riskTop: string[];
  coachingTop: string[];
  confidence: number;
}

export interface MemoryFallbackResult {
  summary: string;
  preferred_customer_types: string[];
  preferred_communication_styles: string[];
  common_objections: string[];
  effective_tactics: string[];
  common_followup_rhythm: string[];
  quoting_style_notes: string[];
  risk_blind_spots: string[];
  manager_coaching_focus: string[];
  memory_items: Array<{
    memory_type:
      | "customer_preference"
      | "communication_pattern"
      | "objection_pattern"
      | "tactic_pattern"
      | "followup_rhythm"
      | "risk_pattern"
      | "coaching_hint";
    title: string;
    description: string;
    evidence: string[];
    confidence_score: number;
    source_count: number;
  }>;
  confidence_score: number;
}

export function buildFallbackMemoryCompileResult(input: MemoryFallbackInput): MemoryFallbackResult {
  const memoryItems: MemoryFallbackResult["memory_items"] = [
    {
      memory_type: "communication_pattern",
      title: "常用沟通方式",
      description: input.communicationTop[0] ?? "沟通方式分布较均衡",
      evidence: input.communicationTop,
      confidence_score: input.confidence,
      source_count: input.communicationTop.length
    },
    {
      memory_type: "objection_pattern",
      title: "常见异议",
      description: input.objectionTop[0] ?? "近期异议信息较少",
      evidence: input.objectionTop,
      confidence_score: input.confidence,
      source_count: input.objectionTop.length
    },
    {
      memory_type: "coaching_hint",
      title: "辅导重点",
      description: input.coachingTop[0] ?? "建议继续保持节奏并加强高风险客户处理",
      evidence: input.coachingTop,
      confidence_score: input.confidence,
      source_count: input.coachingTop.length
    }
  ];

  return {
    summary: "系统使用规则聚合生成本次工作记忆，建议结合近期业务上下文人工复核。",
    preferred_customer_types: input.stageTop,
    preferred_communication_styles: [...input.sourceTypeTop, ...input.communicationTop].slice(0, 6),
    common_objections: input.objectionTop,
    effective_tactics: input.tacticsTop,
    common_followup_rhythm: input.rhythmTop,
    quoting_style_notes: input.tacticsTop.filter((item) => item.includes("报价")).slice(0, 4),
    risk_blind_spots: input.riskTop,
    manager_coaching_focus: input.coachingTop,
    memory_items: memoryItems,
    confidence_score: input.confidence
  };
}
