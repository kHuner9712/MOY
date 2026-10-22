-- ===========================================================================
-- 0004 阶段三 B19 增量列（**能不动 DDL 就不动**：先看列在不在，缺了才补）
--
-- 背景：B19 新增三列 ——
--   plugin_task.confirmed_step_seq      （WF1 续跑定位「最近确认过哪一步」）
--   plugin_artifact.version             （WF2 版本只增）
--   plugin_artifact.version_history     （旧版本可回看/可下载）
--
-- **为什么不是无条件 `ALTER TABLE`（B19 复验的实测结论）**：
--   两条初始化路径的表属主不同——
--     · 空库（测试自建）：表由**应用角色**建 → `ALTER` 能跑；
--     · 已初始化库（`init-schema-phase2/3.sql` 由超级用户跑）：表属主是超级用户，
--       应用角色执行 `ALTER TABLE` 直接 `must be owner of table`。
--   与阶段二 `0002` 的结论一致：**应用角色不做 DDL**。
--
-- 因此本迁移的语义是三段式：
--   1. 三列已存在（init 脚本建的新库）→ **零 DDL**，只做只读自检后返回；
--   2. 列缺失且当前角色有权（空库路径：0003 的建表 DDL 早于 B19，不含这三列）→ 补列；
--   3. 列缺失且无权 → 报**可照做**的错误（跑哪条 init 脚本 / 找 DBA），
--      而不是把 `insufficient_privilege` 原样甩给用户。
--
-- 守卫块标签必须首尾**逐字一致**（`DO $guard$ … END $guard$;`）：
--   首版收尾用了**裸双美元**（与开头的带标签引号不配对）→ `unterminated dollar-quoted string`，空库路径整条迁移语法失败
--   （`test_g11_*` 四条 p0 全红）。守卫写坏了比不写更脆：改完必须在**空库**上跑一遍。
--
-- 执行方式：`app.db.migrate` 内置单事务（本文件不得写 BEGIN/COMMIT）。
-- ===========================================================================

DO $guard$
DECLARE
    missing_task_cols INT;
    missing_art_cols  INT;
BEGIN
    IF to_regclass('public.plugin_task') IS NULL THEN
        RAISE NOTICE '0004: plugin_task 不存在（未跑 0003），跳过';
        RETURN;
    END IF;

    -- ① 只读地数一遍缺哪几列（绝不在"列已存在"时碰 DDL）
    SELECT count(*) INTO missing_task_cols
      FROM (VALUES ('confirmed_step_seq')) AS need(col)
     WHERE NOT EXISTS (
           SELECT 1 FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'plugin_task'
              AND column_name = need.col);

    IF to_regclass('public.plugin_artifact') IS NULL THEN
        missing_art_cols := 0;
    ELSE
        SELECT count(*) INTO missing_art_cols
          FROM (VALUES ('version'), ('version_history')) AS need(col)
         WHERE NOT EXISTS (
               SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'plugin_artifact'
                  AND column_name = need.col);
    END IF;

    IF missing_task_cols = 0 AND missing_art_cols = 0 THEN
        RAISE NOTICE '0004: 三列已存在（init 脚本建的新库），零 DDL，自检通过';
        RETURN;
    END IF;

    -- ② 缺列才补；无 DDL 权限时给可照做的提示
    BEGIN
        IF missing_task_cols > 0 THEN
            ALTER TABLE plugin_task
                ADD COLUMN IF NOT EXISTS confirmed_step_seq SMALLINT NOT NULL DEFAULT 0;
        END IF;
        IF missing_art_cols > 0 THEN
            ALTER TABLE plugin_artifact
                ADD COLUMN IF NOT EXISTS version SMALLINT NOT NULL DEFAULT 1;
            ALTER TABLE plugin_artifact
                ADD COLUMN IF NOT EXISTS version_history JSONB NOT NULL DEFAULT '[]'::jsonb;
        END IF;
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE EXCEPTION
                '0004 需要补 3 列，但当前角色不是表属主（must be owner of table）。'
                '请二选一：① 用超级用户重跑 deploy/init-schema-phase3.sql'
                '（新版已含这三列，重建后本迁移零 DDL）；'
                '② 由 DBA 以表属主身份执行本迁移。';
    END;

    -- ③ 收尾自检：补完必须真的在（防"跑了个不生效的 ALTER"静默通过）
    SELECT count(*) INTO missing_task_cols
      FROM (VALUES ('confirmed_step_seq')) AS need(col)
     WHERE NOT EXISTS (
           SELECT 1 FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'plugin_task'
              AND column_name = need.col);
    IF missing_task_cols > 0 THEN
        RAISE EXCEPTION '0004 自检失败：plugin_task.confirmed_step_seq 仍不存在';
    END IF;

    IF to_regclass('public.plugin_artifact') IS NOT NULL THEN
        SELECT count(*) INTO missing_art_cols
          FROM (VALUES ('version'), ('version_history')) AS need(col)
         WHERE NOT EXISTS (
               SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'plugin_artifact'
                  AND column_name = need.col);
        IF missing_art_cols > 0 THEN
            RAISE EXCEPTION '0004 自检失败：plugin_artifact 的 version/version_history 仍不存在';
        END IF;
    END IF;

    RAISE NOTICE '0004: 列已补齐并自检通过';
END $guard$;
