# Repository Hygiene / Cleanup Audit v1

## 0. 范围与方法

- 审计范围：
  - `settings / templates / org-config / runtime-debug / config-ops` 页面与路由
  - runtime bridge / audit / rollback / concurrency / governance 相关 service/lib
  - `tests/run-tests.ts`
  - `package.json`
  - `.gitignore`
- 执行方式：
  - 先读取源码与路由关系，再做引用检查（`Select-String`、`git ls-files`、目录扫描）
  - 本报告不做任何删除动作，仅给出分级建议

---

## 1. 当前仓库主要目录职责概览

| 目录 | 职责 | 当前状态 |
| --- | --- | --- |
| `app/` | 页面与 API 路由（App Router） | 主业务与 settings 治理入口集中 |
| `services/` | 业务服务层（runtime、governance、audit、rollback） | 规则与聚合逻辑核心 |
| `lib/` | 通用能力（hardening、concurrency、capability） | 关键治理基础层 |
| `types/` | 业务与数据库类型 | 稳定依赖层 |
| `tests/` | 测试入口与测试文件 | 存在“已接入/未接入”并行状态 |
| `docs/` | 方案与交付文档 | 有活跃文档与归档文档并存 |
| `data/` | seed/配置种子 | 被 runtime/template 链路使用 |
| `supabase/` | migration 与数据库演进 | 关键历史不可误删 |
| `scripts/` | 工具脚本 | 当前仅 `seed-demo.ts` 在脚本中被使用 |
| `.history/` | 编辑器历史快照 | 已被 `.gitignore` 忽略，但存在已跟踪文件 |
| `.next/`, `node_modules/` | 本地构建与依赖缓存 | 生成物，不应入库 |

---

## 2. 可删除文件清单（附理由）

| path / file group | category | confidence | reason |
| --- | --- | --- | --- |
| `.history/**`（当前已跟踪的历史快照文件） | delete | high | 与源码无引用关系；`.gitignore` 已明确忽略 `.history`；属于编辑器历史噪音。 |
| `tsconfig.tsbuildinfo`（当前已跟踪） | delete | high | TypeScript 增量构建生成物；`.gitignore` 已忽略 `*.tsbuildinfo`；应从版本库移除。 |

> 说明：本报告阶段不执行删除，仅建议进入第一轮安全清理。

---

## 3. 可合并文件清单（附理由）

| path / file group | category | confidence | reason |
| --- | --- | --- | --- |
| `tests/*.test.ts` 中未被 `tests/run-tests.ts` 接入的 10 个文件（见第 8 节） | merge | high | 当前 `npm test` 只执行 `tests/run-tests.ts`；这批文件不会被现有测试入口运行，存在重复与漂移风险。建议统一到单一测试入口。 |
| `app/api/settings/org-config/*` 与 `app/api/settings/org|ai/route.ts` 的写链路能力边界 | merge | medium | 两组入口都承载 org config 写路径，存在职责交叉。建议后续收敛写入口（保留兼容层）。 |
| `docs/` 下多份 v1 治理文档 + 运营页文档 | merge | medium | 内容分散但互相依赖，建议增加单一“治理索引文档”聚合关系，避免维护碎片化。 |

---

## 4. 建议归档文件清单（附理由）

| path / file group | category | confidence | reason |
| --- | --- | --- | --- |
| `fix-exts.js` | archive | high | 无 `package.json` 脚本引用、无代码引用；属于一次性修复脚本，建议归档到 `scripts/archive/` 或删除。 |
| `tests/manager-desk.test.ts`、`tests/sales-desk.test.ts`、`tests/manager-insights.test.ts`、`tests/manager-insights-snapshot.test.ts` | archive | medium | 使用 `vitest` 风格，但仓库无 `vitest` 依赖与脚本；且未接入 `run-tests.ts`。建议先归档或迁移到统一测试体系。 |
| `docs/MOY-v1.1-*.md` ~ `docs/MOY-v1.5-*.md`（历史阶段文档） | archive | medium | 与 `docs/archive/phases/` 的历史文档语义相近，建议统一进入 archive 子目录并保留索引。 |

---

## 5. 生成物 / 缓存 / 临时文件清单

| path / file group | category | confidence | reason |
| --- | --- | --- | --- |
| `.next/` | generated | high | Next.js 构建输出目录。 |
| `node_modules/` | generated | high | 依赖安装目录。 |
| `*.tsbuildinfo` | generated | high | TypeScript 增量构建缓存。 |
| `coverage/`, `dist/`, `out/`, `*.log`, `.DS_Store` | generated | high | 典型本地生成/缓存/系统文件。 |
| `.history/` | generated | high | 编辑器历史目录，已在 `.gitignore` 忽略。 |

**现状提示**

- `.gitignore` 已覆盖上述项，但当前仓库中仍存在被跟踪的 `.history/**` 与 `tsconfig.tsbuildinfo`，应清理 Git 跟踪状态。

---

## 6. 目录结构问题清单

