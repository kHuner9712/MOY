# MOY 第三阶段（DeepSeek Provider）迭代交付说明

更新时间：2026-03-14

## 1. 新的目录结构（核心增量）

```text
/app
  /api
    /ai
      /config-status/route.ts
      /customer-analysis/route.ts
      /followup-analysis/route.ts
      /runs/route.ts
    /alerts
      /run-scan/route.ts
      /[id]/resolve/route.ts
/lib
  /ai
    /provider.ts
    /providers
      /deepseek.ts
/services
  ai-analysis-service.ts
  ai-prompt-service.ts
  ai-run-service.ts
  ai-client-service.ts
  alert-rule-engine.ts
  alert-workflow-service.ts
/types
  ai.ts
  alert.ts
  database.ts
  followup.ts
/supabase
  /migrations
    202603140001_base.sql
    202603140002_ai_workflow.sql
    202603140003_deepseek_provider.sql
/tests
  run-tests.mts
  provider-parse.test.ts
/docs
  MOY-Phase3-DeepSeek-Upgrade-Notes.md
```

## 2. `supabase/migrations` SQL 文件

### 2.1 `202603140001_base.sql`
- 基础业务表与枚举（organizations/profiles/customers/followups/opportunities/alerts）。
- 第一阶段业务 RLS 与索引。

### 2.2 `202603140002_ai_workflow.sql`
- 新增 AI 审计相关主表：`ai_runs`、`ai_prompt_versions`、`alert_rule_runs`、`ai_feedback`。
- 新增 AI 相关枚举：`ai_trigger_source`、`ai_run_status`、`ai_scenario`。
- 第一版 AI 工作流 RLS 与索引。

### 2.3 `202603140003_deepseek_provider.sql`
- 新增 provider 维度枚举：
  - `ai_provider`（`deepseek/openai/qwen/zhipu`）
  - `ai_provider_scope`（`deepseek/universal`）
  - `ai_result_source`（`provider/fallback`）
- `ai_scenario` 增补：`leak_risk_inference`、`manager_summary`。
- `alerts.source` 增补：`fallback`。
- `ai_runs` 增加字段：
  - `provider`
  - `latency_ms`
  - `result_source`
  - `fallback_reason`
- `ai_prompt_versions` 增加字段：`provider_scope`。
- 增加高频索引：
  - `idx_ai_runs_provider_status_created`
  - `idx_ai_runs_result_source_created`
  - `idx_ai_prompt_versions_provider_scope`
- 插入 DeepSeek 版 prompt seeds（幂等）。

## 3. 新增或改造过的 `services/*` 列表

### 3.1 新增/重构
- `services/ai-analysis-service.ts`
  - 业务编排入口（followup/customer/leak 三场景）。
  - 调用 provider，做 zod 校验，失败自动 fallback。
  - 写入 `ai_runs`，回写 followups/customers/alerts。
- `services/ai-prompt-service.ts`
  - 统一 prompt 管理与版本查询。
  - 支持 `provider_scope` 与场景 guardrails。
- `services/ai-run-service.ts`
  - `ai_runs` 创建、状态更新、历史查询。
  - 支持 `result_source` 与 `fallback_reason` 审计。
- `services/alert-rule-engine.ts`
  - 规则优先 + AI 补强扫描流程。
- `services/alert-workflow-service.ts`
  - 提醒去重、升级、自动 resolved。
- `services/ai-client-service.ts`
  - 前端统一调用内部 API，不直接触达模型。

### 3.2 联动改造
- `services/followup-service.ts`
  - 提交跟进后支持分析状态：`analyzing/completed/failed/fallback`。
- `services/auth-service.ts`
  - 保持 Supabase Auth 流程，兼容已有角色权限校验。

## 4. `.env.example`（第三阶段 DeepSeek 版）

```env
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=

AI_PROVIDER=deepseek
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_REASONER_MODEL=deepseek-reasoner
DEEPSEEK_STRICT_BETA_ENABLED=false
DEEPSEEK_JSON_MODE_ENABLED=true
DEEPSEEK_MAX_TOKENS=
DEEPSEEK_TEMPERATURE=
AI_ANALYSIS_TIMEOUT_MS=30000
AI_FALLBACK_TO_RULE_ENGINE=true
```

## 5. README 中 Supabase 初始化部分（新增/升级摘要）

当前 `README.md` 已明确：
- Supabase 环境变量与本地启动步骤。
- migration 执行顺序（含 `202603140003_deepseek_provider.sql`）。
- `seed:demo` 初始化演示数据方式。
- 认证、RLS、服务层职责说明。
- DeepSeek 配置与 fallback 行为说明。

建议按 README 顺序执行：
1. 配置 `.env.local`
2. 执行 migration（001 -> 002 -> 003）
3. 运行 `npm run seed:demo`
4. `npm run dev` 本地验证

## 6. 本次接入遇到的报错与处理方式

### 6.1 PowerShell 执行策略阻止 `npm`
- 现象：`npm.ps1` 被系统策略禁止执行。
- 处理：改用 `npm.cmd run ...` 进行命令执行。

### 6.2 安装 `server-only` 失败（网络/权限）
- 现象：`npm install server-only` 报 `EACCES`，registry 拉取失败。
- 处理：移除文件中的 `import "server-only"` 标记行（仅边界提示，不影响业务逻辑）。

### 6.3 `next build` 报 `spawn EPERM`
- 现象：沙箱内构建子进程拉起失败。
- 处理：在允许后改为沙箱外执行 build 完整验证。

### 6.4 TypeScript 构建失败（隐式 any）
- 现象：`services/ai-run-service.ts` 中 `row` 参数隐式 `any`。
- 处理：将 map 回调参数显式标注为 `AiRunRow`。

## 7. 当前验收状态

已完成本地验证：
- `npm.cmd run test` 通过
- `npm.cmd run lint` 通过
- `npm.cmd run build` 通过

本阶段目标达成：
- 默认模型提供商切换为 DeepSeek。
- provider-based 架构落地并可扩展。
- AI 结构化输出 + 审计落库 + fallback + 规则引擎/提醒工作流完成。
