-- ===========================================================================
-- 0004 阶段三 B19 增量列（自足迁移：表/列不存在则补，存在则跳过）
--
-- 为什么必须加列：WF2 的产物版本（`version` + `version_history`）与 A1/WF1 的
-- 人工接管续跑（`confirmed_step_seq`）在 `plugin_task` / `plugin_artifact` 上
-- 没有落点。三条都属于「运行期状态」，不是声明快照，不能塞进 JSONB 主字段
-- （塞进去后无法用 SQL 判「已确认过一次」，续跑会重复确认）。
--
-- 为什么用 `ADD COLUMN IF NOT EXISTS` 而不是重建表：
--   1. 空库路径下 0003 已按 `init-schema-phase3.sql` 建表（B19 之后含这三列），
--      本迁移对空库是**零操作**，不会与 0003 打架；
--   2. 存量库（B17/B18 已上线）只补列，不动数据、不锁全表重写。
--
-- 执行方式：`app.db.migrate` 内置单事务（本文件不得写 BEGIN/COMMIT）。
-- ===========================================================================

DO $guard$
BEGIN
    IF to_regclass('public.plugin_task') IS NULL THEN
        RAISE NOTICE '0004: plugin_task 不存在（未跑 0003），跳过';
        RETURN;
    END IF;

    ALTER TABLE plugin_task
        ADD COLUMN IF NOT EXISTS confirmed_step_seq SMALLINT NOT NULL DEFAULT 0;

    IF to_regclass('public.plugin_artifact') IS NOT NULL THEN
        ALTER TABLE plugin_artifact
            ADD COLUMN IF NOT EXISTS version SMALLINT NOT NULL DEFAULT 1;
        ALTER TABLE plugin_artifact
            ADD COLUMN IF NOT EXISTS version_history JSONB NOT NULL DEFAULT '[]'::jsonb;
    END IF;

    RAISE NOTICE '0004: 任务/产物版本列已就绪';
END $guard$;

-- ---------------------------------------------------------------------------
-- 自检提示（踩过的坑，2026-10-23 QA 实测）：
--   本文件用带标签的守卫块 `DO $guard$ ... END $guard$;`。**开盘与收尾的标签必须逐字一致**
--   ——首版写成 `DO $guard$ ... END $$;`，PostgreSQL 直接报
--   `unterminated dollar-quoted string at or near "$guard$"`，空库路径整条迁移语法失败
--   （`test_g11_*` 4 条 p0 全红）。守卫写坏了反而比不写更脆：改完后务必在**空库**上跑一次
--   `test_g11_migration_idempotency.py`，那是唯一能暴露它的场景。
-- ---------------------------------------------------------------------------
