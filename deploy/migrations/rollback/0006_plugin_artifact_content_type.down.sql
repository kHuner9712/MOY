-- ===========================================================================
-- 0006 down：撤销 `plugin_artifact.content_type`
--
-- ⚠ 会丢数据：这一列是阶段四起下载响应 MIME 的唯一来源。回滚后下载会退回
--   `text/plain; charset=utf-8`（P3-B22-04 复现），docx/xlsx 以文本 MIME 落盘。
--   执行前请确认这是有意回滚，而不是"迁移跑错了想重来"——重来用 `--apply` 即可。
--
-- 用法（由 `app.db.migrate --rollback 0006_plugin_artifact_content_type` 调用）：
--   在同一事务 + 迁移锁内执行，成功后由 migrate.py 删除台账行；
--   不自动级联——更晚的版本必须先单独回滚。
-- 幂等性：`IF EXISTS`，列已不在时是纯通知。
-- ===========================================================================

DO $down$
BEGIN
    IF to_regclass('public.plugin_artifact') IS NULL THEN
        RAISE NOTICE '0006 down: plugin_artifact 不存在，跳过';
        RETURN;
    END IF;
    IF NOT EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema = current_schema()
           AND table_name = 'plugin_artifact'
           AND column_name = 'content_type'
    ) THEN
        RAISE NOTICE '0006 down: content_type 已不存在，跳过';
        RETURN;
    END IF;

    BEGIN
        ALTER TABLE plugin_artifact DROP COLUMN IF EXISTS content_type;
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE EXCEPTION
                '0006 down 需要 drop 1 列（plugin_artifact.content_type），但当前角色不是表属主。'
                '请由 DBA 以表属主身份执行，或直接以超级用户运行本文件。';
    END;

    RAISE NOTICE '0006 down: plugin_artifact.content_type 已删除';
END $down$;
