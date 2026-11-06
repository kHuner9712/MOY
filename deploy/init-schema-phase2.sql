-- ===========================================================================
-- 阶段二业务库初始化（第 9-16 周）
--
-- 架构依据：docs/architecture-phase2.md 第 3 章（D-02 业务库归属结论）
-- 执行方式：
--   1) 容器首次启动：挂载为 docker-entrypoint-initdb.d/20-init-schema-phase2.sql（B01 批次）
--      必须排在 10-init-rls.sql **之后**执行：本脚本末尾会 DROP 旧策略名
--      tenant_isolation_* 并重建统一的 p_tenant_isolation / p_catalog_shared。
--   2) 已有数据卷：python -m app.db.migrate（按 schema_migration 版本表增量执行）
--   3) 手工核验：psql -U officeai -d officeai -f deploy/init-schema-phase2.sql
--
-- 设计约束（本文件逐条落实）：
--   - 全部业务表含 tenant_id，并启用 FORCE ROW LEVEL SECURITY；
--   - 平台级目录表（套餐/权限点/系统配置）以 tenant_id = '__platform__' 存平台行，
--     租户会话可只读共享，写入仍受 WITH CHECK 约束；
--   - 本脚本幂等（IF NOT EXISTS / DROP POLICY IF EXISTS），可重复执行；
--   - 不使用 SERIAL（与「前缀 + ULID」ID 规范一致），仅审计表沿用既有 BIGSERIAL。
--
-- ⚠️ 已被本文件取代的旧脚本：deploy/init-rls.sql（仅建 audit_log 与三条策略）。
--    本文件重复声明 audit_log（IF NOT EXISTS）并重建统一策略，故二者同时挂载不会冲突。
-- ===========================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 会话变量：本脚本以表所有者身份执行，而所有业务表启用了 FORCE ROW LEVEL SECURITY
-- （所有者同样受策略约束）。脚本内置平台种子数据写入，故此处显式置为平台模式；
-- 该 SET 仅作用于本脚本所在连接，连接关闭即失效。
-- 应用运行时由 db/session.py 在每个事务内用
--   SELECT set_config('app.tenant_id', $1, TRUE)   -- is_local=TRUE，事务级
-- 重新设置，绝不继承连接池中的历史值。
-- ---------------------------------------------------------------------------
SET app.tenant_id = '__platform__';

