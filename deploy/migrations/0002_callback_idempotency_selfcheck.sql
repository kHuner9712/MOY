-- ===========================================================================
-- 0002 支付回调幂等索引「升级路径自检」（阶段二 B09，chore/非缺陷修复）
--
-- ⚠️ 定性说明（避免被误读为缺陷修复）：
--   这条迁移**不是**补一个缺失的约束。`uq_callback_external` 在基线
--   `deploy/init-schema-phase2.sql` 第 9 节就随建表一起创建：
--     CREATE UNIQUE INDEX IF NOT EXISTS uq_callback_external
--         ON payment_callback_log (channel, external_txn_id);
--   且 SaaS 与私有化两条编排都在 initdb 阶段挂了该基线
--   （`docker-compose.saas.yml` 20- 与 `docker-compose.yml` 20-），
--   因此**首建库一定已有该索引**。本迁移不覆盖「首建」路径。
--
-- 它解决的是**升级/存量库**路径的风险：
--   私有化客户现场与历史环境常出现「照文档手工建了表、但漏了索引」或
--   「被人为 DROP 过索引」的情形。此时：
--     - 缺索引 → `order_service.append_callback` 的
--       `INSERT ... ON CONFLICT (channel, external_txn_id) DO NOTHING` 直接报
--       42883（无匹配唯一约束），支付回调整条链路不可用；
--     - 索引存在但不是 UNIQUE → 同一笔回调被处理两次（重复开通 + 重复开票），
--       属资金类风险。
--   两者都不会在启动时暴露，只会在真实支付时炸。所以需要一条**可验证**的自检，
--   让升级在启动期就失败（fail-fast），而不是等收费链路出事。
--
-- 为什么是「自检」而不是 `CREATE UNIQUE INDEX IF NOT EXISTS`（重要）：
--   迁移 CLI 用业务连接串（`officeai_app`）执行，而 initdb 阶段由超级用户
--   `officeai` 建表 → 业务表属主是 `officeai`。应用角色执行任何 DDL 会直接
--   `InsufficientPrivilegeError: must be owner of table payment_callback_log`
--   （本地 55433 实测）。若在此写 DDL，`OA_DB_AUTO_MIGRATE=true` 的**每次启动
--   都会失败**。因此本迁移：
--     1) 只读 `pg_indexes` 校验（无需 DDL 权限，两类环境都能跑通）；
--     2) 缺失或不唯一时 `RAISE EXCEPTION` —— **失败要响**，并在错误信息里给出
--        需 DBA 以表属主身份执行的那条 DDL 原文；
--     3) 不做任何 DDL，不会与基线产生第二份实现，也不会造成 checksum 漂移。
--
-- 与基线的边界：**不要**改 `deploy/init-schema-phase2.sql`（会让已初始化环境的
-- checksum 漂移，启动直接失败）。
--
-- 执行方式：`app.db.migrate` 内置单事务（本文件不得写 BEGIN/COMMIT）。
-- ===========================================================================

DO $$
DECLARE
    index_def TEXT;
BEGIN
    SELECT indexdef INTO index_def
    FROM pg_indexes
    WHERE schemaname = current_schema()
      AND tablename = 'payment_callback_log'
      AND indexname = 'uq_callback_external';

    IF index_def IS NULL THEN
        RAISE EXCEPTION
            '0002 对账失败：payment_callback_log 缺少唯一索引 uq_callback_external。'
            '请以表属主身份执行：CREATE UNIQUE INDEX uq_callback_external '
            'ON payment_callback_log (channel, external_txn_id);';
    END IF;

    IF index_def NOT LIKE 'CREATE UNIQUE INDEX%' THEN
        RAISE EXCEPTION
            '0002 对账失败：uq_callback_external 不是唯一索引（%）。'
            '支付回调会重复开通/重复开票，必须 DROP 后以 UNIQUE 重建。',
            index_def;
    END IF;
END $$;
