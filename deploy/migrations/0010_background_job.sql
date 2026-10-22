-- ===========================================================================
-- 0010 后台任务表（阶段四 T4-03.1，架构 §3.4 / §2.3）
--
-- 为什么必须有这张表：阶段四同时有三个异步需求（记忆抽取、租户导出、保留期清理），
-- 而全仓无 celery/arq。PG 队列 + `FOR UPDATE SKIP LOCKED` 是标准作业队列，
-- 且**与业务数据同事务**（记忆写入与 job 状态一次提交，无双写）。
--
-- 三条关键约束（进实现会错的地方）：
--   1. **跨租户认领**：本表是 strict RLS，runner 认领时需
--      `SET LOCAL app.tenant_id='__platform__'`，拿到任务后**立即**切回 `job.tenant_id`；
--   2. **租约而非锁**：`locked_by` + `locked_until`，进程崩溃后由下一次认领回收
--      （`status='running' AND locked_until < now()`）；不引 Valkey 锁，避免第二套失效语义；
--   3. **按 kind 去重**：`uq_job_dedupe` 只对"同租户同 kind 同 dedupe_key 且未终结"生效，
--      防止同一会话被反复排队抽取。
--
-- 幂等性：`IF NOT EXISTS` + 存在性守卫。
-- 回滚：`rollback/0010_background_job.down.sql`（⚠ 删表，回滚前强制 pg_dump 备份）。
-- ===========================================================================

DO $guard$
DECLARE
    missing TEXT[];
BEGIN
    SELECT array_agg(name) INTO missing
      FROM (VALUES ('background_job')) AS wanted(name)
     WHERE to_regclass('public.' || name) IS NULL;

    IF missing IS NULL THEN
        RAISE NOTICE '0010: background_job 已存在（init-schema-phase4.sql 建的新库），零 DDL';
        RETURN;
    END IF;

    BEGIN
        EXECUTE $ddl$
            CREATE TABLE IF NOT EXISTS background_job (
                tenant_id    TEXT        NOT NULL,
                job_id       TEXT        NOT NULL,
                kind         TEXT        NOT NULL
                             CHECK (kind IN ('memory_extract','tenant_export',
                                             'retention_cleanup')),
                dedupe_key   TEXT        NOT NULL DEFAULT '',
                payload      JSONB       NOT NULL DEFAULT '{}'::jsonb,
                status       TEXT        NOT NULL DEFAULT 'queued'
                             CHECK (status IN ('queued','running','succeeded',
                                               'failed','cancelled')),
                attempts     SMALLINT    NOT NULL DEFAULT 0 CHECK (attempts >= 0),
                max_attempts SMALLINT    NOT NULL DEFAULT 3 CHECK (max_attempts >= 1),
                run_after    TIMESTAMPTZ NOT NULL DEFAULT now(),
                locked_by    TEXT        NOT NULL DEFAULT '',
                locked_until TIMESTAMPTZ,
                last_error   TEXT        NOT NULL DEFAULT '',
                created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                finished_at  TIMESTAMPTZ,
                PRIMARY KEY (tenant_id, job_id)
            )
        $ddl$;

        -- 到期索引：runner 的 SKIP LOCKED 认领走它
        EXECUTE $ddl$
            CREATE INDEX IF NOT EXISTS idx_job_due
                ON background_job (run_after) WHERE status IN ('queued','running')
        $ddl$;
        -- 同 kind 去重（未终结期）
        EXECUTE $ddl$
            CREATE UNIQUE INDEX IF NOT EXISTS uq_job_dedupe
                ON background_job (tenant_id, kind, dedupe_key)
                WHERE dedupe_key <> '' AND status IN ('queued','running')
        $ddl$;

        EXECUTE 'ALTER TABLE background_job ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE background_job FORCE ROW LEVEL SECURITY';
        EXECUTE 'DROP POLICY IF EXISTS p_tenant_isolation ON background_job';
        EXECUTE $ddl$
            CREATE POLICY p_tenant_isolation ON background_job
                USING (tenant_id = current_setting('app.tenant_id', TRUE)
                    OR current_setting('app.tenant_id', TRUE) = '__platform__')
                WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE)
                    OR current_setting('app.tenant_id', TRUE) = '__platform__')
        $ddl$;

        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'officeai_app') THEN
            EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON background_job TO officeai_app';
        END IF;
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE EXCEPTION
                '0010 需要创建 background_job 表，但当前角色不是库属主。请由 DBA 以表属主'
                '身份执行本迁移，或用超级用户重跑 deploy/init-schema-phase4.sql 后 --apply。';
    END;

    RAISE NOTICE '0010: background_job 已建成（含 RLS / 索引 / 授权）';
END $guard$;
