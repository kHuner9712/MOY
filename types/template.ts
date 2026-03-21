import type { AiScenario } from "@/types/ai";
import type { CustomerStage } from "@/types/customer";
import type { IndustryTemplateStatus as ProductizationIndustryTemplateStatus } from "@/types/productization";
import type { OpportunityStage } from "@/types/opportunity";

// Reuse status semantics from productization layer to avoid parallel enum drift.
export type IndustryTemplateStatus = ProductizationIndustryTemplateStatus;

export type IndustryTemplateSalesMode =
  | "smb_transactional"
  | "consultative_solution"
  | "enterprise_key_account"
  | "channel_distribution"
  | "subscription_expansion";

export interface IndustryTemplateStageHint {
  primitive: "customer" | "opportunity";
  baseStage: CustomerStage | OpportunityStage;
  stageVocabulary: string;
  stageHint: string;
  recommendedExitCriteria: string[];
}

export interface IndustryTemplateRiskPattern {
  patternKey: string;
  title: string;
  severityHint: "info" | "warning" | "critical";
  signalThresholdHint: string;
  triggerSignals: string[];
  suggestedActions: string[];
  managerAttentionRecommended: boolean;
}

export interface IndustryTemplateActionHint {
  actionKey: string;
  title: string;
  whenToUse: string;
  actionSummary: string;
  ownerRoleHint: "sales" | "manager";
  expectedOutcome: string;
}

export interface IndustryTemplatePromptHook {
  hookKey: string;
  scenario: AiScenario;
  strategy: "prepend_context" | "append_checklist" | "inject_constraints";
  promptPatch: string;
}

export type IndustryTemplateNonOverridableSemantic =
  | "object_relationships"
  | "core_state_machines"
  | "permission_semantics"
  | "ai_governance";

export const MOY_CORE_CUSTOMER_STAGES: readonly CustomerStage[] = [
  "lead",
  "initial_contact",
  "needs_confirmed",
  "proposal",
  "negotiation",
  "won",
  "lost"
] as const;

export const MOY_CORE_OPPORTUNITY_STAGES: readonly OpportunityStage[] = [
  "discovery",
  "qualification",
  "proposal",
  "business_review",
  "negotiation",
  "won",
  "lost"
] as const;

export const MOY_NON_OVERRIDABLE_SEMANTICS: readonly IndustryTemplateNonOverridableSemantic[] = [
  "object_relationships",
  "core_state_machines",
  "permission_semantics",
  "ai_governance"
] as const;

export interface IndustryTemplateDefinition {
  templateKey: string;
  name: string;
  version: string;
  status: IndustryTemplateStatus;
  applicableSalesMode: IndustryTemplateSalesMode[];
  stageHints: IndustryTemplateStageHint[];
  commonRiskPatterns: IndustryTemplateRiskPattern[];
  objectionLibrary: string[];
  recommendedActionLibrary: IndustryTemplateActionHint[];
  managerFocusMetrics: string[];
  onboardingHints: string[];
  importMappingHints: string[];
  promptAugmentationHooks: IndustryTemplatePromptHook[];
  baseStateMachineGuards: {
    customerStages: readonly CustomerStage[];
    opportunityStages: readonly OpportunityStage[];
    nonOverridableSemantics: readonly IndustryTemplateNonOverridableSemantic[];
  };
}

