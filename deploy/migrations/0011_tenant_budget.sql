-- ===========================================================================
-- 0011 租户月度预算（阶段四 T4-04.3，架构 §3.4 / §7.5）
--
-- 为什么新建这张表而不是改计费表：PRD 写"`tenant_quota` 增月度额度"，但**该表不存在**
-- （实库 34 表无此名，额度来自 `billing_subscription`，经 `usage_repo.quota()` 读取）。
-- 计费域在阶段二已冻结（D3：不动计费 6 端点），所以新开一张两列的小表，
-- 由运营后台写入、由成本护栏读取，**计费表零改动**。
--
-- 语义：
--   - `monthly_budget_units = 0` → **不限额**（默认），护栏只按套餐额度判；
--   - `warn_ratio`（默认 0.80）→ 到达该比例时在响应 `data` 上带**非阻断**提示；
--   - `enabled = false` → 本租户不启用预算护栏（与 0 的区别：0 是"没设额度"，
--     false 是"明确关掉"，排障时要能区分，所以两个字段都留着）。
--
-- 幂等性：`IF NOT EXISTS` + 存在性守卫；重复执行零改动。
-- 回滚：`rollback/0011_tenant_budget.down.sql`（⚠ 删表会丢已配置的预算，回滚前先导出）。
-- ===========================================================================

DO $guard$
DECLARE
    missing TEXT[];
BEGIN
    SELECT array_agg(name) INTO missing
      FROM (VALUES ('tenant_budget')) AS wanted(name)
     WHERE to_regclass('public.' || name) IS NULL;

    IF missing IS NULL THEN
        RAISE NOTICE '0011: tenant_budget 已存在（init-schema-phase4.sql 建的新库），零 DDL';
        RETURN;
    END IF;

    BEGIN
        EXECUTE $ddl$
            CREATE TABLE IF NOT EXISTS tenant_budget (
                tenant_id            TEXT        NOT NULL,
                monthly_budget_units BIGINT      NOT NULL DEFAULT 0
                                     CHECK (monthly_budget_units >= 0),
                warn_ratio           NUMERIC(3,2) NOT NULL DEFAULT 0.80
                                     CHECK (warn_ratio > 0 AND warn_ratio <= 1),
                enabled              BOOLEAN     NOT NULL DEFAULT TRUE,
                updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (tenant_id)
            )
        $ddl$;

        EXECUTE 'ALTER TABLE tenant_budget ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE tenant_budget FORCE ROW LEVEL SECURITY';
        EXECUTE 'DROP POLICY IF EXISTS p_tenant_isolation ON tenant_budget';
        EXECUTE $ddl$
            CREATE POLICY p_tenant_isolation ON tenant_budget
                USING (tenant_id = current_setting('app.tenant_id', TRUE)
                    OR current_setting('app.tenant_id', TRUE) = '__platform__')
                WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE)
                    OR current_setting('app.tenant_id', TRUE) = '__platform__')
        $ddl$;

        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'officeai_app') THEN
            EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_budget TO officeai_app';
        END IF;
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE EXCEPTION
                '0011 需要创建 tenant_budget 表，但当前角色不是库属主。请由 DBA 以属主'
                '身份执行本迁移，或用超级用户重跑 deploy/init-schema-phase4.sql 后 --apply。';
    END;

    RAISE NOTICE '0011: tenant_budget 已建成（含 RLS / 授权）';
END $guard$;
