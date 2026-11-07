-- ===========================================================================
-- 0017 回滚：删除会话级能力快照列
--
-- 只删本迁移创建的 `plugin_task.session_capabilities`。
-- **不删除** RLS 策略、不改 `ENABLE/FORCE`：隔离能力是阶段三既有结构，
-- 回滚一个能力快照不应该让租户隔离倒退。
--
-- 注意：回滚后历史路径回到「继承调用者权限」；已提交能力声明的任务在
-- 应用侧读不到快照（按 `{}` 处理），因此回滚前应确认没有依赖声明准入的
-- 在途任务。
-- ===========================================================================

DO $session_capabilities_down$
BEGIN
    IF to_regclass('public.plugin_task') IS NULL THEN
        RAISE NOTICE '0017 回滚: plugin_task 不存在，跳过';
    ELSE
        EXECUTE 'ALTER TABLE plugin_task DROP COLUMN IF EXISTS session_capabilities';
        RAISE NOTICE '0017 回滚: plugin_task.session_capabilities 已删除';
    END IF;
END $session_capabilities_down$;
