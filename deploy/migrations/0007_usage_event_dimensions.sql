-- ===========================================================================
-- 0007 `usage_event` 四个计量维度（阶段四 T4-01.5，P4-11 / M11）
--
-- 为什么加这四列（架构 §7.4）：
--   现在能算的只有 `units`（字符折算后的额度单位）。要回答三个业务问题必须再加维度：
--   ① 「哪个档位用得最多」→ `profile`；
--   ② 「真实 token 用量是多少」（毛利与成本口径）→ `prompt_tokens` / `completion_tokens`；
--   ③ 「换底座后口径会不会断」→ `provider`（本项目固定 'dify'，字段留口）。
--   四个值都来自底座已回传的 `metadata.usage`（实测 prompt=47 / completion=16），
--   不需要引入网关或二次调用。
--
-- 为什么全部 `NOT NULL DEFAULT`：
--   历史行必须有值，否则"按档位聚合"会出现 NULL 分组。默认值语义就是
--   「未知 / 未采集」——看板用 `source='estimated' OR profile=''` 单列，
--   并且估算路径**禁止**用 estimated 行算毛利（arch §7.4）。
--
-- 为什么 `profile` 用 TEXT 而不是枚举 CHECK：
--   档位枚举（general/code/long_context/reasoning）在 `registry/profile_routes.yaml`
--   里是配置，加档位不该需要迁移。写入端由 `resolve_route()` 保证取值合法。
--
-- 三段式语义与 0004/0005/0006 同体例（应用角色不做 DDL）：
--   1. 四列都已存在（`init-schema-phase3.sql` 尾部第 12 节建的新库）→ 零 DDL 返回；
--   2. 有缺列且当前角色有权（空库路径）→ 逐列补；
--   3. 有缺列且无权（已初始化库）→ 报可照做的错误（DBA 执行 / 重跑 init 脚本）。
--
-- 幂等性：逐列 `ADD COLUMN IF NOT EXISTS` + 存在性守卫。
-- 自足性：`usage_event` 不存在（空库）时**不 RAISE**，交由基线脚本负责建表。
--
-- 执行方式：`app.db.migrate --apply` 在同一事务 + `pg_advisory_xact_lock` 内执行
--   （本文件**不得**写 BEGIN/COMMIT）。
-- 回滚：`deploy/migrations/rollback/0007_usage_event_dimensions.down.sql`
--   （drop 这四列；聚合表 `usage_monthly` 不含这四列，不受影响）。
-- ===========================================================================

DO $guard$
DECLARE
    missing TEXT[];
BEGIN
    IF to_regclass('public.usage_event') IS NULL THEN
        RAISE NOTICE '0007: usage_event 不存在（阶段二脚本未跑），跳过';
        RETURN;
    END IF;

    -- ① 只读地算出缺哪几列（全在时零 DDL）
    SELECT array_agg(needed.column_name ORDER BY needed.column_name)
      INTO missing
      FROM (
            VALUES ('profile'), ('provider'), ('prompt_tokens'), ('completion_tokens')
           ) AS needed(column_name)
     WHERE NOT EXISTS (
            SELECT 1
              FROM information_schema.columns c
             WHERE c.table_schema = current_schema()
               AND c.table_name = 'usage_event'
               AND c.column_name = needed.column_name
           );

    IF missing IS NULL THEN
        RAISE NOTICE '0007: usage_event 四列已存在（init 脚本建的新库），零 DDL，自检通过';
        RETURN;
    END IF;

    -- ② 缺列才补；无 DDL 权限时给可照做的替代动作
    BEGIN
        ALTER TABLE usage_event ADD COLUMN IF NOT EXISTS profile           TEXT   NOT NULL DEFAULT '';
        ALTER TABLE usage_event ADD COLUMN IF NOT EXISTS provider          TEXT   NOT NULL DEFAULT '';
        ALTER TABLE usage_event ADD COLUMN IF NOT EXISTS prompt_tokens     BIGINT NOT NULL DEFAULT 0;
        ALTER TABLE usage_event ADD COLUMN IF NOT EXISTS completion_tokens BIGINT NOT NULL DEFAULT 0;
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE EXCEPTION
                '0007 需要补 % 列（usage_event: profile/provider/prompt_tokens/completion_tokens），'
                '但当前角色不是表属主（must be owner of table）。请二选一：'
                '① 用超级用户重跑 deploy/init-schema-phase3.sql'
                '（尾部第 12 节已含这四列，重建后本迁移零 DDL）；'
                '② 由 DBA 以表属主身份执行本迁移。'
                '在此之前「按档位 / 按真实 token」的用量看板拿不到数据。',
                missing;
    END;

    -- ③ 收尾自检：补完必须真的在
    SELECT array_agg(needed.column_name ORDER BY needed.column_name)
      INTO missing
      FROM (
            VALUES ('profile'), ('provider'), ('prompt_tokens'), ('completion_tokens')
           ) AS needed(column_name)
     WHERE NOT EXISTS (
            SELECT 1
              FROM information_schema.columns c
             WHERE c.table_schema = current_schema()
               AND c.table_name = 'usage_event'
               AND c.column_name = needed.column_name
           );

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION '0007 自检失败：usage_event 仍缺列 %', missing;
    END IF;

    RAISE NOTICE '0007: usage_event 四列已补齐并自检通过';
END $guard$;
