# Test & Docs Consolidation Audit v1

## 1. 审计范围与方法

本轮按要求先读取并核对了以下内容：

- `docs/repository-hygiene-audit-v1.md`
- `docs/README.md`
- `tests/run-tests.ts`
- `tests` 目录全部 `*.test.ts` 文件
- `docs` 目录全部 `*.md` 文件
- `package.json`

审计方法：

1. 以 `tests/run-tests.ts` 为唯一执行入口，反向比对 `tests/*.test.ts` 实际接入情况。
2. 逐个读取未接入测试文件内容，判断是否与主入口重复、是否为历史实验性、是否仍有独立价值。
3. 逐个扫描 `docs/*.md` 标题与主题，按“active / archive”做边界收敛，不改正文含义。

---

## 2. 测试审计结果

### 2.1 当前测试入口现状

- `npm run test` 实际执行：`tsx tests/run-tests.ts`
- `tests` 目录测试文件总数：34
- 已接入 `run-tests.ts`：24
- 未接入 `run-tests.ts`：10

### 2.2 未接入测试文件逐项分类

| file | category | 理由（基于真实内容） |
| --- | --- | --- |
| `tests/ai-schema.test.ts` | `merge_into_main` | `node:test` 风格，内容与 `run-tests.ts` 中 `runSchemaTests()` 覆盖点高度重合（followup schema parse、invalid rule reject）。可视为已并入主入口覆盖。 |
| `tests/alert-dedupe.test.ts` | `merge_into_main` | 与 `run-tests.ts` 中 `runDedupeTests()` 等价，断言目标一致（升级严重级别、无变化不更新）。 |
| `tests/alert-rules.test.ts` | `merge_into_main` | 与 `run-tests.ts` 中 `runAlertRuleTests()` 等价，覆盖 no-followup timeout / quoted stall / won skip。 |
| `tests/provider-parse.test.ts` | `merge_into_main` | 与 `run-tests.ts` 中 `runProviderParseTests()` 等价，覆盖 deepseek 内容拼接与 JSON 解析 fallback。 |
| `tests/attribution.test.ts` | `archive` | 主要是对象结构与手工计算样例，几乎不调用当前 service/lib 实现，偏历史设计验证，且未进入执行入口。 |
| `tests/value-metrics.test.ts` | `keep_separate` | 覆盖 `types/value-metrics` 常量与类型语义，属于轻量契约测试；当前未接入主入口但仍有独立参考价值。建议后续择机接入 `run-tests.ts`。 |
| `tests/manager-desk.test.ts` | `archive` | `vitest` 风格，依赖 `@/` 别名与前期阶段语义，且仓库当前 test 脚本未运行 vitest；属于历史阶段测试。 |
| `tests/manager-insights.test.ts` | `archive` | `vitest` 风格，主要是历史计算逻辑样例，未接入当前测试入口。 |
| `tests/manager-insights-snapshot.test.ts` | `archive` | `vitest`/`jest` 混用（包含 `jest.useFakeTimers`），且文件结构存在历史实验痕迹，不适合直接并入现有入口。 |
| `tests/sales-desk.test.ts` | `archive` | `vitest` 风格，面向 v1.1 历史阶段页面与类型契约，未接入当前测试链路。 |

### 2.3 本轮对测试做的低风险整理

- 本轮**不直接删除**任何未接入测试文件。
- 对“已被主入口等价覆盖”的 4 个文件，先在审计层标注为 `merge_into_main`，避免重复维护。
- 对历史 `vitest` 系列与实验性测试，先标注 `archive`，待后续单独归档批次处理（不影响当前 `npm run test` 稳定性）。

---

## 3. 文档审计与轻量整理结果

### 3.1 active docs 与 archive docs 边界

已按当前实现状态收敛为三层：

1. Active：架构/模型基线文档
2. Active：runtime/governance/audit/rollback/config-ops 文档
3. Archive：历史阶段文档（含 `docs/archive/phases/` 及 root 下历史 v1.1-v1.5 文档）

### 3.2 本轮文档整理动作

- 更新 `docs/README.md`：
  - 明确 active docs 分组（架构基线、治理链路、其他活跃参考）
  - 明确 archive docs 分组
  - 将 `MOY-v1.1` ~ `MOY-v1.5` 在索引层标注为 archive 语义
  - 补充“暂不迁移路径”的原因：这些历史文档仍有相对链接引用

### 3.3 仍保留在根目录但按 archive 使用的文档

- `docs/MOY-v1.1-Sales-Desk.md`
- `docs/MOY-v1.2-Manager-Desk.md`
- `docs/MOY-v1.3-Manager-Insights.md`
- `docs/MOY-v1.4-Snapshots-and-Calibration.md`
- `docs/MOY-v1.5-Automated-Snapshots.md`

说明：以上文档目前仍被 `docs/MOY-Current-Architecture-and-Phase-Overview.md` 及其互相引用使用，直接移动会带来路径回归风险，故本轮先做索引边界收敛，不做路径迁移。

---

## 4. 为什么这些属于低风险整理

1. 未改动 runtime/audit/governance/rollback/concurrency 任何核心代码。
2. 未修改 `tests/run-tests.ts` 执行链路，不改变现有测试运行逻辑。
3. 文档仅做索引结构和归档语义标注，不改变正文含义与系统行为描述。
4. 未删除任何业务代码、迁移文件或核心服务文件。

---

## 5. 下一轮建议（非本轮执行）

1. 建立 `tests/archive/` 目录并迁移 `archive` 分类测试，减少主测试目录噪音。
2. 评估 `tests/value-metrics.test.ts` 是否并入 `run-tests.ts`（保留断言价值同时统一执行入口）。
3. 若要迁移 root 历史文档到 `docs/archive/`，先做一次链接重写与回归检查，再执行移动。
