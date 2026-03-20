# MOY 当前架构与阶段总览

> **当前有效文档 (Current Valid Document)**
>
> 本文档反映的是 **Phase 16 (v1.0)** 的实际代码状态，是新读者和开发者的首选入口。
>
> - 当前对外交付版本：v1.0
> - 当前内部开发阶段：Phase 16
> - 最新更新时间：2026-03-20
>
> **阅读顺序建议**：
> 1. 本文档（总览）→ 2. README.md（快速启动）→ 3. 历史文档（特定阶段细节）

---

## 1. 产品定位

**产品名**：MOY (墨言 / Mate Of You)
**市场定位**：面向中国境内企业的 B2B AI 销售工作系统 (Web AI Workspace)
**核心价值**：将 AI 能力深度嵌入销售全流程，实现"发现问题 → 触发动作 → 记录处理 → 归因结果 → 展示价值"的完整闭环

---

## 2. 版本与阶段关系

| 标识 | 含义 | 说明 |
|------|------|------|
| **Phase 16** | 内部开发阶段 | 当前功能里程碑代号 |
| **v1.0** | 对外交付版本 | 基于 Phase 16 打包的稳定发布 |
| **Phase 16.5** | 候选版本 | 曾作为 RC，未成为最终交付版本 |

---

## 3. 当前核心模块

| 模块 | 路由 | 说明 | 成熟度 |
|------|------|------|--------|
| Capture | `/capture` | 沟通内容捕获与提取 | ⭐⭐⭐⭐⭐ |
| Today | `/today` | 今日工作计划与任务 | ⭐⭐⭐⭐⭐ |
| Briefings | `/briefings` | 早报与简报生成 | ⭐⭐⭐⭐ |
| Deals | `/deals` | 商机与 Deal Room | ⭐⭐⭐⭐⭐ |
| Touchpoints | `/touchpoints` | 外部触点管理 | ⭐⭐⭐⭐ |
| Outcomes | `/outcomes` | 行动结果记录 | ⭐⭐⭐⭐ |
| Memory | `/memory` | 用户记忆画像 | ⭐⭐⭐⭐ |
| Playbooks | `/playbooks` | 话术本管理 | ⭐⭐⭐ |
| Reports | `/reports` | 报表中心 | ⭐⭐⭐ |
| Alerts | `/alerts` | 预警中心 | ⭐⭐⭐ |
| Growth | `/growth` | 增长管线 | ⭐⭐⭐ |
| Imports | `/imports` | 数据导入中心 | ⭐⭐⭐⭐ |
| Manager Views | `/manager/*` | 管理者视角 | ⭐⭐⭐⭐ |
| Executive Cockpit | `/executive` | 高管驾驶舱 | ⭐⭐⭐⭐ |
| Automation Rules | `/settings/automation` | 自动化规则中心 | ⭐⭐⭐⭐ |
| Settings | `/settings/*` | 组织/团队/AI/模板/用量 | ⭐⭐⭐⭐ |
| Customer Health | `/customers/[id]` | 客户健康度面板 | ⭐⭐⭐⭐ |
| Renewal Watch | `/executive` (section) | 续费预警 | ⭐⭐⭐⭐ |

---

## 4. 当前分层架构

```
┌─────────────────────────────────────────────────────────────┐
│  展示层 (Presentation Layer)                                 │
│  app/(app)/*/page.tsx     # Next.js App Router 页面       │
│  components/*              # React 组件                    │
└─────────────────────────────────────────────────────────────┘
                              ↓ 调用
┌─────────────────────────────────────────────────────────────┐
│  状态层 (State Layer)                                       │
│  hooks/use-*.ts             # 自定义 React Hooks           │
│  components/*/context.tsx   # React Context                │
└─────────────────────────────────────────────────────────────┘
                              ↓ 调用
┌─────────────────────────────────────────────────────────────┐
│  客户端服务层 (Client Service Layer)                         │
│  services/*-client-service.ts  # 客户端 API 调用封装       │
└─────────────────────────────────────────────────────────────┘
                              ↓ 调用
┌─────────────────────────────────────────────────────────────┐
│  API 层 (API Layer)                                         │
│  app/api/*/route.ts         # Next.js API Routes (80+)     │
└─────────────────────────────────────────────────────────────┘
                              ↓ 调用
┌─────────────────────────────────────────────────────────────┐
│  业务服务层 (Business Service Layer)                         │
│  services/*-service.ts      # 业务逻辑 (100+)              │
│  services/mappers.ts       # 类型转换函数                  │
└─────────────────────────────────────────────────────────────┘
                              ↓ 调用
┌─────────────────────────────────────────────────────────────┐
│  数据访问层 (Data Access Layer)                             │
│  lib/supabase/client.ts     # 浏览器端 Supabase Client     │
│  lib/supabase/server.ts     # 服务端 Supabase Client       │
└─────────────────────────────────────────────────────────────┘
                              ↓ 调用
┌─────────────────────────────────────────────────────────────┐
│  数据库 (Database)                                          │
│  Supabase PostgreSQL + RLS                                  │
│  40+ 表, 50+ Enum, 20+ Migrations                         │
└─────────────────────────────────────────────────────────────┘
```

