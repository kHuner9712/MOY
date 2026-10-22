-- ===========================================================================
-- 0003 阶段三表结构（**自足迁移**：表不存在则建表 → 建完再对账）
--
-- 为什么必须自足（G-11 判据）：
--   私有化交付给客户的是一份 `deploy/` 目录，客户在**空库**上只跑
--   `deploy/migrations/*.sql`。迁移必须能独立把库带到目标状态——
--   "只对账不建表"会让空库路径直接 RAISE（B17 首版即踩此坑，G-11 红 7 条）。
--
-- 为什么用 `to_regclass` 守卫而不是 `CREATE TABLE IF NOT EXISTS`（实测结论）：
--   `IF NOT EXISTS` 在**表已存在**时**仍要求** schema 的 CREATE 权限；
--   在 initdb 初始化过的库上以应用角色执行会 `permission denied for schema public`。
--   改为「先看 `to_regclass('public.plugin_catalog')`，存在就整段跳过」：
--     · 容器路径（initdb 跑过基线）→ DDL 零执行、零权限需求；
--     · 空库 / 私有化现场 → 应用角色是 schema 属主，正常建表。
--
-- DDL 段与 `deploy/init-schema-phase3.sql` **逐字等价**（脚本拼接生成，不手工复制）。
-- 对账段保留：缺表 / 缺 FORCE RLS / 缺目录种子即 RAISE，是"迁移写漏了"的最后一道。
--
-- 执行方式：`app.db.migrate` 内置单事务（本文件不得写 BEGIN/COMMIT）。
-- ===========================================================================

-- 平台模式（FORCE RLS 对所有者同样生效，播种平台行需要它）
SET app.tenant_id = '__platform__';

DO $guard$
BEGIN
    IF to_regclass('public.plugin_catalog') IS NOT NULL THEN
        RAISE NOTICE '0003: 阶段三数据表已存在，跳过建表（容器 initdb 路径）';
        RETURN;
    END IF;
    EXECUTE $ddl$
-- ---------------------------------------------------------------------------
-- 1. 市场目录（共享行：tenant_id 恒为 '__platform__'）
--    运营侧上下架 = 改 status（V1.0 用种子 + 直接改库，不新增运营端点，U-07）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS plugin_catalog (
    tenant_id      TEXT        NOT NULL DEFAULT '__platform__',
    plugin_id      TEXT        NOT NULL CHECK (plugin_id ~ '^plg_[a-z][a-z0-9_]{2,40}$'),
    name           TEXT        NOT NULL CHECK (char_length(name) BETWEEN 2 AND 32),
    vendor         TEXT        NOT NULL DEFAULT 'official' CHECK (vendor IN ('official')),
    category       TEXT        NOT NULL
                               CHECK (category IN ('dev','content','hr','data','collab','other')),
    summary        TEXT        NOT NULL CHECK (char_length(summary) BETWEEN 4 AND 60),
    description    TEXT        NOT NULL CHECK (char_length(description) BETWEEN 120 AND 800),
    icon_key       TEXT        NOT NULL,
    latest_version TEXT        NOT NULL CHECK (latest_version ~ '^\d+\.\d+\.\d+$'),
    rating_avg     NUMERIC(2,1) NOT NULL DEFAULT 0 CHECK (rating_avg BETWEEN 0 AND 5),
    rating_count   INTEGER     NOT NULL DEFAULT 0 CHECK (rating_count >= 0),
    install_count  INTEGER     NOT NULL DEFAULT 0 CHECK (install_count >= 0),
    status         TEXT        NOT NULL DEFAULT 'available'
                               CHECK (status IN ('available','hidden','offline')),
    sort_order     INTEGER     NOT NULL DEFAULT 0,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, plugin_id)
);
CREATE INDEX IF NOT EXISTS idx_catalog_status_sort
    ON plugin_catalog (tenant_id, status, sort_order);

