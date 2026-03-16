import type { RiskLevel } from "@/types/customer";

export type OpportunityStage =
  | "discovery"
  | "qualification"
  | "proposal"
  | "business_review"
  | "negotiation"
  | "won"
  | "lost";

export interface Opportunity {
  id: string;
  customerId: string;
  customerName: string;
  name: string;
  expectedAmount: number;
  stage: OpportunityStage;
  ownerId: string;
  ownerName: string;
  lastProgressAt: string;
  riskLevel: RiskLevel;
  closeDate: string;
}
