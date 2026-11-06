-- ===========================================================================
-- 0016 回滚：删除操作记录导出的键集分页索引
--
-- 只删本迁移创建的 `idx_audit_log_tenant_time_id`。
-- **不删除** RLS 策略、不改 `ENABLE/FORCE`：那是阶段二的既有结构，
-- 回滚一个查询优化不应该让隔离能力倒退。
-- ===========================================================================

DO $audit_export_down$
BEGIN
    IF to_regclass('public.idx_audit_log_tenant_time_id') IS NULL THEN
        RAISE NOTICE '0016 回滚: idx_audit_log_tenant_time_id 不存在，跳过';
    ELSE
        EXECUTE 'DROP INDEX IF EXISTS idx_audit_log_tenant_time_id';
        RAISE NOTICE '0016 回滚: idx_audit_log_tenant_time_id 已删除';
    END IF;
END $audit_export_down$;
