-- 文件名：202603280001_manager_insights_snapshot_layer.sql
-- v1.4: Manager Insights Snapshot 层
-- 用于存储每周期的 manager insights 快照，支持历史趋势分析

-- manager_insights_snapshots 表
CREATE TABLE IF NOT EXISTS manager_insights_snapshots (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  period_start date NOT NULL,
  period_end date NOT NULL,
  snapshot_type varchar(20) NOT NULL DEFAULT 'weekly',
  truth_band_distribution jsonb NOT NULL DEFAULT '[]'::jsonb,
  intervention_stats jsonb NOT NULL DEFAULT '{}'::jsonb,
  risk_signals jsonb NOT NULL DEFAULT '{}'::jsonb,
  risk_improvement jsonb NOT NULL DEFAULT '{}'::jsonb,
  notes text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_manager_insights_snapshots_org_period
  ON manager_insights_snapshots(org_id, period_start DESC, period_end DESC);
CREATE INDEX IF NOT EXISTS idx_manager_insights_snapshots_type
  ON manager_insights_snapshots(org_id, snapshot_type, period_start DESC);

ALTER TABLE manager_insights_snapshots ENABLE ROW LEVEL SECURITY;

CREATE POLICY "org_read" ON manager_insights_snapshots FOR SELECT
  USING (org_id IN (
    SELECT org_id FROM profiles WHERE id = auth.uid()
  ));

CREATE POLICY "org_write" ON manager_insights_snapshots FOR ALL
  USING (org_id IN (
    SELECT org_id FROM profiles WHERE id = auth.uid()
    AND role IN ('admin', 'manager')
  ));
