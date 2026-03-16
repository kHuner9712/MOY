# MOY 第三阶段 DeepSeek 接入变更文档

更新时间：2026-03-14

## 1. 产出目录变化

本次围绕“AI provider 抽象 + DeepSeek 落地 + 审计可追踪 + 自动提醒工作流”新增/改造的关键目录如下：

```text
MOY/
├─ app/
│  ├─ api/
│  │  ├─ ai/
│  │  │  ├─ config-status/route.ts
│  │  │  ├─ customer-analysis/route.ts
│  │  │  ├─ followup-analysis/route.ts
│  │  │  └─ runs/route.ts
│  │  └─ alerts/
│  │     ├─ run-scan/route.ts
│  │     └─ [id]/resolve/route.ts
│  └─ (app)/
│     ├─ customers/[id]/page.tsx
│     ├─ followups/new/page.tsx
│     ├─ manager/page.tsx
│     ├─ alerts/page.tsx
│     └─ settings/page.tsx
├─ lib/
│  └─ ai/
│     ├─ provider.ts
│     └─ providers/
│        └─ deepseek.ts
├─ services/
│  ├─ ai-analysis-service.ts
│  ├─ ai-prompt-service.ts
│  ├─ ai-run-service.ts
│  ├─ ai-client-service.ts
│  ├─ alert-rule-engine.ts
│  └─ alert-workflow-service.ts
├─ supabase/
│  └─ migrations/
│     └─ 202603140003_deepseek_provider.sql
├─ tests/
│  ├─ run-tests.mts
│  └─ provider-parse.test.ts
├─ types/
│  ├─ ai.ts
│  ├─ alert.ts
│  ├─ followup.ts
│  └─ database.ts
├─ .env.example
└─ README.md
```

说明：
- 已移除旧的 `lib/openai/server.ts`，默认链路不再依赖 OpenAI。
- `ai_provider` 枚举中保留了 `openai/qwen/zhipu` 作为未来扩展位，但当前实现仅启用 `deepseek`。

## 2. Migration 变更

### 新增 SQL 文件
- `supabase/migrations/202603140003_deepseek_provider.sql`

### 本 migration 核心内容
1. 新增枚举
- `ai_provider`: `deepseek | openai | qwen | zhipu`
- `ai_provider_scope`: `deepseek | universal`
- `ai_result_source`: `provider | fallback`

2. 扩展现有枚举
- `ai_scenario` 增加：`leak_risk_inference`、`manager_summary`
- `alert_source` 增加：`fallback`

3. 扩展审计表字段
- `ai_runs` 增加：
  - `provider`
  - `latency_ms`
  - `result_source`
  - `fallback_reason`
- `ai_prompt_versions` 增加：`provider_scope`

4. 索引优化
- `idx_ai_runs_provider_status_created`
- `idx_ai_runs_result_source_created`
- `idx_ai_prompt_versions_provider_scope`

5. Prompt seed（幂等）
- 写入 DeepSeek 对应场景 prompt 版本：
  - followup_analysis
  - customer_health
  - leak_risk_inference

## 3. DeepSeek Provider 产出

### 架构与职责
- `lib/ai/provider.ts`
  - 统一 provider 抽象接口与工厂选择器
  - 按 `AI_PROVIDER` 选择实现（当前默认 deepseek）
- `lib/ai/providers/deepseek.ts`
  - DeepSeek Chat Completions 服务端调用
  - 支持：
    - baseURL/model/timeout 配置
    - JSON 输出模式
    - tool/function calling 预留
    - strict beta 模式与失败降级
  - 统一返回结构：
    - raw response
    - parsed json
    - token usage
    - model
    - latency
    - finish reason
    - error

### 业务编排
- `services/ai-analysis-service.ts`
  - 跟进分析、客户健康分析、漏单推断三类入口
  - 统一 schema 校验（zod）
  - 失败回退到规则化 fallback
  - 回写 `ai_runs`、`followups`、`customers`、`alerts`

### Prompt 与审计
- `services/ai-prompt-service.ts`
  - prompt 版本化与场景管理
- `services/ai-run-service.ts`
  - AI 执行生命周期审计：queued/running/completed/failed
  - 记录 `result_source` 与 `fallback_reason`

## 4. README 更新项

`README.md` 已补充以下内容：
1. DeepSeek 环境变量说明（替代 OpenAI 默认方案）
2. 如何启用真实 AI 分析
3. strict 模式启用方式与行为
4. 无 API key / 调用失败时 fallback 机制
5. `ai_runs` 与 `alert_rule_runs` 数据流说明
6. Migration 执行顺序（含 003）
7. demo seed 与本地验证流程
8. 常见错误排查

## 5. 接入过程报错与处理

### 报错 1：PowerShell 执行策略导致 `npm` 命令失败
- 现象：`npm.ps1` 被策略拦截
- 处理：改用 `npm.cmd run ...`

### 报错 2：安装 `server-only` 失败（EACCES/网络限制）
- 现象：`npm install server-only` 拉取失败
- 处理：移除代码中的 `import "server-only"` 标记（不影响业务逻辑）

### 报错 3：`next build` 出现 `spawn EPERM`
- 现象：沙箱环境下子进程拉起失败
- 处理：在允许后使用非沙箱限制方式执行 build 完整校验

### 报错 4：TypeScript 构建错误（隐式 any）
- 位置：`services/ai-run-service.ts`
- 现象：`map((row) => ...)` 参数隐式 any
- 处理：显式标注 `row: AiRunRow`

## 6. 最终验证结果

- `npm.cmd run test`：通过
- `npm.cmd run lint`：通过
- `npm.cmd run build`：通过

---

文档路径：`docs/MOY-Phase3-DeepSeek-变更与报错处理.md`
