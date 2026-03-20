# MOY 通知与集成 Roadmap 实施方案

> 目标：提高可复制交付能力、客户试点成功率、用户持续使用率、结果闭环稳定性、中国市场工作流嵌入能力

---

## 一、现状判断

### 1.1 已具备的可交付能力

| 能力领域 | 已有模块 | 成熟度 | 可交付状态 |
|---------|---------|--------|-----------|
| 客户管理 | `/customers`, `customer_health_snapshots`, `renewal_watch_items` | ⭐⭐⭐⭐⭐ | 可直接交付 |
| 商机管理 | `/deals/[id]`, `deal_rooms`, `deal_checkpoints`, `decision_records` | ⭐⭐⭐⭐⭐ | 可直接交付 |
| 工作任务 | `/today`, `work_items`, `task_execution_logs` | ⭐⭐⭐⭐⭐ | 可直接交付 |
| 行动结果 | `/outcomes`, `action_outcomes`, `suggestion_adoptions` | ⭐⭐⭐⭐⭐ | 可直接交付 |
| 自动化规则 | `/settings/automation`, `automation_rules`, `automation_rule_runs` | ⭐⭐⭐⭐ | 可直接交付 |
| 业务事件 | `business_events`, 12 种事件类型 | ⭐⭐⭐⭐ | 可直接交付 |
| 高管驾驶舱 | `/executive`, `executive_briefs` | ⭐⭐⭐⭐ | 可直接交付 |
| 早报简报 | `/briefings`, `morning_briefs`, `executive_briefs` | ⭐⭐⭐⭐ | 可直接交付 |
| 数据导入 | `/imports`, CSV/XLSX 映射、去重、执行 | ⭐⭐⭐⭐ | 可直接交付 |
| 行业模板 | `/settings/templates`, `industry_templates`, `scenario_packs` | ⭐⭐⭐ | 需增强 |
| 通知系统 | `/settings/notifications`, `notifications`, `notification_preferences` | ⭐⭐⭐ | 需落地 |
| 价值展示 | `/executive` 价值总览、`/briefings` 经营结果摘要 | ⭐⭐⭐⭐ | 归因已完成 |
| 结果归因 | `attribution-service`, `action_outcomes.linked_business_event_ids` | ⭐⭐⭐⭐ | 归因已完成 |

### 1.2 服务层复用价值评估

| 服务类别 | 文件数 | 复用价值 | 说明 |
|---------|-------|---------|------|
| 客户相关 | 8 | ⭐⭐⭐⭐⭐ | customer-service, customer-health-service, renewal-watch-service 等 |
| 商机相关 | 10 | ⭐⭐⭐⭐⭐ | deal-room-service, deal-checkpoint-service, intervention-request-service 等 |
| 工作任务 | 8 | ⭐⭐⭐⭐⭐ | work-item-service, work-plan-service, task-priority-service 等 |
| 自动化 | 4 | ⭐⭐⭐⭐⭐ | automation-rule-service, business-event-service, automation-ops 等 |
| 价值归因 | 3 | ⭐⭐⭐⭐ | value-metrics-service, attribution-service, value-metrics-client-service |
| 通知 | 2 | ⭐⭐⭐ | notification-service, notification-client-service |
| 导入 | 7 | ⭐⭐⭐⭐ | import-validation-service, import-mapping-service, import-execution-service 等 |
| 模板 | 5 | ⭐⭐⭐ | industry-template-service, template-application-service, scenario-pack-service |

### 1.3 数据库表结构完整性

| 表类别 | 表数量 | 核心表 | 状态 |
|--------|-------|--------|------|
| 客户管理 | 6 | customers, customer_health_snapshots, renewal_watch_items | 完整 |
| 商机管理 | 5 | deal_rooms, deal_checkpoints, decision_records, intervention_requests | 完整 |
| 工作任务 | 4 | work_items, task_execution_logs, work_plans | 完整 |
| 自动化 | 3 | automation_rules, automation_rule_runs, business_events | 完整 |
| 结果归因 | 3 | action_outcomes, suggestion_adoptions, collaboration_threads | 完整 |
| 通知 | 2 | notifications, notification_preferences | 完整 |
| 导入 | 3 | import_jobs, import_mappings, import_batches | 完整 |
| 模板 | 3 | industry_templates, scenario_packs, template_applications | 完整 |

