-- ===========================================================================
-- 审计日志保留期清理（阶段四 T4-05.3，架构 §10.4 / F-9）
--
-- ⚠ 本文件**只能由库属主（officeai）或 DBA 执行**，不能用应用角色 officeai_app。
--
-- 为什么应用侧清不掉审计表（不是缺陷，是设计）：
--   deploy/init-app-role.sql 第 4 节对 officeai_app 执行了
--     REVOKE UPDATE, DELETE ON audit_log FROM officeai_app;
--   这是阶段二"审计只增不改"的正确设计（审计可篡改 = 审计无意义）。
--   因此 `retention_cleanup` 后台任务在日志里输出
--     audit_log=skipped(no privilege)
--   并把这个数字交给本脚本。**那一行是验收硬判据**（架构 §10.4）：它是
--   "应用侧知道自己没权限"的唯一证据，比"清理任务跑完了"更能证明没漏清。
--
-- 保留期口径：180 天（`OA_RETENTION_AUDIT_DAYS`，与 Settings 同名同默认值）。
-- 改本脚本的天数时必须同步改 .env 里的 OA_RETENTION_AUDIT_DAYS，否则部署手册
-- 写的"审计保留 180 天"与实际执行不符（PRD P4-06 ④ 口径一致要求）。
--
-- 执行：
--   psql "$SUPERUSER_DSN" -v days=180 -f sql/retention-audit.sql
--   容器内：docker compose exec -T postgres psql -U officeai -d officeai \
--             -v days=180 -f /sql/retention-audit.sql
--
-- ⚠ 单次只删 10 万行，**请重复执行直到"本次删除"为 0**。
--   一次删几十万行会长时间持锁并撑爆 WAL，在线业务直接感知为"数据库卡了"。
--   分批到脚本层（而不是 plpgsql 循环）是因为 DO 块里的 COMMIT 在 PG 里不允许，
--   把"一个事务删光"换个写法伪装成分批没有意义。
--
-- ⚠ DELETE 不等于磁盘立刻变小：行只是变成死元组，空间由 autovacuum 回收。
--   需要立刻还盘时另跑（同样需要表属主权限）：
--     VACUUM (VERBOSE, ANALYZE) audit_log;
--
-- 回滚：审计行删除**不可回滚**（这就是"保留期"的定义）。执行前请备份：
--   pg_dump -U officeai -d officeai -t audit_log -f audit_log_before_purge.sql
-- ===========================================================================

\set ON_ERROR_STOP on

-- 默认 180 天；可用 -v days=NN 覆盖
\if :{?days}
\else
  \set days 180
\endif

\echo '== 清理前：待删除行数 =='
SELECT count(*) AS rows_to_delete
  FROM audit_log
 WHERE occurred_at < now() - make_interval(days => :days);

\echo '== 本次删除（上限 100000 行）=='
WITH target AS (
    SELECT id
      FROM audit_log
     WHERE occurred_at < now() - make_interval(days => :days)
     ORDER BY occurred_at
     LIMIT 100000
)
DELETE FROM audit_log
 WHERE id IN (SELECT id FROM target);

\echo '== 清理后：剩余行数（未过期行不受影响）=='
SELECT count(*) AS rows_remaining,
       count(*) FILTER (WHERE occurred_at < now() - make_interval(days => :days))
           AS rows_still_expired
  FROM audit_log;
