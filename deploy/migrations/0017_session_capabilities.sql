-- ===========================================================================
-- 0017 会话级能力快照（矩阵 B6，2026-11-07）
--
-- 只给 `plugin_task` 加一列 JSONB，**不改数据、不改策略匹配**：
--
--   session_capabilities  `{}` = 未提交能力声明（历史路径继承调用者权限，
--                         已登记为待收敛项，不是「默认全开」）；
--                        非空 = 本次任务声明的工具/资源类型/产物格式，
--                         执行期按此准入。列写入后不再更新（重试复制、不放大）。
--
-- 为什么放在任务表而不是请求头：异步执行与重试发生在没有 HTTP 上下文的
-- worker 里，声明必须随任务持久化，否则执行期只能退回按角色粗粒度放行。
--
-- RLS 留存校验：本迁移不改隔离能力，只复核 `p_tenant_isolation` 仍在；
-- 缺失时按阶段三既有表达式重建（与 `init-schema-phase3.sql` 第 10 节一致）。
--
-- 幂等性：表/列/策略全部按存在性守卫；重复执行零 DDL。
--
-- 回滚：`rollback/0017_session_capabilities.down.sql` 只删本列，不碰 RLS。
-- ===========================================================================

DO $session_capabilities$
DECLARE
    column_exists BOOLEAN;
    policy_exists BOOLEAN;
BEGIN
    IF to_regclass('public.plugin_task') IS NULL THEN
        RAISE NOTICE '0017: plugin_task 不存在（未启用阶段三插件任务），跳过';
        RETURN;
    END IF;

    SELECT EXISTS (
               SELECT 1
                 FROM information_schema.columns c
                WHERE c.table_schema = 'public'
                  AND c.table_name = 'plugin_task'
                  AND c.column_name = 'session_capabilities'
           ) INTO column_exists;
    IF NOT column_exists THEN
        BEGIN
            EXECUTE $ddl$
                ALTER TABLE plugin_task
                    ADD COLUMN session_capabilities JSONB NOT NULL DEFAULT '{}'::jsonb
            $ddl$;
        EXCEPTION
            WHEN insufficient_privilege THEN
                RAISE EXCEPTION
                    '0017 加列需要 plugin_task 的属主权限，请由表属主执行。';
        END;
        RAISE NOTICE '0017: plugin_task.session_capabilities 已添加';
    ELSE
        RAISE NOTICE '0017: plugin_task.session_capabilities 已存在，零 DDL';
    END IF;

    -- 补后自检：加列没生效就 fail loud，避免应用侧写入静默丢失。
    SELECT EXISTS (
               SELECT 1
                 FROM information_schema.columns c
                WHERE c.table_schema = 'public'
                  AND c.table_name = 'plugin_task'
                  AND c.column_name = 'session_capabilities'
           ) INTO column_exists;
    IF NOT column_exists THEN
        RAISE EXCEPTION '0017: session_capabilities 添加后仍不存在，终止迁移';
    END IF;

    BEGIN
        EXECUTE 'ALTER TABLE plugin_task ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE plugin_task FORCE ROW LEVEL SECURITY';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE EXCEPTION
                '0017 复核行级安全需要 plugin_task 的属主权限，请由表属主执行。';
    END;

    SELECT EXISTS (
               SELECT 1
                 FROM pg_policies p
                WHERE p.schemaname = 'public'
                  AND p.tablename = 'plugin_task'
                  AND p.policyname = 'p_tenant_isolation'
           ) INTO policy_exists;
    IF NOT policy_exists THEN
        EXECUTE $ddl$
            CREATE POLICY p_tenant_isolation ON plugin_task
            USING (tenant_id = current_setting('app.tenant_id', TRUE)
                OR current_setting('app.tenant_id', TRUE) = '__platform__')
            WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE)
                OR current_setting('app.tenant_id', TRUE) = '__platform__')
        $ddl$;
        RAISE NOTICE '0017: plugin_task 的 p_tenant_isolation 已重建';
    ELSE
        RAISE NOTICE '0017: plugin_task 的 p_tenant_isolation 已存在（RLS 留存校验通过）';
    END IF;
END $session_capabilities$;
