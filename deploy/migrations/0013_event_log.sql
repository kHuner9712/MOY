-- ===========================================================================
-- 0013 会话事件日志（底座 T01/T02，设计文档 §5.1 / 规格 §3.1 原则 E1）
--
-- 为什么要有这张表：它是底座的**状态真源**，不是日志。
--   迁移前 SSE 直接回放进程内 `self._steps`（app/services/agent_service.py:173），
--   于是刷新即丢、多副本不可用、断线不续；而"当前状态"因此在内存里存在第二个
--   可写副本——这正是规格 §3.1 E1 的违反判据。本表落地后，任务步骤 / 产物键 /
--   审批结论 / 压缩摘要 / 用量全部是事件流的投影，六件事（断线续传 / 回放 /
--   fork / 压缩 / 审计导出 / 按相计费）都变成"投影时的取数方式"，而不是六个机制。
--
-- 三条硬约束（设计文档 §5.1，缺一条本迁移就不算落地）：
--   1. **append-only**：应用角色只授 INSERT + SELECT。GRANT 单独做还不够——见下方
--      「附录 A」的 RLS 兜底：若某个库里 event_log 恰好由应用角色建过（表属主 =
--      应用角色），属主权限不受 GRANT 约束（属主永远能改自己的表）。
--
-- **执行身份（勿改）**：本迁移以**属主/迁移角色** DSN 执行（DBA 或
-- `POSTGRES_USER`），应用角色 `officeai_app` 只拿到 SELECT + INSERT。
-- 迁移角色与运行角色必须分离：应用角色是 NOSUPERUSER + NOBYPASSRLS，且对
-- `schema public` **没有 CREATE**——用它跑 DDL 只会在"全新库"上侥幸通过
-- （表已由 initdb 建好），一遇到"已装版本升级"就被拒（CI 路径 B 实测：
-- `permission denied for schema public`）。**不要**通过"给应用角色授 CREATE"
-- 来绕过：那会把 DDL 权限永久留给运行角色，RLS 隔离的前提当场失效。
--   2. **RLS 按 tenant_id**：策略体例照抄 `init-schema-phase2.sql` 第 15 节
--      （含 `__platform__` 例外），不用 `init-rls.sql` 的旧体例。
--   3. **hash 链**：`hash = sha256(prev_hash || event_id || type || payload_canonical)`
--      由 `app/db/repositories/event_log/records.py` 计算。hash 链**不是**安全机制
--      （同库内 DBA 可重写），而是给客户的完整性证据：审计导出附链头，客户可自行
--      重算。因此本表不存"签名"，只存链。
--
-- 分区：`PARTITION BY HASH (session_id)`，16 个分区（`event_log_p00`…`p15`）。
--   为什么按 `session_id` 哈希而不是按时间做 RANGE：① 回放与续传永远按
--   `(session_id, seq)` 取数，同会话必须落在同一分区，否则一次回放要扫全表；
--   ② 事件是 append-only，没有"按时间批量删旧分区"的需求（保留期由合规域管，
--   见 `retention_*`），RANGE 的优势用不上；③ 16 个分区对 `MODULUS` 取余，
--   增删分区会改变映射（这是 HASH 分区的代价），因此**分区数写成脚本内常量**
--   （见 `$ddl$` 块的 `partition_count CONSTANT`），不是散落的字面量。
--
-- 事件类型为什么不在库里做 CHECK：设计文档 §5.1 的 14 类是 V1.0 封闭枚举，
--   `subagent.spawn` 属 V1.1 预留。写成 CHECK 会让 V1.1 加一条事件类型必须再走一次
--   全表重写的 DDL；枚举的封闭性由 `app/db/repositories/event_log/records.py`
--   的 `EVENT_TYPES` 在代码侧强制（未知类型直接拒绝写入，不是静默落库）。
--
-- 幂等性：建表 `IF NOT EXISTS`、建分区/索引同理、策略先 `DROP POLICY IF EXISTS`
--   再建，重复执行零改动。**新装库不预建本表**（`init-schema-*.sql` 未含它）：
--   这是 T01 新增迁移，空库路径由 `deploy/validate_migrations.py` 的 A 路径覆盖。
--
-- 回滚：`rollback/0013_event_log.down.sql`（真删表 + 16 个分区 + 策略；C 路径会验）。
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 1. 主表（分区父表；列与设计文档 §5.1 逐字一致，未增删任何字段）
--
--    `PRIMARY KEY (session_id, seq)` 是**取号正确性的唯一机械保证**：
--    并发写同会话时后到者撞主键冲突，`PostgresEventStore.append` 据此重试
--    （最多 3 次）。没有它，`SELECT COALESCE(MAX(seq),0)+1` 在并发下会静默重复。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS event_log (
  tenant_id   text        NOT NULL,
  session_id  text        NOT NULL,
  seq         bigint      NOT NULL,
  event_id    text        NOT NULL,
  type        text        NOT NULL,
  payload     jsonb       NOT NULL,
  prev_hash   text        NOT NULL,
  hash        text        NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (session_id, seq)
) PARTITION BY HASH (session_id);

