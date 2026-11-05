-- ===========================================================================
-- 0014 通用任务计划生成 → 确认（矩阵第 2 项）
--
-- 为什么是 3 列而不是新表：
--   计划本身是 `plugin_task_step` 骨架 + `PluginManifest` 声明的投影，用户能改的
--   只有"某一步是否纳入本次执行"，落库仍是既有 `plugin_task_step.status`
--   （`skipped` = 未纳入）。因此新增的只是**任务级的计划元数据**：
--     plan_version      创建计划任务时=1，每次编辑 +1（编辑/确认/清理的乐观并发条件）
--     plan_expires_at   确认截止时间（超时清理器据此把任务置 cancelled）
--     plan_confirmed_at 确认时间（写入即"已确认"，重复确认不再触发执行）
--
-- 状态枚举新增 `waiting_plan`：确认前**零执行**。它刻意**不**并入
-- `runner._LIVE_TASK_STATUSES`——否则 `advance` 的 CAS 能拿到计划任务并开始跑
-- 有副作用的工具步骤；取消则单独用 `_CANCELLABLE_TASK_STATUSES` 覆盖它。
--
-- 幂等性：三列存在性守卫、状态 CHECK 按实际约束名守卫、索引按 `pg_indexes` 守卫；
-- 重复执行零 DDL。
-- **新装库走「零 DDL」路径**：`init-schema-phase3.sql`（initdb 超级用户）已建好
-- 三列、新 CHECK 与部分索引，本迁移只做守卫判定。**不要**把 init-schema 里那三列
-- 删掉只留本迁移的 ALTER——迁移 Job 用应用 DSN，应用角色在 `public` 无 CREATE，
-- 新装环境第一次 `--apply` 会撞「不是表属主」（0010/0012 都踩过）。
-- 回滚：`rollback/0014_task_plan.down.sql`（⚠ 会取消仍停在 waiting_plan 的任务）。
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 1. 三列（NOT NULL DEFAULT 0 / 可空时间）
--
--    `plan_version NOT NULL DEFAULT 0` 是刻意选的：存量任务天然是"非计划任务"，
--    不需要数据回填；也让 `TaskRecord.plan_version=0` 的判据与库内事实一致。
-- ---------------------------------------------------------------------------
DO $guard$
DECLARE
    missing TEXT[];
BEGIN
    SELECT array_agg(wanted.col) INTO missing
      FROM (VALUES ('plan_version'),
                   ('plan_expires_at'),
                   ('plan_confirmed_at')) AS wanted(col)
     WHERE NOT EXISTS (
               SELECT 1
                 FROM information_schema.columns c
                WHERE c.table_schema = 'public'
                  AND c.table_name = 'plugin_task'
                  AND c.column_name = wanted.col
           );

    IF missing IS NULL THEN
        RAISE NOTICE '0014: plugin_task 的计划元数据列已存在（新库直建），零 DDL';
        RETURN;
    END IF;

    BEGIN
        EXECUTE $ddl$
            ALTER TABLE plugin_task
                ADD COLUMN IF NOT EXISTS plan_version INTEGER NOT NULL DEFAULT 0
                    CHECK (plan_version >= 0),
                ADD COLUMN IF NOT EXISTS plan_expires_at TIMESTAMPTZ,
                ADD COLUMN IF NOT EXISTS plan_confirmed_at TIMESTAMPTZ
        $ddl$;
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE EXCEPTION
                '0014 需要 ALTER TABLE plugin_task，但当前角色不是表属主。'
                '请以表属主（或 DBA）身份执行本迁移；以 officeai_app 执行不会成功——'
                '它在 public 上没有 CREATE 权限，这是 RLS 隔离的既定设计。';
    END;

    RAISE NOTICE '0014: 已补齐计划元数据列（缺 % 项）', coalesce(array_length(missing, 1), 0);
END $guard$;

-- ---------------------------------------------------------------------------
-- 2. 状态 CHECK 增加 `waiting_plan`
--
--    **不能写死约束名**：`plugin_task_status_check` 是 PostgreSQL 自动命名的，
--    历史环境里可能叫别的名字（手工建库 / 不同版本迁移）。因此按"约束定义里
--    含 waiting_confirm 且属于 plugin_task 的 CHECK"定位实际名字，再重建。
--
--    先判"定义里是否已含 waiting_plan"：含 → 零 DDL 跳过（新库已由 init-schema 建好）。
-- ---------------------------------------------------------------------------
DO $check$
DECLARE
    conname TEXT;
    def     TEXT;
BEGIN
    SELECT c.conname, pg_get_constraintdef(c.oid) INTO conname, def
      FROM pg_constraint c
     WHERE c.conrelid = 'plugin_task'::regclass
       AND c.contype = 'c'
       AND pg_get_constraintdef(c.oid) LIKE '%waiting_confirm%'
     LIMIT 1;

    IF conname IS NOT NULL AND def LIKE '%waiting_plan%' THEN
        RAISE NOTICE '0014: plugin_task.status 已包含 waiting_plan（新库直建），零 DDL';
        RETURN;
    END IF;

    BEGIN
        IF conname IS NOT NULL THEN
            EXECUTE format('ALTER TABLE plugin_task DROP CONSTRAINT %I', conname);
        END IF;
        ALTER TABLE plugin_task
            ADD CONSTRAINT plugin_task_status_check
            CHECK (status IN ('pending','running','queued','waiting_confirm',
                              'waiting_plan','completed','failed','cancelled'));
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE EXCEPTION
                '0014 需要重建 plugin_task.status 的 CHECK 约束，但当前角色不是表属主，'
                '请由表属主（或 DBA）执行。';
    END;

    RAISE NOTICE '0014: plugin_task.status 已支持 waiting_plan';
END $check$;

-- ---------------------------------------------------------------------------
-- 3. 部分索引：计划清理器只扫「停在 waiting_plan 且带截止时间」的行
--
--    为什么是部分索引：绝大多数任务不是计划任务，进索引的行只占极小比例；
--    清理器按 `(tenant_id, plan_expires_at)` 取数，未确认计划的量级与总任务量无关。
--    先查 `pg_indexes` 再建（理由同 0012：不赌 `IF NOT EXISTS` 的属主校验行为）。
-- ---------------------------------------------------------------------------
DO $idx$
DECLARE
    exists_already BOOLEAN;
BEGIN
    SELECT EXISTS (
               SELECT 1
                 FROM pg_indexes i
                WHERE i.schemaname = 'public'
                  AND i.indexname = 'idx_task_plan_expiry'
           ) INTO exists_already;

    IF exists_already THEN
        RAISE NOTICE '0014: idx_task_plan_expiry 已存在（新库直建），零 DDL';
        RETURN;
    END IF;

    BEGIN
        EXECUTE $ddl$
            CREATE INDEX IF NOT EXISTS idx_task_plan_expiry
                ON plugin_task (tenant_id, plan_expires_at)
             WHERE status = 'waiting_plan'
        $ddl$;
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE EXCEPTION
                '0014 建索引需要 plugin_task 的属主权限，请由表属主执行。';
    END;
    RAISE NOTICE '0014: idx_task_plan_expiry 就绪';
END $idx$;