-- ---------------------------------------------------------------------------
-- 0. 迁移版本表（唯一由应用写入的元数据表）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_migration (
    version     TEXT        PRIMARY KEY,
    checksum    TEXT        NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO schema_migration (version, checksum)
VALUES ('0001_phase2_init', 'sha256:managed-by-app-db-migrate')
ON CONFLICT (version) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 1. 企业（租户主表）
--    注：私有化模式仅一行，tenant_id = 'default'
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tenant (
    tenant_id    TEXT        PRIMARY KEY,
    name         TEXT        NOT NULL,
    status       TEXT        NOT NULL DEFAULT 'active'
                             CHECK (status IN ('active', 'suspended')),
    deploy_mode  TEXT        NOT NULL DEFAULT 'saas'
                             CHECK (deploy_mode IN ('saas', 'private')),
    plan_code    TEXT        NOT NULL DEFAULT 'free',
    suspended_reason TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- 2. 知识库元数据（D-10 凭据绑定落在此表；R1/R2 的库级状态权威源）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS knowledge_base_meta (
    tenant_id       TEXT        NOT NULL,
    base_id         TEXT        NOT NULL,
    org_id          TEXT,
    name            TEXT        NOT NULL CHECK (char_length(name) BETWEEN 1 AND 128),
    description     TEXT        NOT NULL DEFAULT '' CHECK (char_length(description) <= 1024),
    visibility      TEXT        NOT NULL DEFAULT 'organization'
                                CHECK (visibility IN ('organization', 'private', 'restricted')),
    chunk_policy    TEXT        NOT NULL DEFAULT 'default'
                                CHECK (chunk_policy IN ('default', 'fine')),
    owner_user_id   TEXT        NOT NULL,
    credential_ref  TEXT,
    document_count  INTEGER     NOT NULL DEFAULT 0 CHECK (document_count >= 0),
    deleted_at      TIMESTAMPTZ,
    created_by      TEXT        NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, base_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_kb_meta_name
    ON knowledge_base_meta (tenant_id, lower(name))
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_kb_meta_owner
    ON knowledge_base_meta (tenant_id, owner_user_id)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_kb_meta_credential
    ON knowledge_base_meta (tenant_id, credential_ref)
    WHERE credential_ref IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 3. 知识库成员授权（P2-KB-03；subject 三类：user / department / role）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS knowledge_base_grant (
    tenant_id     TEXT        NOT NULL,
    grant_id      TEXT        NOT NULL,
    base_id       TEXT        NOT NULL,
    subject_type  TEXT        NOT NULL
                              CHECK (subject_type IN ('user', 'department', 'role')),
    subject_id    TEXT        NOT NULL,
    permission    TEXT        NOT NULL
                              CHECK (permission IN ('viewer', 'editor')),
    granted_by    TEXT        NOT NULL,
    granted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at    TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, grant_id),
    CONSTRAINT fk_grant_base FOREIGN KEY (tenant_id, base_id)
        REFERENCES knowledge_base_meta (tenant_id, base_id) ON DELETE CASCADE
);

-- 同一对象在同一库上的有效授权唯一（撤销用 revoked_at 标记，不物理删除，留审计痕迹）
CREATE UNIQUE INDEX IF NOT EXISTS uq_grant_active
    ON knowledge_base_grant (tenant_id, base_id, subject_type, subject_id)
    WHERE revoked_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_grant_subject
    ON knowledge_base_grant (tenant_id, subject_type, subject_id)
    WHERE revoked_at IS NULL;

-- ---------------------------------------------------------------------------
-- 4. 文档元数据（P2-KB-02 五阶段进度；R3/R4 扩展字段权威源）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS document_meta (
    tenant_id                  TEXT        NOT NULL,
    document_id                TEXT        NOT NULL,
    base_id                    TEXT        NOT NULL,
    file_name                  TEXT        NOT NULL,
    file_size                  BIGINT      NOT NULL DEFAULT 0 CHECK (file_size >= 0),
    content_sha256             CHAR(64),
    chunk_policy               TEXT        NOT NULL DEFAULT 'default'
                                           CHECK (chunk_policy IN ('default', 'fine')),
    stage                      TEXT        NOT NULL DEFAULT 'uploaded'
                                           CHECK (stage IN ('uploaded', 'parsing',
                                                            'preparing', 'completed', 'failed')),
    index_status               TEXT        NOT NULL DEFAULT 'processing'
                                           CHECK (index_status IN ('processing', 'completed',
                                                                   'failed')),
    progress                   SMALLINT    NOT NULL DEFAULT 0
                                           CHECK (progress BETWEEN 0 AND 100),
    estimated_remaining_seconds INTEGER,
    segment_count              INTEGER     NOT NULL DEFAULT 0 CHECK (segment_count >= 0),
    run_id                     TEXT,
    error_code                 TEXT,
    error_message              TEXT,
    uploader_user_id           TEXT        NOT NULL,
    uploaded_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at               TIMESTAMPTZ,
    deleted_at                 TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, document_id),
    CONSTRAINT fk_doc_base FOREIGN KEY (tenant_id, base_id)
        REFERENCES knowledge_base_meta (tenant_id, base_id) ON DELETE CASCADE,
    -- 兼容字段一致性：completed 三态与五阶段必须同真同假（PRD P2-KB-02 第 2 条）
    CONSTRAINT ck_doc_status_stage CHECK ((index_status = 'completed') = (stage = 'completed')),
    CONSTRAINT ck_doc_failed_stage CHECK ((index_status = 'failed') = (stage = 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_doc_base_time
    ON document_meta (tenant_id, base_id, uploaded_at DESC)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_doc_base_stage
    ON document_meta (tenant_id, base_id, stage)
    WHERE deleted_at IS NULL;

-- ---------------------------------------------------------------------------
-- 5. 片段稳定映射表（D-11：segment_key 稳定标识）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS segment_map (
    tenant_id          TEXT        NOT NULL,
    segment_key        TEXT        NOT NULL,                 -- seg_ + sha256(...)[:16]
    base_id            TEXT        NOT NULL,
    document_id        TEXT        NOT NULL,
    run_id             TEXT        NOT NULL,                 -- 处理批次（R5 重新处理换 run）
    segment_index      INTEGER     NOT NULL CHECK (segment_index >= 0),
    native_segment_id  TEXT,
    content_sha256     CHAR(64)    NOT NULL,
    content_excerpt    TEXT        NOT NULL DEFAULT '',
    position_page      INTEGER,
    position_paragraph INTEGER,
    status             TEXT        NOT NULL DEFAULT 'valid'
                                   CHECK (status IN ('valid', 'stale', 'deleted')),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    stale_at           TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, segment_key),
    CONSTRAINT fk_seg_base FOREIGN KEY (tenant_id, base_id)
        REFERENCES knowledge_base_meta (tenant_id, base_id) ON DELETE CASCADE,
    CONSTRAINT fk_seg_doc FOREIGN KEY (tenant_id, document_id)
        REFERENCES document_meta (tenant_id, document_id) ON DELETE CASCADE
);

-- 槽位唯一：同一文档同一批次下的第 N 片唯一（重处理换 run_id 后自然换槽）
CREATE UNIQUE INDEX IF NOT EXISTS uq_seg_slot
    ON segment_map (tenant_id, document_id, run_id, segment_index);

CREATE INDEX IF NOT EXISTS idx_seg_doc_status
    ON segment_map (tenant_id, document_id, status);

CREATE INDEX IF NOT EXISTS idx_seg_stale_gc
    ON segment_map (tenant_id, stale_at)
    WHERE status = 'stale';

-- ---------------------------------------------------------------------------
-- 6. 底座访问凭据（D-10：AES-256-GCM 密文存储，明文只在进程内存）
--    本表任何字段都不得出现在任何接口响应中
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS credential_store (
    tenant_id     TEXT        NOT NULL,
    credential_ref TEXT       NOT NULL,                      -- cred_ + ULID
    provider      TEXT        NOT NULL DEFAULT 'knowledge_base',
    cipher_text   BYTEA       NOT NULL,
    nonce         BYTEA       NOT NULL,
    key_version   SMALLINT    NOT NULL DEFAULT 1,
    status        TEXT        NOT NULL DEFAULT 'active'
                              CHECK (status IN ('active', 'revoked')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    rotated_at    TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, credential_ref)
);

CREATE INDEX IF NOT EXISTS idx_credential_status
    ON credential_store (tenant_id, status);

-- ---------------------------------------------------------------------------
-- 7. 工具连接与调用记录（T2/T3/T4 权威源 + TL1 调用记录列表）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tool_connection (
    tenant_id       TEXT        NOT NULL,
    conn_id         TEXT        NOT NULL,
    tool_type       TEXT        NOT NULL,
    scope           TEXT        NOT NULL DEFAULT 'personal'
                                CHECK (scope IN ('personal', 'organization')),
    owner_user_id   TEXT,
    org_id          TEXT,
    status          TEXT        NOT NULL DEFAULT 'connected'
                                CHECK (status IN ('connected', 'expired', 'revoked')),
    secret_cipher   BYTEA,
    secret_nonce    BYTEA,
    key_version     SMALLINT    NOT NULL DEFAULT 1,
    expires_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, conn_id),
    CONSTRAINT ck_conn_scope_owner
        CHECK ((scope = 'personal' AND owner_user_id IS NOT NULL)
               OR (scope = 'organization' AND org_id IS NOT NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_conn_active
    ON tool_connection (tenant_id, tool_type, scope, coalesce(owner_user_id, ''),
                        coalesce(org_id, ''))
    WHERE status = 'connected';

CREATE TABLE IF NOT EXISTS tool_execution (
    tenant_id            TEXT        NOT NULL,
    execution_id         TEXT        NOT NULL,
    conn_id              TEXT,
    tool_type            TEXT        NOT NULL,
    operation            TEXT        NOT NULL,
    status               TEXT        NOT NULL CHECK (status IN ('success', 'failed')),
    error_code           INTEGER,
    error_message        TEXT,
    result_summary       JSONB,
    operator_user_id     TEXT        NOT NULL,
    duration_ms          INTEGER     NOT NULL DEFAULT 0,
    executed_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, execution_id)
);

CREATE INDEX IF NOT EXISTS idx_exec_tool_time
    ON tool_execution (tenant_id, tool_type, executed_at DESC);

CREATE INDEX IF NOT EXISTS idx_exec_operator_time
    ON tool_execution (tenant_id, operator_user_id, executed_at DESC);

-- ---------------------------------------------------------------------------
-- 8. 用量事件与月度聚合（D-12：唯一计量真相源）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS usage_event (
    tenant_id      TEXT        NOT NULL,
    event_id       TEXT        NOT NULL,                     -- use_ + ULID
    org_id         TEXT,
    department_id  TEXT,
    user_id        TEXT        NOT NULL,
    capability     TEXT        NOT NULL
                               CHECK (capability IN ('chat', 'doc_qa',
                                                     'doc_process', 'automation')),
    resource_type  TEXT        NOT NULL DEFAULT '',
    resource_id    TEXT        NOT NULL DEFAULT '',
    idem_key       TEXT        NOT NULL,                     -- generation_id / document_id / task_id
    units          BIGINT      NOT NULL CHECK (units >= 0),
    period         CHAR(7)     NOT NULL,                     -- YYYY-MM（UTC）
    source         TEXT        NOT NULL DEFAULT 'settled'
                               CHECK (source IN ('settled', 'estimated')),
    occurred_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, event_id)
);

-- 幂等：同租户同能力同幂等键只允许一条（D-12 第 2 条）
CREATE UNIQUE INDEX IF NOT EXISTS uq_usage_event_idem
    ON usage_event (tenant_id, capability, idem_key);

CREATE INDEX IF NOT EXISTS idx_usage_event_period_user
    ON usage_event (tenant_id, period, user_id);

CREATE INDEX IF NOT EXISTS idx_usage_event_period_dept
    ON usage_event (tenant_id, period, department_id);

CREATE TABLE IF NOT EXISTS usage_monthly (
    tenant_id    TEXT        NOT NULL,
    period       CHAR(7)     NOT NULL,
    scope_type   TEXT        NOT NULL CHECK (scope_type IN ('tenant', 'department', 'member')),
    scope_id     TEXT        NOT NULL,
    capability   TEXT        NOT NULL
                             CHECK (capability IN ('chat', 'doc_qa',
                                                   'doc_process', 'automation')),
    units        BIGINT      NOT NULL DEFAULT 0 CHECK (units >= 0),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- scope_id 在 tenant 维度固定为租户自身 ID；member 维度为 usr_ 前缀
    PRIMARY KEY (tenant_id, period, scope_type, scope_id, capability)
);

CREATE INDEX IF NOT EXISTS idx_usage_monthly_period
    ON usage_monthly (tenant_id, period);

-- ---------------------------------------------------------------------------
-- 9. 计费：套餐目录 / 订阅 / 订单 / 账单 / 支付回调留痕
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS billing_plan (
    tenant_id                  TEXT    NOT NULL DEFAULT '__platform__',
    plan_code                  TEXT    NOT NULL
                                       CHECK (plan_code IN ('free', 'professional', 'enterprise')),
    display_name               TEXT    NOT NULL,
    price_per_seat_month_cents BIGINT  NOT NULL DEFAULT 0
                                       CHECK (price_per_seat_month_cents >= 0),
    currency                   CHAR(3) NOT NULL DEFAULT 'CNY',
    min_seats                  INTEGER NOT NULL DEFAULT 1 CHECK (min_seats >= 1),
    quota_units_per_seat_month BIGINT  NOT NULL DEFAULT 0
                                       CHECK (quota_units_per_seat_month >= 0),
    quota_unit_label           TEXT    NOT NULL DEFAULT '字数额度',
    purchasable_quota          BOOLEAN NOT NULL DEFAULT false,
    features                   JSONB   NOT NULL DEFAULT '{}'::jsonb,
    status                     TEXT    NOT NULL DEFAULT 'available'
                                       CHECK (status IN ('available', 'offline')),
    trial_days                 INTEGER NOT NULL DEFAULT 0 CHECK (trial_days >= 0),
    sort_order                 SMALLINT NOT NULL DEFAULT 0,
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, plan_code)
);

CREATE TABLE IF NOT EXISTS billing_subscription (
    tenant_id            TEXT        NOT NULL,
    subscription_id      TEXT        NOT NULL,
    plan_code            TEXT        NOT NULL,
    status               TEXT        NOT NULL
                                     CHECK (status IN ('trialing', 'active', 'past_due',
                                                       'cancelled', 'expired')),
    seats_total          INTEGER     NOT NULL DEFAULT 1 CHECK (seats_total >= 1),
    seats_used           INTEGER     NOT NULL DEFAULT 0 CHECK (seats_used >= 0),
    started_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    current_period_start TIMESTAMPTZ NOT NULL DEFAULT now(),
    current_period_end   TIMESTAMPTZ NOT NULL,
    auto_renew           BOOLEAN     NOT NULL DEFAULT true,
    pending_plan_code    TEXT,
    pending_change_at    TIMESTAMPTZ,
    grace_until          TIMESTAMPTZ,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, subscription_id)
);

-- 同一企业同一时刻只能有一个有效订阅（PRD P2-BL-02 第 5 条）
CREATE UNIQUE INDEX IF NOT EXISTS uq_subscription_active
    ON billing_subscription (tenant_id)
    WHERE status IN ('trialing', 'active', 'past_due');

CREATE TABLE IF NOT EXISTS billing_order (
    tenant_id       TEXT        NOT NULL,
    order_id        TEXT        NOT NULL,
    order_type      TEXT        NOT NULL
                                CHECK (order_type IN ('new', 'renew', 'upgrade', 'seats')),
    plan_code       TEXT        NOT NULL,
    seats           INTEGER     NOT NULL DEFAULT 1 CHECK (seats >= 1),
    amount_cents    BIGINT      NOT NULL CHECK (amount_cents >= 0),
    currency        CHAR(3)     NOT NULL DEFAULT 'CNY',
    status          TEXT        NOT NULL DEFAULT 'pending_payment'
                                CHECK (status IN ('pending_payment', 'paid', 'closed',
                                                  'cancelled', 'refunded')),
    payment_method  TEXT        NOT NULL
                                CHECK (payment_method IN ('online', 'offline_transfer')),
    pay_url         TEXT,
    bank_info       JSONB,
    payment_no_masked TEXT,
    paid_amount_cents BIGINT,
    paid_at         TIMESTAMPTZ,
    confirmed_by    TEXT,
    confirm_remark  TEXT,
    expires_at      TIMESTAMPTZ NOT NULL,
    created_by      TEXT        NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, order_id)
);