-- ---------------------------------------------------------------------------
-- 2. 声明快照（**不可变**：只插不改；仅允许 status active→deprecated/revoked）
--    `manifest` 是历史任务渲染的唯一依据（PRD R2 风险对策）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS plugin_version (
    tenant_id            TEXT        NOT NULL DEFAULT '__platform__',
    plugin_id            TEXT        NOT NULL CHECK (plugin_id ~ '^plg_[a-z][a-z0-9_]{2,40}$'),
    version              TEXT        NOT NULL CHECK (version ~ '^\d+\.\d+\.\d+$'),
    manifest             JSONB       NOT NULL,
    manifest_sha256      CHAR(64)    NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    min_platform_version TEXT        NOT NULL CHECK (min_platform_version ~ '^\d+\.\d+\.\d+$'),
    capabilities         TEXT[]      NOT NULL DEFAULT ARRAY[]::TEXT[],
    actions              TEXT[]      NOT NULL DEFAULT ARRAY[]::TEXT[],
    step_titles          TEXT[]      NOT NULL DEFAULT ARRAY[]::TEXT[],
    status               TEXT        NOT NULL DEFAULT 'active'
                                     CHECK (status IN ('active','deprecated','revoked')),
    published_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, plugin_id, version)
);
CREATE INDEX IF NOT EXISTS idx_version_plugin
    ON plugin_version (tenant_id, plugin_id, published_at DESC);

-- ---------------------------------------------------------------------------
-- 3. 安装态（一企业一插件一行；版本双指针支持"恢复上一版本"，只留最近 2 版）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS plugin_installation (
    tenant_id          TEXT        NOT NULL,
    plugin_id          TEXT        NOT NULL CHECK (plugin_id ~ '^plg_[a-z][a-z0-9_]{2,40}$'),
    version            TEXT        NOT NULL CHECK (version ~ '^\d+\.\d+\.\d+$'),
    previous_version   TEXT        CHECK (previous_version IS NULL
                                          OR previous_version ~ '^\d+\.\d+\.\d+$'),
    status             TEXT        NOT NULL DEFAULT 'installed'
                                   CHECK (status IN ('installed','enabled','disabled','uninstalled')),
    settings           JSONB       NOT NULL DEFAULT '{}'::jsonb,
    capability_version INTEGER     NOT NULL DEFAULT 1 CHECK (capability_version >= 1),
    installed_by       TEXT        NOT NULL,
    installed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    enabled_at         TIMESTAMPTZ,
    disabled_at        TIMESTAMPTZ,
    uninstalled_at     TIMESTAMPTZ,
    last_used_at       TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, plugin_id)
);
CREATE INDEX IF NOT EXISTS idx_installation_status
    ON plugin_installation (tenant_id, status);

-- ---------------------------------------------------------------------------
-- 4. 能力授权（成员 × 插件 × 能力；与角色正交，PRD R4 硬要求）
--    撤销用 revoked_at 软失效：历史任务审计需要"当时授没授"的证据
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS plugin_grant (
    tenant_id   TEXT        NOT NULL,
    plugin_id   TEXT        NOT NULL CHECK (plugin_id ~ '^plg_[a-z][a-z0-9_]{2,40}$'),
    user_id     TEXT        NOT NULL,
    capability  TEXT        NOT NULL
                            CHECK (capability IN ('personal.knowledge.read','personal.tool.use',
                                                  'personal.file.read','org.directory.read',
                                                  'org.repo.write','org.external.publish')),
    granted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at  TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, plugin_id, user_id, capability)
);
-- 每步入口复检授权必须走这个部分索引（10⁵ 行下 < 5ms）
CREATE INDEX IF NOT EXISTS idx_grant_lookup
    ON plugin_grant (tenant_id, plugin_id, user_id) WHERE revoked_at IS NULL;

-- ---------------------------------------------------------------------------
-- 5. 企业级能力总开关（只对 org.* 三项生效；personal.* 恒开，不可被企业关闭）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS plugin_capability_policy (
    tenant_id   TEXT        NOT NULL,
    capability  TEXT        NOT NULL
                            CHECK (capability IN ('org.directory.read','org.repo.write',
                                                  'org.external.publish')),
    enabled     BOOLEAN     NOT NULL DEFAULT FALSE,
    updated_by  TEXT        NOT NULL DEFAULT '',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, capability)
);