-- ---------------------------------------------------------------------------
-- 2. 16 个 HASH 分区（分区数 = 脚本内常量 `partition_count`）
-- ---------------------------------------------------------------------------
DO $ddl$
DECLARE
    -- 分区数**只在这里定义一次**：改分区数属结构变更，必须新开迁移（HASH 分区
    -- 增删会改变已有 session 的落点，不是简单的 ADD PARTITION）。
    partition_count CONSTANT integer := 16;
    idx             integer;
    part_name       text;
    existing        integer;
BEGIN
    -- **零 DDL 守卫**（路径 B）：16 个分区全在就整段跳过。为什么不能只靠
    -- `CREATE TABLE IF NOT EXISTS`——"已存在时是否仍校验属主"属实现细节，
    -- 0012 已经为同一件事写过警告（`pg_indexes` 显式判存在性）；路径 B 上
    -- 应用角色不是表属主，把"跳过"交给实现细节就是拿迁移的可重复执行性赌运气。
    SELECT count(*) INTO existing
      FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'public'
       AND c.relname ~ '^event_log_p[0-9]{2}$';
    IF existing = partition_count THEN
        RAISE NOTICE '0013: % 个 HASH 分区已存在（前次已执行 / 新装库直建），零 DDL',
            partition_count;
        RETURN;
    END IF;

    FOR idx IN 0..(partition_count - 1) LOOP
        part_name := 'event_log_p' || lpad(idx::text, 2, '0');
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS %I PARTITION OF event_log '
            'FOR VALUES WITH (MODULUS %s, REMAINDER %s)',
            part_name, partition_count, idx
        );
    END LOOP;
    RAISE NOTICE '0013: event_log 的 % 个 HASH 分区就绪（已有 % 个）',
        partition_count, coalesce(existing, 0);
EXCEPTION
    WHEN insufficient_privilege THEN
        RAISE EXCEPTION
            '0013 需要 CREATE TABLE（event_log 的 16 个分区），但当前角色不是表属主/'
            '在 public 上无 CREATE 权限，且已有分区不齐（缺 % 个）。请由 DBA 执行：'
            'CREATE TABLE event_log_pNN PARTITION OF event_log '
            'FOR VALUES WITH (MODULUS 16, REMAINDER NN);（NN = 00…15）',
            partition_count - coalesce(existing, 0);
END $ddl$;

-- ---------------------------------------------------------------------------
-- 3. 索引（两条查询形态各一条）
--
--    `(tenant_id, session_id, seq)`：回放与断线续传的唯一取数路径
--    （`WHERE tenant_id = ? AND session_id = ? AND seq > ? ORDER BY seq`）。
--    tenant_id 打头是必要的：RLS 会把它变成额外的过滤条件，且运维侧"某租户
--    某会话的事件"查询也从这个前缀走。
--
--    `(tenant_id, created_at)`：审计导出与保留期管理按时间切片取数。
--
--    在分区父表上 `CREATE INDEX` 会自动为每个分区建同名索引（PG 11+），
--    因此这里只写两条，不要"顺手"给每个分区再手写一遍（会变成 32 条索引）。
--
--    **先查 `pg_indexes` 再建，不依赖 `CREATE INDEX IF NOT EXISTS` 的跳过行为**：
--    `CREATE INDEX` 在解析目标表时就会校验属主（`RangeVarCallbackOwnsTable`），
--    路径 B 上应用角色不是属主 → 即使索引已存在也可能直接 `permission denied`。
--    0012 已为同一件事立过规矩，这里照抄。
-- ---------------------------------------------------------------------------
DO $idx$
DECLARE
    existing integer;
