-- ===========================================================================
-- 0006 `plugin_artifact.content_type`（阶段四 T4-01.5，关闭 P3-B22-04）
--
-- 背景（P3-B22-04）：
--   `plugin_artifact` 没有 content_type 列，`_to_artifact()` 恒回落
--   `text/plain; charset=utf-8`。于是下载 docx/xlsx 时响应的 `Content-Type`
--   是文本类型——`Content-Disposition: attachment` 让浏览器落盘，但只要有人
--   直接打开链接（或下游网关按 MIME 处理），拿到的就是"文本化的 docx"。
--   产物内容本身是对的（0005 已把字节落库），**声明的类型是错的**。
--
-- 为什么是 `VARCHAR(128) NULL` 而不是 `TEXT NOT NULL DEFAULT ''`：
--   ① NULL 表达"未知"（历史行、`link` 类产物本来就没有 MIME），
--      而 `''` 会与"空 MIME"混淆，让读取端不得不再写一层判空；
--   ② 加性变更且可空 → N-1 版本的应用读这一列不会因为缺值崩，
--      `_to_artifact()` 的回落逻辑原样有效（读不到就回 text/plain）。
--   128 足够容纳 `application/vnd.openxmlformats-officedocument.wordprocessingml.document`
--   （72 字符）这类长 MIME，且长度上界能挡住写入端塞进来的垃圾。
--
-- 三段式语义（与 0004/0005 同体例 —— 应用角色不做 DDL）：
--   1. 列已存在（`init-schema-phase3.sql` 建的新库）→ **零 DDL**，只读自检后返回；
--   2. 列缺失且当前角色有权（空库路径：应用角色同时是库/表属主）→ 补列；
--   3. 列缺失且无权（已初始化库：initdb 由超级用户建表）→ 报**可照做**的错误。
--
-- 幂等性：`ADD COLUMN IF NOT EXISTS` + 存在性守卫，连跑两遍第二遍是纯通知。
-- 自足性：空库上 `to_regclass('public.plugin_artifact')` 为 NULL 时**不 RAISE**。
--
-- 执行方式：`app.db.migrate --apply` 在同一事务 + `pg_advisory_xact_lock` 内执行
--   （本文件**不得**写 BEGIN/COMMIT）。
-- 回滚：`deploy/migrations/rollback/0006_plugin_artifact_content_type.down.sql`。
-- ===========================================================================

DO $guard$
DECLARE
    existing_type TEXT;
BEGIN
    -- ① 表都没有就别提列（空库 / 未启用阶段三）：提示后返回，绝不 RAISE
    IF to_regclass('public.plugin_artifact') IS NULL THEN
        RAISE NOTICE '0006: plugin_artifact 不存在（未跑 0003），跳过';
        RETURN;
    END IF;

    -- ② 只读地看列在不在（存在时**绝不**碰 DDL：应用角色不是表属主）
    SELECT data_type INTO existing_type
      FROM information_schema.columns
     WHERE table_schema = current_schema()
       AND table_name = 'plugin_artifact'
       AND column_name = 'content_type';

    IF existing_type IS NOT NULL THEN
        RAISE NOTICE '0006: plugin_artifact.content_type 已存在（init 脚本建的新库），零 DDL，自检通过';
        RETURN;
    END IF;

    -- ③ 缺列才补；无 DDL 权限时给可照做的替代动作
    BEGIN
        ALTER TABLE plugin_artifact ADD COLUMN IF NOT EXISTS content_type VARCHAR(128);
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE EXCEPTION
                '0006 需要补 1 列（plugin_artifact.content_type VARCHAR(128)），'
                '但当前角色不是表属主（must be owner of table）。请二选一：'
                '① 用超级用户重跑 deploy/init-schema-phase3.sql'
                '（新版已含该列，重建后本迁移零 DDL）；'
                '② 由 DBA 以表属主身份执行本迁移。'
                '在此之前，下载响应只能按 text/plain 声明类型，docx/xlsx 会以文本 MIME 落盘。';
    END;

    -- ④ 收尾自检：补完必须真的在（防"跑了个不生效的 ALTER"静默通过）
    SELECT data_type INTO existing_type
      FROM information_schema.columns
     WHERE table_schema = current_schema()
       AND table_name = 'plugin_artifact'
       AND column_name = 'content_type';

    IF existing_type IS NULL THEN
        RAISE EXCEPTION '0006 自检失败：plugin_artifact.content_type 仍不存在';
    END IF;

    RAISE NOTICE '0006: plugin_artifact.content_type 已补齐并自检通过';
END $guard$;
