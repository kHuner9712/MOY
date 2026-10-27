-- ===========================================================================
-- 0012 额度挂起标记（阶段四 P4-11 / 缺陷 P3-B22-01 的 ②③ 两支）
--
-- 为什么需要这一列：PRD 5.3 的软限制要求「超额不停服，但**不要继续烧上游**」。
-- 此前超额后仍然照常上传文档、照常建自动化任务（只剩一句非阻断提示），
-- 于是「额度用尽」对成本毫无约束力。本列把「该行已被额度挂起」变成**库内事实**，
-- 而不是进程内的一次性判断——否则重启/多副本就会把挂起状态丢掉，用户看到的
-- 是「刚才说要等额度，现在又跑了」。
--
-- 为什么加在两张表而不是新开一张表：
--   1. 挂起是**行级属性**（这篇文档 / 这个任务被挂起），不是独立实体；
--   2. 懒恢复要按 `tenant_id` 批量清（`UPDATE ... WHERE hold_reason <> ''`），
--      同表批量比跨表 join 更简单，也不会引入外键级联；
--   3. 新表会让 G-SQL 的 INSERT 列对账多一张无关表。
--
-- 取值：`''`（未挂起）/ `'quota'`（超额）/ `'past_due'`（欠费）。
--   `'past_due'` 现在没有写入方（计费域 D3 冻结），先占位：将来接欠费时**不需要**
--   再改 CHECK 约束（改约束要全表重写，属高风险 DDL）。
--
-- **关键约束（勿改）：本列不得进任何 INSERT 列名列表。**
--   `NOT NULL DEFAULT ''` 使它成为「有默认值、可省略」的列：
--   - R7（字段覆盖对账）会把它算作 omittable，不会因为 INSERT 不写它而判红；
--   - G-SQL（列数/值数 arity 对账）完全不受影响 —— 少写一列就等于没动过 arity。
--   只在「切 queued / 写挂起」的 UPDATE 里带它。
--
-- 幂等性：列存在性守卫 + `IF NOT EXISTS`；重复执行零改动。
-- **新装库走的是「零 DDL」路径**：`init-schema-phase4.sql` 已由 initdb 的超级用户
-- 建好这两列与两条部分索引，本迁移只做守卫判定。**不要**把 phase4 里那两列删掉
-- 只留本迁移的 ALTER —— 迁移 Job 用应用 DSN（`officeai_app` 在 `public` 上无
-- CREATE 权限），那样新装环境第一次 `--apply` 就会撞「不是表属主」，
-- 0010（background_job）已经踩过一次；`test_migration_0012_quota_hold.py` 是回归锁。
-- 回滚：`rollback/0012_quota_hold.down.sql`（⚠ 回滚会丢挂起状态，恢复后需重新判定）。
-- ===========================================================================

DO $guard$
DECLARE
    missing TEXT[];
BEGIN
    SELECT array_agg(wanted.tbl || '.' || wanted.col) INTO missing
      FROM (VALUES ('document_meta', 'hold_reason'),
                   ('plugin_task', 'hold_reason')) AS wanted(tbl, col)
     WHERE NOT EXISTS (
               SELECT 1
                 FROM information_schema.columns c
                WHERE c.table_schema = 'public'
                  AND c.table_name = wanted.tbl
                  AND c.column_name = wanted.col
           );

    IF missing IS NULL THEN
        RAISE NOTICE '0012: document_meta / plugin_task 的 hold_reason 均已存在（新库直建），零 DDL';
        RETURN;
    END IF;

    BEGIN
        EXECUTE $ddl$
            ALTER TABLE document_meta
                ADD COLUMN IF NOT EXISTS hold_reason TEXT NOT NULL DEFAULT ''
                CHECK (hold_reason IN ('', 'quota', 'past_due'))
        $ddl$;

        EXECUTE $ddl$
            ALTER TABLE plugin_task
                ADD COLUMN IF NOT EXISTS hold_reason TEXT NOT NULL DEFAULT ''
                CHECK (hold_reason IN ('', 'quota', 'past_due'))
        $ddl$;
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE EXCEPTION
                '0012 需要 ALTER TABLE document_meta / plugin_task，但当前角色不是表属主。'
                '请以表属主（或 DBA）身份执行本迁移；以 officeai_app 执行不会成功——'
                '它在 public 上没有 CREATE 权限，这是 RLS 隔离的既定设计。';
    END;

    RAISE NOTICE '0012: 已补齐 hold_reason（缺 % 项）', coalesce(array_length(missing, 1), 0);
END $guard$;

-- 部分索引：只索引「被挂起」的少量行。
-- 懒恢复的批量清理（`WHERE tenant_id = ? AND hold_reason <> ''`）与「当前有多少行
-- 被挂起」的运维查询都走它；未挂起的行不进索引，索引体积与总行数无关。
--
-- **为什么先查 `pg_indexes` 再建，而不是直接 `CREATE INDEX IF NOT EXISTS`**：
-- 新装库的索引由 `init-schema-phase4.sql`（超级用户）建好，本迁移在应用角色下执行。
-- `CREATE INDEX IF NOT EXISTS` 命中已有索引时是否仍校验属主，属于实现细节，
-- 不能拿它当"反正会跳过"的依据——显式查存在性才能保证这一步真正是零 DDL。
DO $idx$
DECLARE
    missing TEXT[];
BEGIN
    SELECT array_agg(wanted.name) INTO missing
      FROM (VALUES ('idx_document_meta_hold'),
                   ('idx_plugin_task_hold')) AS wanted(name)
     WHERE NOT EXISTS (
               SELECT 1
                 FROM pg_indexes i
                WHERE i.schemaname = 'public'
                  AND i.indexname = wanted.name
           );

    IF missing IS NULL THEN
        RAISE NOTICE '0012: 挂起部分索引已存在（init-schema-phase4.sql 建的新库），零 DDL';
        RETURN;
    END IF;

    BEGIN
        IF 'idx_document_meta_hold' = ANY (missing) THEN
            EXECUTE $ddl$
                CREATE INDEX IF NOT EXISTS idx_document_meta_hold
                    ON document_meta (tenant_id)
                 WHERE hold_reason <> ''
            $ddl$;
        END IF;

        IF 'idx_plugin_task_hold' = ANY (missing) THEN
            EXECUTE $ddl$
                CREATE INDEX IF NOT EXISTS idx_plugin_task_hold
                    ON plugin_task (tenant_id)
                 WHERE hold_reason <> ''
            $ddl$;
        END IF;
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE EXCEPTION
                '0012 建索引需要 document_meta / plugin_task 的属主权限，请由表属主执行。';
    END;
    RAISE NOTICE '0012: 挂起部分索引就绪（新建 % 个）', coalesce(array_length(missing, 1), 0);
END $idx$;
