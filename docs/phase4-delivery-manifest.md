# 阶段四交付物清单（P4 delivery manifest）

| 项 | 内容 |
| --- | --- |
| 维护人 | **严过关（QA）** —— 只管"状态"是否与实际一致 |
| 复核人 | **齐活林（交付总监）** —— 验收时逐行核对 |
| 生成方式 | `python scripts/gen_delivery_manifest.py`（**勿手写**；`--check` 可只校验不写盘） |
| 真源 | `docs/architecture-phase4.md` §11 任务表（清单与归属）+ §11.1 周次映射 + `git ls-files`（是否已建） |
| 生成时间 | 每次重跑自动刷新；本文件不记录时间戳（避免"看起来新、其实旧"） |

## 核对口径（验收时照此执行）

1. **`已建` 项必须 `ls` 得到**：`python scripts/gen_delivery_manifest.py --check` 退出 0 即通过；该命令还校验
   清单是否失真（已建却写"待建"、或反之）——9 个已建交付物曾被标"待建"（D-28 / LED-043），故为硬门禁。
2. **`待建` 项在阶段四验收时必须为 0**；若不为 0，要么补建，要么在 §13 明确"不进 V1.0"。
3. **`证据` 列是唯一允许手改的列**（可写"已核对 / 客户现场验证 / 对应 PR"）；重跑时人工文字原样保留、自动短 SHA 刷新，未入库的写 `待补`。
4. **`备注` 列由脚本按 `AUDIT_NOTES` 生成**（D-28 的路径/大小核实、确属缺失项的归属），不接受手改。
5. **不要手工往表里加行**——交付物归属变了，请改 `architecture-phase4.md` 的任务表再重跑。
6. `包内产物` 类（离线包内的 `manifest.json` / SBOM / 金标集）**不入仓库**，其"待建"指"随发布产出"。
7. **`状态` 只回答"这个文件 ls 得到吗"，不回答"这个任务做完了没"**。对"改既有文件"类任务（如 T4-01.1
   收敛整改、T4-03.7 扩词），`已建` 不代表改动已完成——完成度以任务表验收命令为准，本清单不得宣称进度。

## 汇总

- 合计 **119** 行：**已建 91** / **待建 28** / **不适用 0**
- 按批次：W1 30 / W2 21 / W3 37 / W4 31

## 清单

