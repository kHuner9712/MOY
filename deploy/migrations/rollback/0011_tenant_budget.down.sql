-- ===========================================================================
-- 0011 down：撤销租户月度预算表（**删表 = 丢已配置的额度**）
--
-- ⚠ 回滚前先导出：SELECT * FROM tenant_budget;（否则要重新问客户要一遍额度）
-- 幂等性：`IF EXISTS`。
-- ===========================================================================

DO $down$
BEGIN
    BEGIN
        DROP TABLE IF EXISTS tenant_budget;
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE EXCEPTION
                '0011 down 需要删除 tenant_budget，但当前角色不是表属主。请由 DBA 执行。';
    END;
    RAISE NOTICE '0011 down: tenant_budget 已删除';
END $down$;
