-- 文件名：supabase/migrations/202603250001_attribution_layer.sql
-- 本 migration 为 action_outcomes 表增加归因字段
-- 用于追踪 action_outcomes 与 business_events 的关联关系

-- 1. 变更说明
-- 为 action_outcomes 表增加 linked_business_event_ids 字段
-- 该字段存储与该 outcome 相关联的 business_event IDs（JSON 数组）

-- 2. 变更内容
ALTER TABLE public.action_outcomes
ADD COLUMN IF NOT EXISTS linked_business_event_ids jsonb DEFAULT '[]'::jsonb;

-- 3. 注释说明
COMMENT ON COLUMN public.action_outcomes.linked_business_event_ids IS '关联的 business_event IDs，用于归因追踪';

-- 4. 索引（可选，用于查询优化）
CREATE INDEX IF NOT EXISTS idx_action_outcomes_linked_events
ON public.action_outcomes USING GIN (linked_business_event_ids);

-- 5. 历史数据回填
-- 通过 work_items 的 source_ref_type/id 关联回填
UPDATE public.action_outcomes ao
SET linked_business_event_ids = (
  SELECT jsonb_agg(wi.source_ref_id::text)
  FROM public.work_items wi
  WHERE wi.id = ao.work_item_id
    AND wi.source_ref_type = 'business_event'
    AND wi.source_ref_id IS NOT NULL
)
WHERE ao.work_item_id IS NOT NULL
  AND ao.linked_business_event_ids = '[]'::jsonb
  AND EXISTS (
    SELECT 1 FROM public.work_items wi
    WHERE wi.id = ao.work_item_id
      AND wi.source_ref_type = 'business_event'
      AND wi.source_ref_id IS NOT NULL
  );
