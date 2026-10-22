-- ===========================================================================
-- 0008 会话与消息落库（阶段四 T4-02.1，架构 §3.1 / §3.2）
--
-- 为什么必须落库（PRD P4-07）：
--   对话历史此前由**客户端**提供，刷新页面即丢、换设备即断；底座侧
--   `conversation_id` 恒为 null（`model_service.py` 的 INT-04a 修复），
--   所以「刷新后逐字恢复」在现状下不可能成立。落库是自研上下文组装的前提。
--
-- 三段式语义（与 0004/0005/0006/0007 同体例 —— 应用角色不做 DDL）：
--   1. **两张表都已存在**（`init-schema-phase4.sql` 建的新库 / DBA 已执行）→
--      **零 DDL**，只做自检后返回（这是新装与"已由 DBA 升级"的正常路径）；
--   2. **缺表且当前角色有权**（空库路径：应用角色同时是库/表属主）→ 建表 + RLS +
--      触发器 + 授权；
--   3. **缺表且无权**（存量库：initdb 由超级用户跑，应用角色对 `public` 无 CREATE
--      权限）→ 报**可照做**的错误：由 DBA 执行 `deploy/init-schema-phase4.sql` 后
--      重跑 `--apply`。绝不静默跳过——跳过会让"迁移说成功、表却不存在"。
--
-- 与 `init-schema-phase4.sql` 的关系（**必须逐条对应**）：
--   迁移负责"存量库补齐"，init 脚本负责"新库直接建全"。两者字段/索引/触发器/RLS
--   有任何一处不一致，都会变成"新装库与升级库结构不同"的长期漂移源。
--
-- **正文不可变为什么用触发器**：PRD 说"只插入不更新正文"，但流式必须能边收边写。
--   靠代码自觉"我只更新一次"没用。触发器把规则落到数据库：
--     · 只有 `OLD.status='streaming'` 时才允许改 `content`；
--     · `settled/aborted/failed` 不得回退到 `streaming`。
--   ⚠ 因此**写首块内容时 OLD.status 必须已是 streaming**：插入 assistant 空壳时
--     直接给 `status='streaming'`，绝不允许"一条 UPDATE 同时 pending→streaming
--     且改 content"（会被触发器拒）。这条约束同时写在
--     `conversation_repo.PostgresMessageStore.finalize()` 的注释里。
--
-- RLS：两张表都进 **strict** 组（ENABLE + FORCE + `p_tenant_isolation`）。个人边界
--   （`user_id`）**不由 RLS 承担**——RLS 只保证租户边界，本人边界由仓储层强制。
--
-- 执行方式：`app.db.migrate --apply`（同事务 + `pg_advisory_xact_lock`，本文件不写
--   BEGIN/COMMIT）。回滚：`rollback/0008_conversation_message.down.sql`
--   （⚠ 删表，回滚前强制 `pg_dump` 备份，见 `ROLLBACK_REQUIRES_BACKUP`）。
-- ===========================================================================

DO $guard$
DECLARE
    missing TEXT[];
