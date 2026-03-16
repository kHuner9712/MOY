import type { Database } from "@/types/database";
import type { DealCheckpointType, DecisionType } from "@/types/deal";

export interface DecisionApprovalLinkage {
  workType: Database["public"]["Enums"]["work_item_type"];
  checkpointType: DealCheckpointType;
}

export function deriveDecisionApprovalLinkage(decisionType: DecisionType): DecisionApprovalLinkage {
  switch (decisionType) {
    case "quote_strategy":
    case "discount_exception":
      return {
        workType: "send_quote",
        checkpointType: "quote_sent"
      };
    case "trial_offer":
      return {
        workType: "schedule_demo",
        checkpointType: "trial_started"
      };
    case "manager_intervention":
      return {
        workType: "manager_checkin",
        checkpointType: "qualification"
      };
    case "resource_support":
      return {
        workType: "review_customer",
        checkpointType: "proposal_sent"
      };
    case "contract_risk":
      return {
        workType: "prepare_proposal",
        checkpointType: "contract_review"
      };
    case "stage_commitment":
    default:
      return {
        workType: "revive_stalled_deal",
        checkpointType: "closing"
      };
  }
}

