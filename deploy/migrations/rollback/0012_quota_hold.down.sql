-- ===========================================================================
-- 0012 down：撤销额度挂起标记
--
-- ⚠ 回滚会**丢掉当前的挂起状态**：恢复后所有被挂起的文档/任务会被当成正常行，
--   下一次判定（上传前 / 建任务前 / 读列表时）会重新判一遍，行为不会错，
--   但用户在回滚窗口内可能看到「原本说要等额度，忽然又开始跑了」。
--   回滚前建议先导出：SELECT tenant_id, document_id, hold_reason FROM document_meta
--   WHERE hold_reason <> '';（plugin_task 同理）
--
-- 幂等性：`IF EXISTS` + 列存在性守卫；重复执行零改动。
-- ===========================================================================

DO $down$
BEGIN
    BEGIN
        DROP INDEX IF EXISTS idx_document_meta_hold;
        DROP INDEX IF EXISTS idx_plugin_task_hold;

        ALTER TABLE IF EXISTS document_meta DROP COLUMN IF EXISTS hold_reason;
        ALTER TABLE IF EXISTS plugin_task DROP COLUMN IF EXISTS hold_reason;
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE EXCEPTION
                '0012 down 需要 DROP COLUMN document_meta.hold_reason / '
                'plugin_task.hold_reason，但当前角色不是表属主。请由 DBA 执行。';
    END;
    RAISE NOTICE '0012 down: hold_reason 列与部分索引已删除';
END $down$;
