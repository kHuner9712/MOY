-- ===========================================================================
-- 0002 支付回调幂等键对账（阶段二 B09 安全加固 / QA P1-03）
--
-- 背景（为什么这条迁移必须存在）：
--   支付回调的幂等**唯一依赖** `payment_callback_log` 上的唯一索引
--   `uq_callback_external (channel, external_txn_id)`：
--   `business/order_service.py` 用 `INSERT ... ON CONFLICT (channel, external_txn_id)
--   DO NOTHING` 领取幂等键。索引缺失 → `ON CONFLICT` 直接报错（42883），回调整条
--   链路不可用；索引「存在但不唯一」→ 同一笔回调被处理两次（重复开通 + 重复开票），
--   属资金类缺陷。所以这条约束必须是**可验证**的，而不是"建库时写过一次"。
--
-- 为什么这里是「自检」而不是 `CREATE UNIQUE INDEX IF NOT EXISTS`（重要）：
--   迁移 CLI 用业务连接串（`officeai_app`）执行。**已初始化**的库（initdb 阶段由
--   超级用户 `officeai` 跑 `init-schema-phase2.sql`）里表属主是 `officeai`，
--   应用角色建索引会直接 `InsufficientPrivilegeError: must be owner of table`；
--   即写 `CREATE INDEX` 会让 `OA_DB_AUTO_MIGRATE=true` 的**每次启动都失败**。
--   因此本迁移：
--     1) 只读 `pg_indexes` 做校验（不需要 DDL 权限，两类环境都能跑通）；
--     2) 缺失或不唯一时 `RAISE EXCEPTION` —— **失败要响**，并在错误信息里给出
--        需要 DBA 以表属主身份执行的那条 DDL 原文；
--     3) 不做任何 DDL，因此不会与基线（0001）的定义产生第二份实现。
--
-- 与基线的边界：**不要**改 `deploy/init-schema-phase2.sql`（会让已初始化环境的
-- checksum 漂移，启动直接失败）。新库由基线建出同名索引，本迁移只做对账。
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
