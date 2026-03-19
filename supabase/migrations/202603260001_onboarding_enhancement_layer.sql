-- Phase 18 Sprint 1: Onboarding Enhancement Layer
-- 为 org_settings 表添加导入摘要字段，支持导入后自动生成经营扫描摘要

-- 1. 添加导入摘要相关字段
ALTER TABLE public.org_settings
ADD COLUMN IF NOT EXISTS import_summary_snapshot JSONB DEFAULT NULL,
ADD COLUMN IF NOT EXISTS import_summary_generated_at TIMESTAMPTZ DEFAULT NULL;

-- 2. 添加首次登录时间字段
ALTER TABLE public.org_settings
ADD COLUMN IF NOT EXISTS first_login_at TIMESTAMPTZ DEFAULT NULL;

-- 3. 添加 onboarding 引导状态字段
ALTER TABLE IF NOT EXISTS org_settings
ADD COLUMN IF NOT EXISTS onboarding_banner_dismissed BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS onboarding_banner_dismissed_at TIMESTAMPTZ DEFAULT NULL;

-- 4. 添加注释
COMMENT ON COLUMN public.org_settings.import_summary_snapshot IS '导入后自动生成的经营扫描摘要，包含健康分布、风险客户、建议动作等';
COMMENT ON COLUMN public.org_settings.import_summary_generated_at IS '导入摘要生成时间';
COMMENT ON COLUMN public.org_settings.first_login_at IS '组织首次登录时间';
COMMENT ON COLUMN public.org_settings.onboarding_banner_dismissed IS '用户是否已关闭 onboarding 引导横幅';
COMMENT ON COLUMN public.org_settings.onboarding_banner_dismissed_at IS 'onboarding 引导横幅关闭时间';

-- 5. 创建索引用于查询
CREATE INDEX IF NOT EXISTS idx_org_settings_import_summary_generated_at
ON public.org_settings (import_summary_generated_at)
WHERE import_summary_generated_at IS NOT NULL;

-- 6. 为 ai_prompts 表添加 import_business_summary 场景（如果不存在）
INSERT INTO public.ai_prompts (scenario, provider_id, system_prompt, developer_prompt, output_schema, version, is_active)
VALUES (
  'import_business_summary',
  'deepseek',
  '你是一个专业的销售运营分析师，负责分析导入数据并生成经营扫描摘要。

你的任务是：
1. 分析客户健康度分布
2. 识别风险客户和停滞商机
3. 提供优先处理建议
4. 推荐应启用的自动化规则
5. 指出管理层应关注的关键点

输出必须基于数据事实，建议必须具体可执行。',
  '基于以下导入数据生成经营扫描摘要：
- 客户健康度分布
- 停滞商机数量
- 高优先级事项
- 严重预警数量

输出格式要求：
1. health_distribution: 各健康等级的客户数量
2. stalled_count: 停滞商机数量
3. priority_items: 优先处理事项列表（最多5项）
4. recommended_rules: 建议启用的规则
5. manager_attention_points: 管理层关注点
6. quick_wins: 快速见效建议',
  '{"type":"object","properties":{"health_distribution":{"type":"object","properties":{"healthy":{"type":"number"},"stable":{"type":"number"},"at_risk":{"type":"number"},"critical":{"type":"number"}}},"stalled_count":{"type":"number"},"priority_items":{"type":"array","items":{"type":"object","properties":{"type":{"type":"string","enum":["risk_customer","stalled_deal","missing_followup","high_value_opportunity"]},"title":{"type":"string"},"customer_name":{"type":"string"},"reason":{"type":"string"},"suggested_action":{"type":"string"}}}},"recommended_rules":{"type":"array","items":{"type":"object","properties":{"rule_name":{"type":"string"},"reason":{"type":"string"},"priority":{"type":"string","enum":["high","medium","low"]}}}},"manager_attention_points":{"type":"array","items":{"type":"string"}},"quick_wins":{"type":"array","items":{"type":"string"}}},"required":["health_distribution","stalled_count","priority_items","recommended_rules","manager_attention_points","quick_wins"]}',
  1,
  true
) ON CONFLICT (scenario, provider_id, version) DO NOTHING;