BEGIN
    SELECT count(*) INTO existing
      FROM pg_indexes
     WHERE schemaname = 'public'
       AND tablename = 'event_log'
       AND indexname IN ('idx_event_log_session_seq', 'idx_event_log_tenant_created');
    IF existing = 2 THEN
        RAISE NOTICE '0013: event_log 两条索引已存在（前次已执行 / 新装库直建），零 DDL';
        RETURN;
    END IF;

    EXECUTE 'CREATE INDEX IF NOT EXISTS idx_event_log_session_seq '
            'ON event_log (tenant_id, session_id, seq)';
    EXECUTE 'CREATE INDEX IF NOT EXISTS idx_event_log_tenant_created '
            'ON event_log (tenant_id, created_at)';
    RAISE NOTICE '0013: event_log 索引就绪（两条，分区索引由父表自动派生）';
EXCEPTION
    WHEN insufficient_privilege THEN
        RAISE EXCEPTION
            '0013 建索引需要 event_log 的属主权限（当前索引不齐：% 条存在）。'
            '请由 DBA 执行：CREATE INDEX idx_event_log_session_seq '
            'ON event_log (tenant_id, session_id, seq); CREATE INDEX '
            'idx_event_log_tenant_created ON event_log (tenant_id, created_at);',
            coalesce(existing, 0);
END $idx$;

-- ---------------------------------------------------------------------------
-- 4. 行级安全（RLS）：p_tenant_isolation 体例照抄 init-schema-phase2.sql:642-663
--
--    要点逐条：
--    a. `ENABLE` + **`FORCE`**：`FORCE` 让策略对**表属主**也生效。这不是多余的
--       一行——本迁移以属主/迁移角色执行，属主（DBA 或曾用应用角色自建的库）
--       会绕过策略；不开 FORCE 时租户隔离就退化成"靠代码自觉"。
--    b. 分区**逐个**建策略：PG 的分区不继承父表的策略（直查分区时只认分区自己的
--       策略），只给父表建等于留了一条直查分区的绕行路径。
--    c. 只有 SELECT / INSERT 两条策略，**没有 UPDATE / DELETE 策略**：
--       "某条命令没有策略"在 RLS 下等于"恒假"，于是 UPDATE/DELETE 恒 0 行。
--       这是 append-only 的第二道机械保证（第一道是 GRANT），见附录 A。
--    d. `__platform__` 例外与既有体例一致：运营后台经 `platform_scope()` 跨租户
--       只读排查（审计导出、故障定位），这是唯一合法跨租户通道。
-- ---------------------------------------------------------------------------
DO $rls$
DECLARE
    partition_count CONSTANT integer := 16;
    idx             integer;
    t               text;
    ready           integer := 0;
    forced          integer;
    sel_ok          integer;
    ins_ok          integer;
