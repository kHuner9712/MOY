-- ===========================================================================
-- 0005 阶段三 B22 产物二进制内容列（`plugin_artifact.content` BYTEA）
--
-- 背景（P3-B22-03，属「内存全绿、生产全废」一族）：
--   `plugin_artifact` 原本只存 `inline_text`（≤32KB 的**文本预览**，已脱敏）。
--   文件类产物（docx / xlsx / zip）的真实字节只活在**内存后端**的
--   `ArtifactRecord.content` 里；一旦 `OA_BUSINESS_STORE=postgres`（私有化部署的
--   唯一形态），下载端点 `api/v1/artifact_files.py` 里 `record.content` 恒为空，
--   于是回落成 `record.inline_text.encode()` —— 用户点「导出 Word 公文」拿到的
--   是**文本预览字节**，不是能被 Word 打开的 .docx。
--   验收报告「4 类公文可导出真 docx（可解压 word/document.xml）」当时只在内存模式成立。
--
-- 为什么是 BYTEA 而不是对象存储：
--   对象存储在私有化路径上是**可选项**（V1.0 明说），不能作为"二进制能否落库"的前提。
--   产物是单件 ≤ 数十 KB 级的公文/表格/代码包，BYTEA 的 TOAST 存储完全够用，
--   且让「私有化只部署 PG 一个组件」这条交付承诺成立。存储地址依旧不经 API 暴露
--   （下载一律走平台代理 `/files/artifacts/{token}`）。
--
-- 三段式语义（与 `0004` 同体例 —— 应用角色不做 DDL）：
--   1. 列已存在（`init-schema-phase3.sql` 建的新库）→ **零 DDL**，只读自检后返回；
--   2. 列缺失且当前角色有权（空库路径：`0003` 的建表 DDL 定格在 B19，不含本列；
--      该路径下应用角色同时是库/表属主）→ 补列；
--   3. 列缺失且无权（已初始化库：`init-schema-phase3.sql` 由超级用户跑，表属主是超级用户）
--      → 报**可照做**的错误（用超级用户重跑 init 脚本 / 由 DBA 以表属主身份执行），
--      而不是把 `must be owner of table` 原样甩给运维。
--
-- ⚠ 与 `0003` 的关系（有意为之，不是漏改）：
--   `0003` 里的建表 DDL 是**历史定格**，不再回改——迁移链的语义是
--   「0003 建表（B19 形态）→ 0004 补版本列 → 0005 补内容列」，
--   空库路径靠本文件把列补齐就够（迁移必须自足，G-11）。
--   新库则直接由 `init-schema-phase3.sql` 一次建全，本文件走第 1 段零 DDL。
--
-- 幂等性：`ADD COLUMN IF NOT EXISTS` + 存在性守卫，连跑两遍第二遍是纯通知。
-- 自足性：空库上 `to_regclass('public.plugin_artifact')` 为 NULL 时**不 RAISE**
--   （`0003` 尚未跑或本库不用阶段三表），只提示后返回——否则空库路径整条迁移崩。
--
-- 执行方式：`app.db.migrate` 内置单事务（本文件不得写 BEGIN/COMMIT）。
-- ===========================================================================

DO $guard$
DECLARE
    existing_type TEXT;
BEGIN
    -- ① 表都没有就别提列（空库 / 未启用阶段三）：提示后返回，绝不 RAISE
    IF to_regclass('public.plugin_artifact') IS NULL THEN
        RAISE NOTICE '0005: plugin_artifact 不存在（未跑 0003），跳过';
        RETURN;
    END IF;

    -- ② 只读地看列在不在（存在时**绝不**碰 DDL：应用角色不是表属主）
    SELECT data_type INTO existing_type
      FROM information_schema.columns
     WHERE table_schema = current_schema()
       AND table_name = 'plugin_artifact'
       AND column_name = 'content';

    IF existing_type IS NOT NULL THEN
        -- 列在但类型不对，是最坏的一种"看起来迁移过了"：往里写字节会在运行期炸
        IF existing_type <> 'bytea' THEN
            RAISE EXCEPTION
                '0005 中止：plugin_artifact.content 已存在但类型是 %（期望 bytea）。'
                '这通常意味着有人用别的类型手工加过列，请先人工核对再重跑。',
                existing_type;
        END IF;
        RAISE NOTICE '0005: plugin_artifact.content 已存在（init 脚本建的新库），零 DDL，自检通过';
        RETURN;
    END IF;

    -- ③ 缺列才补；无 DDL 权限时给可照做的替代动作
    BEGIN
        ALTER TABLE plugin_artifact ADD COLUMN IF NOT EXISTS content BYTEA;
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE EXCEPTION
                '0005 需要补 1 列（plugin_artifact.content BYTEA），但当前角色不是表属主'
                '（must be owner of table）。请二选一：'
                '① 用超级用户重跑 deploy/init-schema-phase3.sql'
                '（新版已含该列，重建后本迁移零 DDL）；'
                '② 由 DBA 以表属主身份执行本迁移。'
                '在此之前，PG 后端下载产物只能拿到文本预览，不是真实文件。';
    END;

    -- ④ 收尾自检：补完必须真的在且是 bytea（防"跑了个不生效的 ALTER"静默通过）
    SELECT data_type INTO existing_type
      FROM information_schema.columns
     WHERE table_schema = current_schema()
       AND table_name = 'plugin_artifact'
       AND column_name = 'content';

    IF existing_type IS NULL THEN
        RAISE EXCEPTION '0005 自检失败：plugin_artifact.content 仍不存在';
    END IF;
    IF existing_type <> 'bytea' THEN
        RAISE EXCEPTION
            '0005 自检失败：plugin_artifact.content 类型为 %（期望 bytea）', existing_type;
    END IF;

    RAISE NOTICE '0005: plugin_artifact.content 已补齐并自检通过';
END $guard$;
