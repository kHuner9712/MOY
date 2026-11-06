-- ===========================================================================
-- 0016 操作记录导出索引（矩阵第 4 项，2026-11-06）
--
-- 只加一个索引 + 复核 RLS 未被人为削弱，**不改表结构、不改数据**。
--
-- idx_audit_log_tenant_time_id (tenant_id, occurred_at ASC, id ASC)
--   导出用键集分页：`WHERE tenant_id = ? AND (occurred_at, id) > (?, ?)`
--   `ORDER BY occurred_at ASC, id ASC`。既有 `idx_audit_log_tenant_time`
--   只覆盖 `(tenant_id, occurred_at DESC)`，同一时间戳的多行仍需额外排序；
--   补上 `id` 后整个排序退化为索引顺序扫描，导出代价与页数无关。
--
-- RLS 留存校验：导出是"大范围读"，本迁移显式 ENABLE + FORCE 并复核
-- `p_tenant_isolation` 策略存在。**不新增策略、不放宽匹配**；若策略缺失，
-- 按既有的同一条表达式重建（与 `init-schema-phase2.sql` 第 14 节一致）。
--
-- 幂等性：索引与策略均按存在性守卫；`ENABLE` / `FORCE` 本身可重复执行。
--
-- 回滚：`rollback/0016_audit_export.down.sql`（只删本迁移创建的索引，
-- 不触碰 RLS —— RLS 是阶段二既有结构，不属于本迁移的私有产物）。
-- ===========================================================================

DO $audit_export$
DECLARE
    index_exists BOOLEAN;
    policy_exists BOOLEAN;
BEGIN
    index_exists := to_regclass('public.idx_audit_log_tenant_time_id') IS NOT NULL;
    IF NOT index_exists THEN
        BEGIN
            EXECUTE $ddl$
                CREATE INDEX idx_audit_log_tenant_time_id
                    ON audit_log (tenant_id, occurred_at, id)
            $ddl$;
        EXCEPTION
            WHEN insufficient_privilege THEN
                RAISE EXCEPTION
                    '0016 建索引需要 audit_log 的属主权限，请由表属主执行。';
        END;
        RAISE NOTICE '0016: idx_audit_log_tenant_time_id 就绪';
    ELSE
        RAISE NOTICE '0016: idx_audit_log_tenant_time_id 已存在，零 DDL';
    END IF;

    BEGIN
        EXECUTE 'ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE audit_log FORCE ROW LEVEL SECURITY';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE EXCEPTION
                '0016 复核行级安全需要 audit_log 的属主权限，请由表属主执行。';
    END;

    SELECT EXISTS (
               SELECT 1
                 FROM pg_policies p
                WHERE p.schemaname = 'public'
                  AND p.tablename = 'audit_log'
                  AND p.policyname = 'p_tenant_isolation'
           ) INTO policy_exists;
    IF NOT policy_exists THEN
        EXECUTE $ddl$
            CREATE POLICY p_tenant_isolation ON audit_log
            USING (tenant_id = current_setting('app.tenant_id', TRUE)
                OR current_setting('app.tenant_id', TRUE) = '__platform__')
            WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE)
                OR current_setting('app.tenant_id', TRUE) = '__platform__')
        $ddl$;
        RAISE NOTICE '0016: audit_log 的 p_tenant_isolation 已重建';
    ELSE
        RAISE NOTICE '0016: audit_log 的 p_tenant_isolation 已存在（RLS 留存校验通过）';
    END IF;
END $audit_export$;
