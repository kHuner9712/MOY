-- ===========================================================================
-- 全库清空（私有化兜底，阶段四 T4-05.3，PRD P4-05 ⑤）
--
-- ⚠⚠ 这是**不可逆**操作。执行前必须先做逻辑备份，否则没有回滚路径：
--      pg_dump -U officeai -d officeai -Fc -f /backup/before-purge_$(date +%F_%H%M).dump
--
-- 为什么私有化要把"删除租户数据"交给 DBA 而不是做成按钮：
--   单租户部署下"删除企业数据"实质就是"清空全库"。让终端用户点一下清库，
--   是事故不是功能。因此 TD2 在 `deploy_mode=private` 下直接返回业务错误
--   （`当前部署形态不支持在线导出或删除，请联系运维执行全库脚本`）。
--
-- 与 TD2 在线删除的**唯一实质差别**：
--   TD2 用应用角色（officeai_app）执行，对 `audit_log` 无 DELETE 权限，
--   所以审计行永远留给 DBA（响应里 `audit_pending_dba: N`）。
--   本文以**库属主**执行，`audit_log` 可以一并清掉——这也是为什么
--   "审计保留期"这条合规义务必须写进合同：谁执行决定了能不能真的删。
--
-- 执行（必须显式确认，防止复制粘贴直接回车）：
--   psql "$SUPERUSER_DSN" -v confirm=YES-PURGE-ALL -f sql/full-purge.sql
--
-- 保留项（**不清**，清了系统起不来）：
--   schema_migration（迁移台账）、permission_catalog（权限点目录）、
--   billing_plan（套餐目录）、plugin_catalog / plugin_version（插件目录）、
--   ops_settings（平台配置）
--
-- 回滚：只能 restore 备份。
--   pg_restore -U officeai -d officeai -c /backup/before-purge_xxx.dump
-- ===========================================================================

\set ON_ERROR_STOP on

\if :{?confirm}
\else
  \set confirm no
\endif

-- 只接受精确的确认串；`\gset` 拿到的 t/f 才能直接喂给 `\if`
SELECT :'confirm' = 'YES-PURGE-ALL' AS purge_ok \gset

\if :purge_ok
\else
  \echo '⚠ 未通过确认校验，已中止（需要 -v confirm=YES-PURGE-ALL）。未执行任何删除。'
  \q
\endif

\echo '== 清空前行数 =='
SELECT relname AS table_name, n_live_tup AS approx_rows
  FROM pg_stat_user_tables
 WHERE schemaname = 'public'
 ORDER BY relname;

BEGIN;

-- 数据权利相关的全部业务表。顺序按"先子后父"书写（本库**刻意不建外键**，
-- 顺序不影响正确性，但能让日志里的人看得懂发生了什么）。
TRUNCATE TABLE
    message,
    conversation,
    memory_embedding,
    user_memory,
    user_memory_setting,
    plugin_artifact,
    plugin_task_step,
    plugin_task,
    plugin_grant,
    plugin_installation,
    plugin_capability_policy,
    plugin_review,
    knowledge_base_grant,
    segment_map,
    document_meta,
    knowledge_base_meta,
    department_member,
    department,
    member_role,
    role,
    role_permission,
    tool_execution,
    tool_connection,
    credential_store,
    integration_outbox,
    payment_callback_log,
    usage_event,
    usage_monthly,
    billing_invoice,
    billing_order,
    billing_subscription,
    tenant_budget,
    background_job,
    audit_log,
    tenant
RESTART IDENTITY;

COMMIT;

\echo '== 清空后行数（上表应全部为 0，保留项仍有数据）=='
SELECT relname AS table_name, n_live_tup AS approx_rows
  FROM pg_stat_user_tables
 WHERE schemaname = 'public'
 ORDER BY relname;

\echo '== 空间回收（DELETE/TRUNCATE 后空间由 VACUUM 归还，不是立刻变小）=='
VACUUM (VERBOSE, ANALYZE);
