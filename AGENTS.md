# MOY 开发执行规范 (AGENTS.md)

> 本文档为 MOY 项目后续所有开发任务的统一执行规范，确保在现有架构基础上持续升级，而非推倒重来。

---

## 项目背景

- **项目名**：MOY (墨言 / Mate Of You)
- **市场定位**：面向中国境内企业的 B2B AI 销售工作系统
- **当前阶段**：持续迭代与稳定升级 (v1.x)
- **技术栈**：Next.js 14 (App Router) + React 18 + TypeScript + Supabase + Tailwind CSS + DeepSeek
- **项目状态**：已完成大部分主骨架，业务闭环完全连通

### 已有模块

| 模块              | 路由                      | 说明                              |
| ----------------- | ------------------------- | --------------------------------- |
| capture           | `/capture`                | 沟通内容捕获与提取                |
| today             | `/today`                  | 今日工作计划与任务                |
| briefings         | `/briefings`              | 早报与简报                        |
| deals             | `/deals`                  | 商机与 Deal Room                  |
| touchpoints       | `/touchpoints`            | 外部触点管理                      |
| outcomes          | `/outcomes`               | 行动结果记录                      |
| memory            | `/memory`                 | 用户记忆画像                      |
| manager views     | `/manager/*`              | 管理者视角（质量/节奏/转化/结果） |
| settings          | `/settings/*`             | 组织/团队/AI/自动化/模板/用量     |
| import center     | `/imports`                | 数据导入中心                      |
| automation rules  | `/settings/automation`    | 自动化规则中心                    |
| executive cockpit | `/executive`              | 高管驾驶舱                        |
| customer health   | `/customers/[id]` (panel) | 客户健康度                        |
| renewal watch     | `/executive` (section)    | 续费预警                          |

---

## 核心开发原则

### 1. 增量修改优先

**规则**：所有任务默认先阅读现有实现，再做增量修改，禁止先入为主地重构整个系统。

**执行要点**：

- 接到任务后，首先搜索并阅读相关模块的现有代码
- 理解现有数据流、服务层、页面结构后再设计改动
- 禁止"假设式"开发——不要假设某个模块不存在或需要重写
- 输出必须包含"现状分析"章节

### 2. 尊重现有架构

**规则**：默认尊重当前架构：Next.js App Router、Supabase、service-layer、现有页面与现有数据流。

**架构约定**：

```
app/
├── (app)/           # 登录后业务页面
│   ├── page.tsx     # 页面组件
│   └── layout.tsx   # 布局壳
├── api/             # API Routes
└── (public)/        # 公开页面

services/            # 业务逻辑层（必须通过 service 访问数据）
lib/                 # 工具函数与共享逻辑
types/               # TypeScript 类型定义
hooks/               # React Hooks
components/          # UI 组件
```

**禁止事项**：

- 在页面组件中直接调用 Supabase client（必须通过 service）
- 绕过 service 层直接操作数据库
- 创建与现有 service 功能重复的新 service

### 3. 复用优先于新建

**规则**：所有方案必须优先复用已有模块、表、服务和页面，而不是新增平行系统。

**检查清单**：

- [ ] 是否已有类似的 service？
- [ ] 是否已有类似的数据库表？
- [ ] 是否已有类似的页面或组件？
- [ ] 是否可以通过扩展现有功能实现？

**决策树**：

```
需要新功能
    ↓
搜索现有实现 → 有 → 评估扩展可行性
                    ↓
              可扩展 → 增量修改
              不可扩展 → 说明原因后再新建
                    ↓
              无 → 说明为何无法复用后再新建
```

### 4. 业务结果导向

**规则**：所有功能设计必须围绕"可见业务结果"，而不是为了增加炫技 AI 能力。

**业务价值映射**：
| 功能类型 | 业务价值指标 |
|----------|--------------|
| 跟进提醒 | 减少漏跟进数量 |
| AI 分析 | 提升跟进质量、缩短决策周期 |
| 自动化规则 | 降低管理成本 |
| 健康度监控 | 提前识别流失风险 |
| 报表生成 | 节省人工统计时间 |

**设计问题清单**：

1. 这个功能如何帮助客户看到可量化结果？
2. 客户如何知道这个功能"有用"？
3. 如何衡量这个功能的成功？

### 5. 中国市场优先

