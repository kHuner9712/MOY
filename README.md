# MOY (墨言 / Mate Of You)

MOY 是面向中小企业销售团队的 **商业级销售执行系统（Sales Execution System）**，不是通用聊天工具。

它的核心目标是把销售推进链路跑通并可治理：
**沟通录入 -> 客户分析 -> 跟进建议 -> 漏单预警 -> 管理可见 -> 配置治理与恢复**。

---

## 1. 项目定位与当前阶段

- **产品定位**：销售执行与管理协同系统（B2B，组织级部署）
- **技术栈**：Next.js 14 + TypeScript + Supabase + DeepSeek
- **当前阶段**：从“可运行”进入“可治理、可恢复、可运营”的平台化阶段
- **关键方向**：模板化、企业定制、权限治理、配置可解释、审计留痕、并发保护、回滚闭环

---

## 2. 当前已落地能力概览

### 2.1 销售工作台与管理视角

- 销售执行链路：`/capture`、`/today`、`/deals`、`/touchpoints`、`/outcomes`、`/memory`
- 管理可见性：`/manager/*`、`/reports`、`/executive`
- 运营与预警：`/alerts`、`/growth`、`/briefings`
- 商业化入口：`/request-demo`、`/start-trial`（来源追踪、资格评估、owner 分配、下游请求与执行动作）

### 2.2 模板与企业配置平台能力

- 行业模板框架（template + scenario pack）
- 企业定制框架（org-level customization）
- 角色与权限模型（owner/admin/manager/sales/viewer）
- 模板/企业配置 runtime 读桥接与来源解释

### 2.3 配置治理与恢复能力（已落地）

- runtime explain/debug（来源、fallback、ignored diagnostics）
- override schema hardening（分层校验与核心语义保护）
- 写路径治理（validate/classify/diagnostics）
- persisted audit（最小持久化审计）
- optimistic concurrency guard（expectedVersion / compare token）
- rollback preview + guarded execution（已覆盖模板 override 与核心 org config）

### 2.4 Settings 运维入口（当前可用）

- `/settings/templates`：模板应用 + org template override 编辑/预览/冲突处理/回滚
- `/settings/org-config`：`org_settings` / `org_ai_settings` / `org_feature_flags` 最小可用编辑器
- `/settings/runtime-debug`：runtime explain 只读调试面板
- `/settings/config-ops`：Config Operations Hub 总览入口
- `/settings/config-timeline`：跨配置域 Timeline & Diff Viewer（摘要级）

---

## 3. 仓库导航

### 3.1 文档入口

- 文档索引：[docs/README.md](docs/README.md)
- 发布摘要：[docs/github-publish-summary-v1.md](docs/github-publish-summary-v1.md)
- 本轮文档对齐说明：[docs/docs-alignment-summary-v1.md](docs/docs-alignment-summary-v1.md)

### 3.2 核心 Active Docs（建议先读）

- [docs/moy-commercial-system-blueprint-v1.md](docs/moy-commercial-system-blueprint-v1.md)
- [docs/commercial-entry-system-v1.md](docs/commercial-entry-system-v1.md)
- [docs/product-architecture-principles-v1.md](docs/product-architecture-principles-v1.md)
- [docs/sales-primitives-model-v1.md](docs/sales-primitives-model-v1.md)
- [docs/industry-template-framework-v1.md](docs/industry-template-framework-v1.md)
- [docs/enterprise-customization-framework-v1.md](docs/enterprise-customization-framework-v1.md)
- [docs/role-and-permission-model-v1.md](docs/role-and-permission-model-v1.md)
- [docs/role-capability-control-plane-v1.md](docs/role-capability-control-plane-v1.md)
- [docs/template-and-org-customization-runtime-bridge-v1.md](docs/template-and-org-customization-runtime-bridge-v1.md)
- [docs/org-runtime-config-read-path-v1.md](docs/org-runtime-config-read-path-v1.md)
- [docs/runtime-config-explain-and-override-hardening-v1.md](docs/runtime-config-explain-and-override-hardening-v1.md)
- [docs/org-override-write-path-governance-v1.md](docs/org-override-write-path-governance-v1.md)
- [docs/persisted-audit-and-version-snapshot-foundation-v1.md](docs/persisted-audit-and-version-snapshot-foundation-v1.md)
- [docs/org-template-override-rollback-v1.md](docs/org-template-override-rollback-v1.md)
- [docs/org-config-editor-ui-v1.md](docs/org-config-editor-ui-v1.md)
- [docs/org-config-rollback-v1.md](docs/org-config-rollback-v1.md)
- [docs/config-operations-hub-v1.md](docs/config-operations-hub-v1.md)
- [docs/config-timeline-and-diff-viewer-v1.md](docs/config-timeline-and-diff-viewer-v1.md)

### 3.3 代码结构（核心）

- `app/`：页面与 API 路由（App Router）
- `services/`：业务服务层（配置治理、审计、回滚、runtime bridge）
- `lib/`：通用能力（concurrency guard、capability helper 等）
- `types/`：领域与数据库类型
- `tests/run-tests.ts`：主测试入口

---

## 4. 测试与质量门禁

### 4.1 测试入口

- `npm run test` 实际执行：`tsx tests/run-tests.ts`
- 主入口覆盖配置治理链路相关测试（runtime explain、governance、audit、rollback、config ops、timeline 等）

### 4.2 本地验证命令

```bash
npm run lint
npm run test
npm run build
```

---

## 5. 本地运行

```bash
npm install
npm run dev
```

- 应用首页：`http://localhost:3000`
- 登录页：`http://localhost:3000/login`

---

## 6. 环境变量（最小示例）

复制 `.env.example` 到 `.env.local`，并至少配置：

```bash
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=
NEXT_PUBLIC_APP_URL=http://localhost:3000

AI_PROVIDER=deepseek
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_REASONER_MODEL=deepseek-reasoner
```

---

## 7. 当前限制（请以此为准）

以下能力尚未完成或仅为最小实现：

- 字段级 rich diff（当前为摘要级结构化 diff）
- 批量回滚
- 自动 merge 冲突处理
- 更完整的统一配置中心（当前仍为分域最小编辑面板）
- 跨配置类型事务回滚
- 高级时间线筛选与统一历史回放

---

## 8. 开发与文档协同约定

- 功能状态以 `services/`、`app/(app)/settings/*`、`tests/run-tests.ts` 为准
- 文档主入口以 [docs/README.md](docs/README.md) 为准
- `docs/archive/*` 与历史阶段文档仅作参考，不作为当前实现主依据

---

## 9. 质量门禁（发布前）

```bash
npm run lint
npm run test
npm run build
```

若任一失败，请优先修复再发布。
