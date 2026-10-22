-- ===========================================================================
-- 阶段三业务库表结构（插件体系与自动化任务）
--
-- 执行时机与顺序：
--   docker-entrypoint-initdb.d 中挂载为 `22-init-schema-phase3.sql`，
--   **必须在 `20-init-schema-phase2.sql` 之后**（依赖阶段二建好的 tenant 表、
--   RLS 策略函数体形态与 `__platform__` 共享约定）。
--
-- 幂等性：全部 `CREATE TABLE IF NOT EXISTS` / 索引 `IF NOT EXISTS` / 种子
--   `ON CONFLICT DO NOTHING`。重复执行不报错、不改已有行。
--
-- 表清单（9 张，全部含 `tenant_id`）：
--   plugin_catalog / plugin_version / plugin_installation / plugin_grant /
--   plugin_capability_policy / plugin_review / plugin_task / plugin_task_step /
--   plugin_artifact
--
-- RLS 口径（architecture-phase3.md §4.4）：
--   - 严格隔离 7 张：p_tenant_isolation（与阶段二同体例，FORCE RLS）；
--   - 共享目录 2 张：plugin_catalog / plugin_version →
--     **并入阶段二共享分组**（本脚本重声明该 DO 块，把两张表加进 shared_tables，
--     否则平台行 tenant_id='__platform__' 在租户会话下读不到，市场会空白）。
--
-- 字段级「不存在」红线（§4.5，评审逐条核对）：
--   本脚本**刻意不建**以下列，任何一条被加回来都意味着可执行载荷或凭据落地：
--     entry_point / module_path / image / command / source_url / download_url /
--     code_sha256 / signature / bundle_url / dependencies /
--     license_key / activation_code / credential_ref / token / secret /
--     rendered_prompt / system_prompt / raw_request / raw_response / steps_source /
--     stack_trace / tool_payload / node_type / model_name
--   （`manifest_sha256` 只用于防篡改校验，不是代码指纹。）
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 会话变量：与 `init-schema-phase2.sql` 同体例。
-- 业务表启用了 FORCE ROW LEVEL SECURITY（所有者亦受限），而本脚本要写平台种子行，
-- 故显式置为平台模式；该 SET 只作用于本脚本所在连接，连接关闭即失效。
-- 应用运行时由 `db/session.py` 在每个事务内用 set_config(..., TRUE) 重新设置。
-- ---------------------------------------------------------------------------
SET app.tenant_id = '__platform__';

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
    confirmed_step_seq SMALLINT NOT NULL DEFAULT 0,             -- 最近一次被确认的步骤（B19/WF1）
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
-- 9. 产物（text ≤32KB 内联已脱敏；file 的真实字节进 `content`，不暴露存储地址）
--
-- `content BYTEA`（B22 补，迁移 `0005` 同源）：
--   文件类产物（docx / xlsx / zip）的字节必须落库，否则 PG（私有化）后端下载时
--   只能回落到 `inline_text` 的文本预览——「导出 Word 公文」拿到的是文本而不是文件。
--   允许 NULL：`text` / `link` / `table` 类产物本来就没有二进制内容。
--   存储地址依旧不经 API 暴露，下载一律走平台代理 `/files/artifacts/{token}`。
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
    content        BYTEA,                                                 -- 二进制内容（B22）
    content_type   VARCHAR(128),                                          -- 下载 MIME（阶段四 0006）
    version        SMALLINT    NOT NULL DEFAULT 1 CHECK (version >= 1),  -- WF2 版本（B19）
    version_history JSONB      NOT NULL DEFAULT '[]'::jsonb,             -- 旧版本可回看/下载
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

-- ---------------------------------------------------------------------------
-- 12. 阶段四加性变更的前置落点（迁移 0007：usage_event 四列）
--
-- 为什么这几列写在本文件而不是 init-schema-phase2.sql：
--   阶段二脚本是迁移链的**基线**（`schema_migration` 里 `0001_phase2_init` 的
--   checksum 在每台已部署的库上都已接管为真实摘要）。改它会立刻被判
--   `migration checksum drift`（30004 启动失败），是**不可接受**的运维代价。
--   而 initdb 只按文件名顺序跑一次、且以超级用户身份——阶段三脚本是该链上
--   最后一个仍可安全追加的地方，所以把「阶段四对既有表的两处加性变更」中的
--   `usage_event` 部分放在这里（`plugin_artifact.content_type` 已在上面
--   CREATE TABLE 里直接建好）。
--
-- 幂等性：`IF NOT EXISTS` + `to_regclass` 守卫，重复执行零改动。
-- 空库/未初始化库：`usage_event` 不存在时整段跳过，不 RAISE
--   （空库路径由迁移 0007 自己补列，迁移必须自足）。
-- ---------------------------------------------------------------------------
DO $phase4$
BEGIN
    IF to_regclass('public.usage_event') IS NULL THEN
        RAISE NOTICE 'phase4: usage_event 不存在（阶段二脚本未跑），跳过加性变更';
        RETURN;
    END IF;
    ALTER TABLE usage_event ADD COLUMN IF NOT EXISTS profile           TEXT   NOT NULL DEFAULT '';
    ALTER TABLE usage_event ADD COLUMN IF NOT EXISTS provider          TEXT   NOT NULL DEFAULT '';
    ALTER TABLE usage_event ADD COLUMN IF NOT EXISTS prompt_tokens     BIGINT NOT NULL DEFAULT 0;
    ALTER TABLE usage_event ADD COLUMN IF NOT EXISTS completion_tokens BIGINT NOT NULL DEFAULT 0;
    RAISE NOTICE 'phase4: usage_event 四列已就绪（0007 将走零 DDL 路径）';
END $phase4$;

-- ---------------------------------------------------------------------------
-- 13. 迁移台账哨兵：`0003` 已由本脚本完成
--
-- 本脚本（initdb 以超级用户执行）建的表就是迁移 `0003_phase3_plugin_tables` 的内容。
-- 不打这行哨兵的话，`python -m app.db.migrate --apply` 会在每台**新装**的库上
-- 再跑一遍 0003：表面幂等（全是 CREATE TABLE IF NOT EXISTS），实际会让应用角色
-- 以「非表属主」身份去重述 0003 里的 RLS 策略重声明块，把一次正常的初始化
-- 变成一次权限事故。checksum 用与 `0001` 相同的哨兵值，首次 `--apply` 时由
-- migrate.py 接管为真实摘要（`MANAGED_CHECKSUM_SENTINEL`）。
-- ---------------------------------------------------------------------------
INSERT INTO schema_migration (version, checksum)
VALUES ('0003_phase3_plugin_tables', 'sha256:managed-by-app-db-migrate')
ON CONFLICT (version) DO NOTHING;
