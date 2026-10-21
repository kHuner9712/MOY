# ===========================================================================
# SaaS 模式多租户隔离初始化（PostgreSQL 行级安全策略）
#
# 架构依据（架构文档 1.3 / 10.2 D-05）：
#   - SaaS 多租户采用「PostgreSQL RLS + 应用层 tenant_id 双保险」；
#   - 本文件在数据库首次初始化时执行（docker-entrypoint-initdb.d）。
#
# ⚠️ D-05 待决：Milvus 是否接受共享实例按 partition/collection 前缀隔离，
#    需架构师与主理人确认。当前实现为共享实例 + 集合名前缀隔离。
# ===========================================================================

-- ---------------------------------------------------------------------------
-- 为业务表启用行级安全策略
-- 说明：示例以审计表为例，实际表结构由业务能力层在 S2 定义。
-- 策略函数从会话变量 app.tenant_id 取当前租户，未设置时默认拒绝全部访问。
-- ---------------------------------------------------------------------------

-- 审计日志表（示例结构，与业务能力层约定保持一致）
CREATE TABLE IF NOT EXISTS audit_log (
    id           BIGSERIAL PRIMARY KEY,
    tenant_id    TEXT        NOT NULL,
    actor_id     TEXT        NOT NULL,
    action       TEXT        NOT NULL,
    resource_type TEXT       NOT NULL,
    resource_id  TEXT,
    result       TEXT        NOT NULL DEFAULT 'success',
    occurred_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_log_tenant_time
    ON audit_log (tenant_id, occurred_at DESC);

-- 启用行级安全（表所有者默认绕过策略，应用连接需使用非超级用户）
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log FORCE ROW LEVEL SECURITY;

-- 租户隔离策略：仅可见当前会话租户的数据
CREATE POLICY tenant_isolation_select ON audit_log
    FOR SELECT
    USING (tenant_id = current_setting('app.tenant_id', TRUE));

CREATE POLICY tenant_isolation_insert ON audit_log
    FOR INSERT
    WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE));

CREATE POLICY tenant_isolation_update ON audit_log
    FOR UPDATE
    USING (tenant_id = current_setting('app.tenant_id', TRUE))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE));

-- ---------------------------------------------------------------------------
-- 说明：
--   1. 应用层仍须在每个查询显式携带 tenant_id 条件（双保险，架构文档 1.3）；
--   2. 适配层使用连接池时，每次借出连接前须执行
--      `SELECT set_config('app.tenant_id', $1, FALSE)` 设置会话变量；
--   3. 该初始化脚本仅在数据卷首次创建时执行，后续变更请走迁移脚本。
-- ---------------------------------------------------------------------------
