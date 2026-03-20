-- 文件名：202603290001_manager_insights_snapshot_automation.sql
-- v1.5: Manager Insights Snapshot 自动化层
-- 本 migration 为 cron-ready 基础设施，支持自动执行周/月快照
-- 依赖：manager_insights_snapshot_layer.sql（v1.4）

-- 1. 启用 pg_cron 扩展（如果尚未启用）
CREATE EXTENSION IF NOT EXISTS pg_cron;

-- 2. 创建 internal snapshot run API 的 HTTP endpoint handler 占位
-- 注意：实际的 /api/internal/manager/insights/snapshot/run 由 Next.js API Route 提供
-- pg_cron 任务通过 SELECT cron.schedule 调度 net.http_post() 调用该 endpoint

-- 3. 调度每周一 02:00 (UTC+8) 执行 manager insights weekly snapshot
-- 使用 cron.schedule(job_name, schedule, command)
-- net.http_post() 发送请求到内部 API endpoint
-- 为避免 auth，internal API 使用服务级别 key 或免密白名单
SELECT cron.schedule(
  'manager-insights-weekly-snapshot',
  '0 2 * * 1', -- 每周一 02:00 UTC+8 (北京时间)
  $$
  SELECT
    net.http_post(
      url := current_setting('app.internal_api_base_url', true) || '/api/internal/manager/insights/snapshot/run',
      headers := jsonb_build_object(
        'Content-Type', 'application/json',
        'X-Internal-Key', current_setting('app.internal_api_key', true)
      ),
      body := jsonb_build_object(
        'snapshotType', 'weekly',
        'periodDays', 7
      )
    )
  $$
);

-- 4. 调度每月 1 日 02:00 (UTC+8) 执行 manager insights monthly snapshot
-- 注意：monthly snapshot 语义为"上一个完整自然月"
-- 例如：2026-04-01 执行时，生成 2026-03-01 ~ 2026-03-31 的快照
-- 这样保证不会有"未来区间"数据，且每月 1 日恰好是上月结算数据最完整的时刻
SELECT cron.schedule(
  'manager-insights-monthly-snapshot',
  '0 2 1 * *', -- 每月 1 日 02:00 UTC+8 (北京时间)
  $$
  SELECT
    net.http_post(
      url := current_setting('app.internal_api_base_url', true) || '/api/internal/manager/insights/snapshot/run',
      headers := jsonb_build_object(
        'Content-Type', 'application/json',
        'X-Internal-Key', current_setting('app.internal_api_key', true)
      ),
      body := jsonb_build_object(
        'snapshotType', 'monthly'
      )
    )
  $$
);

-- 5. 添加内部 API 配置（通过 ALTER DATABASE 或 app.settings）
-- 这两个 setting 需要在 Supabase Dashboard → Database → Settings 中配置：
-- app.internal_api_base_url: 你的 Supabase Project URL (如 https://xxxx.supabase.co)
-- app.internal_api_key: 内部 API 调用的密钥（自定义随机字符串，建议 32+ 字符）
-- 建议通过 Supabase Vault 存储此密钥，而非明文配置文件
