import {
  MOY_CORE_CUSTOMER_STAGES,
  MOY_CORE_OPPORTUNITY_STAGES,
  MOY_NON_OVERRIDABLE_SEMANTICS,
  type IndustryTemplateDefinition
} from "@/types/template";

const BASE_STATE_MACHINE_GUARDS: IndustryTemplateDefinition["baseStateMachineGuards"] = {
  customerStages: MOY_CORE_CUSTOMER_STAGES,
  opportunityStages: MOY_CORE_OPPORTUNITY_STAGES,
  nonOverridableSemantics: MOY_NON_OVERRIDABLE_SEMANTICS
};

export const INDUSTRY_TEMPLATE_SEEDS_V1: IndustryTemplateDefinition[] = [
  {
    templateKey: "saas_subscription",
    name: "SaaS Subscription Template",
    version: "v1.0.0",
    status: "active",
    applicableSalesMode: ["subscription_expansion", "consultative_solution"],
    stageHints: [
      {
        primitive: "customer",
        baseStage: "needs_confirmed",
        stageVocabulary: "trial_value_alignment",
        stageHint: "Confirm trial goals, success criteria, and owner.",
        recommendedExitCriteria: ["Trial goals documented", "First value scenario scheduled"]
      },
      {
        primitive: "opportunity",
        baseStage: "qualification",
        stageVocabulary: "trial_activation",
        stageHint: "Drive activation and first-value completion as top signals.",
        recommendedExitCriteria: ["Key users activated", "Trial timeline locked"]
      },
      {
        primitive: "opportunity",
        baseStage: "business_review",
        stageVocabulary: "value_review",
        stageHint: "Run value review before renewal or expansion discussion.",
        recommendedExitCriteria: ["Value review completed", "Renewal/expansion path confirmed"]
      }
    ],
    commonRiskPatterns: [
      {
        patternKey: "trial_no_activation_72h",
        title: "No activation in 72h after trial start",
        severityHint: "warning",
        signalThresholdHint: "No key-user activity in first 72h of trial",
        triggerSignals: ["trial_inactive", "first_value_not_started"],
        suggestedActions: ["Run activation clinic", "Send success-path checklist"],
        managerAttentionRecommended: false
      },
      {
        patternKey: "renewal_window_silent",
        title: "Renewal window without clear commitment",
        severityHint: "critical",
        signalThresholdHint: "No owner/budget confirmation 14 days before renewal",
        triggerSignals: ["renewal_owner_missing", "budget_unconfirmed", "usage_declining"],
        suggestedActions: ["Manager join renewal call", "Run business value recap"],
        managerAttentionRecommended: true
      }
    ],
    objectionLibrary: [
      "price_too_high",
      "migration_effort_concern",
      "value_not_yet_visible",
      "renewal_budget_unconfirmed"
    ],
    recommendedActionLibrary: [
      {
        actionKey: "saas_activation_clinic",
        title: "Activation clinic",
        whenToUse: "Low activity within first 3 days of trial",
        actionSummary: "Use a 30-minute session to align roles, goals, and dates.",
        ownerRoleHint: "sales",
        expectedOutcome: "Trial enters measurable progression"
      },
      {
        actionKey: "saas_renewal_business_review",
        title: "Renewal business review",
        whenToUse: "Renewal commitment unclear or usage is declining",
        actionSummary: "Sales + manager run value recap and close decision owners.",
        ownerRoleHint: "manager",
        expectedOutcome: "Renewal owner and timeline become explicit"
      }
    ],
    managerFocusMetrics: [
      "trial_activation_rate_7d",
      "days_to_first_value",
      "renewal_risk_account_count",
      "expansion_pipeline_amount"
    ],
    onboardingHints: [
      "Import customer and trial contacts first, then enable renewal alerts.",
      "Run one trial activation review in week one."
    ],
    importMappingHints: [
      "Map trial_start_at to trial start field.",
      "Map last_active_at to usage activity field.",
      "Map renewal_due_at to renewal window field."
    ],
    promptAugmentationHooks: [
      {
        hookKey: "saas_followup_focus",
        scenario: "followup_analysis",
        strategy: "append_checklist",
        promptPatch: "Prioritize activation, first-value, and renewal clarity checks."
      },
      {
        hookKey: "saas_growth_focus",
        scenario: "growth_pipeline_summary",
        strategy: "prepend_context",
        promptPatch: "Highlight trial activation, time-to-value, and renewal risk."
      },
      {
        hookKey: "saas_retention_focus",
        scenario: "retention_watch_review",
        strategy: "inject_constraints",
        promptPatch: "Use observable usage signals for retention judgement."
      }
    ],
    baseStateMachineGuards: BASE_STATE_MACHINE_GUARDS
  },
  {
    templateKey: "manufacturing_key_account",
    name: "Manufacturing Key Account Template",
    version: "v1.0.0",
    status: "active",
    applicableSalesMode: ["enterprise_key_account", "consultative_solution"],
    stageHints: [
      {
        primitive: "customer",
        baseStage: "needs_confirmed",
        stageVocabulary: "technical_requirement_clarification",
        stageHint: "Focus on specs, process, and validation standards.",
        recommendedExitCriteria: ["Specs confirmed", "Sample/pilot plan scheduled"]
      },
      {
        primitive: "opportunity",
        baseStage: "business_review",
        stageVocabulary: "technical_procurement_dual_track",
        stageHint: "Drive technical and procurement tracks in parallel.",
        recommendedExitCriteria: ["Technical review passed", "Procurement milestones clear"]
      },
      {
        primitive: "opportunity",
        baseStage: "negotiation",
        stageVocabulary: "contract_delivery_alignment",
        stageHint: "Negotiate delivery capability, quality duty, and payment milestones.",
        recommendedExitCriteria: ["Contract key terms aligned", "Delivery plan confirmed"]
      }
    ],
    commonRiskPatterns: [
      {
        patternKey: "sample_feedback_missing",
        title: "No sample feedback",
        severityHint: "warning",
        signalThresholdHint: "No technical feedback within 7 days after sample sent",
        triggerSignals: ["sample_feedback_missing", "technical_contact_slow"],
        suggestedActions: ["Run technical callback", "Reconfirm test checklist"],
        managerAttentionRecommended: false
      },
      {
        patternKey: "procurement_gate_stalled",
        title: "Procurement gate stalled",
        severityHint: "critical",
        signalThresholdHint: "Procurement milestone overdue without next owner",
        triggerSignals: ["procurement_overdue", "approval_owner_unclear", "contract_terms_looping"],
        suggestedActions: ["Escalate to manager", "Run joint technical+procurement call"],
        managerAttentionRecommended: true
      }
    ],
    objectionLibrary: [
      "spec_gap_concern",
      "sample_cycle_too_long",
      "procurement_process_complex",
      "price_and_delivery_term_dispute"
    ],
    recommendedActionLibrary: [
      {
        actionKey: "manufacturing_dual_track_sync",
        title: "Dual-track sync",
        whenToUse: "Technical review progressing but procurement stalled",
        actionSummary: "Bring technical and procurement owners to one milestone review.",
        ownerRoleHint: "manager",
        expectedOutcome: "Reduce one-track progression stalls"
      },
      {
        actionKey: "manufacturing_site_review",
        title: "On-site process review",
        whenToUse: "Repeated technical objections and unstable sample conclusions",
        actionSummary: "Validate process on-site and reset test + delivery plan.",
        ownerRoleHint: "sales",
        expectedOutcome: "Convert objections into explicit next actions"
      }
    ],
    managerFocusMetrics: [
      "sample_feedback_cycle_days",
      "technical_checkpoint_pass_rate",
      "procurement_blocked_count",
      "key_account_blocked_amount"
    ],
    onboardingHints: [
      "Import both technical and procurement contacts from day one.",
      "Enable blocker alerts before tightening thresholds."
    ],
    importMappingHints: [
      "Map spec_confirmed_at to technical confirmation field.",
      "Map sample_sent_at/sample_feedback_at to sample loop fields.",
      "Map procurement_stage to procurement progression field."
    ],
    promptAugmentationHooks: [
      {
        hookKey: "manufacturing_followup_focus",
        scenario: "followup_analysis",
        strategy: "append_checklist",
        promptPatch: "Check technical review, sample feedback, and procurement progress together."
      },
      {
        hookKey: "manufacturing_meeting_focus",
        scenario: "meeting_agenda_generation",
        strategy: "prepend_context",
        promptPatch: "Agenda must cover both technical and procurement threads."
      },
      {
        hookKey: "manufacturing_manager_focus",
        scenario: "manager_summary",
        strategy: "inject_constraints",
        promptPatch: "Manager actions should include owner, due date, and escalation path."
      }
    ],
    baseStateMachineGuards: BASE_STATE_MACHINE_GUARDS
  }
];

