export type CustomerStage =
  | "lead"
  | "initial_contact"
  | "needs_confirmed"
  | "proposal"
  | "negotiation"
  | "won"
  | "lost";

export type RiskLevel = "low" | "medium" | "high";

export interface Customer {
  id: string;
  customerName: string;
  companyName: string;
  contactName: string;
  phone: string;
  email: string;
  sourceChannel: string;
  stage: CustomerStage;
  ownerId: string;
  ownerName: string;
  lastFollowupAt: string;
  nextFollowupAt: string;
  winProbability: number;
  riskLevel: RiskLevel;
  tags: string[];
  aiSummary: string;
  aiSuggestion: string;
  aiRiskJudgement: string;
  stalledDays: number;
  hasDecisionMaker: boolean;
  createdAt: string;
  updatedAt: string;
}