| ID | 交付物路径 | 所属任务 | 批次 | 类型 | 状态 | 验收证据 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `T4-01.1` | `apps/adapter/app/api/v1/artifact_files.py` | T4-01.1 | W1 | 代码 | 已建 | b6effcf | — |
| `T4-01.1` | `apps/adapter/app/core/artifact_token.py` | T4-01.1 | W1 | 代码 | 已建 | b6effcf | — |
| `T4-01.1` | `apps/adapter/app/core/config.py` | T4-01.1 | W1 | 代码 | 已建 | f46544f | — |
| `T4-01.1` | `apps/adapter/app/core/security.py` | T4-01.1 | W1 | 代码 | 已建 | f46544f | — |
| `T4-01.1` | `apps/adapter/app/main.py` | T4-01.1 | W1 | 代码 | 已建 | f46544f | — |
| `T4-01.1` | `deploy/images.lock` | T4-01.1 | W1 | 代码 | 已建 | fa735ed | — |
| `T4-01.1` | `deploy/validate_deploy.py` | T4-01.1 | W1 | 代码 | 已建 | dfab8b9 | — |
| `T4-01.2` | `apps/adapter/app/core/errors.py` | T4-01.2 | W1 | 代码 | 已建 | f46544f | — |
| `T4-01.2` | `apps/adapter/tests/unit/test_error_registry.py` | T4-01.2 | W1 | 测试 | 已建 | e1c7abb | — |
| `T4-01.2` | `deploy/.env.example` | T4-01.2 | W1 | 代码 | 已建 | f46544f | — |
| `T4-01.3` | `apps/adapter/app/api/v1/router.py` | T4-01.3 | W1 | 代码 | 已建 | f46544f | — |
| `T4-01.3` | `deploy/validate_openapi.py` | T4-01.3 | W1 | 代码 | 已建 | 0094d41 | — |
| `T4-01.3` | `docs/api/baseline-phase4-10.json` | T4-01.3 | W1 | 代码 | 已建 | e1c7abb | — |
| `T4-01.3` | `docs/api/openapi.snapshot.json` | T4-01.3 | W1 | 代码 | 已建 | 0094d41 | — |
| `T4-01.4` | `apps/adapter/app/db/migrate.py` | T4-01.4 | W1 | 代码 | 已建 | b96ba7d | — |
| `T4-01.4` | `apps/adapter/tests/integration/test_migration_lock.py` | T4-01.4 | W1 | 测试 | 已建 | 2699897 | — |
| `T4-01.4` | `deploy/init-schema-phase3.sql` | T4-01.4 | W1 | 代码 | 已建 | 010db32 | — |
| `T4-01.4` | `deploy/migrations/rollback/` | T4-01.4 | W1 | 迁移 | 已建 | 9a70b39 | — |
| `T4-01.5` | `apps/adapter/app/db/repositories/artifact_repo.py` | T4-01.5 | W1 | 代码 | 已建 | b6effcf | — |
| `T4-01.5` | `apps/adapter/app/db/repositories/usage/__init__.py` | T4-01.5 | W1 | 代码 | 已建 | 6d82160 | — |
| `T4-01.5` | `deploy/migrations/0006_*.sql` | T4-01.5 | W1 | 迁移 | 已建 | 2699897 | — |
| `T4-01.5` | `deploy/migrations/0007_*.sql` | T4-01.5 | W1 | 迁移 | 已建 | 2699897 | — |
| `T4-01.5` | `deploy/migrations/rollback/0006_*.down.sql` | T4-01.5 | W1 | 迁移 | 已建 | 2699897 | — |
| `T4-01.5` | `deploy/migrations/rollback/0007_*.down.sql` | T4-01.5 | W1 | 迁移 | 已建 | 2699897 | — |
| `T4-02.1` | `apps/adapter/tests/integration/test_conversation_rls_pg.py` | T4-02.1 | W1 | 测试 | 已建 | d4ba2c0 | — |
| `T4-02.1` | `deploy/migrations/0008_conversation_message.sql` | T4-02.1 | W1 | 迁移 | 已建 | d4ba2c0 | — |
| `T4-02.1` | `deploy/migrations/rollback/0008_*.down.sql` | T4-02.1 | W1 | 迁移 | 已建 | d4ba2c0 | — |
| `T4-02.2` | `apps/adapter/app/db/repositories/conversation/__init__.py` | T4-02.2 | W1 | 代码 | 已建 | 70844a8 | — |
| `T4-02.2` | `apps/adapter/app/schemas/conversation.py` | T4-02.2 | W1 | 代码 | 已建 | d4ba2c0 | — |
| `T4-02.2` | `apps/adapter/tests/unit/test_conversation_repo.py` | T4-02.2 | W1 | 测试 | 已建 | d4ba2c0 | — |
| `T4-02.3` | `apps/adapter/app/api/v1/model.py` | T4-02.3 | W2 | 代码 | 已建 | f46544f | — |
| `T4-02.3` | `apps/adapter/app/business/context_assembler.py` | T4-02.3 | W2 | 代码 | 已建 | dc9ec39 | — |
| `T4-02.3` | `apps/adapter/app/schemas/model.py` | T4-02.3 | W2 | 代码 | 已建 | f46544f | — |
| `T4-02.3` | `apps/adapter/app/services/model_service.py` | T4-02.3 | W2 | 代码 | 已建 | f46544f | — |
| `T4-02.3` | `apps/adapter/tests/integration/test_message_persistence.py` | T4-02.3 | W2 | 测试 | 已建 | 9617800 | — |
| `T4-02.3` | `apps/adapter/tests/unit/test_context_assembler.py` | T4-02.3 | W2 | 测试 | 已建 | dc9ec39 | — |
| `T4-02.4` | `apps/adapter/app/business/conversation_service.py` | T4-02.4 | W2 | 代码 | 已建 | dc9ec39 | — |
| `T4-02.5` | `apps/adapter/app/api/v1/conversation.py` | T4-02.5 | W2 | 代码 | 已建 | e3fda3b | — |
| `T4-02.5` | `apps/adapter/tests/integration/test_conversation_endpoints.py` | T4-02.5 | W2 | 测试 | 已建 | e3fda3b | — |
| `T4-02.6` | `apps/web/src/components/chat/*` | T4-02.6 | W2 | 代码 | 已建 | a26b5c1 | — |
| `T4-02.6` | `apps/web/src/components/chat/conversation-sidebar.tsx` | T4-02.6 | W2 | 代码 | 已建 | f46544f | — |
| `T4-02.6` | `apps/web/src/hooks/use-conversation-store.ts` | T4-02.6 | W2 | 代码 | 已建 | f46544f | — |
| `T4-02.6` | `apps/web/src/lib/api-client.ts` | T4-02.6 | W2 | 代码 | 已建 | f46544f | — |
| `T4-02.6` | `apps/web/src/lib/types.ts` | T4-02.6 | W2 | 代码 | 已建 | f46544f | — |
| `T4-03.1` | `deploy/migrations/0009_user_memory.sql` | T4-03.1 | W2 | 迁移 | 已建 | 4726264 | — |
| `T4-03.1` | `deploy/migrations/0010_background_job.sql` | T4-03.1 | W2 | 迁移 | 已建 | 4726264 | — |
| `T4-03.1` | `deploy/migrations/rollback/0009_*.down.sql` | T4-03.1 | W2 | 迁移 | 已建 | 4726264 | — |
| `T4-03.1` | `deploy/migrations/rollback/0010_*.down.sql` | T4-03.1 | W2 | 迁移 | 已建 | 4726264 | — |
| `T4-03.2` | `apps/adapter/app/db/repositories/job_repo.py` | T4-03.2 | W2 | 代码 | 已建 | 79250db | — |
| `T4-03.2` | `apps/adapter/app/workers/job_runner.py` | T4-03.2 | W2 | 代码 | 已建 | 79250db | — |
| `T4-03.2` | `apps/adapter/tests/unit/test_job_runner.py` | T4-03.2 | W2 | 测试 | 已建 | 79250db | — |
| `T4-03.3` | `apps/adapter/app/business/memory_service.py` | T4-03.3 | W3 | 代码 | 已建 | 79250db | — |
| `T4-03.3` | `apps/adapter/app/db/repositories/memory/__init__.py` | T4-03.3 | W3 | 代码 | 已建 | 4e8c36c | — |
| `T4-03.3` | `apps/adapter/app/services/memory_extractor.py` | T4-03.3 | W3 | 代码 | 已建 | 79250db | — |
| `T4-03.3` | `apps/adapter/tests/e2e/test_memory_recall.py` | T4-03.3 | W3 | 测试 | 待建 | — | — |
| `T4-03.3` | `apps/adapter/tests/unit/test_memory_extract.py` | T4-03.3 | W3 | 测试 | 已建 | 79250db | — |
| `T4-03.4` | `apps/adapter/tests/unit/test_memory_recall_degrade.py` | T4-03.4 | W3 | 测试 | 已建 | 98d726d | — |
| `T4-03.5` | `apps/adapter/app/api/v1/memory.py` | T4-03.5 | W3 | 代码 | 已建 | 98d726d | — |
| `T4-03.5` | `apps/adapter/tests/integration/test_memory_endpoints.py` | T4-03.5 | W3 | 测试 | 已建 | 98d726d | — |
| `T4-03.6` | `apps/web/src/app/(main)/profile/memories/page.tsx` | T4-03.6 | W3 | 代码 | 已建 | 3f234fb | — |
| `T4-03.6` | `apps/web/src/components/memory/*` | T4-03.6 | W3 | 代码 | 已建 | 3f234fb | — |
| `T4-03.7` | `apps/web/package.json` | T4-03.7 | W3 | 代码 | 已建 | f46544f | — |
| `T4-03.7` | `apps/web/scripts/check-banned-terms.mjs` | T4-03.7 | W3 | 代码 | 已建 | 45f9d3a | — |
| `T4-03.7` | `docs/prd-phase2-increment.md` | T4-03.7 | W3 | 文档 | 已建 | c0dc7ee | — |
| `T4-04.1` | `apps/adapter/app/registry/loader.py` | T4-04.1 | W3 | 代码 | 已建 | f46544f | — |
| `T4-04.1` | `apps/adapter/tests/unit/test_profile_routing_contract.py` | T4-04.1 | W3 | 测试 | 已建 | 8e9f43f | — |
| `T4-04.2` | `apps/adapter/app/business/usage_service.py` | T4-04.2 | W3 | 代码 | 已建 | 6eca802 | — |
| `T4-04.2` | `apps/adapter/app/db/schema_guard.py` | T4-04.2 | W3 | 代码 | 已建 | 8e9f43f | — |
| `T4-04.2` | `apps/adapter/tests/integration/test_quota_hardstop.py` | T4-04.2 | W3 | 测试 | 待建 | — | — |
| `T4-04.3` | `apps/adapter/app/db/repositories/budget_repo.py` | T4-04.3 | W3 | 代码 | 已建 | 8e9f43f | — |
| `T4-04.3` | `apps/adapter/tests/integration/test_tenant_budget.py` | T4-04.3 | W3 | 测试 | 待建 | — | — |
| `T4-04.3` | `deploy/migrations/0011_tenant_budget.sql` | T4-04.3 | W3 | 迁移 | 已建 | 8e9f43f | — |
| `T4-04.3` | `deploy/migrations/rollback/0011_*.down.sql` | T4-04.3 | W3 | 迁移 | 已建 | 8e9f43f | — |
| `T4-04.4` | `apps/adapter/app/business/task_service.py` | T4-04.4 | W3 | 代码 | 已建 | b6effcf | — |
| `T4-04.4` | `apps/adapter/tests/integration/test_task_timeout_lock.py` | T4-04.4 | W3 | 测试 | 待建 | — | — |
| `T4-04.5` | `apps/adapter/app/core/permission_whitelist.py` | T4-04.5 | W3 | 代码 | 待建 | — | — |
| `T4-04.5` | `apps/adapter/tests/unit/test_permission_whitelist.py` | T4-04.5 | W3 | 测试 | 待建 | — | — |
| `T4-04.5` | `docs/security/permission-whitelist.lock` | T4-04.5 | W3 | 代码 | 已建 | 2c85efb | — |
| `T4-04.6` | `apps/adapter/tests/security/test_authn_bypass.py` | T4-04.6 | W3 | 测试 | 待建 | — | — |
| `T4-04.6` | `apps/adapter/tests/unit/test_cross_tenant.py` | T4-04.6 | W3 | 测试 | 待建 | — | — |
| `T4-04.6` | `apps/adapter/tests/unit/test_info_leak.py` | T4-04.6 | W3 | 测试 | 待建 | — | — |
| `T4-04.6` | `apps/adapter/tests/unit/test_object_privilege.py` | T4-04.6 | W3 | 测试 | 待建 | — | — |
| `T4-04.6` | `apps/adapter/tests/unit/test_quota_abuse.py` | T4-04.6 | W3 | 测试 | 待建 | — | — |
| `T4-05.1` | `apps/adapter/app/core/metrics.py` | T4-05.1 | W3 | 代码 | 已建 | 98d726d | — |
| `T4-05.1` | `apps/adapter/tests/unit/test_metrics_labels.py` | T4-05.1 | W3 | 测试 | 待建 | — | — |
| `T4-05.2` | `deploy/alert.rules.yml` | T4-05.2 | W3 | 代码 | 已建 | 2a3a4fc | — |
| `T4-05.2` | `deploy/docker-compose.yml` | T4-05.2 | W3 | 代码 | 已建 | f46544f | — |
| `T4-05.2` | `deploy/prometheus.yml` | T4-05.2 | W3 | 代码 | 已建 | f46544f | — |
| `T4-05.3` | `apps/adapter/app/api/v1/tenant_data.py` | T4-05.3 | W4 | 代码 | 已建 | f30c95e | — |
| `T4-05.3` | `apps/adapter/app/business/tenant_data_service.py` | T4-05.3 | W4 | 代码 | 已建 | f30c95e | — |
| `T4-05.3` | `apps/adapter/app/workers/tasks/retention_cleanup.py` | T4-05.3 | W4 | 代码 | 已建 | f30c95e | — |
| `T4-05.3` | `apps/adapter/app/workers/tasks/tenant_export.py` | T4-05.3 | W4 | 代码 | 待建 | — | — |
| `T4-05.3` | `apps/adapter/tests/integration/test_retention_export_delete.py` | T4-05.3 | W4 | 测试 | 已建 | f30c95e | — |
| `T4-05.3` | `sql/retention-audit.sql` | T4-05.3 | W4 | 代码 | 已建 | f30c95e | — |
| `T4-05.4` | `deploy/.env.lite.example` | T4-05.4 | W4 | 代码 | 已建 | 0d40a18 | D-28 核实：已建（11,151 B / 10.9 KiB） |
| `T4-05.4` | `deploy/docker-compose.lite.yml` | T4-05.4 | W4 | 代码 | 已建 | 0d40a18 | D-28 核实：已建（31,615 B / 30.9 KiB） |
| `T4-05.4` | `deploy/nginx/lite.conf` | T4-05.4 | W4 | 代码 | 已建 | 0d40a18 | D-28 核实：已建（6,015 B / 5.9 KiB） |
| `T4-05.4` | `deploy/nginx/nginx.conf` | T4-05.4 | W4 | 代码 | 已建 | 0d40a18 | D-28 核实：已建（2,228 B / 2.2 KiB） |
| `T4-05.4` | `deploy/validate_lite_up.sh` | T4-05.4 | W4 | 代码 | 已建 | 0d40a18 | D-28 核实：已建（35,098 B / 34.3 KiB） |
| `T4-05.5` | `deploy/backup.sh` | T4-05.5 | W4 | 代码 | 已建 | 5d1f29b | D-28 核实：已建（19,513 B / 19.1 KiB） |
| `T4-05.5` | `deploy/install.sh` | T4-05.5 | W4 | 代码 | 已建 | e5f031a | D-28 核实：已建（45,701 B / 44.6 KiB） |
| `T4-05.5` | `deploy/restore.sh` | T4-05.5 | W4 | 代码 | 已建 | 5d1f29b | D-28 核实：已建（19,086 B / 18.6 KiB） |
| `T4-05.5` | `deploy/rollback.sh` | T4-05.5 | W4 | 代码 | 待建 | — | 确属缺失：归属 W1（回滚前置能力），任务表登记于 T4-05.5（W4） |
| `T4-05.5` | `deploy/upgrade.sh` | T4-05.5 | W4 | 代码 | 待建 | — | 确属缺失：归属 W1（升级前置能力），任务表登记于 T4-05.5（W4） |
| `T4-05.5` | `deploy/validate_packaging.py` | T4-05.5 | W4 | 代码 | 已建 | d55a959 | — |
| `T4-05.5` | `sql/enable-vector-extension.sql` | T4-05.5 | W4 | 代码 | 已建 | 364b8cc | D-28 核实：已建（5,462 B / 5.3 KiB） |
| `T4-05.6` | `.github/workflows/sbom.yml` | T4-05.6 | W4 | 代码 | 待建 | — | — |
| `T4-05.6` | `apps/web/src/app/(main)/about/page.tsx` | T4-05.6 | W4 | 代码 | 待建 | — | — |
| `T4-05.6` | `deploy/sbom/generate.py` | T4-05.6 | W4 | 代码 | 待建 | — | — |
| `T4-05.6` | `deploy/validate_offline_pkgs.py` | T4-05.6 | W4 | 代码 | 待建 | — | — |
| `T4-05.6` | `docs/compliance/dpa-template.md` | T4-05.6 | W4 | 文档 | 待建 | — | — |
| `T4-05.6` | `docs/compliance/privacy-policy.md` | T4-05.6 | W4 | 文档 | 待建 | — | — |
| `T4-05.6` | `docs/compliance/security-whitepaper.md` | T4-05.6 | W4 | 文档 | 待建 | — | — |
| `T4-05.7` | `docs/deployment-guide.md` | T4-05.7 | W4 | 文档 | 待建 | — | — |
| `T4-05.7` | `docs/key-rotation-checklist.md` | T4-05.7 | W4 | 文档 | 待建 | — | — |
| `T4-05.7` | `docs/perf-disclosure.md` | T4-05.7 | W4 | 文档 | 待建 | — | — |
| `T4-05.7` | `docs/user-guide.md` | T4-05.7 | W4 | 文档 | 待建 | — | — |
| `T4-05.7` | `qa/goldset/*` | T4-05.7 | W4 | 代码 | 待建 | — | — |
| `T4-05.7` | `scripts/bench/*.py` | T4-05.7 | W4 | 代码 | 待建 | — | 仅有 scripts/bench/lite-probe/probe.py（P4-35 轻量探针）；T4-05.7 要求的压测脚本未建（待确认） |
