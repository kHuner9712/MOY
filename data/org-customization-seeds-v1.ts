import {
  ORG_FEATURE_KEY_VALUES,
  type OrgCustomizationConfig,
  type OrgFeaturePreference,
  type OrgThresholdPreference
} from "@/types/customization";

function buildFeaturePreferences(overrides: Partial<Record<(typeof ORG_FEATURE_KEY_VALUES)[number], boolean>>): OrgFeaturePreference[] {
  return ORG_FEATURE_KEY_VALUES.map((featureKey) => ({
    featureKey,
    enabled: overrides[featureKey] ?? true,
    source: "org_override",
    note: null
  }));
}

const BASE_THRESHOLDS: OrgThresholdPreference[] = [
  {
    thresholdKey: "followup_sla_days",
    value: 2,
    minValue: 1,
    maxValue: 7,
    unit: "days",
    description: "Default follow-up SLA window",
    targetPatternKeys: []
  },
  {
    thresholdKey: "alert_no_followup_days",
    value: 5,
    minValue: 2,
    maxValue: 15,
    unit: "days",
    description: "Days without follow-up before inactivity alert",
    targetPatternKeys: ["no_followup_timeout"]
  },
  {
    thresholdKey: "alert_stalled_opportunity_days",
    value: 10,
    minValue: 3,
    maxValue: 30,
    unit: "days",
    description: "Days without opportunity progress before stalled alert",
    targetPatternKeys: ["quoted_but_stalled", "high_probability_stalled"]
  },
  {
    thresholdKey: "manager_attention_score",
    value: 75,
    minValue: 50,
    maxValue: 95,
    unit: "score",
    description: "Score above which manager attention is recommended",
    targetPatternKeys: []
  },
  {
    thresholdKey: "rhythm_inactivity_days",
    value: 4,
    minValue: 1,
    maxValue: 14,
    unit: "days",
    description: "Inactivity days that trigger rhythm concern",
    targetPatternKeys: []
  },
  {
    thresholdKey: "renewal_watch_window_days",
    value: 30,
    minValue: 7,
    maxValue: 90,
    unit: "days",
    description: "Days before renewal to enter watch window",
    targetPatternKeys: ["renewal_window_silent"]
  },
  {
    thresholdKey: "risk_signal_threshold",
    value: 2,
    minValue: 1,
    maxValue: 5,
    unit: "count",
    description: "Signal count threshold before escalating risk action",
    targetPatternKeys: []
  }
];

const BASE_GUARDRAILS = {
  disallowCoreStateMachineOverride: true,
  disallowObjectRelationshipOverride: true,
  disallowPermissionSemanticOverride: true,
  disallowAiGovernanceOverride: true
} as const;

