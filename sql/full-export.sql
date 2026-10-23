-- ===========================================================================
-- 全库导出（私有化兜底，阶段四 T4-05.3，PRD P4-05 ⑤）
--
-- 为什么私有化要单独一套：单租户部署下"导出企业数据"实质就是"导出整个库"，
-- 而这件事**不能做成一个按钮**——终端用户点一下就会把全量数据（含其他部门、
-- 含离职成员、含全部审计）打包下载。因此 TD1 在 `deploy_mode=private` 下直接
-- 返回业务错误（`当前部署形态不支持在线导出或删除，请联系运维执行全库脚本`），
-- 由运维按本文件执行。
--
-- 执行（分两步，**先逻辑备份再可读导出**）：
--   1) 逻辑备份（可 restore，唯一可用于回滚的产物）：
--        pg_dump -U officeai -d officeai -Fc -f /backup/officeai_$(date +%F).dump
--   2) 可读导出（交给客户/审计用，CSV 可直接进 Excel）：
--        mkdir -p /tmp/full-export
--        psql "$DSN" -v out=/tmp/full-export -f sql/full-export.sql
--
-- ⚠ 不要只做第 2 步：CSV 不是备份，恢复不了库。
--
-- 回滚：本脚本只读，无需回滚。
-- ===========================================================================

\set ON_ERROR_STOP on

\if :{?out}
\else
  \set out /tmp/moy-full-export
\endif

\echo '== 全库可读导出 → ' :out
\echo '== 提醒：业务数据之外，请同时用 pg_dump -Fc 做一份可恢复的逻辑备份'
\cd :out

-- 逐表导出。列清单与本仓 41 张表对齐；新增表时**必须回来补一行**，
-- 否则"全库导出"会静默少一张表（这类漏项在验收时最难发现）。
\copy (SELECT * FROM tenant ORDER BY tenant_id) TO 'tenant.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT * FROM audit_log ORDER BY id) TO 'audit_log.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT * FROM usage_event ORDER BY occurred_at, event_id) TO 'usage_event.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT * FROM usage_monthly ORDER BY period, scope_type, scope_id, capability) TO 'usage_monthly.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT * FROM conversation ORDER BY created_at, conversation_id) TO 'conversation.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT * FROM message ORDER BY created_at, message_id) TO 'message.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT * FROM user_memory ORDER BY created_at, memory_id) TO 'user_memory.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT * FROM user_memory_setting ORDER BY tenant_id, user_id) TO 'user_memory_setting.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
-- 向量列导出为文本（`embedding::text`）：CSV 里存 '[...]' 才能被人工或脚本复算
\copy (SELECT tenant_id, memory_id, model, dim, embedding::text AS embedding, updated_at FROM memory_embedding ORDER BY memory_id) TO 'memory_embedding.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
-- 产物**不含 content 二进制**（见 sql/tenant-export-manual.sql 说明）
\copy (SELECT tenant_id, artifact_id, task_id, step_id, output_key, type, name, inline_text, preview_text, external_url, storage_key, size_bytes, content_sha256, content_type, version, version_history, expires_at, created_at FROM plugin_artifact ORDER BY created_at, artifact_id) TO 'plugin_artifact.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT * FROM plugin_task ORDER BY tenant_id, task_id) TO 'plugin_task.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT * FROM plugin_task_step ORDER BY tenant_id, task_id, seq) TO 'plugin_task_step.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT * FROM plugin_catalog ORDER BY tenant_id, plugin_id) TO 'plugin_catalog.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT * FROM plugin_version ORDER BY tenant_id, plugin_id, version) TO 'plugin_version.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT * FROM plugin_installation ORDER BY tenant_id, plugin_id) TO 'plugin_installation.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT * FROM plugin_grant ORDER BY tenant_id, plugin_id, user_id) TO 'plugin_grant.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT * FROM plugin_review ORDER BY tenant_id, review_id) TO 'plugin_review.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT * FROM plugin_capability_policy ORDER BY tenant_id, capability) TO 'plugin_capability_policy.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT * FROM knowledge_base_meta ORDER BY tenant_id, base_id) TO 'knowledge_base_meta.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
-- ⚠ content_excerpt 是文档片段（可能含业务正文）；对外提供前请与客户确认脱敏口径
\copy (SELECT * FROM segment_map ORDER BY tenant_id, base_id, segment_key) TO 'segment_map.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT * FROM document_meta ORDER BY tenant_id, document_id) TO 'document_meta.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT * FROM knowledge_base_grant ORDER BY tenant_id, base_id, subject_type, subject_id) TO 'knowledge_base_grant.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT * FROM department ORDER BY tenant_id, department_id) TO 'department.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT * FROM department_member ORDER BY tenant_id, department_id, user_id) TO 'department_member.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT * FROM member_role ORDER BY tenant_id, user_id, role_id) TO 'member_role.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT * FROM role ORDER BY tenant_id, role_id) TO 'role.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT * FROM role_permission ORDER BY tenant_id, role_id, permission) TO 'role_permission.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT * FROM permission_catalog ORDER BY tenant_id, permission) TO 'permission_catalog.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
-- 凭据只导出密文与非敏感元数据；解密密钥在密钥管理系统里，**不在导出范围内**
\copy (SELECT tenant_id, credential_ref, provider, key_version, status, created_at, rotated_at FROM credential_store ORDER BY tenant_id, credential_ref) TO 'credential_store.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT tenant_id, execution_id, conn_id, tool_type, operation, status, error_code, error_message, result_summary, operator_user_id, duration_ms, executed_at FROM tool_execution ORDER BY executed_at, execution_id) TO 'tool_execution.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT tenant_id, conn_id, tool_type, scope, owner_user_id, org_id, status, key_version, expires_at, created_at, updated_at FROM tool_connection ORDER BY tenant_id, conn_id) TO 'tool_connection.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT * FROM integration_outbox ORDER BY created_at, outbox_id) TO 'integration_outbox.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT * FROM payment_callback_log ORDER BY received_at, callback_id) TO 'payment_callback_log.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT * FROM billing_subscription ORDER BY tenant_id) TO 'billing_subscription.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT * FROM billing_order ORDER BY created_at, order_id) TO 'billing_order.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT * FROM billing_invoice ORDER BY tenant_id, period, invoice_id) TO 'billing_invoice.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT * FROM billing_plan ORDER BY tenant_id, plan_code) TO 'billing_plan.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT * FROM tenant_budget ORDER BY tenant_id) TO 'tenant_budget.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT * FROM ops_settings ORDER BY tenant_id, setting_key) TO 'ops_settings.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT * FROM background_job ORDER BY tenant_id, created_at, job_id) TO 'background_job.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT * FROM schema_migration ORDER BY version) TO 'schema_migration.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')

\echo '== 逐表行数核对（与各 CSV 行数 - 1 表头 一致）=='
SELECT relname AS table_name, n_live_tup AS approx_rows
  FROM pg_stat_user_tables
 WHERE schemaname = 'public'
 ORDER BY relname;
