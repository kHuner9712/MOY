-- ===========================================================================
-- 应用连接角色初始化（挂载为 docker-entrypoint-initdb.d/30-init-app-role.sql）
--
-- 背景（2026-10-21 实跑发现的高危缺口）：
--   POSTGRES_USER=officeai 由 initdb 创建，是**超级用户**。而超级用户**永久绕过
--   行级安全策略**（BYPASSRLS），`FORCE ROW LEVEL SECURITY` 同样约束不住它。
--   此前所有部署脚本只建了这个超级用户，导致：
--     1) 多租户隔离在真实部署中完全失效（策略存在但不生效）；
--     2) 用该账号跑隔离测试会全部"通过"——假阳性，比不测更危险。
--   适配层的 `/readyz` 已带 `db_privilege` 自检（检测到 BYPASSRLS 即 not_ready），
--   但若没有可用的普通角色，运维看到 not_ready 时无从修复。本文件补上这个角色。
--
-- 执行顺序：必须排在 20-init-schema-phase2.sql **之后**（表不存在则 GRANT 无意义）。
--
-- ⚠️ 强制运维步骤（本脚本无法从环境变量取密码，SQL 无变量）：
--   本文件创建角色时使用占位密码，首次部署后必须立即执行：
--     ALTER ROLE officeai_app PASSWORD '<强随机密码>';
--   并把该密码写入 .env 的 OA_DATABASE_URL。
--   未完成此步骤前，应用无法连接数据库（刻意设计：宁可连不上，也不让
--   运维沿用超级用户绕过隔离策略）。
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 1. 创建应用角色（幂等）
--    NOSUPERUSER + NOBYPASSRLS 是核心：确保行级安全策略对其生效
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'officeai_app') THEN
        CREATE ROLE officeai_app
            LOGIN
            NOSUPERUSER
            NOBYPASSRLS
            NOCREATEDB
            NOCREATEROLE
            NOREPLICATION
            PASSWORD 'CHANGE_ME_APP_PASSWORD';
    ELSE
        -- 已存在时强制校正关键属性，防止运维手工建角色时漏掉 NOBYPASSRLS
        ALTER ROLE officeai_app NOSUPERUSER NOBYPASSRLS NOREPLICATION;
    END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- 2. 库与模式权限
-- ---------------------------------------------------------------------------
GRANT CONNECT ON DATABASE officeai TO officeai_app;
GRANT USAGE ON SCHEMA public TO officeai_app;

-- ---------------------------------------------------------------------------
-- 3. 表与序列权限
--    DML 全给；DDL（CREATE/DROP/TRUNCATE）一律不给——表结构变更只能走迁移脚本
-- ---------------------------------------------------------------------------
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO officeai_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO officeai_app;

-- 后续迁移新建的表同样自动获得权限（仅对执行者 officeai 创建的对象生效）
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO officeai_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO officeai_app;

-- ---------------------------------------------------------------------------
-- 4. 操作记录只增不改（PRD：审计不可篡改）
-- ---------------------------------------------------------------------------
REVOKE UPDATE, DELETE ON audit_log FROM officeai_app;

-- ---------------------------------------------------------------------------
-- 5. 自检证据（部署日志中应看到 rolbypassrls = f）
--    SELECT rolname, rolbypassrls FROM pg_roles WHERE rolname = 'officeai_app';
--    期望：officeai_app | f
--    若显示 t，说明本脚本未生效或角色被改动，此时 /readyz 会判 not_ready。
-- ---------------------------------------------------------------------------
