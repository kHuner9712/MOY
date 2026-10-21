# ===========================================================================
# 底座数据库初始化（docker-entrypoint-initdb.d）
#
# 统一 postgres 实例内的三个逻辑库：
#   - officeai：业务库（适配层/业务能力层，POSTGRES_DB 环境变量已自动创建）
#   - dify    ：底座库（Dify api/worker，MIGRATION_ENABLED=true 时自动建表）
#   - keycloak：身份库（Keycloak 首次启动自动建表）
#
# 执行时机：postgres 数据目录为空的首次启动。已有数据的实例不会重跑，
# 需手动执行：docker exec oa-postgres psql -U officeai -f /dev/stdin < 本文件
#
# 历史教训（2026-10-21 实跑）：初版编排只建了 officeai 一个库，
# dify-api 迁移与 keycloak 启动双双失败（database "dify" does not exist）。
# ===========================================================================

-- 底座库（Dify）
SELECT 'CREATE DATABASE dify'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'dify')\gexec

-- 身份库（Keycloak）
SELECT 'CREATE DATABASE keycloak'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'keycloak')\gexec

-- 底座插件库（Dify plugin_daemon，独立库，官方 1.0.0 同款）
SELECT 'CREATE DATABASE dify_plugin'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'dify_plugin')\gexec
