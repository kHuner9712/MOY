import type {
  IndustryFamily,
  IndustryTemplateSeed,
  ScenarioPackType,
  SeededPlaybookTemplateSeed
} from "@/types/productization";

function packItems(type: ScenarioPackType, title: string, summary: string, items: string[]) {
  return {
    packType: type,
    title,
    summary,
    packPayload: { items }
  };
}

function playbookSeed(seed: SeededPlaybookTemplateSeed): SeededPlaybookTemplateSeed {
  return seed;
}

function baseTemplatePayload(profile: string) {
  return {
    customer_stages: ["lead", "initial_contact", "needs_confirmed", "proposal", "negotiation", "won", "lost"],
    opportunity_stages: ["discovery", "qualification", "proposal", "business_review", "negotiation", "won", "lost"],
    demo_seed_profile: profile
  };
}

export const BUILTIN_INDUSTRY_TEMPLATE_SEEDS: IndustryTemplateSeed[] = [
  {
    templateKey: "generic",
    displayName: "Generic B2B",
    industryFamily: "generic",
    summary: "Balanced default for SMB B2B teams with standard cadence and risk control.",
    templatePayload: {
      ...baseTemplatePayload("generic_demo"),
      default_alert_rules: { no_followup_timeout: 7, quoted_but_stalled: 10, high_probability_stalled: 5 },
      suggested_checkpoints: ["need_confirmed", "quote_sent", "decision_maker_confirmed", "closing"],
      manager_attention_signals: ["high_risk_customer", "overdue_followup", "stalled_quote"],
      prep_preferences: ["value-first opening", "clear next step owner", "2-3 day followup rhythm"],
      brief_preferences: ["today priorities", "high-risk customers", "drafts pending confirmation"],
      recommended_onboarding_path: ["configure org profile", "import first customers", "generate first today plan", "create first deal room"],
      suitable_team_types: ["general b2b", "early-stage sales team"]
    },
    scenarioPacks: [
      packItems("objections", "Common Objections", "Typical SMB objections", ["budget pressure", "unclear decision maker", "implementation concern"]),
      packItems("decision_chain", "Decision Chain", "Typical B2B chain", ["business owner", "department lead", "procurement/finance"]),
      packItems("quote_strategy", "Quote Strategy", "Conservative pricing motion", ["phase-based quote", "value before price", "ROI anchor"]),
      packItems("meeting_goals", "Meeting Goals", "Standard meeting outcomes", ["confirm key need", "set next owner", "lock next date"]),
      packItems("risk_signals", "Risk Signals", "Leakage pre-signals", ["long no followup", "stalled after quote", "multiple talks no commitment"]),
      packItems("manager_interventions", "Manager Intervention", "When manager should step in", ["high value + high risk", "blocked checkpoint", "repeated overdue tasks"]),
      packItems("followup_patterns", "Followup Patterns", "Baseline rhythm", ["follow up every 2-3 days", "always define next step", "checkpoint reminder 24h before"])
    ],
    seededPlaybookTemplates: [
      playbookSeed({
        playbookType: "followup_rhythm",
        title: "48h second-touch rhythm",
        summary: "Lock owner and next action within 48h after first communication.",
        payload: {
          entries: [
            {
              entry_title: "48h second touch",
              entry_summary: "Reconfirm goals and assign explicit next owner.",
              recommended_actions: ["restate customer need", "confirm decision owner and date"],
              caution_notes: ["avoid generic check-in without progression"]
            }
          ]
        }
      })
    ]
  },
  {
    templateKey: "b2b_software",
    displayName: "B2B Software",
    industryFamily: "b2b_software",
    summary: "Trial/demo centric enterprise software motion with multi-role decisions.",
    templatePayload: {
      ...baseTemplatePayload("software_demo"),
      default_alert_rules: { no_followup_timeout: 5, quoted_but_stalled: 7, high_probability_stalled: 4 },
      suggested_checkpoints: ["qualification", "need_confirmed", "proposal_sent", "decision_maker_confirmed", "contract_review", "closing"],
      manager_attention_signals: ["trial_without_owner", "procurement_stall", "multi-role_misalignment"],
      prep_preferences: ["quantified ROI", "trial acceptance criteria", "procurement timeline back-plan"],
      brief_preferences: ["trial progress", "stakeholder alignment", "procurement risk"],
      recommended_onboarding_path: ["choose template", "import customers + opportunities", "seed trial tasks", "run manager morning brief"],
      suitable_team_types: ["saas", "enterprise software"]
    },
    scenarioPacks: [
      packItems("objections", "Software Objections", "Common software objections", ["security concern", "migration risk", "integration effort"]),
      packItems("decision_chain", "Software Decision Chain", "Typical software buying committee", ["business owner", "it owner", "procurement", "finance"]),
      packItems("quote_strategy", "Software Quote Strategy", "Trial-to-quote motion", ["bind quote to trial acceptance", "module-based quote"]),
      packItems("meeting_goals", "Software Meeting Goals", "Demo/trial meeting outcomes", ["align acceptance criteria", "confirm procurement schedule"]),
      packItems("risk_signals", "Software Risk Signals", "Signals before deal stall", ["trial delayed no owner", "quote sent no review"]),
      packItems("manager_interventions", "Software Manager Intervention", "Manager support timing", ["high amount procurement blockage", "stakeholder conflict"]),
      packItems("followup_patterns", "Software Followup Patterns", "Recommended rhythm", ["sync every 2 days during trial", "48h quote followup"])
    ],
    seededPlaybookTemplates: [
      playbookSeed({
        playbookType: "quote_strategy",
        title: "Trial-to-quote progression",
        summary: "Tie quote progression to explicit trial acceptance results.",
        payload: {
          entries: [
            {
              entry_title: "Bind quote to trial criteria",
              entry_summary: "Complete trial review before final quote commitment.",
              recommended_actions: ["define acceptance criteria", "run value review before quote"],
              caution_notes: ["do not lead with lowest price"]
            }
          ]
        }
      })
    ]
  },
  {
    templateKey: "education_training",
    displayName: "Education & Training",
    industryFamily: "education_training",
    summary: "Fast-moving trial class and enrollment conversion motion.",
    templatePayload: {
      ...baseTemplatePayload("education_demo"),
      default_alert_rules: { no_followup_timeout: 3, quoted_but_stalled: 5, high_probability_stalled: 3 },
      suggested_checkpoints: ["qualification", "need_confirmed", "proposal_sent", "quote_sent", "closing"],
      manager_attention_signals: ["trial_class_no_callback", "budget_repeatedly_unclear", "decision_owner_missing"],
      prep_preferences: ["learning outcome framing", "post-trial callback", "cycle commitment"],
      brief_preferences: ["trial conversion", "rapid callback", "owner communication"],
      recommended_onboarding_path: ["import leads", "map stages", "generate trial followup tasks", "review conversion brief"],
      suitable_team_types: ["training org", "education service"]
    },
    scenarioPacks: [
      packItems("objections", "Education Objections", "Typical objections", ["budget concern", "unclear value", "schedule conflict"]),
      packItems("decision_chain", "Education Decision Chain", "Typical roles", ["principal/owner", "ops lead"]),
      packItems("quote_strategy", "Education Quote Strategy", "Pricing options", ["tiered package", "cycle-based discount"]),
      packItems("meeting_goals", "Education Meeting Goals", "Trial review goals", ["capture trial feedback", "confirm enrollment timeline"]),
      packItems("risk_signals", "Education Risk Signals", "Stall signals", ["no callback after trial", "price discussion repeated"]),
      packItems("manager_interventions", "Education Manager Intervention", "Escalation timing", ["high-intent account overdue", "key institute stalled"]),
      packItems("followup_patterns", "Education Followup Patterns", "Suggested rhythm", ["24h callback after trial", "second push within 3 days"])
    ],
    seededPlaybookTemplates: [
      playbookSeed({
        playbookType: "meeting_strategy",
        title: "Trial-class conversion talk",
        summary: "Use trial feedback to drive immediate enrollment decision.",
        payload: {
          entries: [
            {
              entry_title: "24h post-trial recap",
              entry_summary: "Reconfirm key outcomes and remove enrollment blockers.",
              recommended_actions: ["confirm budget window", "set enrollment date"],
              caution_notes: ["avoid broad product pitch without conversion ask"]
            }
          ]
        }
      })
    ]
  },
  {
    templateKey: "manufacturing",
    displayName: "Manufacturing / Industrial",
    industryFamily: "manufacturing",
    summary: "Long-cycle technical + procurement collaboration with sample/trial checkpoints.",
    templatePayload: {
      ...baseTemplatePayload("manufacturing_demo"),
      default_alert_rules: { no_followup_timeout: 8, quoted_but_stalled: 12, high_probability_stalled: 6 },
      suggested_checkpoints: ["qualification", "need_confirmed", "proposal_sent", "budget_confirmed", "trial_started", "contract_review", "closing"],
      manager_attention_signals: ["technical_evaluation_overdue", "sample_no_feedback", "procurement_delay"],
      prep_preferences: ["spec confirmation", "trial plan clarity", "procurement route mapping"],
      brief_preferences: ["technical blockers", "decision chain status", "contract risk"],
      recommended_onboarding_path: ["import customers + deals", "fill technical fields", "seed trial checkpoints", "run risk scan"],
      suitable_team_types: ["industrial sales", "solution manufacturing"]
    },
    scenarioPacks: [
      packItems("objections", "Manufacturing Objections", "Typical objections", ["spec mismatch", "trial cost concern", "long procurement cycle"]),
      packItems("decision_chain", "Manufacturing Decision Chain", "Typical roles", ["tech lead", "production lead", "procurement"]),
      packItems("quote_strategy", "Manufacturing Quote Strategy", "Pricing strategy", ["split by line item", "trial-stage quote"]),
      packItems("meeting_goals", "Manufacturing Meeting Goals", "Technical review goals", ["confirm specs", "lock trial plan", "confirm procurement timeline"]),
      packItems("risk_signals", "Manufacturing Risk Signals", "Stall signals", ["no feedback after sample", "contract review delayed"]),
      packItems("manager_interventions", "Manufacturing Manager Intervention", "Escalation timing", ["large deal technical conflict", "procurement approval delay"]),
      packItems("followup_patterns", "Manufacturing Followup Patterns", "Suggested rhythm", ["48h sync before key checkpoint", "24h recap after review"])
    ],
    seededPlaybookTemplates: [
      playbookSeed({
        playbookType: "risk_recovery",
        title: "Technical-procurement dual thread",
        summary: "Drive technical and procurement tracks in parallel to avoid long stalls.",
        payload: {
          entries: [
            {
              entry_title: "Dual-thread progression",
              entry_summary: "Sync technical validation and procurement milestones together.",
              recommended_actions: ["assign technical confirmer", "lock procurement review date"],
              caution_notes: ["avoid progressing only technical thread"]
            }
          ]
        }
      })
    ]
  },
  {
    templateKey: "channel_sales",
    displayName: "Channel / Distribution",
    industryFamily: "channel_sales",
    summary: "Channel recruitment and policy alignment with region and activation constraints.",
    templatePayload: {
      ...baseTemplatePayload("channel_demo"),
      default_alert_rules: { no_followup_timeout: 6, quoted_but_stalled: 9, high_probability_stalled: 5 },
      suggested_checkpoints: ["qualification", "need_confirmed", "proposal_sent", "quote_sent", "contract_review", "closing"],
      manager_attention_signals: ["policy_dispute", "region_conflict", "activation_plan_missing"],
      prep_preferences: ["policy clarification", "rebate alignment", "activation commitment"],
      brief_preferences: ["channel priority", "region risk", "policy clarification tasks"],
      recommended_onboarding_path: ["import channel leads", "map policy tags", "seed callback tasks", "review weekly channel brief"],
      suitable_team_types: ["channel expansion", "distribution sales"]
    },
    scenarioPacks: [
      packItems("objections", "Channel Objections", "Common objections", ["policy unclear", "rebate mismatch", "region conflict"]),
      packItems("decision_chain", "Channel Decision Chain", "Typical roles", ["channel owner", "regional manager", "finance"]),
      packItems("quote_strategy", "Channel Quote Strategy", "Policy quote strategy", ["staged policy disclosure", "activation-bound rebate"]),
      packItems("meeting_goals", "Channel Meeting Goals", "Cooperation review goals", ["clarify policy details", "confirm activation plan", "align region scope"]),
      packItems("risk_signals", "Channel Risk Signals", "Stall signals", ["active communication no activation plan", "contract terms unresolved"]),
      packItems("manager_interventions", "Channel Manager Intervention", "Intervention timing", ["key channel negotiation", "region conflict escalation"]),
      packItems("followup_patterns", "Channel Followup Patterns", "Suggested rhythm", ["weekly channel sync", "24h recap after policy meeting"])
    ],
    seededPlaybookTemplates: [
      playbookSeed({
        playbookType: "customer_segment",
        title: "Channel qualification motion",
        summary: "Validate activation capability before aggressive policy concession.",
        payload: {
          entries: [
            {
              entry_title: "Activation first",
              entry_summary: "Confirm activation capability before policy concession.",
              recommended_actions: ["validate regional resources", "confirm activation target"],
              caution_notes: ["avoid early margin concession"]
            }
          ]
        }
      })
    ]
  },
  {
    templateKey: "consulting_services",
    displayName: "Consulting / Services",
    industryFamily: "consulting_services",
    summary: "Diagnosis-first service selling with boundary clarity and management alignment.",
    templatePayload: {
      ...baseTemplatePayload("consulting_demo"),
      default_alert_rules: { no_followup_timeout: 5, quoted_but_stalled: 8, high_probability_stalled: 4 },
      suggested_checkpoints: ["qualification", "need_confirmed", "proposal_sent", "quote_sent", "decision_maker_confirmed", "closing"],
      manager_attention_signals: ["scope_unclear", "value_not_quantified", "executive_alignment_low"],
      prep_preferences: ["problem diagnosis first", "structured proposal narrative", "scope/milestone clarity"],
      brief_preferences: ["high-value customer progression", "manager intervention timing", "risk review"],
      recommended_onboarding_path: ["import project history", "configure service stages", "seed manager intervention templates", "run quality dashboard"],
      suitable_team_types: ["consulting sales", "service project sales"]
    },
    scenarioPacks: [
      packItems("objections", "Consulting Objections", "Typical objections", ["price too high", "scope unclear", "outcome uncertainty"]),
      packItems("decision_chain", "Consulting Decision Chain", "Typical roles", ["business executive", "project owner", "procurement/legal"]),
      packItems("quote_strategy", "Consulting Quote Strategy", "Pricing strategy", ["diagnosis + implementation phases", "milestone-based pricing"]),
      packItems("meeting_goals", "Consulting Meeting Goals", "Meeting outcomes", ["prioritize business problems", "confirm scope boundary", "lock milestones"]),
      packItems("risk_signals", "Consulting Risk Signals", "Stall signals", ["scope debated repeatedly", "executive sponsor missing"]),
      packItems("manager_interventions", "Consulting Manager Intervention", "Intervention timing", ["executive alignment meeting", "scope dispute escalation"]),
      packItems("followup_patterns", "Consulting Followup Patterns", "Suggested rhythm", ["24h summary after each meeting", "48h alignment before milestones"])
    ],
    seededPlaybookTemplates: [
      playbookSeed({
        playbookType: "objection_handling",
        title: "Value-decomposition pricing response",
        summary: "Break proposal value into measurable modules before price defense.",
        payload: {
          entries: [
            {
              entry_title: "Value-decomposition quote",
              entry_summary: "Decompose solution into measurable value modules.",
              recommended_actions: ["confirm top business problem", "offer phased package"],
              caution_notes: ["avoid oversized one-shot quote"]
            }
          ]
        }
      })
    ]
  }
];

export const INDUSTRY_TEMPLATE_KEYWORDS: Array<{ family: IndustryFamily; keywords: string[] }> = [
  { family: "b2b_software", keywords: ["saas", "software", "crm", "to b", "tob", "enterprise service"] },
  { family: "education_training", keywords: ["education", "training", "course", "school", "academy", "trial class"] },
  { family: "manufacturing", keywords: ["manufacturing", "industrial", "factory", "equipment", "sample", "pilot production"] },
  { family: "channel_sales", keywords: ["channel", "distribution", "reseller", "agency", "rebate", "region policy"] },
  { family: "consulting_services", keywords: ["consulting", "advisory", "service", "project diagnosis", "professional service"] }
];
