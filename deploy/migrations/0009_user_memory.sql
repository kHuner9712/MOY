-- ===========================================================================
-- 0009 记忆内核三表（阶段四 T4-03.1，架构 §3.3）
--
-- `user_memory`（事实）/ `memory_embedding`（向量）/ `user_memory_setting`（开关）。
-- 记忆是**个人数据**：RLS 只保证租户边界，`user_id` 边界由仓储层强制。
--
-- ⚠ **`CREATE EXTENSION vector` 需要超级用户**（实测 pgvector 0.8.7 的
--   `pg_available_extension_versions.trusted = false`）。应用角色 `officeai_app`
--   是 NOSUPERUSER，**永远建不了这个扩展**——所以：
--     · 新装库：`init-schema-phase4.sql`（initdb 以超级用户执行）已建好扩展，
--       本迁移走"扩展已在"的零 DDL 路径；
--     · 存量库：由 DBA 执行 `CREATE EXTENSION IF NOT EXISTS vector`
--       （或重跑 init 脚本），否则本迁移报可照做的错。
--   把 `CREATE EXTENSION` 写成硬性第一步、不做降级，会让升级路径直接死在第一句。
--
-- 维度固定 1024（`vector(1024)`）是**索引前提**：换 embedding 模型必须重建该列
-- （先 DROP INDEX 再 ALTER COLUMN TYPE），写进运维手册（决策 A-2）。
--
-- 幂等性：`IF NOT EXISTS` + 存在性守卫；重复执行零改动。
-- 回滚：`rollback/0009_user_memory.down.sql`（⚠ 删表，回滚前强制 pg_dump 备份）。
-- ===========================================================================

DO $guard$
DECLARE
    missing TEXT[];
BEGIN
    -- ① 扩展：已存在就跳过；不存在且无权 → 可照做的错
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
        BEGIN
            CREATE EXTENSION IF NOT EXISTS vector;
        EXCEPTION
            WHEN insufficient_privilege THEN
                RAISE EXCEPTION
                    '0009 需要 pgvector 扩展（CREATE EXTENSION vector），但当前角色不是超级用户。'
                    '请由 DBA 执行：CREATE EXTENSION IF NOT EXISTS vector;'
                    '（或重跑 deploy/init-schema-phase4.sql，它已包含该语句）';
        END;
    END IF;

    -- ② 三张表齐全 → 零 DDL（新装库）
    SELECT array_agg(name) INTO missing
      FROM (VALUES ('user_memory'), ('memory_embedding'), ('user_memory_setting'))
             AS wanted(name)
     WHERE to_regclass('public.' || name) IS NULL;

    IF missing IS NULL THEN
        RAISE NOTICE '0009: 记忆三表已存在（init-schema-phase4.sql 建的新库），零 DDL';
        RETURN;
    END IF;

    BEGIN
        EXECUTE $ddl$
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
            )
        $ddl$;

        -- 召回主索引：只覆盖"未被取代"的行（`superseded_by IS NULL`）
        EXECUTE $ddl$
            CREATE INDEX IF NOT EXISTS idx_memory_recall
                ON user_memory (tenant_id, user_id, category, updated_at DESC)
                WHERE superseded_by IS NULL
        $ddl$;
        -- 同义去重键：只对非空 hash 且未取代的行唯一
        EXECUTE $ddl$
            CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_hash
                ON user_memory (tenant_id, user_id, category, normalized_hash)
                WHERE superseded_by IS NULL AND normalized_hash <> ''
        $ddl$;

        EXECUTE $ddl$
            CREATE TABLE IF NOT EXISTS memory_embedding (
                tenant_id  TEXT        NOT NULL,
                memory_id  TEXT        NOT NULL,
                model      TEXT        NOT NULL DEFAULT '',
                dim        INTEGER     NOT NULL,
                embedding  vector(1024) NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (tenant_id, memory_id)
            )
        $ddl$;
        -- HNSW 余弦索引：召回 top-k 的延迟来源
        EXECUTE $ddl$
            CREATE INDEX IF NOT EXISTS idx_memory_embedding_hnsw
                ON memory_embedding USING hnsw (embedding vector_cosine_ops)
        $ddl$;

        EXECUTE $ddl$
            CREATE TABLE IF NOT EXISTS user_memory_setting (
                tenant_id         TEXT        NOT NULL,
                user_id           TEXT        NOT NULL,
                enabled           BOOLEAN     NOT NULL DEFAULT TRUE,
                category_switches JSONB       NOT NULL DEFAULT '{}'::jsonb,
                updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (tenant_id, user_id)
            )
        $ddl$;

        -- ③ RLS：strict（ENABLE + FORCE + p_tenant_isolation）
        EXECUTE 'ALTER TABLE user_memory ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE user_memory FORCE ROW LEVEL SECURITY';
        EXECUTE 'DROP POLICY IF EXISTS p_tenant_isolation ON user_memory';
        EXECUTE $ddl$
            CREATE POLICY p_tenant_isolation ON user_memory
                USING (tenant_id = current_setting('app.tenant_id', TRUE)
                    OR current_setting('app.tenant_id', TRUE) = '__platform__')
                WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE)
                    OR current_setting('app.tenant_id', TRUE) = '__platform__')
        $ddl$;

        EXECUTE 'ALTER TABLE memory_embedding ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE memory_embedding FORCE ROW LEVEL SECURITY';
        EXECUTE 'DROP POLICY IF EXISTS p_tenant_isolation ON memory_embedding';
        EXECUTE $ddl$
            CREATE POLICY p_tenant_isolation ON memory_embedding
                USING (tenant_id = current_setting('app.tenant_id', TRUE)
                    OR current_setting('app.tenant_id', TRUE) = '__platform__')
                WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE)
                    OR current_setting('app.tenant_id', TRUE) = '__platform__')
        $ddl$;

        EXECUTE 'ALTER TABLE user_memory_setting ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE user_memory_setting FORCE ROW LEVEL SECURITY';
        EXECUTE 'DROP POLICY IF EXISTS p_tenant_isolation ON user_memory_setting';
        EXECUTE $ddl$
            CREATE POLICY p_tenant_isolation ON user_memory_setting
                USING (tenant_id = current_setting('app.tenant_id', TRUE)
                    OR current_setting('app.tenant_id', TRUE) = '__platform__')
                WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE)
                    OR current_setting('app.tenant_id', TRUE) = '__platform__')
        $ddl$;

        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'officeai_app') THEN
            EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON user_memory TO officeai_app';
            EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON memory_embedding TO officeai_app';
            EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON user_memory_setting TO officeai_app';
        END IF;
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE EXCEPTION
                '0009 需要创建记忆表（缺 %），但当前角色不是库属主。请二选一：'
                '① 用超级用户执行 deploy/init-schema-phase4.sql（幂等）后重跑 --apply；'
                '② 由 DBA 以表属主身份执行本迁移。',
                missing;
    END;

    RAISE NOTICE '0009: 记忆三表已建成（含 RLS / 索引 / 授权）';
END $guard$;
