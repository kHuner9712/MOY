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

-- ---------------------------------------------------------------------------
-- 6. pgvector 扩展（记忆向量）
--
-- ⚠ 必须由 initdb（超级用户）执行：`vector` 的
--   `pg_available_extension_versions.trusted = false`，应用角色 `officeai_app`
--   是 NOSUPERUSER，**永远建不出这个扩展**（实测 pgvector 0.8.7）。
--   存量库升级时由 DBA 手工执行同一句。
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------------
-- 7. 记忆三表（迁移 0009 的入网落点）
--
-- `user_memory` 事实 / `memory_embedding` 向量 / `user_memory_setting` 开关。
-- 记忆是个人数据：RLS 只保证租户边界，`user_id` 边界由仓储层强制。
-- 维度固定 1024（决策 A-2）：换 embedding 模型必须重建该列（先 DROP INDEX）。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_memory (
    tenant_id              TEXT        NOT NULL,
    memory_id              TEXT        NOT NULL,
    user_id                TEXT        NOT NULL,
    category               TEXT        NOT NULL
                           CHECK (category IN ('identity','org','preference',
                                               'convention','constraint','project')),
    content                TEXT        NOT NULL CHECK (content <> ''),
    confidence             TEXT        NOT NULL DEFAULT 'high'
                           CHECK (confidence IN ('high','low')),
    source_conversation_id TEXT        NOT NULL DEFAULT '',
    source_message_id      TEXT        NOT NULL DEFAULT '',
    superseded_by          TEXT,
    normalized_hash        TEXT        NOT NULL DEFAULT '',
    hit_count              INTEGER     NOT NULL DEFAULT 1 CHECK (hit_count >= 1),
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, memory_id)
);
CREATE INDEX IF NOT EXISTS idx_memory_recall
    ON user_memory (tenant_id, user_id, category, updated_at DESC)
    WHERE superseded_by IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_hash
    ON user_memory (tenant_id, user_id, category, normalized_hash)
    WHERE superseded_by IS NULL AND normalized_hash <> '';

CREATE TABLE IF NOT EXISTS memory_embedding (
    tenant_id  TEXT        NOT NULL,
    memory_id  TEXT        NOT NULL,
    model      TEXT        NOT NULL DEFAULT '',
    dim        INTEGER     NOT NULL,
    embedding  vector(1024) NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, memory_id)
);
CREATE INDEX IF NOT EXISTS idx_memory_embedding_hnsw
    ON memory_embedding USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS user_memory_setting (
    tenant_id         TEXT        NOT NULL,
    user_id           TEXT        NOT NULL,
    enabled           BOOLEAN     NOT NULL DEFAULT TRUE,
    category_switches JSONB       NOT NULL DEFAULT '{}'::jsonb,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, user_id)
);

-- ---------------------------------------------------------------------------
-- 8. 后台任务表（迁移 0010 的入网落点）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS background_job (
    tenant_id    TEXT        NOT NULL,
    job_id       TEXT        NOT NULL,
    kind         TEXT        NOT NULL
                 CHECK (kind IN ('memory_extract','tenant_export','retention_cleanup')),
    dedupe_key   TEXT        NOT NULL DEFAULT '',
    payload      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    status       TEXT        NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
    attempts     SMALLINT    NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts SMALLINT    NOT NULL DEFAULT 3 CHECK (max_attempts >= 1),
    run_after    TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_by    TEXT        NOT NULL DEFAULT '',
    locked_until TIMESTAMPTZ,
    last_error   TEXT        NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, job_id)
);
CREATE INDEX IF NOT EXISTS idx_job_due
    ON background_job (run_after) WHERE status IN ('queued','running');
CREATE UNIQUE INDEX IF NOT EXISTS uq_job_dedupe
    ON background_job (tenant_id, kind, dedupe_key)
    WHERE dedupe_key <> '' AND status IN ('queued','running');

-- ---------------------------------------------------------------------------
-- 9. RLS + 授权：记忆三表与任务表都进 strict 组
-- ---------------------------------------------------------------------------
DO $rls_mem$
DECLARE
    strict_tables TEXT[] := ARRAY['user_memory', 'memory_embedding',
                                  'user_memory_setting', 'background_job'];
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
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'officeai_app') THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON user_memory TO officeai_app';
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON memory_embedding TO officeai_app';
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON user_memory_setting TO officeai_app';
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON background_job TO officeai_app';
    END IF;
    RAISE NOTICE 'phase4: 记忆三表与 background_job 已启用 FORCE RLS 并授权';
END $rls_mem$;

-- ---------------------------------------------------------------------------
-- 10. 租户月度预算（阶段四 0011 / T4-04.3，架构 §3.4 / §7.5）
--
-- 为什么新建而不是改计费表：PRD 说的 `tenant_quota` **不存在**（额度来自
-- `billing_subscription`），而计费域在阶段二已冻结 → 新开一张三列小表，
-- 由运营后台写入、由成本护栏读取，计费表零改动。
-- `monthly_budget_units = 0` 表示**没设额度**，`enabled = false` 表示**明确关掉**：
-- 两者都不拦，但排障时必须能区分，所以两个字段都留。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tenant_budget (
    tenant_id            TEXT        NOT NULL,
    monthly_budget_units BIGINT      NOT NULL DEFAULT 0
                         CHECK (monthly_budget_units >= 0),
    warn_ratio           NUMERIC(3,2) NOT NULL DEFAULT 0.80
                         CHECK (warn_ratio > 0 AND warn_ratio <= 1),
    enabled              BOOLEAN     NOT NULL DEFAULT TRUE,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id)
);

DO $rls_budget$
BEGIN
    EXECUTE 'ALTER TABLE tenant_budget ENABLE ROW LEVEL SECURITY';
    EXECUTE 'ALTER TABLE tenant_budget FORCE ROW LEVEL SECURITY';
    EXECUTE 'DROP POLICY IF EXISTS p_tenant_isolation ON tenant_budget';
    EXECUTE $policy$
        CREATE POLICY p_tenant_isolation ON tenant_budget
            USING (tenant_id = current_setting('app.tenant_id', TRUE)
                OR current_setting('app.tenant_id', TRUE) = '__platform__')
            WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE)
                OR current_setting('app.tenant_id', TRUE) = '__platform__')
    $policy$;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'officeai_app') THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_budget TO officeai_app';
    END IF;
    RAISE NOTICE 'phase4: tenant_budget 已启用 FORCE RLS 并授权';
END $rls_budget$;
