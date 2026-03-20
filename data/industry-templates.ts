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
  },
  {
    templateKey: "saas_subscription",
    displayName: "SaaS 订阅销售",
    industryFamily: "b2b_software",
    summary: "面向 SaaS 订阅型产品的销售模式，强调试用转化、续费管理和客户成功。",
    templatePayload: {
      ...baseTemplatePayload("saas_demo"),
      customer_stages: ["lead", "trial", "active", "expansion", "renewal", "churned"],
      opportunity_stages: ["discovery", "qualification", "trial_started", "trial_completed", "proposal", "negotiation", "closed_won", "closed_lost"],
      default_alert_rules: { no_followup_timeout: 3, trial_stalled: 2, renewal_risk: 14, usage_decline: 7 },
      suggested_checkpoints: ["trial_activated", "first_value_achieved", "decision_maker_engaged", "proposal_sent", "contract_signed", "onboarding_complete"],
      manager_attention_signals: ["trial_without_engagement", "renewal_at_risk", "usage_declining", "expansion_opportunity"],
      prep_preferences: ["试用成功标准确认", "续费价值回顾", "扩展场景挖掘"],
      brief_preferences: ["试用转化率", "续费风险", "扩展机会"],
      recommended_onboarding_path: ["导入客户和订阅数据", "配置试用阶段", "设置续费预警规则", "生成客户健康报告"],
      suitable_team_types: ["saas销售", "客户成功团队", "订阅业务"]
    },
    scenarioPacks: [
      packItems("objections", "SaaS 常见异议", "订阅销售典型异议", ["价格太贵", "不确定效果", "现有系统够用", "决策周期长"]),
      packItems("decision_chain", "SaaS 决策链", "典型决策角色", ["业务负责人", "IT负责人", "采购", "财务"]),
      packItems("quote_strategy", "SaaS 报价策略", "订阅定价策略", ["按席位/用量阶梯定价", "年付折扣", "增值服务包"]),
      packItems("meeting_goals", "SaaS 会议目标", "试用/续费会议目标", ["确认试用成功标准", "锁定续费时间", "挖掘扩展需求"]),
      packItems("risk_signals", "SaaS 风险信号", "流失预警信号", ["试用无活跃", "用量持续下降", "续费无响应"]),
      packItems("manager_interventions", "SaaS 管理介入", "介入时机", ["高价值客户流失风险", "扩展机会停滞"]),
      packItems("followup_patterns", "SaaS 跟进节奏", "建议节奏", ["试用期间每2天同步", "续费前14天启动", "用量下降即时响应"])
    ],
    seededPlaybookTemplates: [
      playbookSeed({
        playbookType: "followup_rhythm",
        title: "试用转化跟进节奏",
        summary: "试用期间保持高频互动，确保首次价值实现。",
        payload: {
          entries: [
            {
              entry_title: "试用首周跟进",
              entry_summary: "确保客户完成首次价值体验。",
              recommended_actions: ["确认试用目标", "引导完成首个成功场景", "锁定决策时间"],
              caution_notes: ["避免过早报价"]
            },
            {
              entry_title: "试用末期转化",
              entry_summary: "推动从试用到付费的决策。",
              recommended_actions: ["回顾试用成果", "确认续费意向", "处理异议"],
              caution_notes: ["不要在无准备情况下等待试用结束"]
            }
          ]
        }
      })
    ]
  },
  {
    templateKey: "marketing_agency",
    displayName: "营销/代运营",
    industryFamily: "consulting_services",
    summary: "营销代理和代运营服务模式，强调项目交付、效果追踪和客户续约。",
    templatePayload: {
      ...baseTemplatePayload("agency_demo"),
      customer_stages: ["lead", "proposal", "contract_signed", "onboarding", "active", "renewal", "churned"],
      opportunity_stages: ["discovery", "proposal_sent", "negotiation", "contract_signed", "delivery", "renewal"],
      default_alert_rules: { no_followup_timeout: 3, delivery_overdue: 2, effect_decline: 7, renewal_risk: 14 },
      suggested_checkpoints: ["需求确认", "方案通过", "合同签署", "项目启动", "首月交付", "效果复盘", "续约谈判"],
      manager_attention_signals: ["效果不达标", "客户投诉", "续约风险", "预算调整"],
      prep_preferences: ["客户目标对齐", "竞品分析", "效果数据准备", "周报/月报模板"],
      brief_preferences: ["项目进度", "客户满意度", "效果指标", "续约机会"],
      recommended_onboarding_path: ["导入客户和项目数据", "配置服务阶段", "设置交付提醒规则", "生成客户效果报告"],
      suitable_team_types: ["营销代理", "代运营团队", "广告投放", "内容营销"]
    },
    scenarioPacks: [
      packItems("objections", "代运营常见异议", "营销服务典型异议", ["效果不确定", "价格贵", "服务周期长", "沟通成本高"]),
      packItems("decision_chain", "代运营决策链", "典型决策角色", ["市场负责人", "品牌经理", "采购", "财务"]),
      packItems("quote_strategy", "代运营报价策略", "服务定价策略", ["基础服务费+效果提成", "阶梯报价", "季度/年度合同"]),
      packItems("meeting_goals", "代运营会议目标", "项目会议目标", ["确认营销目标", "对齐交付节奏", "复盘效果数据"]),
      packItems("risk_signals", "代运营风险信号", "流失预警信号", ["效果连续不达标", "客户响应变慢", "预算下调"]),
      packItems("manager_interventions", "代运营管理介入", "介入时机", ["关键客户投诉", "效果连续下滑", "续约谈判"]),
      packItems("followup_patterns", "代运营跟进节奏", "建议节奏", ["周报同步", "月度复盘", "季度战略对齐"])
    ],
    seededPlaybookTemplates: [
      playbookSeed({
        playbookType: "meeting_strategy",
        title: "月度效果复盘会议",
        summary: "用数据说话，展示价值并推动下一步行动。",
        payload: {
          entries: [
            {
              entry_title: "数据先行",
              entry_summary: "用核心指标展示本月成果。",
              recommended_actions: ["展示关键指标变化", "对比目标完成情况", "分析成功案例"],
              caution_notes: ["数据要真实可验证"]
            },
            {
              entry_title: "问题与优化",
              entry_summary: "坦诚问题并提出改进方案。",
              recommended_actions: ["识别未达标指标", "分析原因", "提出下月优化计划"],
              caution_notes: ["避免只报喜不报忧"]
            },
            {
              entry_title: "下一步行动",
              entry_summary: "明确下阶段目标和资源需求。",
              recommended_actions: ["确认下月目标", "锁定资源投入", "约定下次复盘时间"],
              caution_notes: ["目标要可量化可追踪"]
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
  { family: "consulting_services", keywords: ["consulting", "advisory", "service", "project diagnosis", "professional service"] },
  { family: "saas_subscription", keywords: ["订阅", "subscription", "试用", "trial", "续费", "renewal", "客户成功", "customer success", "churn", "流失"] },
  { family: "marketing_agency", keywords: ["营销", "marketing", "代运营", "agency", "广告", "advertising", "投放", "投放优化", "内容营销", "content marketing"] }
];
