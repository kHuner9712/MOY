-- ===========================================================================
-- 单租户数据导出（DBA 兜底，阶段四 T4-05.3，PRD P4-05 ③）
--
-- 用途：TD1 在线导出（`POST /api/v1/ops/tenant-data/export`）不可用时的手工通道——
--   私有化部署、数据量超过 20 万行上限、或客户 IT 要求"DBA 直连数据库取数"。
--   在线导出与本文**口径一致**（同样 5 类 + 行数清单），差别只在：
--   在线导出会把包写进产物库并返回短期下载链接，这里落的是**执行机**上的 CSV。
--
-- 执行（`-v tenant` 必填，避免误导出全库；OUT 目录需先创建）：
--   mkdir -p /tmp/moy-export
--   psql "$SUPERUSER_DSN" -v tenant=tnt_abc -v out=/tmp/moy-export \
--        -f sql/tenant-export-manual.sql
--
-- ⚠ 为什么查询里写 `current_setting('moy.tenant')` 而不是 `:'tenant'`：
--   `\copy` 是 psql **客户端**命令，它**不展开 `:变量`**——写成
--   `\copy (SELECT ... WHERE tenant_id = :'tenant')` 会原样送给服务端并报
--   `syntax error at or near ":"`（实测）。因此先把变量搬进会话参数
--   （`set_config`，这一步是普通 SQL，`:'tenant'` 能正常展开），
--   再让 `\copy` 里的查询去读那个参数。
--
-- ⚠ 另外三个必须知道的点：
--   1. **以库属主执行会绕过 RLS**（超级用户 / BYPASSRLS 不受策略约束），所以每条
--      查询都显式带 `tenant_id = current_setting('moy.tenant')`——不要依赖 RLS 兜底；
--   2. 产物只导出**清单**，不含二进制正文：`plugin_artifact.content` 是 BYTEA，
--      混进 CSV 会让文件不可读且体积失控。单件下载走产物代理接口；
--   3. 记忆只导出 `user_memory`（用户可见内容）；`memory_embedding` 是派生向量、
--      `user_memory_setting` 是开关偏好，两者都不含用户撰写的内容。
--
-- 回滚：本脚本只读（只写文件），无需回滚。
-- ===========================================================================

\set ON_ERROR_STOP on

\if :{?tenant}
\else
  \echo '缺少 -v tenant=<tenant_id>，中止（避免误导出全库）'
  \q
\endif

\if :{?out}
\else
  \set out /tmp/moy-export
\endif

-- 变量搬进会话参数：之后的 `\copy` 查询统一读 current_setting('moy.tenant')
SELECT set_config('moy.tenant', :'tenant', false);

\echo '== 导出企业：' :tenant ' → 目录：' :out
\cd :out

-- 1) 操作记录
\copy (SELECT tenant_id, id, actor_id, actor_role, action, action_display_name, resource_type, resource_id, result, change_summary, ip_masked, trace_id, occurred_at FROM audit_log WHERE tenant_id = current_setting('moy.tenant') ORDER BY occurred_at, id) TO 'audit.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')

-- 2) 用量事件（含阶段四 0007 的四列 token 维度，账单可复算）
\copy (SELECT tenant_id, event_id, org_id, department_id, user_id, capability, resource_type, resource_id, units, period, source, profile, provider, prompt_tokens, completion_tokens, occurred_at FROM usage_event WHERE tenant_id = current_setting('moy.tenant') ORDER BY occurred_at, event_id) TO 'usage.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')

-- 3) 会话与消息（拆两个文件更利于人工核对）
\copy (SELECT tenant_id, conversation_id, user_id, title, archived, message_count, last_message_at, created_at, updated_at, deleted_at FROM conversation WHERE tenant_id = current_setting('moy.tenant') ORDER BY created_at, conversation_id) TO 'chat-conversation.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')
\copy (SELECT tenant_id, message_id, conversation_id, user_id, role, content, status, truncated, profile, created_at FROM message WHERE tenant_id = current_setting('moy.tenant') ORDER BY created_at, message_id) TO 'chat-message.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')

-- 4) 记忆
\copy (SELECT tenant_id, memory_id, user_id, category, content, confidence, source_conversation_id, superseded_by, hit_count, created_at, updated_at FROM user_memory WHERE tenant_id = current_setting('moy.tenant') ORDER BY created_at, memory_id) TO 'memory.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')

-- 5) 产物清单（**不含 content 二进制**）
\copy (SELECT tenant_id, artifact_id, task_id, step_id, output_key, type, name, size_bytes, content_sha256, content_type, version, expires_at, created_at FROM plugin_artifact WHERE tenant_id = current_setting('moy.tenant') ORDER BY created_at, artifact_id) TO 'artifact.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')

-- 6) 行数清单：交客户前请与各文件行数（减 1 行表头）比对
\echo '== 行数清单（CSV 行数 = 下列数字 + 1 表头）=='
SELECT 'audit' AS file, count(*) AS rows
  FROM audit_log WHERE tenant_id = current_setting('moy.tenant')
UNION ALL SELECT 'usage', count(*)
  FROM usage_event WHERE tenant_id = current_setting('moy.tenant')
UNION ALL SELECT 'chat-conversation', count(*)
  FROM conversation WHERE tenant_id = current_setting('moy.tenant')
UNION ALL SELECT 'chat-message', count(*)
  FROM message WHERE tenant_id = current_setting('moy.tenant')
UNION ALL SELECT 'memory', count(*)
  FROM user_memory WHERE tenant_id = current_setting('moy.tenant')
UNION ALL SELECT 'artifact', count(*)
  FROM plugin_artifact WHERE tenant_id = current_setting('moy.tenant')
ORDER BY file;
