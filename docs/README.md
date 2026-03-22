# Docs Index

本索引用于区分 **active docs** 与 **archive docs**，并提供 MOY 当前配置治理链路的统一导航。

> 约定：`archive` 文档仅作历史参考，**不应**作为当前实现的主依据。

---

## 1. Must Read (当前必读)

1. [product-architecture-principles-v1.md](./product-architecture-principles-v1.md)
2. [sales-primitives-model-v1.md](./sales-primitives-model-v1.md)
3. [industry-template-framework-v1.md](./industry-template-framework-v1.md)
4. [enterprise-customization-framework-v1.md](./enterprise-customization-framework-v1.md)
5. [role-and-permission-model-v1.md](./role-and-permission-model-v1.md)
6. [org-runtime-config-read-path-v1.md](./org-runtime-config-read-path-v1.md)
7. [runtime-config-explain-and-override-hardening-v1.md](./runtime-config-explain-and-override-hardening-v1.md)
8. [org-override-write-path-governance-v1.md](./org-override-write-path-governance-v1.md)
9. [persisted-audit-and-version-snapshot-foundation-v1.md](./persisted-audit-and-version-snapshot-foundation-v1.md)
10. [org-template-override-rollback-v1.md](./org-template-override-rollback-v1.md)
11. [org-config-editor-ui-v1.md](./org-config-editor-ui-v1.md)
12. [org-config-rollback-v1.md](./org-config-rollback-v1.md)
13. [config-operations-hub-v1.md](./config-operations-hub-v1.md)
14. [config-timeline-and-diff-viewer-v1.md](./config-timeline-and-diff-viewer-v1.md)
15. [moy-commercial-readiness-checklist-v1.md](./moy-commercial-readiness-checklist-v1.md)

---

## 2. Recommended Reading Order

1. 架构与业务基线：`product-architecture-principles` -> `sales-primitives-model`
2. 模板与企业定制框架：`industry-template-framework` -> `enterprise-customization-framework`
3. 权限边界：`role-and-permission-model`
4. runtime 读桥接：`template-and-org-customization-runtime-bridge` -> `manager-executive-runtime-preference-bridge` -> `org-runtime-config-read-path`
5. 治理链路：`runtime-config-explain-and-override-hardening` -> `org-override-write-path-governance` -> `persisted-audit-and-version-snapshot-foundation`
6. 恢复链路：`org-template-override-rollback` -> `org-config-rollback`
7. 入口与运营视角：`runtime-explain-debug-panel` -> `org-template-override-editor-ui` -> `org-config-editor-ui` -> `config-operations-hub` -> `config-timeline-and-diff-viewer`

---

## 3. Active Docs

### 3.1 Architecture / Primitives

- [product-architecture-principles-v1.md](./product-architecture-principles-v1.md) `status: active`
- [sales-primitives-model-v1.md](./sales-primitives-model-v1.md) `status: active`
- [MOY-Current-Architecture-and-Phase-Overview.md](./MOY-Current-Architecture-and-Phase-Overview.md) `status: active`

### 3.2 Template / Customization / Permission Baseline

- [industry-template-framework-v1.md](./industry-template-framework-v1.md) `status: active`
- [enterprise-customization-framework-v1.md](./enterprise-customization-framework-v1.md) `status: active`
- [role-and-permission-model-v1.md](./role-and-permission-model-v1.md) `status: active`

### 3.3 Governance Chain (Runtime -> Hardening -> Audit -> Concurrency -> Rollback)

- [template-and-org-customization-runtime-bridge-v1.md](./template-and-org-customization-runtime-bridge-v1.md) `status: active`
- [manager-executive-runtime-preference-bridge-v1.md](./manager-executive-runtime-preference-bridge-v1.md) `status: active`
- [org-runtime-config-read-path-v1.md](./org-runtime-config-read-path-v1.md) `status: active`
- [runtime-config-explain-and-override-hardening-v1.md](./runtime-config-explain-and-override-hardening-v1.md) `status: active`
- [runtime-explain-debug-panel-v1.md](./runtime-explain-debug-panel-v1.md) `status: active`
- [org-override-write-path-governance-v1.md](./org-override-write-path-governance-v1.md) `status: active`
- [persisted-audit-and-version-snapshot-foundation-v1.md](./persisted-audit-and-version-snapshot-foundation-v1.md) `status: active`
- [override-concurrency-guard-v1.md](./override-concurrency-guard-v1.md) `status: active`
- [org-template-override-rollback-v1.md](./org-template-override-rollback-v1.md) `status: active`

