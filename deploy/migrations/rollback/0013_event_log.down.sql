-- ===========================================================================
-- 0013 down：撤销会话事件日志（真删表 + 16 个分区 + 策略 + 索引）
--
-- ⚠ 回滚会**丢掉整条事件流**：事件日志是状态真源（规格 §3.1 E1），删掉它等于
--   删掉全部会话历史、回放能力、fork 血统与审计证据。恢复不了——本表的 hash 链
--   保证了"改一行就会被发现"，也保证了"删了就再也算不出来"。
--   回滚前必须先导出：`SELECT * FROM event_log ORDER BY session_id, seq;`
--   （审计导出要附链头，见设计文档 §5.1。）
--
-- 为什么不能只写一句 `DROP TABLE event_log`：
--   1. `deploy/validate_migrations.py` 的 C 路径专门验"down 脚本是否空转"——
--      空转的回滚脚本比没有回滚脚本更糟（运维以为回滚了，其实结构还在）；
--   2. 分区是独立 relation，`DROP TABLE` 父表**会**连带删分区，但显式逐个删能让
--      "已手工 detach 的分区"这种半回滚状态也被清干净，不留孤儿表；
--   3. 策略与索引随表删除而消失，无需单独 DROP（此处不写无用语句，只写校验）。
--
-- 幂等性：全部 `IF EXISTS`；重复执行零改动。
-- 顺序：先删分区 → 再删父表（反过来父表带走的只是它自己名下的分区）。
-- ===========================================================================

DO $down$
DECLARE
    -- 与 0013 主脚本的分区数常量保持一致
    partition_count CONSTANT integer := 16;
    idx             integer;
    part_name       text;
BEGIN
    FOR idx IN 0..(partition_count - 1) LOOP
        part_name := 'event_log_p' || lpad(idx::text, 2, '0');
        IF to_regclass(part_name) IS NOT NULL THEN
            EXECUTE format('DROP TABLE %I', part_name);
        END IF;
    END LOOP;

    IF to_regclass('event_log') IS NULL THEN
        RAISE NOTICE '0013 down: event_log 不存在，零改动';
        RETURN;
    END IF;

    -- CASCADE 只为兜住"删主表前又冒出来的附属对象"（如将来新增的外键/视图），
    -- 本迁移自身不带任何依赖对象。
    DROP TABLE event_log CASCADE;
    RAISE NOTICE '0013 down: event_log 及 % 个分区已删除', partition_count;
EXCEPTION
    WHEN insufficient_privilege THEN
        RAISE EXCEPTION
            '0013 down 需要 DROP TABLE event_log（含 16 个分区），但当前角色不是表属主。'
            '请以表属主（或 DBA）身份执行；应用角色对已经由 DBA 建过的表没有 DROP 权限——'
            '这是 RLS 隔离的既定设计。';
END $down$;
