# Docs Index

本索引用于区分当前 active docs 与 archive docs，并为 settings/config/runtime/governance 相关主题提供集中入口。

## Active Docs

### 架构与模型基线

- [`product-architecture-principles-v1.md`](./product-architecture-principles-v1.md)
- [`sales-primitives-model-v1.md`](./sales-primitives-model-v1.md)
- [`industry-template-framework-v1.md`](./industry-template-framework-v1.md)
- [`enterprise-customization-framework-v1.md`](./enterprise-customization-framework-v1.md)
- [`role-and-permission-model-v1.md`](./role-and-permission-model-v1.md)
- [`MOY-Current-Architecture-and-Phase-Overview.md`](./MOY-Current-Architecture-and-Phase-Overview.md)

### Runtime / Governance / Audit / Rollback

- [`template-and-org-customization-runtime-bridge-v1.md`](./template-and-org-customization-runtime-bridge-v1.md)
- [`org-runtime-config-read-path-v1.md`](./org-runtime-config-read-path-v1.md)
- [`manager-executive-runtime-preference-bridge-v1.md`](./manager-executive-runtime-preference-bridge-v1.md)
- [`runtime-config-explain-and-override-hardening-v1.md`](./runtime-config-explain-and-override-hardening-v1.md)
- [`runtime-explain-debug-panel-v1.md`](./runtime-explain-debug-panel-v1.md)
- [`org-override-write-path-governance-v1.md`](./org-override-write-path-governance-v1.md)
- [`persisted-audit-and-version-snapshot-foundation-v1.md`](./persisted-audit-and-version-snapshot-foundation-v1.md)
- [`override-concurrency-guard-v1.md`](./override-concurrency-guard-v1.md)
- [`org-template-override-rollback-v1.md`](./org-template-override-rollback-v1.md)
- [`org-template-override-editor-ui-v1.md`](./org-template-override-editor-ui-v1.md)
- [`org-config-governance-expansion-v1.md`](./org-config-governance-expansion-v1.md)
- [`org-config-editor-ui-v1.md`](./org-config-editor-ui-v1.md)
- [`org-config-rollback-v1.md`](./org-config-rollback-v1.md)
- [`config-operations-hub-v1.md`](./config-operations-hub-v1.md)
- [`config-timeline-and-diff-viewer-v1.md`](./config-timeline-and-diff-viewer-v1.md)
- [`repository-hygiene-audit-v1.md`](./repository-hygiene-audit-v1.md)
- [`test-and-docs-consolidation-audit-v1.md`](./test-and-docs-consolidation-audit-v1.md)
- [`github-publish-summary-v1.md`](./github-publish-summary-v1.md)

### 其他活跃参考

- [`MOY-MVP-项目交付文档.md`](./MOY-MVP-项目交付文档.md)
- [`MOY-Supabase-Delivery-Notes.md`](./MOY-Supabase-Delivery-Notes.md)
- [`Roadmap-Notifications-and-Integrations.md`](./Roadmap-Notifications-and-Integrations.md)
- [`Roadmap-Value-Attribution.md`](./Roadmap-Value-Attribution.md)

## Archive Docs

### 已归档目录

- [`archive/phases/`](./archive/phases)

### 历史阶段文档（保留在根目录，按 archive 语义使用）

- [`MOY-v1.1-Sales-Desk.md`](./MOY-v1.1-Sales-Desk.md)
- [`MOY-v1.2-Manager-Desk.md`](./MOY-v1.2-Manager-Desk.md)
- [`MOY-v1.3-Manager-Insights.md`](./MOY-v1.3-Manager-Insights.md)
- [`MOY-v1.4-Snapshots-and-Calibration.md`](./MOY-v1.4-Snapshots-and-Calibration.md)
- [`MOY-v1.5-Automated-Snapshots.md`](./MOY-v1.5-Automated-Snapshots.md)

说明：

1. 以上 5 份历史阶段文档当前仍被其他文档以相对路径引用，暂不直接移动，先在索引层明确 archive 语义。
2. 后续若执行路径迁移，需要同步修正文档内相互引用链接。

## Cleanup Boundary Note

当前仓库存在“未跟踪但被代码引用”的文件，本轮仅做低风险索引收敛，不直接清理这些文件。

示例（非完整列表）：

- `data/industry-template-seeds-v1.ts`
- `data/org-customization-seeds-v1.ts`

## Testing Consolidation Status (v2)

### 已并入 `tests/run-tests.ts`（原文件已归档）

- `tests/archive/merged/ai-schema.test.ts`
- `tests/archive/merged/alert-dedupe.test.ts`
- `tests/archive/merged/alert-rules.test.ts`
- `tests/archive/merged/provider-parse.test.ts`

说明：

1. 以上测试语义已由主入口覆盖，`run-tests.ts` 采用当前仓库的 `assert + logPass` 风格统一执行。
2. 本轮保留归档副本，便于历史追溯，不在主测试目录重复执行。

### 已归档历史测试（未接入主入口）

- `tests/archive/legacy/attribution.test.ts`
- `tests/archive/legacy/manager-desk.test.ts`
- `tests/archive/legacy/manager-insights.test.ts`
- `tests/archive/legacy/manager-insights-snapshot.test.ts`
- `tests/archive/legacy/sales-desk.test.ts`

### 保持独立（keep_separate）

- `tests/value-metrics.test.ts`

说明：

1. 该文件以类型/常量契约校验为主，当前保留独立形态，避免在本轮引入额外测试入口调整。
