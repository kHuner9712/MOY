import type { MarketingPage, MarketingPageKey } from "@/types/commercialization";

function nowIso(): string {
  return new Date().toISOString();
}

const now = nowIso();

export const DEFAULT_MARKETING_PAGES: Record<MarketingPageKey, MarketingPage> = {
  home: {
    id: "builtin:home",
    pageKey: "home",
    status: "published",
    title: "MOY 墨言",
    subtitle: "面向中小企业销售团队的 Web AI 工作台",
    contentPayload: {
      heroCta: ["申请 Demo", "开始试用"],
      highlights: [
        "快速录入沟通并自动结构化",
        "今日任务编排与风险提醒",
        "Deal Room 协同推进关键商机",
        "行业模板 + 导入迁移 + PWA 高效落地"
      ]
    },
    seoPayload: {
      keywords: ["MOY", "墨言", "销售 AI 工作台", "B2B CRM"]
    },
    updatedBy: null,
    createdAt: now,
    updatedAt: now
  },
  product: {
    id: "builtin:product",
    pageKey: "product",
    status: "published",
    title: "产品能力",
    subtitle: "从线索进入到成交复盘的一体化执行系统",
    contentPayload: {
      modules: [
        "Capture 多入口采集",
        "Today 主动任务编排",
        "Briefings 行动前准备",
        "Deal Command 商机协同中枢",
        "Closed-Loop 结果回收与打法沉淀",
        "Growth 商业化转化闭环"
      ]
    },
    seoPayload: {
      keywords: ["sales workspace", "deal command", "trial conversion"]
    },
    updatedBy: null,
    createdAt: now,
    updatedAt: now
  },
  industries: {
    id: "builtin:industries",
    pageKey: "industries",
    status: "published",
    title: "行业方案",
    subtitle: "6 套行业模板，支持 onboarding/demo/trial 快速落地",
    contentPayload: {
      templateKeys: ["generic", "b2b_software", "education_training", "manufacturing", "channel_sales", "consulting_services"]
    },
    seoPayload: {
      keywords: ["行业模板", "销售流程模板", "场景包"]
    },
    updatedBy: null,
    createdAt: now,
    updatedAt: now
  },
  demo: {
    id: "builtin:demo",
    pageKey: "demo",
    status: "published",
    title: "申请 Demo",
    subtitle: "提交后我们会在 24 小时内联系你安排演示",
    contentPayload: {
      formType: "request-demo"
    },
    seoPayload: {},
    updatedBy: null,
    createdAt: now,
    updatedAt: now
  },
  trial: {
    id: "builtin:trial",
    pageKey: "trial",
    status: "published",
    title: "开始试用",
    subtitle: "选择行业模板并快速完成试用开通",
    contentPayload: {
      formType: "start-trial"
    },
    seoPayload: {},
    updatedBy: null,
    createdAt: now,
    updatedAt: now
  },
  contact: {
    id: "builtin:contact",
    pageKey: "contact",
    status: "published",
    title: "联系我们",
    subtitle: "获取迁移建议、行业方案和试用支持",
    contentPayload: {
      formType: "contact"
    },
    seoPayload: {},
    updatedBy: null,
    createdAt: now,
    updatedAt: now
  }
};
