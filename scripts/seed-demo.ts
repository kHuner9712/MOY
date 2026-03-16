import "dotenv/config";

import { createClient, type User } from "@supabase/supabase-js";

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
const serviceRoleKey = process.env.SUPABASE_SERVICE_ROLE_KEY;

if (!supabaseUrl || !serviceRoleKey) {
  throw new Error("Missing env: NEXT_PUBLIC_SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY");
}

const admin = createClient(supabaseUrl, serviceRoleKey, {
  auth: {
    autoRefreshToken: false,
    persistSession: false
  }
});

const ORG = {
  id: "11111111-1111-1111-1111-111111111111",
  name: "桐鸣科技",
  slug: "tongming"
} as const;

const DEMO_USERS = [
  {
    email: process.env.NEXT_PUBLIC_DEMO_MANAGER_EMAIL ?? "manager@demo.moy",
    password: process.env.NEXT_PUBLIC_DEMO_DEFAULT_PASSWORD ?? "Demo#123456",
    displayName: "张睿",
    role: "manager" as const,
    title: "销售总监",
    teamName: "销售管理中心"
  },
  {
    email: process.env.NEXT_PUBLIC_DEMO_SALES_1_EMAIL ?? "linyue@demo.moy",
    password: process.env.NEXT_PUBLIC_DEMO_DEFAULT_PASSWORD ?? "Demo#123456",
    displayName: "林悦",
    role: "sales" as const,
    title: "资深客户经理",
    teamName: "华东销售一组"
  },
  {
    email: process.env.NEXT_PUBLIC_DEMO_SALES_2_EMAIL ?? "chenhang@demo.moy",
    password: process.env.NEXT_PUBLIC_DEMO_DEFAULT_PASSWORD ?? "Demo#123456",
    displayName: "陈航",
    role: "sales" as const,
    title: "客户经理",
    teamName: "华东销售一组"
  },
  {
    email: process.env.NEXT_PUBLIC_DEMO_SALES_3_EMAIL ?? "wufan@demo.moy",
    password: process.env.NEXT_PUBLIC_DEMO_DEFAULT_PASSWORD ?? "Demo#123456",
    displayName: "吴凡",
    role: "sales" as const,
    title: "行业拓展经理",
    teamName: "华南销售组"
  }
];

function isoDaysAgo(days: number): string {
  const now = new Date();
  now.setDate(now.getDate() - days);
  return now.toISOString();
}

function isoDaysAfter(days: number): string {
  const now = new Date();
  now.setDate(now.getDate() + days);
  return now.toISOString();
}

async function findUserByEmail(email: string): Promise<User | null> {
  let page = 1;
  while (true) {
    const { data, error } = await admin.auth.admin.listUsers({
      page,
      perPage: 200
    });
    if (error) throw error;

    const found = data.users.find((user) => user.email?.toLowerCase() === email.toLowerCase());
    if (found) return found;
    if (data.users.length < 200) return null;
    page += 1;
  }
}

async function getOrCreateUser(email: string, password: string): Promise<User> {
  const existed = await findUserByEmail(email);
  if (existed) {
    await admin.auth.admin.updateUserById(existed.id, {
      password,
      email_confirm: true
    });
    return existed;
  }

  const { data, error } = await admin.auth.admin.createUser({
    email,
    password,
    email_confirm: true
  });
  if (error || !data.user) throw error ?? new Error(`Unable to create user ${email}`);
  return data.user;
}

