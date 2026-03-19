-- 文件名：supabase/migrations/202603250002_notification_layer.sql
-- 本 migration 为通知系统创建基础表结构
-- 用于支持站内信、微信、邮件等多渠道通知

-- 1. 变更说明
-- 创建 notifications 表用于存储通知记录
-- 创建 notification_preferences 表用于存储用户通知偏好

-- 2. 创建 notifications 表
CREATE TABLE IF NOT EXISTS public.notifications (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
  user_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  channel text NOT NULL DEFAULT 'in_app',
  priority text NOT NULL DEFAULT 'normal',
  title text NOT NULL,
  content text NOT NULL,
  action_url text,
  source_type text NOT NULL,
  source_id text NOT NULL,
  scheduled_at timestamptz,
  sent_at timestamptz,
  status text NOT NULL DEFAULT 'pending',
  error_message text,
  created_at timestamptz NOT NULL DEFAULT timezone('utc', now()),
  updated_at timestamptz NOT NULL DEFAULT timezone('utc', now()),
  
  CONSTRAINT notifications_channel_check CHECK (channel IN ('in_app', 'wechat', 'email')),
  CONSTRAINT notifications_priority_check CHECK (priority IN ('critical', 'high', 'normal', 'low')),
  CONSTRAINT notifications_status_check CHECK (status IN ('pending', 'sent', 'failed', 'cancelled')),
  CONSTRAINT notifications_source_type_check CHECK (source_type IN ('business_event', 'morning_brief', 'intervention_request', 'automation_rule', 'work_item', 'manual'))
);

-- 3. 创建 notification_preferences 表
CREATE TABLE IF NOT EXISTS public.notification_preferences (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
  user_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  channel text NOT NULL,
  is_enabled boolean NOT NULL DEFAULT true,
  config jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT timezone('utc', now()),
  updated_at timestamptz NOT NULL DEFAULT timezone('utc', now()),
  
  CONSTRAINT notification_preferences_channel_check CHECK (channel IN ('in_app', 'wechat', 'email')),
  CONSTRAINT notification_preferences_unique_user_channel UNIQUE (user_id, channel)
);

-- 4. 索引
CREATE INDEX IF NOT EXISTS idx_notifications_org_user ON public.notifications(org_id, user_id);
CREATE INDEX IF NOT EXISTS idx_notifications_status ON public.notifications(status, created_at);
CREATE INDEX IF NOT EXISTS idx_notifications_scheduled ON public.notifications(scheduled_at) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_notifications_source ON public.notifications(source_type, source_id);

-- 5. RLS 策略
ALTER TABLE public.notifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.notification_preferences ENABLE ROW LEVEL SECURITY;

CREATE POLICY notifications_select_policy ON public.notifications
FOR SELECT USING (org_id = (SELECT org_id FROM public.profiles WHERE id = auth.uid()) AND user_id = auth.uid());

CREATE POLICY notifications_insert_policy ON public.notifications
FOR INSERT WITH CHECK (org_id = (SELECT org_id FROM public.profiles WHERE id = auth.uid()));

CREATE POLICY notifications_update_policy ON public.notifications
FOR UPDATE USING (org_id = (SELECT org_id FROM public.profiles WHERE id = auth.uid()) AND user_id = auth.uid());

CREATE POLICY notification_preferences_select_policy ON public.notification_preferences
FOR SELECT USING (user_id = auth.uid());

CREATE POLICY notification_preferences_insert_policy ON public.notification_preferences
FOR INSERT WITH CHECK (user_id = auth.uid());

CREATE POLICY notification_preferences_update_policy ON public.notification_preferences
FOR UPDATE USING (user_id = auth.uid());

-- 6. 注释说明
COMMENT ON TABLE public.notifications IS '通知队列表，用于存储待发送和已发送的通知';
COMMENT ON TABLE public.notification_preferences IS '通知偏好表，用于存储用户的通知渠道偏好设置';

-- 7. 为现有用户创建默认通知偏好
INSERT INTO public.notification_preferences (org_id, user_id, channel, is_enabled, config)
SELECT 
  p.org_id,
  p.id as user_id,
  'in_app' as channel,
  true as is_enabled,
  '{"dailyDigestEnabled": true, "weeklyDigestEnabled": true, "criticalImmediate": true}'::jsonb as config
FROM public.profiles p
WHERE NOT EXISTS (
  SELECT 1 FROM public.notification_preferences np WHERE np.user_id = p.id AND np.channel = 'in_app'
);

-- 8. 为经理角色创建微信通知偏好（预留）
INSERT INTO public.notification_preferences (org_id, user_id, channel, is_enabled, config)
SELECT 
  p.org_id,
  p.id as user_id,
  'wechat' as channel,
  false as is_enabled,
  '{}'::jsonb as config
FROM public.profiles p
WHERE p.role = 'manager'
AND NOT EXISTS (
  SELECT 1 FROM public.notification_preferences np WHERE np.user_id = p.id AND np.channel = 'wechat'
);

-- 9. 为经理角色创建邮件通知偏好（预留）
INSERT INTO public.notification_preferences (org_id, user_id, channel, is_enabled, config)
SELECT 
  p.org_id,
  p.id as user_id,
  'email' as channel,
  false as is_enabled,
  '{}'::jsonb as config
FROM public.profiles p
WHERE p.role = 'manager'
AND NOT EXISTS (
  SELECT 1 FROM public.notification_preferences np WHERE np.user_id = p.id AND np.channel = 'email'
);
