-- ===========================================================================
-- 启用 pgvector 扩展（`vector`）—— **存量客户升级时唯一需要 DBA 动手的 SQL**
--
-- 归属：阶段四 T4-05 第三批（架构 §9.2 / §9.3.1）
--
-- 为什么这一句不能被脚本自动完成（三条事实，缺一条都推不翻这个结论）：
--   1. `vector` 扩展 **`trusted = false`**。实测（`pgvector/pgvector:pg18`）：
--        SELECT name, version, trusted, superuser FROM pg_available_extension_versions
--         WHERE name = 'vector';
--        vector | 0.8.7 | trusted=false | superuser=true
--   2. 应用角色 `officeai_app` 是 **NOSUPERUSER**（连它是库属主也一样）。实测：
--        psql -U appowner -d officeai -c "CREATE EXTENSION IF NOT EXISTS vector;"
--        ERROR:  permission denied to create extension "vector"
--        HINT:   Must be superuser to create this extension.
--   3. 扩展是**按库**安装的物理对象，不是实例级开关：
--      `pg_available_extensions` 里 available = t 只代表「安装文件在镜像里」，
--      `pg_extension` 里 installed = f 才代表「这个库真的启用了」。
--      所以**每个库各要执行一次**（业务库 officeai、底座库 dify）。
--
-- 新装客户**不需要**执行本文件：`deploy/init-schema-phase4.sql` 的
-- `CREATE EXTENSION IF NOT EXISTS vector;` 由 initdb 以超级用户（POSTGRES_USER）
-- 自动执行。只有「库已存在、不走 initdb」的存量升级才要人工跑一次。
--
-- 用法（在数据库宿主机上，或者 `docker compose exec -T postgres bash` 进去）：
--
--   psql -U <超级用户> -d officeai -f /app/deploy/sql/enable-vector-extension.sql
--   psql -U <超级用户> -d dify     -f /app/deploy/sql/enable-vector-extension.sql
--
--   # 容器内更常见的写法（超级用户 = OA_POSTGRES_USER，默认 officeai）：
--   docker compose -f deploy/docker-compose.lite.yml --env-file deploy/.env.lite \
--     exec -T postgres psql -U officeai -d officeai -f /docker-entrypoint-initdb.d/../..
--   # ↑ 嫌长就用下面这条等价的一行命令（结果完全相同）：
--   docker compose -f deploy/docker-compose.lite.yml --env-file deploy/.env.lite \
--     exec -T postgres psql -U officeai -d officeai -c "CREATE EXTENSION IF NOT EXISTS vector;"
--
-- 幂等性：本文件可**重复执行**，已有扩展时只做版本校验，零改动。
-- 权限：必须由**超级用户**执行（不是库属主，也不是 officeai_app）。
--
-- 跑完怎么确认成功：文件末尾会打印 `extname = vector` 与版本号（要求 >= 0.8）；
-- 也可以自己查：
--   SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';
--
-- 失败对照表（照着做就能修）：
--   · `ERROR: could not open extension control file .../vector.control: No such file`
--     → 这个数据库实例用的镜像里没有 pgvector 安装文件。轻量版必须用
--       `pgvector/pgvector:pg18`，纯 `postgres:18` 只有 pg_trgm（实测）。
--   · `ERROR: permission denied to create extension "vector"`
--     → 当前连接角色不是超级用户。换 `POSTGRES_USER` 对应的角色再跑。
--   · `ERROR: extension "vector" has no update path`
--     → 已安装的版本比镜像内低太多，需要 `ALTER EXTENSION vector UPDATE;`（同样是超级用户）。
-- ===========================================================================

\set ON_ERROR_STOP on
\echo '--- 启用 pgvector（vector）扩展：开始 ---'

DO $enable_vector$
DECLARE
    installed_version TEXT;
BEGIN
    -- ① 安装文件在不在：这一步失败说明镜像不对，改 SQL 改不出来
    IF NOT EXISTS (
        SELECT 1 FROM pg_available_extensions WHERE name = 'vector'
    ) THEN
        RAISE EXCEPTION
            '当前数据库实例里没有 vector 扩展的安装文件（pg_available_extensions 无 vector）。'
            '轻量版/标准版都必须使用 pgvector/pgvector:pg18 镜像；'
            '纯 postgres:* 镜像只自带 pg_trgm。';
    END IF;

    -- ② 幂等安装：已存在时是空操作
    CREATE EXTENSION IF NOT EXISTS vector;

    -- ③ 版本门槛：记忆表用 `vector(1024)` 与 HNSW 索引，要求 >= 0.8
    SELECT extversion INTO installed_version
      FROM pg_extension WHERE extname = 'vector';

    IF installed_version IS NULL THEN
        RAISE EXCEPTION 'vector 扩展安装后仍然查不到（pg_extension 无记录），请检查数据库日志';
    END IF;

    IF string_to_array(installed_version, '.')::INT[] < string_to_array('0.8', '.')::INT[] THEN
        RAISE EXCEPTION
            'vector 扩展版本过低（%），要求 >= 0.8；请 DBA 执行 ALTER EXTENSION vector UPDATE;',
            installed_version;
    END IF;

    RAISE NOTICE 'vector 扩展已就绪，版本 %（库：%）', installed_version, current_database();
END
$enable_vector$;

\echo '--- 自检：下面必须各返回一行（extname = vector）---'
SELECT current_database() AS database,
       extname,
       extversion
  FROM pg_extension
 WHERE extname = 'vector';

\echo '--- 自检：下面必须返回 t（应用角色能否看到该扩展）---'
SELECT EXISTS (
           SELECT 1 FROM pg_extension WHERE extname = 'vector'
       ) AS vector_installed;

\echo '--- 启用 pgvector（vector）扩展：完成。两个库都要各跑一次 ---'
