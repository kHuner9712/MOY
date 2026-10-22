-- ===========================================================================
-- 阶段四业务库表结构（会话与消息，迁移 0008 的入网落点）
--
-- 执行时机与顺序：
--   docker-entrypoint-initdb.d 中挂载为 `30-init-schema-phase4.sql`，
--   **必须在 `22-init-schema-phase3.sql` 之后**（依赖阶段二建好的 tenant 表、
--   `p_tenant_isolation` 体例与 `__platform__` 共享约定）。
--
-- 为什么会话/消息要有 init 脚本（而不是只靠迁移 0008）：
--   initdb 以**超级用户**跑这些脚本，表属主是超级用户；而迁移由**应用角色**
--   `officeai_app` 执行（compose 的 migrate Job 用的就是应用 DSN），
--   `officeai_app` 在 `public` 上没有 CREATE 权限（`NOSUPERUSER NOBYPASSRLS`）。
--   所以：
--     · **新装**：本脚本建好表 → 迁移 0008 走"零 DDL"路径（只做自检）；
--     · **存量升级**：由 DBA 以表属主身份执行本脚本（幂等），再 `--apply` 记台账。
--   若把建表只放在迁移里，新装环境的第一次 `migrate --apply` 会直接
--   `permission denied for schema public`——即"能装不能升"。
--
-- 幂等性：全部 `CREATE ... IF NOT EXISTS` / `DROP TRIGGER IF EXISTS` 后重建 /
--   `INSERT ... ON CONFLICT DO NOTHING`。重复执行零改动。
--
-- 内容与 `deploy/migrations/0008_conversation_message.sql` **必须逐条对应**：
--   迁移负责"存量库补齐"，本脚本负责"新库直接建全"；两者字段/索引/触发器/RLS
--   有任何一处不一致，都会变成"新装库与升级库结构不同"的长期漂移源。
-- ===========================================================================

SET app.tenant_id = '__platform__';

-- ---------------------------------------------------------------------------
-- 1. 会话
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conversation (
    tenant_id            TEXT        NOT NULL,
    conversation_id      TEXT        NOT NULL,           -- conv_ + ULID
    user_id              TEXT        NOT NULL,           -- 归属个人（不租户共享）
    title                TEXT        NOT NULL DEFAULT '' CHECK (char_length(title) <= 120),
    archived             BOOLEAN     NOT NULL DEFAULT FALSE,
    dify_conversation_id TEXT,                           -- 可空、不回填（决策 A-7）
    message_count        INTEGER     NOT NULL DEFAULT 0 CHECK (message_count >= 0),
    last_message_at      TIMESTAMPTZ,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at           TIMESTAMPTZ,                    -- 软删，30 天可恢复
    PRIMARY KEY (tenant_id, conversation_id)
);

CREATE INDEX IF NOT EXISTS idx_conversation_list
    ON conversation (tenant_id, user_id, last_message_at DESC NULLS LAST)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_conversation_recover
    ON conversation (tenant_id, deleted_at) WHERE deleted_at IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 2. 消息（append-only）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS message (
    tenant_id        TEXT        NOT NULL,
    message_id       TEXT        NOT NULL,               -- msg_ + ULID
    conversation_id  TEXT        NOT NULL,
    user_id          TEXT        NOT NULL DEFAULT '',
    role             TEXT        NOT NULL CHECK (role IN ('user','assistant','system')),
    content          TEXT        NOT NULL DEFAULT '',
    status           TEXT        NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending','streaming','settled','aborted','failed')),
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
);

CREATE INDEX IF NOT EXISTS idx_message_page
    ON message (tenant_id, conversation_id, seq DESC);

CREATE INDEX IF NOT EXISTS idx_message_orphan
    ON message (status) WHERE status IN ('pending', 'streaming');

-- ---------------------------------------------------------------------------
-- 3. append-only 触发器
--
-- ⚠ 只有 `OLD.status='streaming'` 时允许改 content。因此**插入 assistant 空壳时
--   必须直接给 `status='streaming'`**，绝不允许"一条 UPDATE 同时 pending→streaming
--   且改 content"（会被本触发器拒）。这条约束同时写在
--   `conversation_repo.finalize()` 与 `ConversationService` 的注释里。
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION message_guard_content() RETURNS trigger AS $guard$
BEGIN
    IF NEW.content IS DISTINCT FROM OLD.content AND OLD.status <> 'streaming' THEN
        RAISE EXCEPTION 'message content is immutable after finalize (status=%)', OLD.status;
    END IF;
    IF OLD.status IN ('settled', 'aborted', 'failed') AND NEW.status = 'streaming' THEN
        RAISE EXCEPTION 'message status cannot regress to streaming';
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END
$guard$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_message_guard ON message;
CREATE TRIGGER trg_message_guard BEFORE UPDATE ON message
    FOR EACH ROW EXECUTE FUNCTION message_guard_content();

-- ---------------------------------------------------------------------------
-- 4. RLS：strict（ENABLE + FORCE + p_tenant_isolation），与阶段二/三同体例
-- ---------------------------------------------------------------------------
DO $rls$
DECLARE
    strict_tables TEXT[] := ARRAY['conversation', 'message'];
    t TEXT;
BEGIN
    FOREACH t IN ARRAY strict_tables LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS p_tenant_isolation ON %I', t);
        EXECUTE format(
            'CREATE POLICY p_tenant_isolation ON %I '
            'USING (tenant_id = current_setting(''app.tenant_id'', TRUE) '
            '    OR current_setting(''app.tenant_id'', TRUE) = ''__platform__'') '
            'WITH CHECK (tenant_id = current_setting(''app.tenant_id'', TRUE) '
            '    OR current_setting(''app.tenant_id'', TRUE) = ''__platform__'')', t);
    END LOOP;
    RAISE NOTICE 'phase4: conversation / message 已启用 FORCE RLS 并建立 p_tenant_isolation';
END $rls$;

-- ---------------------------------------------------------------------------
-- 5. 应用角色授权（表由超级用户创建，必须显式 GRANT）
-- ---------------------------------------------------------------------------
DO $grant$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'officeai_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON conversation TO officeai_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON message TO officeai_app;
        RAISE NOTICE 'phase4: 已向 officeai_app 授予 conversation / message 读写权限';
    ELSE
        RAISE NOTICE 'phase4: 未找到 officeai_app（非标准部署），跳过显式授权';
    END IF;
END $grant$;
