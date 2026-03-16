import type { CustomerStage, RiskLevel } from "@/types/customer";
import type { FollowupMethod } from "@/types/followup";
import type { OpportunityStage } from "@/types/opportunity";

export const customerStageLabel: Record<CustomerStage, string> = {
  lead: "线索",
  initial_contact: "初聊",
  needs_confirmed: "需求确认",
  proposal: "报价",
  negotiation: "谈判",
  won: "赢单",
  lost: "丢单"
};

export const opportunityStageLabel: Record<OpportunityStage, string> = {
  discovery: "探索",
  qualification: "评估",
  proposal: "方案/报价",
  business_review: "商务评审",
  negotiation: "谈判",
  won: "赢单",
  lost: "丢单"
};

export const followupMethodLabel: Record<FollowupMethod, string> = {
  phone: "电话",
  wechat: "微信",
  email: "邮件",
  meeting: "面谈",
  other: "其他"
};

export const riskLabel: Record<RiskLevel, string> = {
  low: "低风险",
  medium: "中风险",
  high: "高风险"
};

export const riskTone: Record<RiskLevel, "default" | "secondary" | "destructive"> = {
  low: "secondary",
  medium: "default",
  high: "destructive"
};