BEGIN
    -- ① 两张表齐全 → 零 DDL（新装库 / DBA 已执行 init 脚本）
    IF to_regclass('public.conversation') IS NOT NULL
       AND to_regclass('public.message') IS NOT NULL THEN
        RAISE NOTICE '0008: conversation / message 已存在（init-schema-phase4.sql 建的新库），零 DDL';
        RETURN;
    END IF;

    SELECT array_agg(name) INTO missing
      FROM (VALUES ('conversation'), ('message')) AS wanted(name)
     WHERE to_regclass('public.' || name) IS NULL;

    -- ② 缺表才建；无 DDL 权限时给可照做的替代动作
    BEGIN
        EXECUTE $ddl$
            CREATE TABLE IF NOT EXISTS conversation (
                tenant_id            TEXT        NOT NULL,
                conversation_id      TEXT        NOT NULL,
                user_id              TEXT        NOT NULL,
                title                TEXT        NOT NULL DEFAULT ''
                                     CHECK (char_length(title) <= 120),
                archived             BOOLEAN     NOT NULL DEFAULT FALSE,
                dify_conversation_id TEXT,
                message_count        INTEGER     NOT NULL DEFAULT 0 CHECK (message_count >= 0),
                last_message_at      TIMESTAMPTZ,
                created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
                deleted_at           TIMESTAMPTZ,
                PRIMARY KEY (tenant_id, conversation_id)
            )
        $ddl$;

        EXECUTE $ddl$
            CREATE INDEX IF NOT EXISTS idx_conversation_list
                ON conversation (tenant_id, user_id, last_message_at DESC NULLS LAST)
                WHERE deleted_at IS NULL
        $ddl$;

        EXECUTE $ddl$
            CREATE INDEX IF NOT EXISTS idx_conversation_recover
                ON conversation (tenant_id, deleted_at) WHERE deleted_at IS NOT NULL
        $ddl$;

        EXECUTE $ddl$
            CREATE TABLE IF NOT EXISTS message (
                tenant_id        TEXT        NOT NULL,
                message_id       TEXT        NOT NULL,
                conversation_id  TEXT        NOT NULL,
                user_id          TEXT        NOT NULL DEFAULT '',
                role             TEXT        NOT NULL
                                 CHECK (role IN ('user','assistant','system')),
                content          TEXT        NOT NULL DEFAULT '',
                status           TEXT        NOT NULL DEFAULT 'pending'
                                 CHECK (status IN
                                        ('pending','streaming','settled','aborted','failed')),
                error_reason     TEXT        NOT NULL DEFAULT '',
                truncated        BOOLEAN     NOT NULL DEFAULT FALSE,
                profile          TEXT        NOT NULL DEFAULT '',
                generation_id    TEXT        NOT NULL DEFAULT '',
                prompt_units     INTEGER     NOT NULL DEFAULT 0 CHECK (prompt_units >= 0),
                completion_units INTEGER     NOT NULL DEFAULT 0 CHECK (completion_units >= 0),
                seq              BIGINT      GENERATED ALWAYS AS IDENTITY,
                created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (tenant_id, message_id)
            )
        $ddl$;

        EXECUTE $ddl$
            CREATE INDEX IF NOT EXISTS idx_message_page
                ON message (tenant_id, conversation_id, seq DESC)
        $ddl$;

        EXECUTE $ddl$
            CREATE INDEX IF NOT EXISTS idx_message_orphan
                ON message (status) WHERE status IN ('pending', 'streaming')
        $ddl$;

        EXECUTE $ddl$
            CREATE OR REPLACE FUNCTION message_guard_content() RETURNS trigger AS $body$
            BEGIN
                IF NEW.content IS DISTINCT FROM OLD.content
                   AND OLD.status <> 'streaming' THEN
                    RAISE EXCEPTION
                        'message content is immutable after finalize (status=%)', OLD.status;
                END IF;
                IF OLD.status IN ('settled', 'aborted', 'failed')
                   AND NEW.status = 'streaming' THEN
                    RAISE EXCEPTION 'message status cannot regress to streaming';
                END IF;
                NEW.updated_at := now();
                RETURN NEW;
            END
            $body$ LANGUAGE plpgsql
        $ddl$;

        EXECUTE 'DROP TRIGGER IF EXISTS trg_message_guard ON message';
        EXECUTE $ddl$
            CREATE TRIGGER trg_message_guard BEFORE UPDATE ON message
                FOR EACH ROW EXECUTE FUNCTION message_guard_content()
        $ddl$;

        EXECUTE 'ALTER TABLE conversation ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE conversation FORCE ROW LEVEL SECURITY';
        EXECUTE 'DROP POLICY IF EXISTS p_tenant_isolation ON conversation';
        EXECUTE $ddl$
            CREATE POLICY p_tenant_isolation ON conversation
                USING (tenant_id = current_setting('app.tenant_id', TRUE)
                    OR current_setting('app.tenant_id', TRUE) = '__platform__')
                WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE)
                    OR current_setting('app.tenant_id', TRUE) = '__platform__')
        $ddl$;

        EXECUTE 'ALTER TABLE message ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE message FORCE ROW LEVEL SECURITY';
        EXECUTE 'DROP POLICY IF EXISTS p_tenant_isolation ON message';
        EXECUTE $ddl$
            CREATE POLICY p_tenant_isolation ON message
                USING (tenant_id = current_setting('app.tenant_id', TRUE)
                    OR current_setting('app.tenant_id', TRUE) = '__platform__')
                WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE)
                    OR current_setting('app.tenant_id', TRUE) = '__platform__')
        $ddl$;

        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'officeai_app') THEN
            EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON conversation TO officeai_app';
            EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON message TO officeai_app';
        END IF;
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE EXCEPTION
                '0008 需要创建会话/消息表（缺 %），但当前角色不是库属主'
                '（应用角色 officeai_app 在 public 上没有 CREATE 权限）。请二选一：'
                '① 用超级用户执行 deploy/init-schema-phase4.sql（幂等），再重跑 --apply'
                '（届时本迁移走零 DDL 路径）；'
                '② 由 DBA 以表属主身份执行本迁移。'
                '在此之前，对话历史无法落库（刷新即丢）。',
                missing;
    END;

    -- ③ 收尾自检：建完必须真的在，否则本迁移就是"跑了但没生效"
    IF to_regclass('public.conversation') IS NULL OR to_regclass('public.message') IS NULL THEN
        RAISE EXCEPTION '0008 自检失败：conversation / message 仍缺失';
    END IF;
    RAISE NOTICE '0008: conversation / message 已建成（含 RLS / 触发器 / 授权）';
END $guard$;