-- 同企业同周期只允许一个待支付订单（PRD P2-BL-04 第 5 条）
CREATE UNIQUE INDEX IF NOT EXISTS uq_order_pending
    ON billing_order (tenant_id)
    WHERE status = 'pending_payment';

CREATE INDEX IF NOT EXISTS idx_order_tenant_time
    ON billing_order (tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_order_status_time
    ON billing_order (status, created_at DESC);

CREATE TABLE IF NOT EXISTS billing_invoice (
    tenant_id      TEXT        NOT NULL,
    invoice_id     TEXT        NOT NULL,
    order_id       TEXT        NOT NULL,
    period         CHAR(7)     NOT NULL,
    amount_cents   BIGINT      NOT NULL CHECK (amount_cents >= 0),
    currency       CHAR(3)     NOT NULL DEFAULT 'CNY',
    status         TEXT        NOT NULL DEFAULT 'pending'
                               CHECK (status IN ('issued', 'pending')),
    download_path  TEXT,
    issued_at      TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, invoice_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_invoice_order
    ON billing_invoice (tenant_id, order_id);

CREATE TABLE IF NOT EXISTS payment_callback_log (
    tenant_id       TEXT        NOT NULL,
    callback_id     TEXT        NOT NULL,                    -- cbk_ + ULID
    channel         TEXT        NOT NULL DEFAULT 'wechat_pay',
    external_txn_id TEXT        NOT NULL,
    order_id        TEXT,
    amount_cents    BIGINT,
    signature_ok    BOOLEAN     NOT NULL DEFAULT false,
    payload_sha256  CHAR(64)    NOT NULL,
    processed       BOOLEAN     NOT NULL DEFAULT false,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, callback_id)
);

-- 回调幂等：同渠道同外部流水号只处理一次（PRD P2-BL-04 第 3 条）
CREATE UNIQUE INDEX IF NOT EXISTS uq_callback_external
    ON payment_callback_log (channel, external_txn_id);

-- ---------------------------------------------------------------------------
-- 10. 组织架构（D-13：业务库为权威源；Keycloak 仅投影）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS department (
    tenant_id       TEXT        NOT NULL,
    department_id   TEXT        NOT NULL,
    org_id          TEXT        NOT NULL,
    parent_id       TEXT,
    name            TEXT        NOT NULL CHECK (char_length(name) BETWEEN 1 AND 64),
    path            TEXT        NOT NULL,                    -- 物化路径 /dept_a/dept_b/
    level           SMALLINT    NOT NULL CHECK (level BETWEEN 1 AND 5),
    manager_user_id TEXT,
    member_count    INTEGER     NOT NULL DEFAULT 0 CHECK (member_count >= 0),
    sort_order      SMALLINT    NOT NULL DEFAULT 0,
    deleted_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, department_id),
    CONSTRAINT fk_dept_parent FOREIGN KEY (tenant_id, parent_id)
        REFERENCES department (tenant_id, department_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_dept_name
    ON department (tenant_id, org_id, coalesce(parent_id, ''), lower(name))
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_dept_path
    ON department (tenant_id, org_id, path);

CREATE TABLE IF NOT EXISTS department_member (
    tenant_id     TEXT        NOT NULL,
    department_id TEXT        NOT NULL,
    user_id       TEXT        NOT NULL,
    is_primary    BOOLEAN     NOT NULL DEFAULT false,
    joined_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, department_id, user_id),
    CONSTRAINT fk_dm_dept FOREIGN KEY (tenant_id, department_id)
        REFERENCES department (tenant_id, department_id) ON DELETE CASCADE
);

-- 主部门唯一（PRD Q2-5：成员可多部门，主部门唯一）
CREATE UNIQUE INDEX IF NOT EXISTS uq_dm_primary
    ON department_member (tenant_id, user_id)
    WHERE is_primary;

CREATE INDEX IF NOT EXISTS idx_dm_user
    ON department_member (tenant_id, user_id);

-- ---------------------------------------------------------------------------
-- 11. 角色与权限点
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS permission_catalog (
    tenant_id    TEXT     NOT NULL DEFAULT '__platform__',
    permission   TEXT     NOT NULL,                          -- resource:sub:action
    group_key    TEXT     NOT NULL,
    group_name   TEXT     NOT NULL,
    display_name TEXT     NOT NULL,
    description  TEXT     NOT NULL DEFAULT '',
    sort_order   SMALLINT NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id, permission)
);