---

## 二、关键问题分析

### 2.1 虽然可演示，但未必容易复制到真实客户的问题

| 问题 | 影响 | 根因 |
|------|------|------|
| **客户 onboarding 缺乏模板化** | 每个新客户都需要手动配置 | 缺少行业模板自动应用机制 |
| **数据导入后缺少引导** | 用户导入数据后不知道下一步做什么 | 缺少 onboarding checklist |
| **团队 adoption 缺乏闭环** | 经理看不到团队成员的使用情况 | 缺少团队 adoption dashboard |
| **自动化规则缺少效果验证** | 用户不知道规则是否有效 | 缺少规则效果追踪和推送 |
| **通知渠道未实际落地** | 站内信可用，但飞书/钉钉未接入 | 缺少 provider 实现 |

### 2.2 影响日活/周活的关键问题

| 问题 | 影响 | 优先级 |
|------|------|--------|
| 早报生成后无推送提醒 | 用户不知道早报已生成 | P1 |
| 今日任务缺少优先级排序 | 用户打开后不知道先做什么 | P1 |
| 风险预警缺少即时通知 | 用户错过关键事件处理时机 | P2 |
| 团队成员缺少使用提醒 | 低使用率成员缺少干预 | P2 |

### 2.3 影响销售转化和续费的关键问题

| 问题 | 影响 | 优先级 |
|------|------|--------|
| 价值展示不够直观 | 客户看不到系统带来的实际价值 | P1 |
| 结果归因链路不完整 | 无法证明"系统帮我做了什么" | P1 |
| 缺少客户成功报告 | 无法向决策者展示 ROI | P2 |
| 缺少续费预警可视化 | 错过续费挽回时机 | P2 |

### 2.4 命名和口径不一致问题

| 问题 | 位置 | 影响 | 优先级 |
|------|------|------|--------|
| "Deal Room" vs "商机作战室" | 多处页面 | 用户困惑 | P2 |
| "Touchpoint" vs "外部触点" | 多处页面 | 用户困惑 | P2 |
| "Health Score" vs "健康度评分" | 多处页面 | 用户困惑 | P2 |
| 时间格式不统一 | 部分页面使用 UTC，部分使用北京时间 | 用户困惑 | P1 |

---

## 三、分 Sprint 计划

### Sprint 1：客户 Onboarding 与模板化交付 (2 周)

**目标**：让新客户能在 10 分钟内完成基础配置并看到第一个价值点

#### 3.1.1 Onboarding Checklist 组件

**新增内容**：
1. Onboarding 进度条（显示在 `/today` 页面顶部）
   - 导入客户数据（已完成/未完成）
   - 配置自动化规则（已完成/未完成）
   - 完成第一个跟进（已完成/未完成）
   - 查看第一份早报（已完成/未完成）

2. Onboarding 引导卡片
   - 下一步建议
   - 快捷操作按钮

#### 3.1.2 行业模板自动应用

**新增内容**：
1. 行业选择引导
   - 新组织首次登录时展示行业选择
   - 根据行业自动推荐模板

2. 模板一键应用
   - 自动创建自动化规则
   - 自动创建 playbook
   - 自动配置 AI 提示词

#### 3.1.3 Sprint 1 验收标准

- [ ] 新组织首次登录能看到行业选择引导
- [ ] 选择行业后能一键应用模板
- [ ] `/today` 页面显示 onboarding 进度
- [ ] 数据导入完成后显示下一步建议

---

### Sprint 2：团队 Adoption 与执行闭环增强 (2 周)

**目标**：让经理能看到团队使用情况，并能有效干预低使用率成员

#### 3.2.1 团队 Adoption Dashboard

**新增内容**：
1. 团队使用概览（在 `/manager` 页面）
   - 本周活跃成员数
   - 本周任务完成率
   - 本周 AI 使用率
   - 低使用率成员列表