### 层级职责约定

| 层级 | 禁止行为 |
|------|----------|
| 展示层 | 禁止直接调用 Supabase Client |
| API 层 | 禁止在 route 中写复杂业务逻辑 |
| 服务层 | 禁止绕过 service 直接操作数据库 |

---

## 5. 关键数据能力

### 5.1 自动化规则 (Automation Ops)

| 组件 | 文件/表 | 说明 |
|------|---------|------|
| 规则定义 | `automation_rules` 表 | 12 种触发类型 |
| 规则执行 | `automation_rule_runs` 表 | 记录执行结果 |
| 业务事件 | `business_events` 表 | 事件总线 |
| 预警去重 | `lib/alert-dedupe.ts` | 防止重复预警 |

### 5.2 高管驾驶舱 (Executive Cockpit)

| 组件 | 文件/表 | 说明 |
|------|---------|------|
| 业务概览 | `/executive` 页面 | 经营概览 |
| 高管简报 | `executive_briefs` 表 | AI 生成简报 |
| 执行闭环 | `/manager/outcomes` | 团队执行结果 |

### 5.3 客户健康度 (Customer Health)

| 组件 | 文件/表 | 说明 |
|------|---------|------|
| 健康度快照 | `customer_health_snapshots` 表 | 每日快照 |
| 风险标记 | `risk_flags` JSONB | 多维度风险 |
| 续费预警 | `renewal_watch_items` 表 | 续费监控 |

### 5.4 AI Fallback 机制

| 组件 | 文件 | 说明 |
|------|------|------|
| AI Provider | `lib/ai/provider.ts` | Provider 工厂 |
| DeepSeek 实现 | `lib/ai/providers/deepseek.ts` | DeepSeek API |
| Fallback | `lib/mobile-fallback.ts` | 降级处理 |
| AI 执行审计 | `ai_runs`, `ai_feedback` 表 | 执行记录 |

---

## 6. 数据库规模

| 类别 | 数量 | 代表表 |
|------|------|--------|
| 数据表 | 40+ | customers, deals, followups, work_items |
| Enum 类型 | 50+ | customer_stage, opportunity_stage, ai_scenario |
| Migration | 20+ | 20260314-20260325 |

---

## 7. 技术栈

| 层级 | 技术 | 版本 |
|------|------|------|
| 框架 | Next.js | 14.2.5 |
| UI | React | 18.2.0 |
| 语言 | TypeScript | 5.7.2 |
| 样式 | Tailwind CSS | 3.4.16 |
| UI 组件 | shadcn/ui (Radix) | - |
| 数据库 | Supabase PostgreSQL | - |
| 认证 | Supabase Auth | - |
| AI | DeepSeek API | deepseek-chat, deepseek-reasoner |
| 验证 | Zod | 3.24.2 |

---

## 8. 文档阅读顺序

| 优先级 | 文档 | 说明 |
|--------|------|------|
| **必读** | README.md | 项目快速启动 |
| **必读** | 本文档 | 当前架构总览 |
| **参考** | AGENTS.md | 开发执行规范 |
| **参考** | docs/Roadmap-Notifications-and-Integrations.md | 通知与集成 Roadmap |
| **参考** | docs/Roadmap-Value-Attribution.md | 价值归因 Roadmap |
| **历史** | docs/archive/phases/* | 各阶段详细交付文档 |

---

## 9. 质量门槛

```bash
# Lint
npm run lint          # 无错误

# TypeScript
npm run build         # 编译通过

# 测试
npm run test          # 97 tests, all passing
```

---

## 10. 已知限制

| 限制 | 状态 | 说明 |
|------|------|------|
| 飞书/钉钉集成 | 预留接口 | 待下期规划 |
| 微信通知 | 预留接口 | 需要企业资质 |
| 移动端 App | PWA | 暂不支持原生 App |
| AI 对话式交互 | 骨架 | 待下期评估 |

---

_文档版本：v1.0_
_最后更新：2026-03-20_
