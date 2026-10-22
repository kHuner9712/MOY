-- ===========================================================================
-- 0007 down：撤销 `usage_event` 四个计量维度
--
-- ⚠ 会丢数据：profile / provider / prompt_tokens / completion_tokens 一旦删除，
--   「按档位」「真实 token / 毛利」两类看板将永久失去历史可比性（新行会重新从
--   默认值开始）。回滚前请 `pg_dump -t usage_event` 留档。
--
-- 用法（由 `app.db.migrate --rollback 0007_usage_event_dimensions` 调用）：
--   在同一事务 + 迁移锁内执行，成功后由 migrate.py 删除台账行，不自动级联。
-- 幂等性：逐列 `DROP COLUMN IF EXISTS`。
-- ===========================================================================

DO $down$
BEGIN
    IF to_regclass('public.usage_event') IS NULL THEN
        RAISE NOTICE '0007 down: usage_event 不存在，跳过';
        RETURN;
    END IF;

    BEGIN
        ALTER TABLE usage_event DROP COLUMN IF EXISTS completion_tokens;
        ALTER TABLE usage_event DROP COLUMN IF EXISTS prompt_tokens;
        ALTER TABLE usage_event DROP COLUMN IF EXISTS provider;
        ALTER TABLE usage_event DROP COLUMN IF EXISTS profile;
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE EXCEPTION
                '0007 down 需要 drop 4 列（usage_event 计量维度），但当前角色不是表属主。'
                '请由 DBA 以表属主身份执行，或直接以超级用户运行本文件。';
    END;

    RAISE NOTICE '0007 down: usage_event 四列已删除';
END $down$;