CREATE TABLE IF NOT EXISTS role (
    tenant_id    TEXT        NOT NULL,
    role_id      TEXT        NOT NULL,
    org_id       TEXT        NOT NULL,
    role_key     TEXT        NOT NULL,
    display_name TEXT        NOT NULL,
    is_builtin   BOOLEAN     NOT NULL DEFAULT false,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, role_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_role_key
    ON role (tenant_id, org_id, role_key);

CREATE TABLE IF NOT EXISTS role_permission (
    tenant_id  TEXT NOT NULL,
    role_id    TEXT NOT NULL,
    permission TEXT NOT NULL,
    PRIMARY KEY (tenant_id, role_id, permission),
    CONSTRAINT fk_rp_role FOREIGN KEY (tenant_id, role_id)
        REFERENCES role (tenant_id, role_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS member_role (
    tenant_id   TEXT        NOT NULL,
    org_id      TEXT        NOT NULL,
    user_id     TEXT        NOT NULL,
    role_id     TEXT        NOT NULL,
    assigned_by TEXT,
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, org_id, user_id),
    CONSTRAINT fk_mr_role FOREIGN KEY (tenant_id, role_id)
        REFERENCES role (tenant_id, role_id)
);

-- ---------------------------------------------------------------------------
-- 12. 操作记录（audit_log 既有表扩展；不物理删除、不提供更新入口）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    id           BIGSERIAL PRIMARY KEY,
    tenant_id    TEXT        NOT NULL,
    actor_id     TEXT        NOT NULL,
    action       TEXT        NOT NULL,
    resource_type TEXT       NOT NULL,
    resource_id  TEXT,
    result       TEXT        NOT NULL DEFAULT 'success',
    occurred_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS org_id             TEXT;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS actor_role         TEXT;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS action_display_name TEXT;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS change_summary     JSONB;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS ip_masked          TEXT;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS trace_id           TEXT;

CREATE INDEX IF NOT EXISTS idx_audit_log_tenant_time
    ON audit_log (tenant_id, occurred_at DESC);

-- 矩阵第 4 项（迁移 0016）：操作记录导出的键集分页索引。
-- 导出按 `occurred_at ASC, id ASC` 正序翻页，需要 (tenant_id, occurred_at, id)
-- 才能让整个排序走索引；查询端点 U6 仍用上面的 DESC 索引。
CREATE INDEX IF NOT EXISTS idx_audit_log_tenant_time_id
    ON audit_log (tenant_id, occurred_at, id);

CREATE INDEX IF NOT EXISTS idx_audit_log_actor
    ON audit_log (tenant_id, actor_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_log_action
    ON audit_log (tenant_id, action, occurred_at DESC);

-- ---------------------------------------------------------------------------
-- 13. 运营系统配置（平台级）与外部投影出箱（D-13 单向同步）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ops_settings (
    tenant_id     TEXT        NOT NULL DEFAULT '__platform__',
    setting_key   TEXT        NOT NULL,
    setting_value JSONB       NOT NULL,
    updated_by    TEXT,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, setting_key)
);

CREATE TABLE IF NOT EXISTS integration_outbox (
    tenant_id      TEXT        NOT NULL,
    outbox_id      TEXT        NOT NULL,
    kind           TEXT        NOT NULL CHECK (kind IN ('kc_group_upsert', 'kc_group_delete',
                                                        'kc_user_group_sync')),
    payload        JSONB       NOT NULL,
    status         TEXT        NOT NULL DEFAULT 'pending'
                               CHECK (status IN ('pending', 'sent', 'failed')),
    attempts       SMALLINT    NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_error     TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, outbox_id)
);

CREATE INDEX IF NOT EXISTS idx_outbox_due
    ON integration_outbox (status, next_attempt_at);

-- ---------------------------------------------------------------------------
-- 14. 行级安全策略（D-02 / D-05：RLS + 应用层 tenant_id 双保险）
--
-- 两类策略：
--   (a) 严格租户隔离：仅可见/可写当前会话租户；平台模式下（app.tenant_id =
--       '__platform__'）全量可见 —— 仅 /ops/** 路由可在通过权限点校验后置为
--       平台模式（代码级守卫见 architecture-phase2.md 3.4 节）。
--   (b) 共享目录：额外允许读取 tenant_id = '__platform__' 的平台行（套餐、权限点、配置）。
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    t            TEXT;
    strict_tables TEXT[] := ARRAY[
        'tenant', 'knowledge_base_meta', 'knowledge_base_grant', 'document_meta',
        'segment_map', 'credential_store', 'tool_connection', 'tool_execution',
        'usage_event', 'usage_monthly', 'billing_subscription', 'billing_order',
        'billing_invoice', 'payment_callback_log', 'department', 'department_member',
        'role', 'role_permission', 'member_role', 'audit_log', 'integration_outbox'
    ];
    shared_tables TEXT[] := ARRAY['permission_catalog', 'billing_plan', 'ops_settings'];
BEGIN
    FOREACH t IN ARRAY strict_tables LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation_select ON %I', t);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation_insert ON %I', t);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation_update ON %I', t);
        EXECUTE format('DROP POLICY IF EXISTS p_tenant_isolation ON %I', t);
        EXECUTE format(
            'CREATE POLICY p_tenant_isolation ON %I '
            'USING (tenant_id = current_setting(''app.tenant_id'', TRUE) '
            '    OR current_setting(''app.tenant_id'', TRUE) = ''__platform__'') '
            'WITH CHECK (tenant_id = current_setting(''app.tenant_id'', TRUE) '
            '    OR current_setting(''app.tenant_id'', TRUE) = ''__platform__'')', t);
    END LOOP;

    FOREACH t IN ARRAY shared_tables LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS p_catalog_shared ON %I', t);
        EXECUTE format(
            'CREATE POLICY p_catalog_shared ON %I '
            'USING (tenant_id = current_setting(''app.tenant_id'', TRUE) '
            '    OR tenant_id = ''__platform__'' '
            '    OR current_setting(''app.tenant_id'', TRUE) = ''__platform__'') '
            'WITH CHECK (tenant_id = current_setting(''app.tenant_id'', TRUE) '
            '    OR current_setting(''app.tenant_id'', TRUE) = ''__platform__'')', t);
    END LOOP;
END $$;

-- ---------------------------------------------------------------------------
-- 15. 平台种子数据（三级套餐 3 条 + 权限点 20 条；运行期由运营后台覆盖，不删行）
--     价格与额度即 PRD 5.1 默认配置（Q2-1），全部走配置不硬编码前端
--     注：ops_settings 属共享目录表但**不预置任何行**，运行期首次写入。
--         写「共享表应能读到种子数据」类断言时不要包含它，否则误报为泄漏。
-- ---------------------------------------------------------------------------
INSERT INTO billing_plan (tenant_id, plan_code, display_name, price_per_seat_month_cents,
                          min_seats, quota_units_per_seat_month, purchasable_quota,
                          features, status, trial_days, sort_order)
VALUES
    ('__platform__', 'free', '免费版', 0, 3, 200000, false,
     '{"kb_count":3,"kb_document_limit":50,"tool_count":1,"plugin_market":false,
       "org_structure":false,"role_permission":false,"audit_log":false,"private_deploy":false}'::jsonb,
     'available', 0, 1),
    ('__platform__', 'professional', '专业版', 3900, 5, 3000000, true,
     '{"kb_count":null,"kb_document_limit":2000,"tool_count":5,"plugin_market":true,
       "org_structure":true,"role_permission":true,"audit_log":true,"private_deploy":false}'::jsonb,
     'available', 14, 2),
    ('__platform__', 'enterprise', '企业版', 9900, 20, 8000000, true,
     '{"kb_count":null,"kb_document_limit":10000,"tool_count":5,"plugin_market":true,
       "org_structure":true,"role_permission":true,"audit_log":true,"private_deploy":true}'::jsonb,
     'available', 30, 3)
ON CONFLICT (tenant_id, plan_code) DO NOTHING;

INSERT INTO permission_catalog (tenant_id, permission, group_key, group_name,
                                display_name, description, sort_order)
VALUES
    ('__platform__', 'knowledge:base:create',      'knowledge', '知识库', '创建知识库', '新建知识库并在其中上传资料', 1),
    ('__platform__', 'knowledge:base:update',      'knowledge', '知识库', '编辑知识库设置', '修改名称、描述、可见范围与内容切分精细度', 2),
    ('__platform__', 'knowledge:base:delete',      'knowledge', '知识库', '删除知识库', '删除知识库及其下全部文档', 3),
    ('__platform__', 'knowledge:document:upload',  'knowledge', '知识库', '上传文档', '向知识库上传文档', 4),
    ('__platform__', 'knowledge:document:delete',  'knowledge', '知识库', '删除文档', '删除知识库中的文档', 5),
    ('__platform__', 'knowledge:grant:manage',     'knowledge', '知识库', '管理成员授权', '设置谁可以查看与上传该知识库', 6),
    ('__platform__', 'knowledge:base:read',        'knowledge', '知识库', '提问与检索知识库', '在对话与检索中使用知识库', 7),
    ('__platform__', 'tool:connection:manage',     'tool',      '办公工具', '连接与断开办公工具', '连接、断开、重新连接办公工具', 8),
    ('__platform__', 'tool:execute',               'tool',      '办公工具', '调用办公工具', '在对话与自动化任务中调用办公工具', 9),
    ('__platform__', 'team:member:invite',         'member',    '成员', '邀请成员', '邀请新成员加入企业', 10),
    ('__platform__', 'team:member:update',         'member',    '成员', '变更角色与移出成员', '调整成员角色、部门与在册状态', 11),
    ('__platform__', 'team:structure:manage',      'member',    '成员', '管理部门层级', '新建、移动、重命名与删除部门', 12),
    ('__platform__', 'team:role:manage',           'member',    '成员', '配置角色权限', '设置每个角色可以做的事', 13),
    ('__platform__', 'billing:subscription:read',  'billing',   '账单', '查看套餐与用量', '查看套餐、席位与用量', 14),
    ('__platform__', 'billing:subscription:manage','billing',   '账单', '开通与变更套餐席位', '开通、升级、降级与增减席位', 15),
    ('__platform__', 'audit:log:read',             'audit',     '操作记录', '查看操作记录', '查询企业内的操作记录', 16),
    ('__platform__', 'ops:console:access',         'ops',       '运营管理', '访问运营管理', '进入运营管理后台', 91),
    ('__platform__', 'ops:order:confirm',          'ops',       '运营管理', '订单核销', '确认对公转账到账并核销订单', 92),
    ('__platform__', 'ops:tenant:manage',          'ops',       '运营管理', '企业启用与停用', '启用或停用企业账号', 93),
    ('__platform__', 'ops:settings:manage',        'ops',       '运营管理', '修改系统配置', '调整额度阈值、试用天数与公告', 94)
ON CONFLICT (tenant_id, permission) DO NOTHING;

COMMIT;
