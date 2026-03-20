-- 文件名：202603270001_manager_desk_intervention_layer.sql
-- v1.2 P3: Manager Desk 介入建议状态持久化

-- 本 migration 新增 manager_desk_intervention_records 表
-- 用于存储经理介入建议的最终决议（completed / dismissed）
-- 解决 work_item.status 无法区分 "完成" 与 "忽略" 的问题

CREATE TABLE IF NOT EXISTS manager_desk_intervention_records (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  intervention_key varchar(500) NOT NULL,
  resolution_status varchar(50) NOT NULL DEFAULT 'dismissed',
  resolved_by uuid REFERENCES profiles(id) ON DELETE SET NULL,
  resolved_at timestamptz NOT NULL DEFAULT now(),
  outcome_note text,
  customer_id uuid REFERENCES customers(id) ON DELETE SET NULL,
  opportunity_id uuid REFERENCES opportunities(id) ON DELETE SET NULL,
  deal_room_id uuid REFERENCES deal_rooms(id) ON DELETE SET NULL,
  work_item_id uuid REFERENCES work_items(id) ON DELETE SET NULL,
  risk_item_id varchar(200),
  risk_reason text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_intervention_records_org ON manager_desk_intervention_records(org_id);
CREATE INDEX IF NOT EXISTS idx_intervention_records_key ON manager_desk_intervention_records(org_id, intervention_key);
CREATE INDEX IF NOT EXISTS idx_intervention_records_customer ON manager_desk_intervention_records(customer_id);
CREATE INDEX IF NOT EXISTS idx_intervention_records_deal_room ON manager_desk_intervention_records(deal_room_id);
CREATE INDEX IF NOT EXISTS idx_intervention_records_resolved_by ON manager_desk_intervention_records(resolved_by);

ALTER TABLE manager_desk_intervention_records ENABLE ROW LEVEL SECURITY;

CREATE POLICY "org_read" ON manager_desk_intervention_records FOR SELECT
  USING (org_id IN (
    SELECT org_id FROM profiles WHERE id = auth.uid()
  ));

CREATE POLICY "org_write" ON manager_desk_intervention_records FOR ALL
  USING (org_id IN (
    SELECT org_id FROM profiles WHERE id = auth.uid() AND role IN ('admin', 'manager')
  ));

CREATE OR REPLACE FUNCTION update_intervention_timestamp()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_intervention_timestamp
  BEFORE UPDATE ON manager_desk_intervention_records
  FOR EACH ROW EXECUTE FUNCTION update_intervention_timestamp();