export const ORG_CUSTOMIZATION_SEEDS_V1: OrgCustomizationConfig[] = [
  {
    customizationKey: "default_org_customization",
    name: "Default Org Customization",
    version: "v1.0.0",
    status: "active",
    scope: [
      "org_identity_boundary",
      "org_configurable_preferences",
      "template_selection_overlay",
      "reporting_visibility_preferences"
    ],
    description: "Baseline org-level preferences with conservative thresholds and full guardrails.",
    featurePreferences: buildFeaturePreferences({}),
    thresholdPreferences: BASE_THRESHOLDS,
    automationRulePreferences: {
      high_risk_customer_inactivity: {
        enabled: true,
        thresholdOverride: 5,
        note: "Inactivity alert at day 5"
      },
      trial_stalled_watch: {
        enabled: true,
        thresholdOverride: 3,
        note: "Trial stalled when no activation signals for 3 days"
      },
      renewal_due_watch: {
        enabled: true,
        thresholdOverride: 30,
        note: "Renewal watch enters at 30 days"
      }
    },
    onboardingPreferences: {
      preferredChecklistKeys: ["industry_template", "org_profile", "ai_setup", "team_invite", "first_data", "import_first_batch"],
      prioritizeTemplateSelection: true,
      importFirstMode: true,
      customHints: ["Apply one industry template before importing historical records."]
    },
    importMappingPreferences: {
      preferredColumnAliases: {
        company_name: ["company", "客户公司", "企业名称"],
        contact_name: ["contact", "联系人", "姓名"],
        owner_name: ["owner", "销售负责人", "客户经理"],
        phone: ["mobile", "电话", "手机号"],
        email: ["mail", "邮箱", "电子邮件"]
      },
      ownerMatchMode: "balanced",
      customHints: ["Always verify owner mapping against active organization members."]
    },
    templateSelection: {
      enabledTemplateKeys: ["saas_subscription", "manufacturing_key_account"],
      disabledTemplateKeys: [],
      defaultTemplateKey: "saas_subscription",
      stageVocabularyOverrides: {},
      stageHintOverrides: {},
      managerFocusMetricOverrides: [],
      importMappingHintOverrides: []
    },
    promptStrategyPreference: {
      mode: "template_first",
      additionalPromptHooks: [],
      scenarioStrategy: {}
    },
    reportingPreference: {
      managerMetricFilters: ["followup_timeliness_score", "high_risk_unresolved_count", "team_execution_score"],
      executiveMetricFilters: ["open_events", "critical_risks", "renewal_at_risk", "conversion_readiness"],
      hideLowConfidenceAiInsights: true,
      defaultDateRangeDays: 14
    },
    guardrails: BASE_GUARDRAILS
  },
  {
    customizationKey: "saas_org_overlay",
    name: "SaaS Sales Team Overlay",
    version: "v1.0.0",
    status: "active",
    scope: ["org_configurable_preferences", "template_selection_overlay", "reporting_visibility_preferences"],
    description: "Org overlay optimized for faster trial activation and subscription expansion rhythm.",
    featurePreferences: buildFeaturePreferences({
      demo_seed_tools: true,
      manager_quality_view: true
    }),
    thresholdPreferences: [
      {
        thresholdKey: "followup_sla_days",
        value: 1,
        minValue: 1,
        maxValue: 7,
        unit: "days",
        description: "SaaS teams require faster follow-up SLA",
        targetPatternKeys: []
      },
      {
        thresholdKey: "alert_no_followup_days",
        value: 3,
        minValue: 2,
        maxValue: 10,
        unit: "days",
        description: "Earlier inactivity warning for trial and renewal periods",
        targetPatternKeys: ["no_followup_timeout", "trial_no_activation_72h"]
      },
      {
        thresholdKey: "manager_attention_score",
        value: 70,
        minValue: 50,
        maxValue: 95,
        unit: "score",
        description: "Escalate manager attention sooner for renewal risk",
        targetPatternKeys: ["renewal_window_silent"]
      },
      {
        thresholdKey: "renewal_watch_window_days",
        value: 45,
        minValue: 15,
        maxValue: 90,
        unit: "days",
        description: "Longer renewal watch window for subscription deals",
        targetPatternKeys: ["renewal_window_silent"]
      },
      {
        thresholdKey: "risk_signal_threshold",
        value: 2,
        minValue: 1,
        maxValue: 5,
        unit: "count",
        description: "Escalate when two core SaaS risk signals co-occur",
        targetPatternKeys: ["trial_no_activation_72h", "renewal_window_silent"]
      }
    ],
    automationRulePreferences: {
      high_risk_customer_inactivity: {
        enabled: true,
        thresholdOverride: 3,
        note: "Shorter inactivity threshold"
      },
      trial_stalled_watch: {
        enabled: true,
        thresholdOverride: 2,
        note: "Escalate trial stall faster"
      },
      renewal_due_watch: {
        enabled: true,
        thresholdOverride: 45,
        note: "Track renewal pipeline earlier"
      }
    },
    onboardingPreferences: {
      preferredChecklistKeys: ["industry_template", "first_data", "import_first_batch", "post_import_bootstrap", "first_plan_or_brief"],
      prioritizeTemplateSelection: true,
      importFirstMode: false,
      customHints: ["Run a trial activation clinic in week one and log first-value evidence."]
    },
    importMappingPreferences: {
      preferredColumnAliases: {
        trial_start_at: ["trial_start", "试用开始时间"],
        renewal_due_at: ["renewal_date", "续费到期日"],
        active_users: ["active_users", "活跃用户数"],
        owner_name: ["owner", "客户成功经理", "销售负责人"]
      },
      ownerMatchMode: "balanced",
      customHints: ["Map trial and renewal fields before importing historical opportunities."]
    },
    templateSelection: {
      enabledTemplateKeys: ["saas_subscription"],
      disabledTemplateKeys: ["manufacturing_key_account"],
      defaultTemplateKey: "saas_subscription",
      stageVocabularyOverrides: {
        "customer:needs_confirmed": "trial_goal_confirmed",
        "opportunity:qualification": "trial_activation"
      },
      stageHintOverrides: {
        "opportunity:business_review": "Prioritize value review, renewal risk and expansion path."
      },
      managerFocusMetricOverrides: ["trial_activation_rate_7d", "days_to_first_value", "renewal_risk_account_count"],
      importMappingHintOverrides: ["Prefer explicit renewal_due_at and trial_start_at mappings."]
    },
    promptStrategyPreference: {
      mode: "org_overlay",
      additionalPromptHooks: [
        {
          hookKey: "saas_org_overlay_followup",
          scenario: "followup_analysis",
          strategy: "append_checklist",
          promptPatch: "Force-check trial activation owner, first-value date and renewal commitment clarity."
        }
      ],
      scenarioStrategy: {
        growth_pipeline_summary: {
          mode: "prepend_context",
          promptPatch: "Use trial activation and first-value speed as primary funnel health signals."
        },
        retention_watch_review: {
          mode: "inject_constraints",
          promptPatch: "Classify renewal risk only with observed activity and owner commitment evidence."
        }
      }
    },
    reportingPreference: {
      managerMetricFilters: ["trial_activation_rate_7d", "days_to_first_value", "renewal_risk_account_count", "expansion_pipeline_amount"],
      executiveMetricFilters: ["critical_risks", "trial_stalled", "renewal_at_risk", "converted_count"],
      hideLowConfidenceAiInsights: true,
      defaultDateRangeDays: 7
    },
    guardrails: BASE_GUARDRAILS
  },
  {
    customizationKey: "manufacturing_key_account_org_overlay",
    name: "Manufacturing Key Account Overlay",
    version: "v1.0.0",
    status: "active",
    scope: ["org_configurable_preferences", "template_selection_overlay", "reporting_visibility_preferences"],
    description: "Org overlay focused on long-cycle key account progression and technical/procurement dual-track execution.",
    featurePreferences: buildFeaturePreferences({
      ai_morning_brief: true,
      playbooks: true,
      manager_quality_view: true
    }),
    thresholdPreferences: [
      {
        thresholdKey: "followup_sla_days",
        value: 3,
        minValue: 1,
        maxValue: 10,
        unit: "days",
        description: "Manufacturing follow-up rhythm is slower than SaaS by default",
        targetPatternKeys: []
      },
      {
        thresholdKey: "alert_stalled_opportunity_days",
        value: 14,
        minValue: 5,
        maxValue: 45,
        unit: "days",
        description: "Stalled opportunity threshold for longer technical/procurement cycles",
        targetPatternKeys: ["procurement_gate_stalled", "quoted_but_stalled"]
      },
      {
        thresholdKey: "manager_attention_score",
        value: 80,
        minValue: 50,
        maxValue: 95,
        unit: "score",
        description: "Escalate only when key-account risk is strong and persistent",
        targetPatternKeys: ["procurement_gate_stalled"]
      },
      {
        thresholdKey: "rhythm_inactivity_days",
        value: 6,
        minValue: 2,
        maxValue: 20,
        unit: "days",
        description: "Longer inactivity tolerance for enterprise procurement tracks",
        targetPatternKeys: []
      },
      {
        thresholdKey: "risk_signal_threshold",
        value: 3,
        minValue: 1,
        maxValue: 6,
        unit: "count",
        description: "Escalate when multiple technical/procurement signals accumulate",
        targetPatternKeys: ["sample_feedback_missing", "procurement_gate_stalled"]
      }
    ],
    automationRulePreferences: {
      high_risk_customer_inactivity: {
        enabled: true,
        thresholdOverride: 6,
        note: "Enterprise account cadence"
      },
      trial_stalled_watch: {
        enabled: false,
        thresholdOverride: null,
        note: "Not the primary path for manufacturing key-account motion"
      },
      renewal_due_watch: {
        enabled: true,
        thresholdOverride: 30,
        note: "Keep default renewal watch"
      }
    },
    onboardingPreferences: {
      preferredChecklistKeys: ["industry_template", "team_invite", "first_data", "import_first_batch", "owner_mapping", "manager_view"],
      prioritizeTemplateSelection: true,
      importFirstMode: true,
      customHints: ["Import technical and procurement contact dimensions before first manager review."]
    },
    importMappingPreferences: {
      preferredColumnAliases: {
        technical_owner: ["tech_owner", "技术负责人"],
        procurement_owner: ["procurement_owner", "采购负责人"],
        sample_sent_at: ["sample_date", "样品发送时间"],
        sample_feedback_at: ["sample_feedback", "样品反馈时间"],
        owner_name: ["owner", "客户经理", "销售负责人"]
      },
      ownerMatchMode: "strict",
      customHints: ["Keep technical and procurement owner mappings separated for accountability."]
    },
    templateSelection: {
      enabledTemplateKeys: ["manufacturing_key_account"],
      disabledTemplateKeys: ["saas_subscription"],
      defaultTemplateKey: "manufacturing_key_account",
      stageVocabularyOverrides: {
        "customer:needs_confirmed": "technical_requirement_clarified",
        "opportunity:business_review": "technical_procurement_dual_track"
      },
      stageHintOverrides: {
        "opportunity:negotiation": "Validate contract terms and delivery milestones with both business and technical owners."
      },
      managerFocusMetricOverrides: ["technical_checkpoint_pass_rate", "procurement_blocked_count", "key_account_blocked_amount"],
      importMappingHintOverrides: ["Map sample and procurement milestones before enabling strict blocker alerts."]
    },
    promptStrategyPreference: {
      mode: "org_overlay",
      additionalPromptHooks: [
        {
          hookKey: "manufacturing_org_overlay_followup",
          scenario: "followup_analysis",
          strategy: "append_checklist",
          promptPatch: "Validate technical feedback, procurement owner and contract milestone clarity together."
        }
      ],
      scenarioStrategy: {
        manager_summary: {
          mode: "inject_constraints",
          promptPatch: "Manager actions must include technical owner and procurement owner separately."
        },
        meeting_agenda_generation: {
          mode: "prepend_context",
          promptPatch: "Agenda should include technical review, procurement gate and delivery readiness sections."
        }
      }
    },
    reportingPreference: {
      managerMetricFilters: ["technical_checkpoint_pass_rate", "procurement_blocked_count", "sample_feedback_cycle_days", "key_account_blocked_amount"],
      executiveMetricFilters: ["critical_risks", "deal_blocked", "manager_attention_required", "converted_count"],
      hideLowConfidenceAiInsights: true,
      defaultDateRangeDays: 30
    },
    guardrails: BASE_GUARDRAILS
  }
];