-- ---------------------------------------------------------------------------
-- 6. 评价（一人一插件一条，重复提交=覆盖更新；不存邮箱/手机/部门路径）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS plugin_review (
    tenant_id   TEXT        NOT NULL,
    review_id   TEXT        NOT NULL,
    plugin_id   TEXT        NOT NULL CHECK (plugin_id ~ '^plg_[a-z][a-z0-9_]{2,40}$'),
    user_id     TEXT        NOT NULL,
    rating      SMALLINT    NOT NULL CHECK (rating BETWEEN 1 AND 5),
    content     TEXT        NOT NULL DEFAULT '' CHECK (char_length(content) <= 200),
    anonymous   BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, review_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_review_user
    ON plugin_review (tenant_id, plugin_id, user_id);

-- ---------------------------------------------------------------------------
-- 7. 任务（对外即 A 域 task_id；本地持久化，底座不再是唯一真相源 — D-20）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS plugin_task (
    tenant_id        TEXT        NOT NULL,
    task_id          TEXT        NOT NULL,
    plugin_id        TEXT        NOT NULL CHECK (plugin_id ~ '^plg_[a-z][a-z0-9_]{2,40}$'),
    plugin_version   TEXT        NOT NULL CHECK (plugin_version ~ '^\d+\.\d+\.\d+$'),
    conversation_id  TEXT        NOT NULL DEFAULT '',
    initiator_id     TEXT        NOT NULL,
    trigger_type     TEXT        NOT NULL DEFAULT 'manual'
                                 CHECK (trigger_type IN ('manual','retry')),
    status           TEXT        NOT NULL DEFAULT 'pending'
                                 CHECK (status IN ('pending','running','queued','waiting_confirm',
                                                   'completed','failed','cancelled')),
    progress         SMALLINT    NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    current_step_seq SMALLINT    NOT NULL DEFAULT 0,
    inputs           JSONB       NOT NULL DEFAULT '{}'::jsonb,
    result_summary   TEXT        NOT NULL DEFAULT '',
    checklist        JSONB       NOT NULL DEFAULT '[]'::jsonb,
    warnings         JSONB       NOT NULL DEFAULT '[]'::jsonb,
    error_message    TEXT,
    retry_of         TEXT,
    from_step        SMALLINT,
    confirm_step_seq SMALLINT,
    cancel_requested BOOLEAN     NOT NULL DEFAULT FALSE,
    usage_units      BIGINT      NOT NULL DEFAULT 0 CHECK (usage_units >= 0),
    idem_key         TEXT        NOT NULL,
    started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at      TIMESTAMPTZ,
    expires_at       TIMESTAMPTZ NOT NULL DEFAULT now() + INTERVAL '180 days',
    PRIMARY KEY (tenant_id, task_id),
    CONSTRAINT fk_task_of FOREIGN KEY (tenant_id, retry_of)
        REFERENCES plugin_task (tenant_id, task_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_task_idem
    ON plugin_task (tenant_id, plugin_id, idem_key);
CREATE INDEX IF NOT EXISTS idx_task_plugin_time
    ON plugin_task (tenant_id, plugin_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_task_status_time
    ON plugin_task (tenant_id, status, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_task_initiator
    ON plugin_task (tenant_id, initiator_id, started_at DESC);

-- ---------------------------------------------------------------------------
-- 8. 任务步骤（失败只留业务语言 reason_text + operator_action，不留堆栈/原始报文）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS plugin_task_step (
    tenant_id         TEXT        NOT NULL,
    step_id           TEXT        NOT NULL,
    task_id           TEXT        NOT NULL,
    seq               SMALLINT    NOT NULL CHECK (seq BETWEEN 1 AND 8),
    title             TEXT        NOT NULL,
    action            TEXT        NOT NULL,
    status            TEXT        NOT NULL DEFAULT 'pending'
                                  CHECK (status IN ('pending','running','completed',
                                                    'failed','skipped')),
    attempt           SMALLINT    NOT NULL DEFAULT 0 CHECK (attempt BETWEEN 0 AND 1),
    retryable         BOOLEAN     NOT NULL DEFAULT FALSE,
    output_summary    TEXT        NOT NULL DEFAULT '',
    reason_text       TEXT        NOT NULL DEFAULT '',
    operator_action   TEXT        NOT NULL DEFAULT '',
    tool_execution_id TEXT,
    duration_ms       INTEGER     NOT NULL DEFAULT 0 CHECK (duration_ms >= 0),
    started_at        TIMESTAMPTZ,
    finished_at       TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, step_id),
    CONSTRAINT fk_step_task FOREIGN KEY (tenant_id, task_id)
        REFERENCES plugin_task (tenant_id, task_id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_step_seq
    ON plugin_task_step (tenant_id, task_id, seq);

-- ---------------------------------------------------------------------------
-- 9. 产物（text ≤32KB 内联已脱敏；file 走对象存储键，不暴露存储地址）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS plugin_artifact (
    tenant_id      TEXT        NOT NULL,
    artifact_id    TEXT        NOT NULL,
    task_id        TEXT        NOT NULL,
    step_id        TEXT        NOT NULL,
    output_key     TEXT        NOT NULL,
    type           TEXT        NOT NULL CHECK (type IN ('text','link','file','table')),
    name           TEXT        NOT NULL CHECK (char_length(name) BETWEEN 1 AND 128),
    inline_text    TEXT        NOT NULL DEFAULT '',
    preview_text   TEXT        NOT NULL DEFAULT '',
    external_url   TEXT        CHECK (external_url IS NULL
                                      OR external_url ~ '^https?://'),
    storage_key    TEXT,
    size_bytes     BIGINT      NOT NULL DEFAULT 0 CHECK (size_bytes >= 0),
    content_sha256 CHAR(64),
    expires_at     TIMESTAMPTZ NOT NULL DEFAULT now() + INTERVAL '180 days',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, artifact_id),
    CONSTRAINT fk_artifact_task FOREIGN KEY (tenant_id, task_id)
        REFERENCES plugin_task (tenant_id, task_id) ON DELETE CASCADE,
    CONSTRAINT ck_inline_size CHECK (octet_length(inline_text) <= 32768)
);
CREATE INDEX IF NOT EXISTS idx_art_task
    ON plugin_artifact (tenant_id, task_id, step_id);

-- ---------------------------------------------------------------------------
-- 10. RLS：严格隔离 7 张 + 共享目录 2 张（重声明阶段二的 DO 块，把新表并进去）
--     为什么必须重声明：共享表的策略允许读 tenant_id='__platform__'，
--     若沿用严格策略，租户会话读不到平台目录行 → 市场空白（比报错更难排查）。
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    t             TEXT;
    strict_tables TEXT[] := ARRAY[
        'plugin_installation', 'plugin_grant', 'plugin_capability_policy',
        'plugin_review', 'plugin_task', 'plugin_task_step', 'plugin_artifact'
    ];
    shared_tables TEXT[] := ARRAY[
        'permission_catalog', 'billing_plan', 'ops_settings',
        'plugin_catalog', 'plugin_version'
    ];
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
-- 11. 种子：6 条目录行 + 3 条企业能力开关（默认关闭，Q3-2）
--
-- 注 1：`plugin_version` 的 6 条声明快照**不写在本文件**，由运行期
--       `PluginRepo.ensure_versions()` 从 `app/plugins/manifests/*.json` 幂等登记
--       （INSERT ... ON CONFLICT DO NOTHING）。理由：把 6 份 JSON 复制进 SQL 会造成
--       「库里 manifest 与磁盘文件不一致」，而版本不可变的判据正是
--       `manifest_sha256` 与文件内容一致——复制一份就等于给自己埋一个长期漂移源。
-- 注 2：`plugin_capability_policy` 的 3 行只在**企业首次访问**时按租户插入；
--       本文件不预置任何租户行（租户是运行期创建的，initdb 阶段还不存在）。
--       因此这里只写目录种子。
-- ---------------------------------------------------------------------------
INSERT INTO plugin_catalog (
    tenant_id, plugin_id, name, vendor, category, summary, description, icon_key,
    latest_version, rating_avg, rating_count, install_count, status, sort_order
) VALUES
    ('__platform__', 'plg_dev_workbench', '前端研发工作台', 'official', 'dev',
     '从一句需求到仓库里的一次提交',
     '填写需求、选好仓库，工作台会先拆解需求要点、给出技术方案并等你确认；确认后生成代码与用例文件，提交前再确认一次，最后给出包含提交记录与合并请求的核查清单。全过程每一步都能看到产出，离开页面也会继续推进，回来还能查看每一步留下了什么、哪一步没做成以及该怎么补。',
     'code', '1.0.0', 4.6, 12, 36, 'available', 1),
    ('__platform__', 'plg_content_ops', '内容运营工作台', 'official', 'content',
     '一个主题，多平台稿件与发布排期表',
     '输入主题并选择平台，工作台先检索企业素材，没有可靠素材时会如实标注、不编造内容；随后按平台策划选题、生成稿件与配图建议，排出发布计划并等你确认后发布。未接入的平台会给出可下载的稿件与发布指引，发布状态如实显示为待人工发布，不会伪造发布成功的回执。全过程每一步都能看到产出与来源。',
     'pen', '1.0.0', 4.4, 9, 28, 'available', 2),
    ('__platform__', 'plg_hr_admin', '行政人事工作台', 'official', 'hr',
     '从招聘到公文的日常事务助手',
     '写招聘说明、按简历给出可辩驳的初评、为进入面试的候选人准备提纲并发出邀约，也能起草录用通知与各类公文。所有涉及金额的位置一律留空由你填写，对外发送前都会先请你确认；简历无法读取时会如实标注，不会猜测内容。适合日常招聘与行政事务的连续处理，每一步的产出都能单独查看与复制。',
     'users', '1.0.0', 4.5, 7, 21, 'available', 3),
    ('__platform__', 'plg_weekly_report', '周报生成', 'official', 'collab',
     '把这一周的记录整理成一份周报',
     '自动收集你本周在知识库里的工作记录与文档，按项目归拢成条目清晰的周报：完成了什么、遇到什么阻碍、下周打算做什么。写好后可以先看一遍，确认无误再发送给指定收件人；没有记录可依据的部分会如实标注，不会替你编造工作量。周报会同时保留在任务记录里，随时可以回来复制正文或重新发送。',
     'file-text', '1.0.0', 4.2, 5, 17, 'available', 4),
    ('__platform__', 'plg_meeting_minutes', '会议纪要', 'official', 'collab',
     '把会议材料整理成纪要与待办清单',
     '上传会议材料或录音整理稿，工作台先抽取议题与结论，再生成结构化纪要与待办清单：谁在什么时候做什么。纪要与待办会一起归档到团队文档，方便会后直接跟进；材料里没有提到的部分不会补写，待办责任人留空时会提示你补充。纪要支持按结论、待办分段查看，也能整段复制到别处使用。',
     'clipboard', '1.0.0', 4.3, 6, 19, 'available', 5),
    ('__platform__', 'plg_mail_draft', '邮件起草', 'official', 'collab',
     '按要点起草一封得体的邮件并发出',
     '告诉工作台收件人、目的与要点，它会起草一封语气得体的邮件，并在发送前把标题、正文与收件人一起给你确认；确认后通过你已连接的邮箱发出，并把发送结果如实告诉你。语气可选正式或简洁，正文会控制在一屏内，方便对方快速读完。草稿会同时留存在任务记录里，需要时可以改完再发一次。',
     'mail', '1.0.0', 4.1, 4, 14, 'available', 6)
ON CONFLICT (tenant_id, plugin_id) DO NOTHING;
    $ddl$;
END $guard$;

-- ---------------------------------------------------------------------------
-- 对账（建表之后执行）：缺表 / 缺 FORCE RLS / 目录种子不足 → RAISE
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    missing_tables TEXT;
    no_force_rls   TEXT;
    catalog_count  INTEGER;
    expected_tables CONSTANT TEXT[] := ARRAY[
        'plugin_catalog', 'plugin_version', 'plugin_installation', 'plugin_grant',
        'plugin_capability_policy', 'plugin_review', 'plugin_task',
        'plugin_task_step', 'plugin_artifact'
    ];
BEGIN
    SELECT string_agg(t, ', ') INTO missing_tables
    FROM unnest(expected_tables) AS t
    WHERE NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = current_schema() AND table_name = t
    );
    IF missing_tables IS NOT NULL THEN
        RAISE EXCEPTION
            '0003 对账失败：缺少阶段三数据表（%）。请以表属主身份执行 '
            'deploy/init-schema-phase3.sql 后重试。',
            missing_tables;
    END IF;

    SELECT string_agg(c.relname, ', ') INTO no_force_rls
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = current_schema()
       AND c.relkind = 'r'
       AND c.relname = ANY (expected_tables)
       AND c.relforcerowsecurity = FALSE;
    IF no_force_rls IS NOT NULL THEN
        RAISE EXCEPTION
            '0003 对账失败：以下表未开启强制行级隔离（%）。'
            '请重新执行 deploy/init-schema-phase3.sql 的 RLS 段落。',
            no_force_rls;
    END IF;

    SELECT count(*) INTO catalog_count
      FROM plugin_catalog
     WHERE tenant_id = '__platform__' AND status <> 'offline';
    IF catalog_count < 6 THEN
        RAISE EXCEPTION
            '0003 对账失败：插件目录可用行数为 %（期望 ≥ 6）。'
            '请重新执行 deploy/init-schema-phase3.sql 的种子段落。',
            catalog_count;
    END IF;
END $$;
