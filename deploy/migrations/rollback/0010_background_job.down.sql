-- ===========================================================================
-- 0010 down：撤销 background_job（**删表 = 丢队列**）
--
-- ⚠ 回滚会丢弃未执行的任务（记忆抽取 / 租户导出 / 保留期清理）。
--   回滚前请确认没有 queued/running 行：SELECT status, count(*) FROM background_job GROUP BY 1;
-- 幂等性：`IF EXISTS`。
-- ===========================================================================

DO $down$
BEGIN
    BEGIN
        DROP TABLE IF EXISTS background_job;
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE EXCEPTION
                '0010 down 需要删除 background_job，但当前角色不是表属主。请由 DBA 执行。';
    END;
    RAISE NOTICE '0010 down: background_job 已删除';
END $down$;
