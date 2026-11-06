-- ===========================================================================
-- 0015 MCP server 登记（矩阵第 3 项，2026-11-06）
--
-- 只建一张**声明表**，不存任何凭据明文：
--   endpoint         http(s) 地址，或 stdio 可执行文件路径
--   args             stdio 启动参数（JSON 数组）
--   credential_ref   凭据**引用**（明文在 credential_store 的密文里）
--   allowed_tools    工具白名单（JSON 数组）
--   enabled          默认 false（能力开关关闭时整表不可用）
--
-- 幂等性：建表/索引/策略均按存在性守卫；新装库已由 `init-schema-phase3.sql`
-- 建好（零 DDL 路径），本迁移只补守卫判定。**不要**把 init-schema 中的建表删掉
-- 只留本迁移——应用角色在 public 无 CREATE，第一次 --apply 会撞「不是表属主」。
--
-- 回滚：`rollback/0015_mcp_server.down.sql`（⚠ 会丢失全部登记）。
-- ===========================================================================

DO $mcp$
DECLARE
    table_exists BOOLEAN;
    index_exists BOOLEAN;
    policy_exists BOOLEAN;
BEGIN
    table_exists := to_regclass('public.mcp_server') IS NOT NULL;

    IF NOT table_exists THEN
        BEGIN
            EXECUTE $ddl$
                CREATE TABLE mcp_server (
                    tenant_id        TEXT        NOT NULL,
                    server_id        TEXT        NOT NULL
                                     CHECK (server_id ~ '^mcp_[A-Za-z0-9]{6,40}$'),
                    name             TEXT        NOT NULL
                                     CHECK (name ~ '^[a-z][a-z0-9_-]{1,31}$'),
                    transport        TEXT        NOT NULL
                                     CHECK (transport IN ('http','stdio')),
                    endpoint         TEXT        NOT NULL DEFAULT '',
                    args             JSONB       NOT NULL DEFAULT '[]'::jsonb
                                     CHECK (jsonb_typeof(args) = 'array'),
                    credential_ref   TEXT        NOT NULL DEFAULT '',
                    allowed_tools    JSONB       NOT NULL DEFAULT '[]'::jsonb
                                     CHECK (jsonb_typeof(allowed_tools) = 'array'),
                    enabled          BOOLEAN     NOT NULL DEFAULT FALSE,
                    timeout_ms       INTEGER     NOT NULL DEFAULT 15000
                                     CHECK (timeout_ms BETWEEN 1000 AND 120000),
                    max_output_bytes INTEGER     NOT NULL DEFAULT 65536
                                     CHECK (max_output_bytes BETWEEN 1024 AND 1048576),
                    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (tenant_id, server_id),
                    CONSTRAINT uq_mcp_server_name UNIQUE (tenant_id, name)
                )
            $ddl$;
        EXCEPTION
            WHEN insufficient_privilege THEN
                RAISE EXCEPTION
                    '0015 需要 CREATE TABLE mcp_server，但当前角色在 public 上没有 CREATE 权限。'
                    '请以表属主（或 DBA）身份执行本迁移；以 officeai_app 执行不会成功——'
                    '它在 public 上没有 CREATE 权限，这是 RLS 隔离的既定设计。';
        END;
        RAISE NOTICE '0015: 已创建 mcp_server 表';
    ELSE
        RAISE NOTICE '0015: mcp_server 表已存在（新库直建），零 DDL';
    END IF;

    index_exists := to_regclass('public.idx_mcp_server_enabled') IS NOT NULL;
    IF NOT index_exists THEN
        BEGIN
            EXECUTE $ddl$
                CREATE INDEX idx_mcp_server_enabled
                    ON mcp_server (tenant_id, enabled)
            $ddl$;
        EXCEPTION
            WHEN insufficient_privilege THEN
                RAISE EXCEPTION
                    '0015 建索引需要 mcp_server 的属主权限，请由表属主执行。';
        END;
        RAISE NOTICE '0015: idx_mcp_server_enabled 就绪';
    ELSE
        RAISE NOTICE '0015: idx_mcp_server_enabled 已存在，零 DDL';
    END IF;

    -- 严格 RLS：表达式与 init-schema 的 `p_tenant_isolation` 完全一致。
    BEGIN
        EXECUTE 'ALTER TABLE mcp_server ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE mcp_server FORCE ROW LEVEL SECURITY';
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE EXCEPTION
                '0015 启用行级安全需要 mcp_server 的属主权限，请由表属主执行。';
    END;

    SELECT EXISTS (
               SELECT 1
                 FROM pg_policies p
                WHERE p.schemaname = 'public'
                  AND p.tablename = 'mcp_server'
                  AND p.policyname = 'p_tenant_isolation'
           ) INTO policy_exists;
    IF NOT policy_exists THEN
        EXECUTE $ddl$
            CREATE POLICY p_tenant_isolation ON mcp_server
            USING (tenant_id = current_setting('app.tenant_id', TRUE)
                OR current_setting('app.tenant_id', TRUE) = '__platform__')
            WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE)
                OR current_setting('app.tenant_id', TRUE) = '__platform__')
        $ddl$;
        RAISE NOTICE '0015: p_tenant_isolation 就绪';
    ELSE
        RAISE NOTICE '0015: p_tenant_isolation 已存在，零 DDL';
    END IF;
END $mcp$;