### 3.4 Settings Surfaces

- [org-template-override-editor-ui-v1.md](./org-template-override-editor-ui-v1.md) `status: active`
- [org-config-governance-expansion-v1.md](./org-config-governance-expansion-v1.md) `status: active`
- [org-config-editor-ui-v1.md](./org-config-editor-ui-v1.md) `status: active`
- [org-config-rollback-v1.md](./org-config-rollback-v1.md) `status: active`

### 3.5 Operations Surfaces

- [config-operations-hub-v1.md](./config-operations-hub-v1.md) `status: active`
- [config-timeline-and-diff-viewer-v1.md](./config-timeline-and-diff-viewer-v1.md) `status: active`
- [github-publish-summary-v1.md](./github-publish-summary-v1.md) `status: active`
- [docs-alignment-summary-v1.md](./docs-alignment-summary-v1.md) `status: active`

### 3.6 Hygiene / Audit References

- [repository-hygiene-audit-v1.md](./repository-hygiene-audit-v1.md) `status: active`
- [test-and-docs-consolidation-audit-v1.md](./test-and-docs-consolidation-audit-v1.md) `status: active`

### 3.7 Commercial Readiness Standard

- [moy-commercial-readiness-checklist-v1.md](./moy-commercial-readiness-checklist-v1.md) `status: active`

### 3.8 其他活跃参考

- [MOY-MVP-项目交付文档.md](./MOY-MVP-项目交付文档.md)
- [MOY-Supabase-Delivery-Notes.md](./MOY-Supabase-Delivery-Notes.md)
- [Roadmap-Notifications-and-Integrations.md](./Roadmap-Notifications-and-Integrations.md)
- [Roadmap-Value-Attribution.md](./Roadmap-Value-Attribution.md)

---

## 4. Archive Docs

### 4.1 归档目录

- [archive/phases/](./archive/phases/) `status: archive`

### 4.2 历史阶段文档（根目录保留，按 archive 语义使用）

- [MOY-v1.1-Sales-Desk.md](./MOY-v1.1-Sales-Desk.md) `status: archive`
- [MOY-v1.2-Manager-Desk.md](./MOY-v1.2-Manager-Desk.md) `status: archive`
- [MOY-v1.3-Manager-Insights.md](./MOY-v1.3-Manager-Insights.md) `status: archive`
- [MOY-v1.4-Snapshots-and-Calibration.md](./MOY-v1.4-Snapshots-and-Calibration.md) `status: archive`
- [MOY-v1.5-Automated-Snapshots.md](./MOY-v1.5-Automated-Snapshots.md) `status: archive`

说明：以上历史文档仍有相互引用，当前先保持路径不变，仅在索引层明确 archive 边界。

---

## 5. Settings / Config / Runtime 页面入口（实现面）

- `/settings/templates`：Template Override Editor（写入治理、冲突保护、回滚）
- `/settings/org-config`：Org Config Editor（org_settings/org_ai_settings/org_feature_flags）
- `/settings/runtime-debug`：Runtime Explain Debug
- `/settings/config-ops`：Config Operations Hub
- `/settings/config-timeline`：Config Timeline & Diff Viewer

---

## 6. Testing Structure Status

### 6.1 主入口

- `npm run test` -> `tests/run-tests.ts`

### 6.2 已并入主入口（归档副本保留）

- `tests/archive/merged/ai-schema.test.ts`
- `tests/archive/merged/alert-dedupe.test.ts`
- `tests/archive/merged/alert-rules.test.ts`
- `tests/archive/merged/provider-parse.test.ts`

### 6.3 已归档历史测试

- `tests/archive/legacy/attribution.test.ts`
- `tests/archive/legacy/manager-desk.test.ts`
- `tests/archive/legacy/manager-insights.test.ts`
- `tests/archive/legacy/manager-insights-snapshot.test.ts`
- `tests/archive/legacy/sales-desk.test.ts`

### 6.4 keep_separate

- `tests/value-metrics.test.ts`

---

## 7. Cleanup Boundary Note

当前仓库仍存在“未跟踪但被代码引用”的文件；在完成引用治理前，暂不做激进清理。

示例：

- `data/industry-template-seeds-v1.ts`
- `data/org-customization-seeds-v1.ts`
