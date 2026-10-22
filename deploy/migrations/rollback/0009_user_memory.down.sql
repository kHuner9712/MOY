-- ===========================================================================
-- 0009 down：撤销记忆三表（**删表 = 删数据**）
--
-- ⚠ 回滚这一版会永久删除全部用户记忆与向量。`migrate.py` 对 0008/0009 强制先做
--   `pg_dump -Fc` 整库备份（`ROLLBACK_REQUIRES_BACKUP`）：备份失败则拒绝回滚。
-- 不删 `vector` 扩展：它可能被别的功能（知识库）共用，删扩展 = 连带删列，风险更高。
-- 幂等性：`IF EXISTS`。
-- ===========================================================================

DO $down$
BEGIN
    BEGIN
        DROP TABLE IF EXISTS memory_embedding;
        DROP TABLE IF EXISTS user_memory;
        DROP TABLE IF EXISTS user_memory_setting;
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE EXCEPTION
                '0009 down 需要删除记忆三表，但当前角色不是表属主。请由 DBA 以表属主身份执行。';
    END;
    RAISE NOTICE '0009 down: 记忆三表已删除（数据不可恢复，备份见 rollback/<ts>/）';
END $down$;