async function main(): Promise<void> {
  console.log("Seeding MOY demo data...");

  const { error: orgError } = await admin.from("organizations").upsert(
    {
      id: ORG.id,
      name: ORG.name,
      slug: ORG.slug
    },
    {
      onConflict: "id"
    }
  );
  if (orgError) throw orgError;

  const userMap = new Map<string, User>();
  for (const item of DEMO_USERS) {
    const authUser = await getOrCreateUser(item.email, item.password);
    userMap.set(item.email, authUser);
  }

  const manager = userMap.get(DEMO_USERS[0].email)!;
  const sales1 = userMap.get(DEMO_USERS[1].email)!;
  const sales2 = userMap.get(DEMO_USERS[2].email)!;
  const sales3 = userMap.get(DEMO_USERS[3].email)!;

  const profileRows = DEMO_USERS.map((item) => {
    const authUser = userMap.get(item.email)!;
    return {
      id: authUser.id,
      org_id: ORG.id,
      display_name: item.displayName,
      role: item.role,
      is_active: true,
      title: item.title,
      team_name: item.teamName
    };
  });

  const { error: profileError } = await admin.from("profiles").upsert(profileRows, { onConflict: "id" });
  if (profileError) throw profileError;

  const promptRows = [
    {
      org_id: ORG.id,
      name: "Followup Analysis",
      version: "v2-deepseek",
      scenario: "followup_analysis",
      provider_scope: "deepseek",
      system_prompt: "You are MOY AI, a B2B sales followup analyst.",
      developer_prompt: "Return strict JSON only. Use only provided facts.",
      output_schema: {
        type: "object"
      },
      is_active: true
    },
    {
      org_id: ORG.id,
      name: "Customer Health",
      version: "v2-deepseek",
      scenario: "customer_health",
      provider_scope: "deepseek",
      system_prompt: "You are MOY AI, a customer health assessor.",
      developer_prompt: "Return strict JSON only. Use only provided facts.",
      output_schema: {
        type: "object"
      },
      is_active: true
    },
    {
      org_id: ORG.id,
      name: "Leak Risk Inference",
      version: "v2-deepseek",
      scenario: "leak_risk_inference",
      provider_scope: "deepseek",
      system_prompt: "You are MOY AI, a leakage risk inference assistant.",
      developer_prompt: "Return strict JSON only. Use only provided facts.",
      output_schema: {
        type: "object"
      },
      is_active: true
    }
  ];

  const { error: promptError } = await admin.from("ai_prompt_versions").upsert(promptRows, {
    onConflict: "org_id,scenario,version"
  });
  if (promptError) throw promptError;

  const customers = [
    {
      id: "20000000-0000-0000-0000-000000000001",
      org_id: ORG.id,
      owner_id: sales1.id,
      name: "刘俊",
      company_name: "云桥制造科技",
      contact_name: "刘俊",
      phone: "13800112301",
      email: "liujun@yqmake.com",
      source_channel: "老客户转介绍",
      current_stage: "proposal",
      last_followup_at: isoDaysAgo(1),
      next_followup_at: isoDaysAfter(1),
      win_probability: 72,
      risk_level: "medium",
      tags: ["制造业", "多工厂"],
      ai_summary: "客户对销售过程自动记录兴趣较高。",
      ai_suggestion: "准备分阶段上线计划，推进商务评审。",
      ai_risk_judgement: "若一周内未提供 ROI 测算，热度可能下降。",
      has_decision_maker: true,
      created_by: sales1.id
    },
    {
      id: "20000000-0000-0000-0000-000000000002",
      org_id: ORG.id,
      owner_id: sales1.id,
      name: "王舒怡",
      company_name: "盛拓物流",
      contact_name: "王舒怡",
      phone: "13710232208",
      email: "wangsy@st-logi.cn",
      source_channel: "官网线索",
      current_stage: "needs_confirmed",
      last_followup_at: isoDaysAgo(4),
      next_followup_at: isoDaysAfter(2),
      win_probability: 58,
      risk_level: "low",
      tags: ["物流"],
      ai_summary: "需求已明确，关注多角色协作。",
      ai_suggestion: "安排场景化演示并确认接口需求。",
      ai_risk_judgement: "风险较低。",
      has_decision_maker: true,
      created_by: sales1.id
    },
    {
      id: "20000000-0000-0000-0000-000000000003",
      org_id: ORG.id,
      owner_id: sales2.id,
      name: "赵磊",
      company_name: "华曜电气",
      contact_name: "赵磊",
      phone: "13622551402",
      email: "zhaolei@huayao-power.com",
      source_channel: "展会活动",
      current_stage: "negotiation",
      last_followup_at: isoDaysAgo(5),
      next_followup_at: isoDaysAgo(1),
      win_probability: 81,
      risk_level: "high",
      tags: ["高客单"],
      ai_summary: "已进入价格和服务条款谈判。",
      ai_suggestion: "提出双轨报价并锁定法务评审时间。",
      ai_risk_judgement: "停滞超过 4 天，存在被竞品抢单风险。",
      has_decision_maker: true,
      created_by: sales2.id
    },
    {
      id: "20000000-0000-0000-0000-000000000004",
      org_id: ORG.id,
      owner_id: sales2.id,
      name: "邓琪",
      company_name: "优慧连锁教育",
      contact_name: "邓琪",
      phone: "13920556621",
      email: "dengqi@yhschool.cn",
      source_channel: "渠道伙伴",
      current_stage: "initial_contact",
      last_followup_at: isoDaysAgo(8),
      next_followup_at: isoDaysAfter(3),
      win_probability: 36,
      risk_level: "medium",
      tags: ["教育"],
      ai_summary: "预算审批流程尚未明确。",
      ai_suggestion: "推进预算窗口确认与需求研讨。",
      ai_risk_judgement: "采购链路不清晰，推进存在不确定性。",
      has_decision_maker: false,
      created_by: sales2.id
    },
    {
      id: "20000000-0000-0000-0000-000000000005",
      org_id: ORG.id,
      owner_id: sales3.id,
      name: "徐瑶",
      company_name: "北辰智能设备",
      contact_name: "徐瑶",
      phone: "13900034555",
      email: "xuyao@beichen-iot.com",
      source_channel: "官网线索",
      current_stage: "proposal",
      last_followup_at: isoDaysAgo(7),
      next_followup_at: isoDaysAgo(2),
      win_probability: 66,
      risk_level: "high",
      tags: ["工业设备"],
      ai_summary: "已提交报价，客户反馈积极。",
      ai_suggestion: "推动采购和业务负责人联合评审。",
      ai_risk_judgement: "报价后推进停滞，漏单风险偏高。",
      has_decision_maker: true,
      created_by: sales3.id
    },
    {
      id: "20000000-0000-0000-0000-000000000006",
      org_id: ORG.id,
      owner_id: sales3.id,
      name: "周洋",
      company_name: "亿鼎家居",
      contact_name: "周洋",
      phone: "13488916320",
      email: "zhouyang@yd-home.com",
      source_channel: "直播活动",
      current_stage: "needs_confirmed",
      last_followup_at: isoDaysAgo(9),
      next_followup_at: isoDaysAfter(2),
      win_probability: 49,
      risk_level: "medium",
      tags: ["零售"],
      ai_summary: "需求明确但决策链较长。",
      ai_suggestion: "安排业务 + IT + 采购三方会议。",
      ai_risk_judgement: "若两周内无负责人确认，可能进入长期停滞。",
      has_decision_maker: false,
      created_by: sales3.id
    }
  ];

  const { error: customerError } = await admin.from("customers").upsert(customers, { onConflict: "id" });
  if (customerError) throw customerError;

  const followups = [
    {
      id: "30000000-0000-0000-0000-000000000001",
      org_id: ORG.id,
      customer_id: customers[0].id,
      owner_id: sales1.id,
      communication_type: "meeting",
      summary: "完成报价讲解，客户希望看到分阶段上线计划。",
      customer_needs: "先覆盖 20 人试点，后续扩展。",
      objections: "担心销售学习成本。",
      next_step: "输出试点实施排期并安排产品答疑。",
      next_followup_at: isoDaysAfter(1),
      needs_ai_analysis: true,
      ai_summary: "客户关注试点效果和上线节奏。",
      ai_suggestion: "准备试点 KPI 模板并锁定关键验收人。",
      ai_risk_level: "medium",
      ai_leak_risk: false,
      created_by: sales1.id,
      created_at: isoDaysAgo(1)
    },
    {
      id: "30000000-0000-0000-0000-000000000002",
      org_id: ORG.id,
      customer_id: customers[2].id,
      owner_id: sales2.id,
      communication_type: "wechat",
      summary: "客户反馈竞品报价更低，要求重评价格。",
      customer_needs: "希望按部门分配账号并控制预算。",
      objections: "价格与实施周期顾虑。",
      next_step: "准备两档方案并约法务会。",
      next_followup_at: isoDaysAfter(0),
      needs_ai_analysis: true,
      ai_summary: "客户在价格敏感期，需要高层协同推进。",
      ai_suggestion: "建议经理加入谈判并明确决策时点。",
      ai_risk_level: "high",
      ai_leak_risk: true,
      created_by: sales2.id,
      created_at: isoDaysAgo(5)
    },
    {
      id: "30000000-0000-0000-0000-000000000003",
      org_id: ORG.id,
      customer_id: customers[4].id,
      owner_id: sales3.id,
      communication_type: "meeting",
      summary: "采购表示需要总部审批。",
      customer_needs: "上线前完成管理员培训。",
      objections: "跨工厂数据同步稳定性疑虑。",
      next_step: "补充稳定性案例并确认审批节点。",
      next_followup_at: isoDaysAfter(1),
      needs_ai_analysis: true,
      ai_summary: "采购环节推进偏慢。",
      ai_suggestion: "安排总部负责人会议，缩短决策链路。",
      ai_risk_level: "high",
      ai_leak_risk: true,
      created_by: sales3.id,
      created_at: isoDaysAgo(7)
    }
  ];

  const { error: followupError } = await admin.from("followups").upsert(followups, { onConflict: "id" });
  if (followupError) throw followupError;

  const opportunities = [
    {
      id: "40000000-0000-0000-0000-000000000001",
      org_id: ORG.id,
      customer_id: customers[0].id,
      owner_id: sales1.id,
      title: "销售工作台一期采购",
      amount: 168000,
      stage: "proposal",
      risk_level: "medium",
      expected_close_date: isoDaysAfter(14).slice(0, 10),
      last_activity_at: isoDaysAgo(1),
      created_by: sales1.id
    },
    {
      id: "40000000-0000-0000-0000-000000000002",
      org_id: ORG.id,
      customer_id: customers[1].id,
      owner_id: sales1.id,
      title: "客户跟进流程升级项目",
      amount: 98000,
      stage: "qualification",
      risk_level: "low",
      expected_close_date: isoDaysAfter(24).slice(0, 10),
      last_activity_at: isoDaysAgo(3),
      created_by: sales1.id
    },
    {
      id: "40000000-0000-0000-0000-000000000003",
      org_id: ORG.id,
      customer_id: customers[2].id,
      owner_id: sales2.id,
      title: "集团销售 AI 升级计划",
      amount: 260000,
      stage: "negotiation",
      risk_level: "high",
      expected_close_date: isoDaysAfter(10).slice(0, 10),
      last_activity_at: isoDaysAgo(5),
      created_by: sales2.id
    },
    {
      id: "40000000-0000-0000-0000-000000000004",
      org_id: ORG.id,
      customer_id: customers[4].id,
      owner_id: sales3.id,
      title: "销售管理数字化改造",
      amount: 142000,
      stage: "proposal",
      risk_level: "high",
      expected_close_date: isoDaysAfter(12).slice(0, 10),
      last_activity_at: isoDaysAgo(7),
      created_by: sales3.id
    }
  ];

  const { error: opportunityError } = await admin.from("opportunities").upsert(opportunities, { onConflict: "id" });
  if (opportunityError) throw opportunityError;

  const alerts = [
    {
      id: "50000000-0000-0000-0000-000000000001",
      org_id: ORG.id,
      customer_id: customers[2].id,
      owner_id: sales2.id,
      rule_type: "high_probability_stalled",
      severity: "critical",
      status: "open",
      title: "高成交概率客户停滞",
      description: "客户已 5 天未推进关键节点，建议经理协同跟进。",
      due_at: isoDaysAfter(1)
    },
    {
      id: "50000000-0000-0000-0000-000000000002",
      org_id: ORG.id,
      customer_id: customers[4].id,
      owner_id: sales3.id,
      rule_type: "quoted_but_stalled",
      severity: "warning",
      status: "watching",
      title: "报价后推进停滞",
      description: "建议尽快确认采购节点并安排联合评审。",
      due_at: isoDaysAfter(2)
    },
    {
      id: "50000000-0000-0000-0000-000000000003",
      org_id: ORG.id,
      customer_id: customers[5].id,
      owner_id: sales3.id,
      rule_type: "missing_decision_maker",
      severity: "warning",
      status: "open",
      title: "决策人未明确",
      description: "多次沟通后仍未确认决策层，存在推进中断风险。",
      due_at: isoDaysAfter(3)
    }
  ];

  const { error: alertError } = await admin.from("alerts").upsert(alerts, { onConflict: "id" });
  if (alertError) throw alertError;

  console.log("Seed completed.");
  console.log("Demo accounts:");
  DEMO_USERS.forEach((item) => {
    console.log(`- ${item.displayName} (${item.role}) => ${item.email} / ${item.password}`);
  });
}

main().catch((error) => {
  console.error("Seed failed:", error);
  process.exit(1);
});