**规则**：对中国境内市场优先：集成、通知、工作入口、经营语言、报表语言都以中国企业使用习惯为准。

**本地化要点**：

- 时间格式：`YYYY-MM-DD HH:mm`（北京时间 UTC+8）
- 货币：人民币（CNY），千分位逗号分隔
- 称呼：销售/经理/管理员，而非 Sales/Manager/Admin
- 工作时间：周一至周五 9:00-18:00 为默认工作时间
- 通知渠道：微信（预留接口）、站内信优先
- 文档语言：中文优先，代码注释中文

---

## 任务输出规范

### 必须包含的章节

每个任务的输出必须包含以下章节：

#### 1. 现状分析

```markdown
### 现状分析

**相关文件**：

- [file1.ts](file:///path/to/file1.ts)
- [file2.ts](file:///path/to/file2.ts)

**现有实现**：

- 现有数据流：...
- 现有服务层：...
- 现有页面结构：...

**可复用点**：

- ...

**需要修改点**：

- ...
```

#### 2. 修改计划

```markdown
### 修改计划

**改动范围**：最小可行改动

**步骤**：

1. ...
2. ...
3. ...

**不改动**：

- ...
```

#### 3. 受影响文件

```markdown
### 受影响文件

| 文件                 | 改动类型 | 说明     |
| -------------------- | -------- | -------- |
| services/xxx.ts      | 修改     | 新增方法 |
| app/api/xxx/route.ts | 修改     | 新增参数 |
| types/xxx.ts         | 修改     | 新增类型 |
```

#### 4. 风险与兼容性

```markdown
### 风险与兼容性

**向后兼容**：是/否

- ...

**潜在风险**：

- ...

**缓解措施**：

- ...
```

#### 5. 验证步骤

```markdown
### 验证步骤

1. 功能验证：
   - [ ] 步骤1
   - [ ] 步骤2

2. 回归验证：
   - [ ] 现有功能不受影响

3. 边界验证：
   - [ ] 空数据处理
   - [ ] 权限边界
```

---

## 数据库变更规范

### Migration 要求

涉及数据库变更时，必须：

#### 1. 给出 Migration

```sql
-- 文件名：supabase/migrations/YYYYMMDDHHMMSS_xxx_description.sql

-- 1. 变更说明
-- 本 migration 新增/修改 xxx 表，用于 xxx 功能

-- 2. 变更内容
CREATE TABLE IF NOT EXISTS xxx (
  ...
);

-- 3. RLS 策略
CREATE POLICY "xxx" ON xxx FOR ...;

-- 4. 索引
CREATE INDEX IF NOT EXISTS idx_xxx ON xxx (...);
```

#### 2. 说明向后兼容性

```markdown
### 向后兼容性

**兼容性评估**：

- 新增表/字段：完全兼容
- 修改字段类型：需要迁移脚本
- 删除字段：不兼容，需要评估影响

**兼容方案**：

- ...
```

#### 3. 说明历史数据回填

````markdown
### 历史数据回填

**需要回填的数据**：

- ...

**回填脚本**：

```sql
-- 回填脚本（可在 migration 中执行或单独执行）
UPDATE xxx SET yyy = 'default_value' WHERE yyy IS NULL;
```
````

**回填时机**：

- migration 执行时自动回填
- 或：后台任务分批回填

```

---

## 页面变更规范

### 优先增强现有页面

**规则**：涉及页面变更时，优先增强现有页面，不轻易新增一级导航。

**导航层级约定**：
```

一级导航（需谨慎新增）：

- /today
- /customers
- /deals
- /briefings
- /manager
- /settings
- /executive

二级导航（可适度新增）：

- /manager/outcomes
- /manager/rhythm
- /manager/conversion
- /settings/ai
- /settings/team
- ...

```

**新增页面决策树**：
```

需要新增页面
↓
是否可以放入现有页面作为 Tab/Section？
↓ 是 → 增强现有页面
↓ 否
是否可以放入现有二级导航？
↓ 是 → 新增二级页面
↓ 否
是否必须作为一级导航？
↓ 是 → 需要产品确认
↓ 否 → 重新评估

````

---

## 实现完成规范

### 默认执行并汇报

所有实现完成后，默认执行并汇报：

```bash
# 1. Lint 检查
npm run lint

# 2. TypeScript 类型检查（build 时自动执行）
npm run build

# 3. 测试（若存在）
npm run test
````

**汇报格式**：

```markdown
### 执行结果