| 问题 | category | confidence | reason |
| --- | --- | --- | --- |
| settings 读模型页面增多（`runtime-debug` / `config-ops` / `config-timeline` / `org-config` / `templates`）但缺统一文档入口 | keep | medium | 功能已可用，但新成员难快速定位“哪个页面看什么”。建议新增 settings 治理导航文档。 |
| API 层存在“历史写入口 + 新治理入口”并行（`/api/settings/org|ai` 与 `/api/settings/org-config/*`） | merge | medium | 容易导致调用方路径选择不一致，后续应逐步收敛。 |
| `docs/` 根目录聚合了活跃规范、历史版本、路线图，层级边界不够清晰 | archive | medium | 建议按 `active/`, `archive/`, `roadmap/` 或索引化治理。 |

---

## 7. 依赖与脚本清理建议

| 项目 | category | confidence | reason |
| --- | --- | --- | --- |
| `fix-exts.js`（孤立脚本） | archive | high | 无任何脚本入口和引用。 |
| `vitest` 测试风格文件与当前 `npm test`（`tsx tests/run-tests.ts`）不一致 | merge | high | 建议二选一：统一迁移到 `run-tests.ts`，或补齐 `vitest` 体系并明确双入口策略。 |
| 依赖整体清理（通用 dep prune） | keep | low | 当前未发现可直接高置信删除的运行依赖；建议后续通过受控脚本做二次验证。 |

---

## 8. 测试与文档清理建议

### 8.1 测试清理建议

以下测试文件当前未被 `tests/run-tests.ts` 导入，默认不会被 `npm test` 执行：

- `tests/ai-schema.test.ts`
- `tests/alert-dedupe.test.ts`
- `tests/alert-rules.test.ts`
- `tests/attribution.test.ts`
- `tests/manager-desk.test.ts`
- `tests/manager-insights.test.ts`
- `tests/manager-insights-snapshot.test.ts`
- `tests/provider-parse.test.ts`
- `tests/sales-desk.test.ts`
- `tests/value-metrics.test.ts`

| file group | category | confidence | reason |
| --- | --- | --- | --- |
| 上述 10 个未接入测试文件 | merge / archive | high | 与当前测试执行入口脱节；部分为 vitest 风格，且存在内容/编码历史问题，建议统一处理。 |

### 8.2 文档清理建议

| file group | category | confidence | reason |
| --- | --- | --- | --- |
| `docs/*-v1.md`（活跃治理文档） | keep | high | 与当前 runtime/audit/rollback/editor/hub/timeline 实现匹配，属于项目真实状态文档。 |
| `docs/archive/phases/*` | keep | high | 已归档，职责明确。 |
| `docs/MOY-v1.1~v1.5-*` | archive | medium | 历史阶段说明，可保留但建议移动到归档层并加索引。 |

---

## 9. 建议分两轮执行的 cleanup 计划

### 第一轮（安全清理，低风险）

1. 移除 Git 跟踪的生成/历史文件：
   - `.history/**`
   - `tsconfig.tsbuildinfo`
2. 归档孤立脚本：
   - `fix-exts.js` -> `scripts/archive/fix-exts.js`（或直接删除）
3. 新增/更新一份 docs 索引（不改业务逻辑）：
   - 标注 active vs archive 文档

### 第二轮（结构清理，需评审）

1. 测试体系收敛：
   - 将未接入测试并入 `run-tests.ts` 或统一迁移到标准 runner
2. API 写路径收敛：
   - 评估 `org|ai` 与 `org-config/*` 的兼容桥接策略
3. settings 运维文档/导航统一：
   - 明确 `runtime-debug` / `config-ops` / `config-timeline` / `org-config` 页面职责边界

---

## 10. 风险提示：哪些文件不要误删

| path / file group | category | confidence | reason |
| --- | --- | --- | --- |
| `services/template-org-runtime-bridge-service.ts` | keep | high | runtime explain 与消费链路核心。 |
| `services/org-config-audit-service.ts` | keep | high | persisted audit 读写基座。 |
| `services/org-config-governance-service.ts` / `services/org-config-rollback-service.ts` / `services/org-template-override-rollback-service.ts` | keep | high | 写入治理与 rollback 闭环核心。 |
| `lib/override-concurrency-guard.ts` / `lib/template-override-hardening.ts` / `lib/org-config-write-governance.ts` | keep | high | hardening + optimistic concurrency 核心，不可误删。 |
| `app/api/settings/**` 与 `services/settings-client-service.ts` 对应接口 | keep | high | 页面调用链直接依赖，删任一端会破坏 settings 功能。 |
| `supabase/migrations/*.sql`（含 `202603290002_persisted_org_config_audit_foundation.sql`） | keep | high | 数据库演进历史与环境重建基础。 |
| `types/database.ts`、`services/mappers.ts` | keep | high | 数据层类型与映射基线。 |
| 当前未跟踪但已被引用的治理文件（如 `data/industry-template-seeds-v1.ts`、`data/org-customization-seeds-v1.ts` 及相关新 service/route/docs/tests） | keep | high | 这些不是垃圾文件，而是“应补提交流程”的业务文件；清理时不可按未跟踪误删。 |

---

## 建议先执行的安全清理列表

1. 从版本库移除 `.history/**`（保留本地可选，不再入库）。
2. 从版本库移除 `tsconfig.tsbuildinfo`。
3. 归档 `fix-exts.js`（或删除）。
4. 输出一份 `docs/` 索引页，标注活跃文档与归档文档。
5. 对“未跟踪但被引用”的文件先做提交/归位决策，再做后续清理，避免误删正在使用的治理能力。

