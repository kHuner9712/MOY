import type { AlertRuleHit } from "@/types/alert";
import type { Customer } from "@/types/customer";
import type { FollowupRecord } from "@/types/followup";
import type { Opportunity } from "@/types/opportunity";

export interface AlertRuleThresholds {
  noFollowupTimeoutDays: number;
  quotedButStalledDays: number;
  highProbabilityThreshold: number;
  highProbabilityStalledDays: number;
  positiveReplyStalledDays: number;
  minFollowupsForNoDecisionMaker: number;
}

export interface AlertRuleContext {
  now: Date;
  customer: Customer;
  followups: FollowupRecord[];
  opportunities: Opportunity[];
}

export const DEFAULT_ALERT_RULE_THRESHOLDS: AlertRuleThresholds = {
  noFollowupTimeoutDays: 5,
  quotedButStalledDays: 7,
  highProbabilityThreshold: 70,
  highProbabilityStalledDays: 4,
  positiveReplyStalledDays: 6,
  minFollowupsForNoDecisionMaker: 3
};

function daysBetween(now: Date, source: string): number {
  return Math.max(0, Math.floor((now.getTime() - new Date(source).getTime()) / (24 * 60 * 60 * 1000)));
}

function dueAfterDays(now: Date, days: number): string {
  const dueAt = new Date(now);
  dueAt.setDate(dueAt.getDate() + days);
  return dueAt.toISOString();
}

function isActiveStage(stage: Customer["stage"]): boolean {
  return stage !== "won" && stage !== "lost";
}

function includesPositiveSignals(followups: FollowupRecord[]): boolean {
  const recentText = followups
    .slice(0, 5)
    .map((item) => `${item.summary} ${item.customerNeeds} ${item.nextPlan}`)
    .join(" ")
    .toLowerCase();

  const keywords = ["积极", "推进", "试用", "预算", "采购", "不错", "认可", "希望", "尽快", "安排"];
  return keywords.some((keyword) => recentText.includes(keyword));
}

export function evaluateAlertRules(context: AlertRuleContext, thresholds: AlertRuleThresholds = DEFAULT_ALERT_RULE_THRESHOLDS): AlertRuleHit[] {
  if (!isActiveStage(context.customer.stage)) return [];

  const hits: AlertRuleHit[] = [];
  const lastFollowupAt = context.customer.lastFollowupAt || context.customer.createdAt;
  const stalledDays = daysBetween(context.now, lastFollowupAt);
  const followupCount = context.followups.length;

  if (stalledDays >= thresholds.noFollowupTimeoutDays) {
    hits.push({
      ruleType: "no_followup_timeout",
      source: "rule",
      level: stalledDays >= thresholds.noFollowupTimeoutDays + 3 ? "critical" : "warning",
      title: "客户超过阈值天数未跟进",
      description: `客户已 ${stalledDays} 天未跟进，推进节奏存在中断风险。`,
      evidence: [`最近跟进距今 ${stalledDays} 天`, `当前阶段：${context.customer.stage}`],
      suggestedOwnerAction: ["24 小时内完成一次有效触达", "同步下一次明确跟进时间"],
      dueAt: dueAfterDays(context.now, 1)
    });
  }

  const quotedOpportunity = context.opportunities
    .filter((item) => item.stage === "proposal" || item.stage === "business_review" || item.stage === "negotiation")
    .sort((a, b) => new Date(b.lastProgressAt).getTime() - new Date(a.lastProgressAt).getTime())[0];

  if (quotedOpportunity) {
    const quotedStalledDays = daysBetween(context.now, quotedOpportunity.lastProgressAt);
    if (quotedStalledDays >= thresholds.quotedButStalledDays) {
      hits.push({
        ruleType: "quoted_but_stalled",
        source: "rule",
        level: quotedStalledDays >= thresholds.quotedButStalledDays + 4 ? "critical" : "warning",
        title: "已报价但长期未推进",
        description: `商机“${quotedOpportunity.name}”报价后 ${quotedStalledDays} 天未进入下一推进节点。`,
        evidence: [`商机阶段：${quotedOpportunity.stage}`, `最近推进距今 ${quotedStalledDays} 天`],
        suggestedOwnerAction: ["推动采购与业务决策方同会", "提供报价差异说明与 ROI 复盘"],
        dueAt: dueAfterDays(context.now, 2)
      });
    }
  }

  if (!context.customer.hasDecisionMaker && followupCount >= thresholds.minFollowupsForNoDecisionMaker) {
    hits.push({
      ruleType: "no_decision_maker",
      source: "rule",
      level: followupCount >= thresholds.minFollowupsForNoDecisionMaker + 2 ? "critical" : "warning",
      title: "多次沟通但未明确决策人",
      description: "沟通次数已较多，但仍未明确最终决策角色，成交链路不清晰。",
      evidence: [`跟进记录 ${followupCount} 条`, "客户决策人信息为空或不明确"],
      suggestedOwnerAction: ["要求客户确认业务决策人与采购审批链", "组织含决策人的联合评审会"],
      dueAt: dueAfterDays(context.now, 2)
    });
  }

  if (context.customer.winProbability >= thresholds.highProbabilityThreshold && stalledDays >= thresholds.highProbabilityStalledDays) {
    hits.push({
      ruleType: "high_probability_stalled",
      source: "rule",
      level: "critical",
      title: "高成交概率客户出现停滞",
      description: `成交概率 ${context.customer.winProbability}% 的客户已停滞 ${stalledDays} 天。`,
      evidence: [`成交概率 ${context.customer.winProbability}%`, `停滞 ${stalledDays} 天`],
      suggestedOwnerAction: ["经理协同推进关键节点", "确认客户内部审批的具体日期"],
      dueAt: dueAfterDays(context.now, 1)
    });
  }

  if (
    includesPositiveSignals(context.followups) &&
    (context.customer.stage === "lead" || context.customer.stage === "initial_contact" || context.customer.stage === "needs_confirmed") &&
    stalledDays >= thresholds.positiveReplyStalledDays
  ) {
    hits.push({
      ruleType: "positive_reply_but_no_progress",
      source: "rule",
      level: "warning",
      title: "客户反馈积极但长期未推进",
      description: "近期沟通出现积极信号，但商机阶段和关键动作没有实质推进。",
      evidence: ["最近跟进包含积极意向表达", `当前阶段：${context.customer.stage}`, `停滞 ${stalledDays} 天`],
      suggestedOwnerAction: ["明确下一次会议议程和输出", "将积极反馈转化为可签字的决策节点"],
      dueAt: dueAfterDays(context.now, 2)
    });
  }

  return hits;
}