BEGIN
    -- **零 DDL 守卫**（路径 B 的关键）：已初始化库上本表可能已由超级用户/初始化
    -- 脚本建好（`psql -f 0013` 与 `migrate --apply` 都可能跑过）。此时应用角色
    -- 对 `event_log` 不是属主，`ALTER TABLE ... ENABLE ROW LEVEL SECURITY` 会抛
    -- `must be owner of table`——那是"守卫缺失"的典型症状，不是权限配置问题。
    --
    -- **守卫必须逐项验，不能只看"策略名存在"**（缺陷 D5）：只看名字的话，
    -- ① `relforcerowsecurity = false`（属主可绕过策略）、
    -- ② 别人建了个同名 `USING (true)` 策略（全员可读，隔离归零）、
    -- ③ 16 个分区里只有一个有策略（直查分区绕过父表策略），
    -- 三种"隔离已降级"的库都会被判成"已就绪"并整段跳过——守卫反而成了
    -- 静默放行的通道。因此逐表逐项验：FORCE 标志、SELECT 策略非恒真、
    -- INSERT 策略非恒真、且**父表与 16 个分区一个都不能少**。
    FOR idx IN -1..(partition_count - 1) LOOP
        IF idx = -1 THEN
            t := 'event_log';
        ELSE
            t := 'event_log_p' || lpad(idx::text, 2, '0');
        END IF;

        SELECT count(*) INTO forced
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relname = t
           AND c.relrowsecurity AND c.relforcerowsecurity;

        SELECT count(*) INTO sel_ok
          FROM pg_policies
         WHERE schemaname = 'public' AND tablename = t
           AND policyname = 'p_tenant_isolation_select'
           AND qual IS NOT NULL
           AND btrim(regexp_replace(qual, '\s', '', 'g')) NOT IN ('true', '(true)');

        SELECT count(*) INTO ins_ok
          FROM pg_policies
         WHERE schemaname = 'public' AND tablename = t
           AND policyname = 'p_tenant_isolation_insert'
           AND with_check IS NOT NULL
           AND btrim(regexp_replace(with_check, '\s', '', 'g')) NOT IN ('true', '(true)');

        IF forced = 1 AND sel_ok = 1 AND ins_ok = 1 THEN
            ready := ready + 1;
        END IF;
    END LOOP;

    IF ready = partition_count + 1 THEN
        RAISE NOTICE '0013: RLS 已就绪（% 张表：FORCE + 非恒真 SELECT/INSERT 策略齐全），零 DDL',
            ready;
        RETURN;
    END IF;
    RAISE NOTICE '0013: RLS 仅 %/% 张表符合口径，补齐剩余部分', ready, partition_count + 1;

    FOR idx IN -1..(partition_count - 1) LOOP
        IF idx = -1 THEN
            t := 'event_log';
        ELSE
            t := 'event_log_p' || lpad(idx::text, 2, '0');
        END IF;

        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS p_tenant_isolation ON %I', t);
        EXECUTE format('DROP POLICY IF EXISTS p_tenant_isolation_select ON %I', t);
        EXECUTE format('DROP POLICY IF EXISTS p_tenant_isolation_insert ON %I', t);

        -- 读：本租户，或平台视角
        EXECUTE format(
            'CREATE POLICY p_tenant_isolation_select ON %I FOR SELECT '
            'USING (tenant_id = current_setting(''app.tenant_id'', TRUE) '
            '    OR current_setting(''app.tenant_id'', TRUE) = ''__platform__'')', t);

        -- 写：只允许写入**当前租户**的行（WITH CHECK 不可省，否则可以写入别人的
        -- tenant_id 再靠 SELECT 策略把它藏起来——那是一条伪造事件链的通道）
        EXECUTE format(
            'CREATE POLICY p_tenant_isolation_insert ON %I FOR INSERT '
            'WITH CHECK (tenant_id = current_setting(''app.tenant_id'', TRUE) '
            '    OR current_setting(''app.tenant_id'', TRUE) = ''__platform__'')', t);
    END LOOP;
    RAISE NOTICE '0013: event_log 及 % 个分区的租户隔离策略就绪（只 SELECT/INSERT）', partition_count;
EXCEPTION
    WHEN insufficient_privilege THEN
        RAISE EXCEPTION
            '0013 需要 event_log 及其 16 个分区的属主权限来启用 RLS'
            '（ENABLE/FORCE ROW LEVEL SECURITY 与 CREATE POLICY），但当前角色不是属主。'
            '请由 DBA 执行（对 event_log 与 event_log_p00…p15 逐张执行）：'
            'ALTER TABLE <表> ENABLE ROW LEVEL SECURITY; '
            'ALTER TABLE <表> FORCE ROW LEVEL SECURITY; '
            'CREATE POLICY p_tenant_isolation_select ON <表> FOR SELECT USING (...) ; '
            'CREATE POLICY p_tenant_isolation_insert ON <表> FOR INSERT WITH CHECK (...) ;'
            '（完整定义见本文件第 4 节）—— 缺 RLS = 跨租户可读，属隔离降级，本迁移拒绝跳过。';
END $rls$;

