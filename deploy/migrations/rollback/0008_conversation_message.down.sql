-- ===========================================================================
-- 0008 down：撤销会话与消息（**删表 = 删数据**）
--
-- ⚠ 回滚这一版会永久删除全部会话与消息。`migrate.py` 对 0008/0009 强制先做
--   `pg_dump -Fc` 整库备份（`ROLLBACK_REQUIRES_BACKUP`，架构 §10.2 / R-4）：
--   备份失败则**直接拒绝回滚**——「回滚成功但丢数据」不可逆，「回滚不了」只是原地不动。
--
-- 用法（由 `app.db.migrate --rollback 0008_conversation_message` 调用）：
--   在同一事务 + 迁移锁内执行，成功后由 migrate.py 删除台账行，不自动级联。
-- 幂等性：`IF EXISTS`，表已不在时是纯通知。
-- 顺序：先删 message（无外键，但保持"后建先删"的直觉），再删 conversation，
--   最后删触发器函数（函数被 DROP TABLE 解绑后仍需显式删除）。
-- ===========================================================================

DO $down$
BEGIN
    IF to_regclass('public.message') IS NULL AND to_regclass('public.conversation') IS NULL THEN
        RAISE NOTICE '0008 down: conversation / message 均不存在，跳过';
        RETURN;
    END IF;

    BEGIN
        DROP TRIGGER IF EXISTS trg_message_guard ON message;
        DROP TABLE IF EXISTS message;
        DROP TABLE IF EXISTS conversation;
        DROP FUNCTION IF EXISTS message_guard_content();
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE EXCEPTION
                '0008 down 需要删除 conversation / message 两张表，但当前角色不是表属主。'
                '请由 DBA 以表属主身份执行本文件（回滚本身是运维动作，不走应用角色）。';
    END;

    RAISE NOTICE '0008 down: conversation / message 已删除（数据不可恢复，备份见 rollback/<ts>/）';
END $down$;
