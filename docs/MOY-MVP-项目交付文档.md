# MOY MVP 项目交付文档

> ⚠️ **历史阶段文档 (Historical Phase Document)**
>
> 本文档记录的是 **Phase 1 (MVP 初始交付)** 的项目状态，不代表当前实现。
>
> - 主要对应阶段：Phase 1
> - 当前阶段：Phase 18 (v1.5)
> - 当前有效总览请参考：[README.md](../README.md)、[MOY-Current-Architecture-and-Phase-Overview.md](./MOY-Current-Architecture-and-Phase-Overview.md)
>
> ---
>
> ## 1. 文档目的
本文件用于独立沉淀本次 MVP 交付结果，覆盖：

- 项目目录结构
- 已完成页面（文字版截图说明）
- 技术决策
- 搭建过程中的报错与处理记录

---

## 2. 项目目录结构

> 说明：以下目录树已过滤 `.next`、`node_modules` 等运行产物目录，仅保留业务与工程文件。

```text
MOY/
├─ app/
│  ├─ globals.css
│  ├─ layout.tsx
│  ├─ page.tsx
│  ├─ login/
│  │  └─ page.tsx
│  └─ (app)/
│     ├─ layout.tsx
│     ├─ dashboard/
│     │  └─ page.tsx
│     ├─ customers/
│     │  ├─ page.tsx
│     │  └─ [id]/
│     │     └─ page.tsx
│     ├─ followups/
│     │  └─ new/
│     │     └─ page.tsx
│     ├─ opportunities/
│     │  └─ page.tsx
│     ├─ alerts/
│     │  └─ page.tsx
│     ├─ manager/
│     │  └─ page.tsx
│     └─ settings/
│        └─ page.tsx
├─ components/
│  ├─ ui/
│  │  ├─ badge.tsx
│  │  ├─ button.tsx
│  │  ├─ card.tsx
│  │  ├─ input.tsx
│  │  ├─ label.tsx
│  │  ├─ select.tsx
│  │  ├─ separator.tsx
│  │  ├─ sheet.tsx
│  │  ├─ switch.tsx
│  │  ├─ table.tsx
│  │  └─ textarea.tsx
│  ├─ auth/
│  │  └─ auth-provider.tsx
│  ├─ layout/
│  │  ├─ app-header.tsx
│  │  ├─ app-shell.tsx
│  │  └─ app-sidebar.tsx
│  ├─ shared/
│  │  ├─ app-data-provider.tsx
│  │  ├─ page-header.tsx
│  │  ├─ root-providers.tsx
│  │  └─ stat-card.tsx
│  ├─ dashboard/
│  │  ├─ manager-dashboard.tsx
│  │  └─ sales-dashboard.tsx
│  ├─ customers/
│  │  ├─ customer-ai-panel.tsx
│  │  ├─ customer-filters.tsx
│  │  ├─ customer-table.tsx
│  │  ├─ followup-drawer.tsx
│  │  └─ followup-timeline.tsx
│  ├─ opportunities/
│  │  └─ opportunity-table.tsx
│  ├─ alerts/
│  │  └─ alert-list.tsx
│  └─ manager/
│     └─ sales-detail-table.tsx
├─ data/
│  ├─ mock-alerts.ts
│  ├─ mock-customers.ts
│  ├─ mock-followups.ts
│  ├─ mock-opportunities.ts
│  ├─ mock-stats.ts
│  └─ mock-users.ts
├─ lib/
│  ├─ auth.ts
│  ├─ constants.ts
│  ├─ format.ts
│  ├─ metrics.ts
│  └─ utils.ts
├─ services/
│  ├─ ai-analysis-service.ts
│  ├─ alert-service.ts
│  ├─ auth-service.ts
│  ├─ customer-service.ts
│  ├─ followup-service.ts
│  └─ opportunity-service.ts
├─ types/
│  ├─ alert.ts
│  ├─ auth.ts
│  ├─ customer.ts
│  ├─ dashboard.ts
│  ├─ followup.ts
│  └─ opportunity.ts
├─ README.md
├─ package.json
├─ package-lock.json
├─ tsconfig.json
├─ tailwind.config.ts
├─ postcss.config.js
├─ next.config.mjs
├─ components.json
└─ .eslintrc.json
```

---

## 3. 已完成页面（文字版截图说明）

> 备注：本节用“页面结构 + 信息密度”形式代替截图，便于在文档中快速审阅。

### 3.1 登录页 `/login`
- 品牌信息：`桐鸣科技 / MOY 墨言 / Mate Of You`
- 双栏布局：左侧产品价值说明，右侧 mock 登录卡片
- 支持选择 `sales` 与 `manager` 两类用户直接进入系统

### 3.2 控制台 `/dashboard`
- `sales` 视图：
  - 今日待跟进客户数
  - 本周新增客户数
  - 重点客户提醒
  - 最近沟通记录
  - AI 建议卡片（mock）
  - 漏单风险提示（mock）
