-- ===========================================================================
-- 0003 阶段三表结构「升级路径对账」（阶段三 B17，chore 性质：不做 DDL）
--
-- ⚠️ 定性说明（与 0002 同一体例，务必先读）：
--   阶段三的 9 张表由**基线** `deploy/init-schema-phase3.sql` 在 initdb 阶段创建
--   （compose 挂载为 `22-init-schema-phase3.sql`，排在 phase2 的 `20-` 之后）。
--   本迁移**不建表**，只对「已初始化库」做一次只读对账。
--
-- 为什么不做 DDL（本地 55433 实测结论）：
--   迁移 CLI 用业务连接串（`officeai_app`）执行，而 initdb 阶段建表的角色是超级用户
--   `officeai` → 业务表属主是 `officeai`。应用角色执行任何 DDL 会直接
--   `InsufficientPrivilegeError: must be owner of table ...`，写进迁移会让
--   `OA_DB_AUTO_MIGRATE=true` 的每次启动都失败。
--   含 DDL 的迁移须由 DBA 以表属主身份执行（阶段四部署手册已登记，
--   `OA_MIGRATE_DATABASE_URL` 记阶段四待办）。
--
-- 它解决什么问题：
--   私有化客户现场与历史环境常出现「按文档手工建了一部分表」或「升级时漏跑基线」。
--   此时缺表不会在启动时暴露，只会在用户第一次打开插件市场时炸——所以升级期
--   fail-fast 比运行期报错便宜得多。
--
-- 判据（任一不满足即 RAISE，迁移失败，错误信息里给出需要人工执行的 SQL 文件名）：
--   1) 9 张表全部存在；
--   2) 7 张严格表开启 FORCE ROW LEVEL SECURITY（共享目录 2 张同样要求 FORCE）；
--   3) 6 条目录种子存在（市场不能是空的）。
--
-- 执行方式：`app.db.migrate` 内置单事务（本文件不得写 BEGIN/COMMIT）。
-- ===========================================================================

-- 读 `plugin_catalog` 的平台行需要平台会话变量（FORCE RLS 对所有者同样生效）
SET app.tenant_id = '__platform__';

DO $$
DECLARE
    missing_tables TEXT;
    no_force_rls   TEXT;
    catalog_count  INTEGER;
    expected_tables CONSTANT TEXT[] := ARRAY[
        'plugin_catalog', 'plugin_version', 'plugin_installation', 'plugin_grant',
        'plugin_capability_policy', 'plugin_review', 'plugin_task',
        'plugin_task_step', 'plugin_artifact'
    ];
BEGIN
    SELECT string_agg(t, ', ') INTO missing_tables
    FROM unnest(expected_tables) AS t
    WHERE NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = current_schema() AND table_name = t
    );
    IF missing_tables IS NOT NULL THEN
        RAISE EXCEPTION
            '0003 对账失败：缺少阶段三数据表（%）。'
            '请以表属主身份执行 deploy/init-schema-phase3.sql 后重试。',
            missing_tables;
    END IF;

    SELECT string_agg(c.relname, ', ')
      INTO no_force_rls
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = current_schema()
       AND c.relkind = 'r'
       AND c.relname = ANY (expected_tables)
       AND c.relforcerowsecurity = FALSE;
    IF no_force_rls IS NOT NULL THEN
        RAISE EXCEPTION
            '0003 对账失败：以下表未开启强制行级隔离（%）。'
            '请重新执行 deploy/init-schema-phase3.sql 的 RLS 段落。',
            no_force_rls;
    END IF;

    SELECT count(*) INTO catalog_count
      FROM plugin_catalog
     WHERE tenant_id = '__platform__' AND status <> 'offline';
    IF catalog_count < 6 THEN
        RAISE EXCEPTION
            '0003 对账失败：插件目录可用行数为 %（期望 ≥ 6）。'
            '请重新执行 deploy/init-schema-phase3.sql 的种子段落。',
            catalog_count;
    END IF;
END $$;
