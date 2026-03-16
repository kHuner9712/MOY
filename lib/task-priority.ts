import type { CustomerStage, RiskLevel } from "@/types/customer";
import type { WorkPriorityBand, WorkType } from "@/types/work";

export interface TaskPriorityInput {
  workType: WorkType;
  customerValueScore: number; // 0-100
  riskLevel: RiskLevel | null;
  dueAt: string | null;
  scheduledFor: string | null;
  customerStage: CustomerStage | null;
  lastFollowupAt: string | null;
  highProbabilityOpportunity: boolean;
  managerFlagged: boolean;
  rhythmFit: "good" | "neutral" | "poor";
  backlogSize: number;
}

export interface TaskPriorityOutput {
  priorityScore: number;
  priorityBand: WorkPriorityBand;
  rationale: string;
}

function clamp(num: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, num));
}

function daysDiffFromNow(iso: string | null): number | null {
  if (!iso) return null;
  const diff = new Date(iso).getTime() - Date.now();
  return Math.round(diff / (24 * 60 * 60 * 1000));
}

function mapStageWeight(stage: CustomerStage | null): number {
  if (!stage) return 0;
  if (stage === "negotiation") return 14;
  if (stage === "proposal") return 10;
  if (stage === "needs_confirmed") return 6;
  if (stage === "initial_contact") return 3;
  return 0;
}

function toBand(score: number): WorkPriorityBand {
  if (score >= 85) return "critical";
  if (score >= 65) return "high";
  if (score >= 40) return "medium";
  return "low";
}

export function computeTaskPriority(input: TaskPriorityInput): TaskPriorityOutput {
  const reasons: string[] = [];

  let score = 18;

  const valuePart = clamp(input.customerValueScore, 0, 100) * 0.22;
  score += valuePart;
  if (valuePart >= 18) reasons.push("客户价值较高，应优先处理");

  if (input.riskLevel === "high") {
    score += 24;
    reasons.push("风险等级高，不处理可能导致漏单");
  } else if (input.riskLevel === "medium") {
    score += 12;
    reasons.push("存在中等风险，建议尽快推进");
  }

  const dueDiff = daysDiffFromNow(input.dueAt);
  if (dueDiff !== null) {
    if (dueDiff < 0) {
      score += 22;
      reasons.push(`任务已逾期 ${Math.abs(dueDiff)} 天`);
    } else if (dueDiff === 0) {
      score += 16;
      reasons.push("任务今日到期");
    } else if (dueDiff <= 2) {
      score += 10;
      reasons.push("任务即将到期");
    }
  }

  const scheduledDiff = daysDiffFromNow(input.scheduledFor);
  if (scheduledDiff !== null && scheduledDiff <= 0) {
    score += 8;
    reasons.push("计划安排在今日执行");
  }

  const stageWeight = mapStageWeight(input.customerStage);
  score += stageWeight;
  if (stageWeight >= 10) reasons.push("处于关键成交阶段");

  if (input.highProbabilityOpportunity) {
    score += 12;
    reasons.push("关联高概率商机，推进收益更高");
  }

  if (input.managerFlagged) {
    score += 9;
    reasons.push("管理者已标注关注");
  }

  if (input.rhythmFit === "good") {
    score += 6;
    reasons.push("符合你的历史有效跟进节奏");
  } else if (input.rhythmFit === "poor") {
    score -= 4;
    reasons.push("当前节奏偏离历史有效方式");
  }

  if (input.workType === "resolve_alert") {
    score += 10;
    reasons.push("属于风险处置任务");
  }

  if (input.backlogSize >= 20) {
    score -= 6;
    reasons.push("当前待办堆积较高，建议聚焦关键动作");
  } else if (input.backlogSize >= 12) {
    score -= 3;
  }

  const lastFollowupDiff = daysDiffFromNow(input.lastFollowupAt);
  if (lastFollowupDiff !== null && lastFollowupDiff <= -7) {
    score += 7;
    reasons.push("该客户已超过一周未跟进");
  }

  const finalScore = clamp(Number(score.toFixed(2)), 0, 100);
  const band = toBand(finalScore);
  const rationale = reasons.slice(0, 4).join("；") || "基于价值、风险和时效综合排序";

  return {
    priorityScore: finalScore,
    priorityBand: band,
    rationale
  };
}