-- ---------------------------------------------------------------------------
-- 5. 应用角色授权：**只授 INSERT + SELECT**
--
--    与 `init-app-role.sql` 的授权体例一致（那里对全部表授了四权，本表是例外：
--    事件流 append-only，UPDATE/DELETE 一旦授出，"不可变"就只剩文档承诺）。
--    注意 `init-app-role.sql` 里还有 `ALTER DEFAULT PRIVILEGES ... GRANT ...
--    UPDATE, DELETE`，所以**必须显式 REVOKE**，否则新表的默认权限里已含四权。
--
--    分区要单独授权：PG 的权限不随分区继承（直查/写入分区各自校验）。
--
--    角色不存在时跳过（空库自建路径下角色名可能不同，不因授权失败阻断建表）。
-- ---------------------------------------------------------------------------
DO $grant$
DECLARE
    partition_count CONSTANT integer := 16;
    idx             integer;
    t               text;
    granted         integer := 0;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'officeai_app') THEN
        RAISE NOTICE '0013: 角色 officeai_app 不存在，跳过后端授权（空库自建路径）';
        RETURN;
    END IF;

    -- **零 DDL 守卫**（路径 B）：当前角色不是表属主时（表由超级用户/初始化脚本
    -- 建好），`REVOKE/GRANT` 会抛 `must be owner of table`。此时授权通常已由
    -- 建表方给过——先**逐项验事实**（缺陷 D5：只看父表等于给"分区没授权"和
    -- "UPDATE/DELETE 被顺手授出"留了两条静默通道），够了就跳过；不够则给出
    -- 可照做的 DBA SQL 并中止。
    granted := 0;
    FOR idx IN -1..(partition_count - 1) LOOP
        IF idx = -1 THEN
            t := 'event_log';
        ELSE
            t := 'event_log_p' || lpad(idx::text, 2, '0');
        END IF;
        IF has_table_privilege('officeai_app', t, 'SELECT')
           AND has_table_privilege('officeai_app', t, 'INSERT')
           AND NOT has_table_privilege('officeai_app', t, 'UPDATE')
           AND NOT has_table_privilege('officeai_app', t, 'DELETE') THEN
            granted := granted + 1;
        END IF;
    END LOOP;

    IF granted = partition_count + 1 THEN
        RAISE NOTICE '0013: 授权已就绪（% 张表均为 SELECT+INSERT 且无 UPDATE/DELETE），零 DDL',
            granted;
        RETURN;
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_class c
          JOIN pg_roles r ON r.oid = c.relowner
         WHERE c.oid = 'event_log'::regclass
           AND pg_has_role(current_user, r.oid, 'USAGE')
    ) THEN
        RAISE EXCEPTION
            '0013 需要 event_log 及其 16 个分区的属主权限来收回 UPDATE/DELETE 并授 '
            'SELECT/INSERT，但当前角色不是属主，且当前授权不满足口径（%/% 张表合规）。'
            '请由 DBA 对 event_log 与 event_log_p00…p15 逐张执行：'
            'REVOKE ALL ON <表> FROM officeai_app; '
            'GRANT SELECT, INSERT ON <表> TO officeai_app;'
            '—— 多授 UPDATE/DELETE 等于 append-only 只剩文档承诺，本迁移拒绝跳过。',
            granted, partition_count + 1;
    END IF;

    FOR idx IN -1..(partition_count - 1) LOOP
        IF idx = -1 THEN
            t := 'event_log';
        ELSE
            t := 'event_log_p' || lpad(idx::text, 2, '0');
        END IF;
        EXECUTE format('REVOKE ALL ON %I FROM officeai_app', t);
        EXECUTE format('GRANT SELECT, INSERT ON %I TO officeai_app', t);
    END LOOP;
    RAISE NOTICE '0013: officeai_app 已获 event_log 及分区的 SELECT/INSERT（无 UPDATE/DELETE）';
EXCEPTION
    WHEN insufficient_privilege THEN
        RAISE EXCEPTION
            '0013 无法调整 event_log 的授权（需要属主或授权选项），请由表属主执行：'
            'REVOKE ALL ON <event_log 与 event_log_p00…p15> FROM officeai_app; '
            'GRANT SELECT, INSERT ON <同上> TO officeai_app; '
            '授权缺失时应用会拿到 "permission denied for table event_log"，'
            '而不是静默不记录——这是有意的 fail-closed。';
END $grant$;

-- ---------------------------------------------------------------------------
-- 附录 A：为什么 GRANT 之后还要 RLS 兜底（评审必读，勿删）
--
-- 0013 由迁移 Job 以**属主/迁移角色** DSN 执行（不是应用角色——见文件头
-- 「执行身份」）。于是 event_log 的属主是**另一个角色**，`REVOKE`/`GRANT` 对它
-- 才真正生效；但"应用角色在某些库里恰好是属主"仍然是可能的（例如从未做过角色
-- 分离的旧库），而 SQL 语义里属主天然拥有全部权限、REVOKE 对属主**不生效**——
-- 只看 GRANT 的话，"不可 UPDATE/DELETE"就只是一句注释。
--
-- 因此第 4 节的 `FORCE ROW LEVEL SECURITY` + 只建 SELECT/INSERT 策略承担真正的
-- append-only 保证：FORCE 让策略对属主也生效，缺 UPDATE/DELETE 策略即恒假。
-- 实测口径（`tests/integration/test_event_log_append_only_pg.py`）：
-- `UPDATE event_log SET payload = '{}'::jsonb` 影响 0 行、原行不变。
--
-- 因此在**属主分离**的库里（标准形态）是两道保证同时成立：GRANT 只给
-- SELECT/INSERT，RLS 让 UPDATE/DELETE 恒 0 行；在"应用角色恰好是属主"的库里，
-- GRANT 侧退化，但 FORCE RLS 仍然兜住——两种形态都安全，这是刻意的。
-- ---------------------------------------------------------------------------
