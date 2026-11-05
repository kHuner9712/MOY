-- ===========================================================================
-- 0014 回滚：去掉任务计划元数据与 `waiting_plan` 状态
--
-- ⚠ **回滚会取消所有仍停在 `waiting_plan` 的任务**：旧 CHECK 不允许该状态，
--   不先处理会让 `ALTER TABLE ... ADD CONSTRAINT` 直接失败。取消是"未执行"的
--   真实结局（这些任务本来就没跑过任何步骤），不会丢产物。
--
-- 顺序：先取消 waiting_plan 行 → 重建旧 CHECK → 删部分索引 → 删三列。
-- ===========================================================================

DO $down$
DECLARE
    conname TEXT;
BEGIN
    UPDATE plugin_task
       SET status = 'cancelled',
           finished_at = now(),
           error_message = '计划功能已回滚，未执行'
     WHERE status = 'waiting_plan';

    SELECT c.conname INTO conname
      FROM pg_constraint c
     WHERE c.conrelid = 'plugin_task'::regclass
       AND c.contype = 'c'
       AND pg_get_constraintdef(c.oid) LIKE '%waiting_confirm%'
     LIMIT 1;

    IF conname IS NOT NULL THEN
        EXECUTE format('ALTER TABLE plugin_task DROP CONSTRAINT IF EXISTS %I', conname);
    END IF;

    ALTER TABLE plugin_task
        ADD CONSTRAINT plugin_task_status_check
        CHECK (status IN ('pending','running','queued','waiting_confirm',
                          'completed','failed','cancelled'));

    DROP INDEX IF EXISTS idx_task_plan_expiry;

    ALTER TABLE plugin_task
        DROP COLUMN IF EXISTS plan_confirmed_at,
        DROP COLUMN IF EXISTS plan_expires_at,
        DROP COLUMN IF EXISTS plan_version;

    RAISE NOTICE '0014: 已回滚（waiting_plan 任务已置 cancelled）';
END $down$;