- `manager` 视图：
  - 团队本周新增客户
  - 跟进完成率
  - 商机阶段分布
  - 超时未跟进客户
  - 销售排行榜（mock）
  - 高风险商机与高风险客户清单

### 3.3 客户列表页 `/customers`
- 客户统计卡片（总数、活跃推进、待跟进、高风险）
- 支持搜索（客户名/公司/电话/邮箱）
- 支持按负责人、阶段、风险等级筛选
- 支持排序（更新时间、下次跟进、成交概率）
- 表格行可点击进入客户详情

### 3.4 客户详情页 `/customers/[id]`
- 基本信息区（字段完整覆盖）
- 跟进时间线（按时间倒序展示）
- AI 分析卡片（总结、建议、风险判断）
- 风险提醒区（关联漏单提醒）
- 商机概览区（金额、阶段、推进时间）
- 可打开“新增跟进记录”侧边抽屉

### 3.5 跟进录入页 `/followups/new`
- 独立录入入口（与详情抽屉并存）
- 字段覆盖：沟通方式、摘要、客户需求、异议、下一步计划、下次跟进时间、是否需要 AI 分析
- 提交后写入 mock store，并反馈成功状态

### 3.6 商机列表页 `/opportunities`
- 列表展示商机名称、客户、预计金额、阶段、负责人、最近推进、风险、预计关闭日期
- 支持按商机阶段筛选
- 顶部统计卡片聚合商机规模与风险

### 3.7 漏单提醒页 `/alerts`
- 展示 5 类规则对应提醒
- 展示提醒级别（关键风险/预警/提示）与状态
- 支持状态流转：待处理、观察中、已解决

### 3.8 管理者看板页 `/manager`
- 仅 `manager` 可访问
- 团队视角的阶段分布、工作量、高风险清单、长期未跟进名单
- 支持按销售成员切换查看其客户明细（mock）

### 3.9 设置页 `/settings`
- 当前账号信息
- 个人偏好开关（AI 建议、漏单提醒）
- Supabase / OpenAI / Auth 接入占位说明

---

## 4. 关键技术决策

### 4.1 前端架构
- 采用 `Next.js App Router`，用 `app/(app)` 路由组承载登录后后台区域
- 用 `layout.tsx` 统一后台壳（侧栏 + 顶栏 + 响应式导航）
- 页面组件与业务组件分层，避免单文件过大

### 4.2 类型与数据建模
- 在 `types/*` 统一定义核心业务类型（User、Customer、Followup、Opportunity、Alert）
- 所有 mock 数据和服务层均基于同一套 TypeScript 类型，降低后续替换成本

### 4.3 状态管理策略
- 采用轻量方案：`AuthProvider + AppDataProvider`（React Context）
- `AppDataProvider` 负责 mock 数据读写、跟进新增联动、告警联动
- 保持“页面无状态化”，尽量通过 hooks 获取业务数据

### 4.4 服务层抽象
- `services/*` 提供 mock 实现，并在注释中标注 Supabase/OpenAI 替换路径
- 页面不直接依赖具体数据源，后续可平滑替换为真实后端

### 4.5 组件体系
- 使用 Tailwind + shadcn/ui 基础组件（Button/Card/Table/Sheet/Select 等）
- 业务组件按域拆分：dashboard/customers/opportunities/alerts/manager
- 保持企业级 SaaS 风格，强调信息层级与桌面端可读性

---

## 5. 报错与处理记录

### 5.1 `npm install` 在 PowerShell 中被执行策略拦截
- 现象：`npm.ps1` 无法加载（Execution Policy 限制）
- 处理：改用 `npm.cmd install`

### 5.2 依赖安装初次超时
- 现象：`npm.cmd install` 超时
- 处理：使用提权（解除沙箱网络限制）后安装成功

### 5.3 `next build` 报错 `spawn EPERM`
- 现象：沙箱环境下 build 无法 fork 子进程
- 处理：提权执行 `npm.cmd run build`，构建通过

### 5.4 `next dev` 报错 `spawn EPERM`
- 现象：沙箱环境下 dev server 无法启动
- 处理：提权启动并通过端口监听验证（`DEV_LISTENING_OK`）

### 5.5 终端中文乱码
- 现象：PowerShell 输出中中文显示异常
- 结论：仅终端编码显示问题，源码文件内容正常

---

## 6. 结果与可运行性

- `npm install`：通过
- `npm run lint`：通过（无 warning/error）
- `npm run build`：通过
- `npm run dev`：已验证可拉起（端口监听成功）

---

## 7. 备注
若后续需要“截图版”文档，可补充：

1. 登录页、销售控制台、管理者控制台各 1 张
2. 客户列表、客户详情（含抽屉）、商机、提醒、管理者看板各 1 张
3. 汇总为一页演示手册（适合销售/投资人展示）