2. 成员使用详情
   - 登录频次
   - 任务完成率
   - AI 辅助使用率
   - 跟进及时率

#### 3.2.2 低使用率成员干预

**新增内容**：
1. 低使用率预警
   - 连续 3 天未登录
   - 本周任务完成率 < 50%
   - 本周 AI 使用率 < 30%

2. 一键干预
   - 发送提醒通知
   - 创建辅导任务
   - 安排一对一沟通

#### 3.2.3 Sprint 2 验收标准

- [ ] `/manager` 页面显示团队使用概览
- [ ] 能识别低使用率成员
- [ ] 能一键发送干预通知
- [ ] `/executive` 页面显示执行闭环面板

---

### Sprint 3：中国企业消息入口与通知落地增强 (2 周)

**目标**：让通知系统能实际触达用户，支持站内信、飞书、钉钉等渠道

#### 3.3.1 通知 Provider 抽象

**新增内容**：
1. NotificationProvider 接口
   - `send(params)` - 发送通知
   - `isConfigured()` - 检查配置状态
   - `getProviderInfo()` - 获取 provider 信息

2. 实现 InAppProvider（站内信）
3. 实现 MockProvider（用于测试）
4. 预留 FeishuProvider、DingTalkProvider 接口

#### 3.3.2 通知触发点集成

**新增内容**：
1. 业务事件触发通知
   - `business_events` 创建时触发
   - 根据 severity 决定优先级

2. 早报生成触发通知
   - 早报生成完成后发送通知
   - 定时发送（北京时间 8:00）

3. 经理介入触发通知
   - 创建 intervention_request 时通知目标用户

#### 3.3.3 Sprint 3 验收标准

- [ ] 站内信 Provider 正常工作
- [ ] 业务事件触发通知
- [ ] 早报生成触发通知
- [ ] 通知中心页面可用

---

## 四、风险与兼容性

### 4.1 数据库变更风险

| 变更 | 风险等级 | 兼容性 | 缓解措施 |
|------|----------|--------|----------|
| `onboarding_progress` 表 | 低 | 新增表 | 无兼容性问题 |
| `team_adoption_stats` 表 | 低 | 新增表 | 无兼容性问题 |
| `notification_providers` 配置 | 低 | 配置表 | 无兼容性问题 |

### 4.2 服务层变更风险

| 变更 | 风险等级 | 影响范围 | 缓解措施 |
|------|----------|----------|----------|
| 新增 onboarding-progress-service | 低 | 新增服务 | 无影响 |
| 新增 team-adoption-service | 低 | 新增服务 | 无影响 |
| 修改 business-event-service | 中 | 现有服务 | 保持现有接口不变，新增通知触发 |
| 修改 morning-brief-service | 中 | 现有服务 | 保持现有接口不变，新增通知触发 |

---

## 五、推荐实施顺序

### 5.1 必须做（Sprint 1）

| 优先级 | 任务 | 理由 |
|--------|------|------|
| P0 | Onboarding Checklist | 新客户第一印象，直接影响试点成功率 |
| P0 | 行业模板自动应用 | 减少配置时间，提高交付效率 |
| P1 | 数据导入后引导 | 降低用户迷失感 |

### 5.2 应该做（Sprint 2）

| 优先级 | 任务 | 理由 |
|--------|------|------|
| P1 | 团队 Adoption Dashboard | 经理需要了解团队使用情况 |
| P1 | 低使用率成员干预 | 提高团队整体使用率 |
| P2 | 执行闭环可视化 | 识别执行瓶颈 |

### 5.3 暂时不做

| 任务 | 理由 | 后续计划 |
|------|------|----------|
| 飞书/钉钉 SDK 集成 | 需要企业资质和审批 | 下期规划 |
| 微信通知集成 | 需要企业微信服务号资质 | 下期规划 |
| 移动端 App | 需要额外开发资源 | 下期规划 |
| AI 对话式交互 | 当前阶段价值不明确 | 待评估 |

---

_文档版本：v1.0_
_创建日期：2026-03-16_
_预计周期：6 周（3 个 Sprint）_