**Lint**：✅ 通过 / ❌ 失败

- 错误数：x
- 警告数：x

**TypeCheck**：✅ 通过 / ❌ 失败

- 错误数：x

**Test**：✅ 通过 / ❌ 失败 / ⏭️ 跳过（无测试）

- 通过：x
- 失败：x

**Build**：✅ 通过 / ❌ 失败
```

---

## 代码风格规范

### 保持风格统一

**规则**：输出代码时尽量保持风格统一，与仓库现有命名和目录风格一致。

**命名约定**：
| 类型 | 约定 | 示例 |
|------|------|------|
| 文件名 | kebab-case | `customer-service.ts` |
| 组件名 | PascalCase | `CustomerTable.tsx` |
| 函数名 | camelCase | `getCustomerById` |
| 常量 | SCREAMING_SNAKE_CASE | `MAX_RETRY_COUNT` |
| 类型/接口 | PascalCase | `CustomerProfile` |
| 数据库表 | snake_case | `customer_health_snapshots` |

**目录约定**：

```
services/          # xxx-service.ts（业务逻辑）
lib/               # xxx.ts（工具函数）
types/             # xxx.ts（类型定义）
hooks/             # use-xxx.ts（React Hooks）
components/ui/     # 基础 UI 组件
components/xxx/    # 业务组件
```

**注释语言**：中文优先

---

## 基础设施规范

### 禁止引入重型基础设施

**规则**：默认不要引入新的重型基础设施，除非现有架构无法承载。

**现有基础设施**：

- 数据库：Supabase PostgreSQL
- 认证：Supabase Auth
- 存储：Supabase Storage（预留）
- AI：DeepSeek API
- 前端：Next.js + React
- 样式：Tailwind CSS + shadcn/ui

**引入新基础设施的评估标准**：

1. 现有方案是否完全无法满足？
2. 是否有更轻量的替代方案？
3. 引入后的运维成本如何？
4. 对现有架构的影响程度？

---

## 需求歧义处理

### 基于现状做最佳判断

**规则**：若需求存在歧义，优先基于仓库现状做最佳判断，不要把任务转化成重新做产品定义。

**处理流程**：

```
需求歧义
    ↓
搜索现有实现 → 有类似实现 → 参考现有模式
                ↓
            无类似实现 → 基于业务目标做合理假设
                ↓
            输出假设说明，继续执行
```

**输出格式**：

```markdown
### 歧义处理

**歧义点**：...

**现有参考**：[相关代码链接]

**决策**：...

**理由**：...
```

---

## 结果归因链路

### 关注价值闭环

**规则**：默认关注结果归因链路：发现问题 -> 触发动作 -> 记录处理 -> 归因结果 -> 展示价值。

**链路示例**：

```
发现问题：客户健康度下降
    ↓
触发动作：生成跟进任务
    ↓
记录处理：销售执行跟进
    ↓
归因结果：健康度回升
    ↓
展示价值：系统帮助挽回了 x 个高风险客户
```

**设计检查**：

- [ ] 是否有明确的问题发现机制？
- [ ] 是否有明确的动作触发路径？
- [ ] 是否有处理记录？
- [ ] 是否有结果归因？
- [ ] 是否有价值展示？

---

## 附录：快速参考

### 常用命令

```bash
# 开发
npm run dev

# 构建
npm run build

# Lint
npm run lint

# 测试
npm run test

# Demo 数据种子
npm run seed:demo
```

### 关键文件位置

| 类型            | 位置                                  |
| --------------- | ------------------------------------- |
| 数据库类型      | `types/database.ts`                   |
| Supabase 客户端 | `lib/supabase/client.ts`, `server.ts` |
| 服务层          | `services/*-service.ts`               |
| API 路由        | `app/api/*/route.ts`                  |
| 页面            | `app/(app)/*/page.tsx`                |
| Migration       | `supabase/migrations/*.sql`           |

### 权限角色

| 角色    | 权限                       |
| ------- | -------------------------- |
| owner   | 全部权限                   |
| admin   | 全部权限（除删除组织）     |
| manager | 团队管理、报表、执行驾驶舱 |
| sales   | 个人客户、商机、跟进       |
| viewer  | 只读                       |

---

_最后更新：v1.0 稳定版_
