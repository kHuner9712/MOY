-- MOY phase-11: organization & productization layer

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'ai_scenario') THEN
    IF NOT EXISTS (
      SELECT 1 FROM pg_enum WHERE enumlabel = 'onboarding_recommendation' AND enumtypid = 'public.ai_scenario'::regtype
    ) THEN
      ALTER TYPE public.ai_scenario ADD VALUE 'onboarding_recommendation';
    END IF;

    IF NOT EXISTS (
      SELECT 1 FROM pg_enum WHERE enumlabel = 'usage_health_summary' AND enumtypid = 'public.ai_scenario'::regtype
    ) THEN
      ALTER TYPE public.ai_scenario ADD VALUE 'usage_health_summary';
    END IF;
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'org_member_role') THEN
    CREATE TYPE public.org_member_role AS ENUM ('owner', 'admin', 'manager', 'sales', 'viewer');
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'org_seat_status') THEN
    CREATE TYPE public.org_seat_status AS ENUM ('invited', 'active', 'suspended', 'removed');
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'org_invite_status') THEN
    CREATE TYPE public.org_invite_status AS ENUM ('pending', 'accepted', 'expired', 'revoked');
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'org_feature_key') THEN
    CREATE TYPE public.org_feature_key AS ENUM (
      'ai_auto_analysis',
      'ai_auto_planning',
      'ai_morning_brief',
      'ai_deal_command',
      'external_touchpoints',
      'prep_cards',
      'playbooks',
      'manager_quality_view',
      'outcome_learning',
      'demo_seed_tools'
    );
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'org_ai_fallback_mode') THEN
    CREATE TYPE public.org_ai_fallback_mode AS ENUM (
      'strict_provider_first',
      'provider_then_rules',
      'rules_only'
    );
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'org_usage_scope') THEN
    CREATE TYPE public.org_usage_scope AS ENUM ('daily', 'monthly');
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'org_plan_tier') THEN
    CREATE TYPE public.org_plan_tier AS ENUM ('demo', 'trial', 'starter', 'growth', 'enterprise');
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'org_plan_status') THEN
    CREATE TYPE public.org_plan_status AS ENUM ('active', 'paused', 'expired');
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'onboarding_run_type') THEN
    CREATE TYPE public.onboarding_run_type AS ENUM (
      'first_time_setup',
      'demo_seed',
      'trial_bootstrap',
      'reinitialize_demo'
    );
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'onboarding_run_status') THEN
    CREATE TYPE public.onboarding_run_status AS ENUM ('queued', 'running', 'completed', 'failed');
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS public.org_settings (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL UNIQUE REFERENCES public.organizations(id) ON DELETE CASCADE,
  org_display_name text NOT NULL,
  brand_name text NOT NULL DEFAULT 'MOY',
  industry_hint text,
  timezone text NOT NULL DEFAULT 'Asia/Shanghai',
  locale text NOT NULL DEFAULT 'zh-CN',
  default_customer_stages jsonb NOT NULL DEFAULT '["lead","initial_contact","needs_confirmed","proposal","negotiation","won","lost"]'::jsonb,
  default_opportunity_stages jsonb NOT NULL DEFAULT '["discovery","qualification","proposal","business_review","negotiation","won","lost"]'::jsonb,
  default_alert_rules jsonb NOT NULL DEFAULT '{"no_followup_timeout":7,"quoted_but_stalled":10,"high_probability_stalled":5}'::jsonb,
  default_followup_sla_days int NOT NULL DEFAULT 3,
  onboarding_completed boolean NOT NULL DEFAULT false,
  onboarding_step_state jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT timezone('utc', now()),
  updated_at timestamptz NOT NULL DEFAULT timezone('utc', now())
);

CREATE TABLE IF NOT EXISTS public.org_feature_flags (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
  feature_key public.org_feature_key NOT NULL,
  is_enabled boolean NOT NULL DEFAULT true,
  config_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT timezone('utc', now()),
  updated_at timestamptz NOT NULL DEFAULT timezone('utc', now()),
  CONSTRAINT org_feature_flags_unique UNIQUE (org_id, feature_key)
);

CREATE TABLE IF NOT EXISTS public.org_ai_settings (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL UNIQUE REFERENCES public.organizations(id) ON DELETE CASCADE,
  provider public.ai_provider NOT NULL DEFAULT 'deepseek',
  model_default text NOT NULL DEFAULT 'deepseek-chat',
  model_reasoning text NOT NULL DEFAULT 'deepseek-reasoner',
  fallback_mode public.org_ai_fallback_mode NOT NULL DEFAULT 'provider_then_rules',
  auto_analysis_enabled boolean NOT NULL DEFAULT true,
  auto_plan_enabled boolean NOT NULL DEFAULT true,
  auto_brief_enabled boolean NOT NULL DEFAULT true,
  auto_touchpoint_review_enabled boolean NOT NULL DEFAULT true,
  human_review_required_for_sensitive_actions boolean NOT NULL DEFAULT true,
  max_daily_ai_runs int,
  max_monthly_ai_runs int,
  created_at timestamptz NOT NULL DEFAULT timezone('utc', now()),
  updated_at timestamptz NOT NULL DEFAULT timezone('utc', now())
);

CREATE TABLE IF NOT EXISTS public.org_memberships (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
  user_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  role public.org_member_role NOT NULL DEFAULT 'sales',
  seat_status public.org_seat_status NOT NULL DEFAULT 'active',
  invited_by uuid REFERENCES public.profiles(id) ON DELETE SET NULL,
  invited_at timestamptz,
  joined_at timestamptz,
  last_active_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT timezone('utc', now()),
  updated_at timestamptz NOT NULL DEFAULT timezone('utc', now()),
  CONSTRAINT org_memberships_unique UNIQUE (org_id, user_id)
);

CREATE TABLE IF NOT EXISTS public.org_invites (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
  email text NOT NULL,
  intended_role public.org_member_role NOT NULL,
  invite_status public.org_invite_status NOT NULL DEFAULT 'pending',
  invite_token text NOT NULL UNIQUE DEFAULT gen_random_uuid()::text,
  invited_by uuid REFERENCES public.profiles(id) ON DELETE SET NULL,
  expires_at timestamptz NOT NULL DEFAULT timezone('utc', now()) + interval '7 days',
  created_at timestamptz NOT NULL DEFAULT timezone('utc', now()),
  updated_at timestamptz NOT NULL DEFAULT timezone('utc', now())
);

CREATE TABLE IF NOT EXISTS public.org_usage_counters (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
  usage_date date NOT NULL,
  usage_scope public.org_usage_scope NOT NULL,
  ai_runs_count int NOT NULL DEFAULT 0,
  prep_cards_count int NOT NULL DEFAULT 0,
  drafts_count int NOT NULL DEFAULT 0,
  reports_count int NOT NULL DEFAULT 0,
  touchpoint_events_count int NOT NULL DEFAULT 0,
  document_processed_count int NOT NULL DEFAULT 0,
  work_plan_generations_count int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT timezone('utc', now()),
  updated_at timestamptz NOT NULL DEFAULT timezone('utc', now()),
  CONSTRAINT org_usage_counters_unique UNIQUE (org_id, usage_date, usage_scope)
);

CREATE TABLE IF NOT EXISTS public.user_usage_counters (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
  user_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  usage_date date NOT NULL,
  usage_scope public.org_usage_scope NOT NULL,
  ai_runs_count int NOT NULL DEFAULT 0,
  prep_cards_count int NOT NULL DEFAULT 0,
  drafts_count int NOT NULL DEFAULT 0,
  reports_count int NOT NULL DEFAULT 0,
  touchpoint_events_count int NOT NULL DEFAULT 0,
  document_processed_count int NOT NULL DEFAULT 0,
  work_plan_generations_count int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT timezone('utc', now()),
  updated_at timestamptz NOT NULL DEFAULT timezone('utc', now()),
  CONSTRAINT user_usage_counters_unique UNIQUE (org_id, user_id, usage_date, usage_scope)
);

CREATE TABLE IF NOT EXISTS public.org_plan_profiles (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL UNIQUE REFERENCES public.organizations(id) ON DELETE CASCADE,
  plan_tier public.org_plan_tier NOT NULL DEFAULT 'trial',
  seat_limit int NOT NULL DEFAULT 5,
  ai_run_limit_monthly int NOT NULL DEFAULT 500,
  document_limit_monthly int NOT NULL DEFAULT 300,
  touchpoint_limit_monthly int NOT NULL DEFAULT 1200,
  advanced_features_enabled boolean NOT NULL DEFAULT false,
  expires_at timestamptz,
  status public.org_plan_status NOT NULL DEFAULT 'active',
  created_at timestamptz NOT NULL DEFAULT timezone('utc', now()),
  updated_at timestamptz NOT NULL DEFAULT timezone('utc', now())
);

CREATE TABLE IF NOT EXISTS public.onboarding_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
  initiated_by uuid NOT NULL REFERENCES public.profiles(id) ON DELETE RESTRICT,
  run_type public.onboarding_run_type NOT NULL,
  status public.onboarding_run_status NOT NULL DEFAULT 'queued',
  summary text NOT NULL DEFAULT '',
  detail_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT timezone('utc', now()),
  updated_at timestamptz NOT NULL DEFAULT timezone('utc', now())
);

CREATE INDEX IF NOT EXISTS idx_org_settings_org_id ON public.org_settings(org_id);
CREATE INDEX IF NOT EXISTS idx_org_settings_created_at ON public.org_settings(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_org_feature_flags_org_id ON public.org_feature_flags(org_id);
CREATE INDEX IF NOT EXISTS idx_org_feature_flags_feature_key ON public.org_feature_flags(feature_key);

CREATE INDEX IF NOT EXISTS idx_org_ai_settings_org_id ON public.org_ai_settings(org_id);

CREATE INDEX IF NOT EXISTS idx_org_memberships_org_id ON public.org_memberships(org_id);
CREATE INDEX IF NOT EXISTS idx_org_memberships_user_id ON public.org_memberships(user_id);
CREATE INDEX IF NOT EXISTS idx_org_memberships_role ON public.org_memberships(role);
CREATE INDEX IF NOT EXISTS idx_org_memberships_seat_status ON public.org_memberships(seat_status);
CREATE INDEX IF NOT EXISTS idx_org_memberships_created_at ON public.org_memberships(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_org_invites_org_id ON public.org_invites(org_id);
CREATE INDEX IF NOT EXISTS idx_org_invites_email ON public.org_invites(email);
CREATE INDEX IF NOT EXISTS idx_org_invites_status ON public.org_invites(invite_status);
CREATE INDEX IF NOT EXISTS idx_org_invites_created_at ON public.org_invites(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_org_usage_counters_org_date_scope ON public.org_usage_counters(org_id, usage_date, usage_scope);
CREATE INDEX IF NOT EXISTS idx_org_usage_counters_created_at ON public.org_usage_counters(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_user_usage_counters_org_user_date_scope ON public.user_usage_counters(org_id, user_id, usage_date, usage_scope);
CREATE INDEX IF NOT EXISTS idx_user_usage_counters_created_at ON public.user_usage_counters(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_org_plan_profiles_org_id ON public.org_plan_profiles(org_id);
CREATE INDEX IF NOT EXISTS idx_org_plan_profiles_plan_tier ON public.org_plan_profiles(plan_tier);
CREATE INDEX IF NOT EXISTS idx_org_plan_profiles_status ON public.org_plan_profiles(status);

CREATE INDEX IF NOT EXISTS idx_onboarding_runs_org_id ON public.onboarding_runs(org_id);
CREATE INDEX IF NOT EXISTS idx_onboarding_runs_status ON public.onboarding_runs(status);
CREATE INDEX IF NOT EXISTS idx_onboarding_runs_created_at ON public.onboarding_runs(created_at DESC);

DROP TRIGGER IF EXISTS set_org_settings_updated_at ON public.org_settings;
CREATE TRIGGER set_org_settings_updated_at
BEFORE UPDATE ON public.org_settings
FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();

DROP TRIGGER IF EXISTS set_org_feature_flags_updated_at ON public.org_feature_flags;
CREATE TRIGGER set_org_feature_flags_updated_at
BEFORE UPDATE ON public.org_feature_flags
FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();

DROP TRIGGER IF EXISTS set_org_ai_settings_updated_at ON public.org_ai_settings;
CREATE TRIGGER set_org_ai_settings_updated_at
BEFORE UPDATE ON public.org_ai_settings
FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();

DROP TRIGGER IF EXISTS set_org_memberships_updated_at ON public.org_memberships;
CREATE TRIGGER set_org_memberships_updated_at
BEFORE UPDATE ON public.org_memberships
FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();

DROP TRIGGER IF EXISTS set_org_invites_updated_at ON public.org_invites;
CREATE TRIGGER set_org_invites_updated_at
BEFORE UPDATE ON public.org_invites
FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();

DROP TRIGGER IF EXISTS set_org_usage_counters_updated_at ON public.org_usage_counters;
CREATE TRIGGER set_org_usage_counters_updated_at
BEFORE UPDATE ON public.org_usage_counters
FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();

DROP TRIGGER IF EXISTS set_user_usage_counters_updated_at ON public.user_usage_counters;
CREATE TRIGGER set_user_usage_counters_updated_at
BEFORE UPDATE ON public.user_usage_counters
FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();

DROP TRIGGER IF EXISTS set_org_plan_profiles_updated_at ON public.org_plan_profiles;
CREATE TRIGGER set_org_plan_profiles_updated_at
BEFORE UPDATE ON public.org_plan_profiles
FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();

DROP TRIGGER IF EXISTS set_onboarding_runs_updated_at ON public.onboarding_runs;
CREATE TRIGGER set_onboarding_runs_updated_at
BEFORE UPDATE ON public.onboarding_runs
FOR EACH ROW EXECUTE PROCEDURE public.set_updated_at();

INSERT INTO public.org_settings (
  org_id,
  org_display_name,
  brand_name,
  industry_hint,
  timezone,
  locale,
  onboarding_step_state
)
SELECT
  o.id,
  o.name,
  'MOY',
  null,
  'Asia/Shanghai',
  'zh-CN',
  '{"org_profile":false,"ai_setup":false,"team_invite":false,"first_data":false,"first_plan_or_brief":false,"first_deal_room":false,"manager_view":false}'::jsonb
FROM public.organizations o
ON CONFLICT (org_id) DO NOTHING;

WITH ranked_profiles AS (
  SELECT
    p.id,
    p.org_id,
    p.role,
    p.created_at,
    row_number() OVER (
      PARTITION BY p.org_id
      ORDER BY (p.role = 'manager') DESC, p.created_at ASC
    ) AS rn
  FROM public.profiles p
  WHERE p.is_active = true
)
INSERT INTO public.org_memberships (
  org_id,
  user_id,
  role,
  seat_status,
  invited_by,
  invited_at,
  joined_at,
  last_active_at
)
SELECT
  rp.org_id,
  rp.id,
  CASE
    WHEN rp.rn = 1 THEN 'owner'::public.org_member_role
    WHEN rp.role = 'manager' THEN 'manager'::public.org_member_role
    ELSE 'sales'::public.org_member_role
  END,
  'active'::public.org_seat_status,
  NULL,
  rp.created_at,
  rp.created_at,
  timezone('utc', now())
FROM ranked_profiles rp
ON CONFLICT (org_id, user_id) DO NOTHING;

INSERT INTO public.org_ai_settings (
  org_id,
  provider,
  model_default,
  model_reasoning,
  fallback_mode,
  auto_analysis_enabled,
  auto_plan_enabled,
  auto_brief_enabled,
  auto_touchpoint_review_enabled,
  human_review_required_for_sensitive_actions,
  max_daily_ai_runs,
  max_monthly_ai_runs
)
SELECT
  o.id,
  'deepseek'::public.ai_provider,
  'deepseek-chat',
  'deepseek-reasoner',
  'provider_then_rules'::public.org_ai_fallback_mode,
  true,
  true,
  true,
  true,
  true,
  300,
  5000
FROM public.organizations o
ON CONFLICT (org_id) DO NOTHING;

INSERT INTO public.org_plan_profiles (
  org_id,
  plan_tier,
  seat_limit,
  ai_run_limit_monthly,
  document_limit_monthly,
  touchpoint_limit_monthly,
  advanced_features_enabled,
  expires_at,
  status
)
SELECT
  o.id,
  CASE WHEN o.slug ILIKE '%demo%' THEN 'demo'::public.org_plan_tier ELSE 'trial'::public.org_plan_tier END,
  CASE WHEN o.slug ILIKE '%demo%' THEN 10 ELSE 8 END,
  CASE WHEN o.slug ILIKE '%demo%' THEN 800 ELSE 1500 END,
  CASE WHEN o.slug ILIKE '%demo%' THEN 300 ELSE 500 END,
  CASE WHEN o.slug ILIKE '%demo%' THEN 1200 ELSE 2500 END,
  false,
  timezone('utc', now()) + interval '30 days',
  'active'::public.org_plan_status
FROM public.organizations o
ON CONFLICT (org_id) DO NOTHING;

INSERT INTO public.org_feature_flags (org_id, feature_key, is_enabled, config_json)
SELECT o.id, f.feature_key, true, '{}'::jsonb
FROM public.organizations o
CROSS JOIN (
  VALUES
    ('ai_auto_analysis'::public.org_feature_key),
    ('ai_auto_planning'::public.org_feature_key),
    ('ai_morning_brief'::public.org_feature_key),
    ('ai_deal_command'::public.org_feature_key),
    ('external_touchpoints'::public.org_feature_key),
    ('prep_cards'::public.org_feature_key),
    ('playbooks'::public.org_feature_key),
    ('manager_quality_view'::public.org_feature_key),
    ('outcome_learning'::public.org_feature_key),
    ('demo_seed_tools'::public.org_feature_key)
) f(feature_key)
ON CONFLICT (org_id, feature_key) DO NOTHING;

CREATE OR REPLACE FUNCTION public.current_user_membership_role()
RETURNS public.org_member_role
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT coalesce(
    (
      SELECT m.role
      FROM public.org_memberships m
      WHERE m.org_id = public.current_user_org_id()
        AND m.user_id = auth.uid()
        AND m.seat_status = 'active'
      LIMIT 1
    ),
    CASE WHEN public.current_user_role() = 'manager' THEN 'manager'::public.org_member_role ELSE 'sales'::public.org_member_role END
  )
$$;

CREATE OR REPLACE FUNCTION public.is_current_user_org_owner_or_admin()
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT coalesce(public.current_user_membership_role() IN ('owner', 'admin'), false)
$$;

CREATE OR REPLACE FUNCTION public.can_current_user_view_org_usage()
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT coalesce(public.current_user_membership_role() IN ('owner', 'admin', 'manager'), false)
$$;

ALTER TABLE public.org_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.org_feature_flags ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.org_ai_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.org_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.org_invites ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.org_usage_counters ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_usage_counters ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.org_plan_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.onboarding_runs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS org_settings_select_policy ON public.org_settings;
CREATE POLICY org_settings_select_policy
ON public.org_settings
FOR SELECT
USING (
  org_id = public.current_user_org_id()
  AND public.can_current_user_view_org_usage()
);

DROP POLICY IF EXISTS org_settings_write_policy ON public.org_settings;
CREATE POLICY org_settings_write_policy
ON public.org_settings
FOR ALL
USING (
  org_id = public.current_user_org_id()
  AND public.is_current_user_org_owner_or_admin()
)
WITH CHECK (
  org_id = public.current_user_org_id()
  AND public.is_current_user_org_owner_or_admin()
);

DROP POLICY IF EXISTS org_feature_flags_select_policy ON public.org_feature_flags;
CREATE POLICY org_feature_flags_select_policy
ON public.org_feature_flags
FOR SELECT
USING (
  org_id = public.current_user_org_id()
  AND public.can_current_user_view_org_usage()
);

DROP POLICY IF EXISTS org_feature_flags_write_policy ON public.org_feature_flags;
CREATE POLICY org_feature_flags_write_policy
ON public.org_feature_flags
FOR ALL
USING (
  org_id = public.current_user_org_id()
  AND public.is_current_user_org_owner_or_admin()
)
WITH CHECK (
  org_id = public.current_user_org_id()
  AND public.is_current_user_org_owner_or_admin()
);

DROP POLICY IF EXISTS org_ai_settings_select_policy ON public.org_ai_settings;
CREATE POLICY org_ai_settings_select_policy
ON public.org_ai_settings
FOR SELECT
USING (
  org_id = public.current_user_org_id()
  AND public.can_current_user_view_org_usage()
);

DROP POLICY IF EXISTS org_ai_settings_write_policy ON public.org_ai_settings;
CREATE POLICY org_ai_settings_write_policy
ON public.org_ai_settings
FOR ALL
USING (
  org_id = public.current_user_org_id()
  AND public.is_current_user_org_owner_or_admin()
)
WITH CHECK (
  org_id = public.current_user_org_id()
  AND public.is_current_user_org_owner_or_admin()
);

DROP POLICY IF EXISTS org_memberships_select_policy ON public.org_memberships;
CREATE POLICY org_memberships_select_policy
ON public.org_memberships
FOR SELECT
USING (
  org_id = public.current_user_org_id()
  AND (
    public.can_current_user_view_org_usage()
    OR user_id = auth.uid()
  )
);

DROP POLICY IF EXISTS org_memberships_write_policy ON public.org_memberships;
CREATE POLICY org_memberships_write_policy
ON public.org_memberships
FOR ALL
USING (
  org_id = public.current_user_org_id()
  AND public.is_current_user_org_owner_or_admin()
)
WITH CHECK (
  org_id = public.current_user_org_id()
  AND public.is_current_user_org_owner_or_admin()
);

DROP POLICY IF EXISTS org_invites_select_policy ON public.org_invites;
CREATE POLICY org_invites_select_policy
ON public.org_invites
FOR SELECT
USING (
  org_id = public.current_user_org_id()
  AND public.is_current_user_org_owner_or_admin()
);

DROP POLICY IF EXISTS org_invites_write_policy ON public.org_invites;
CREATE POLICY org_invites_write_policy
ON public.org_invites
FOR ALL
USING (
  org_id = public.current_user_org_id()
  AND public.is_current_user_org_owner_or_admin()
)
WITH CHECK (
  org_id = public.current_user_org_id()
  AND public.is_current_user_org_owner_or_admin()
);

DROP POLICY IF EXISTS org_usage_counters_select_policy ON public.org_usage_counters;
CREATE POLICY org_usage_counters_select_policy
ON public.org_usage_counters
FOR SELECT
USING (
  org_id = public.current_user_org_id()
  AND public.can_current_user_view_org_usage()
);

DROP POLICY IF EXISTS org_usage_counters_write_policy ON public.org_usage_counters;
CREATE POLICY org_usage_counters_write_policy
ON public.org_usage_counters
FOR ALL
USING (
  org_id = public.current_user_org_id()
  AND public.is_current_user_org_owner_or_admin()
)
WITH CHECK (
  org_id = public.current_user_org_id()
  AND public.is_current_user_org_owner_or_admin()
);

DROP POLICY IF EXISTS user_usage_counters_select_policy ON public.user_usage_counters;
CREATE POLICY user_usage_counters_select_policy
ON public.user_usage_counters
FOR SELECT
USING (
  org_id = public.current_user_org_id()
  AND (
    public.can_current_user_view_org_usage()
    OR user_id = auth.uid()
  )
);

DROP POLICY IF EXISTS user_usage_counters_write_policy ON public.user_usage_counters;
CREATE POLICY user_usage_counters_write_policy
ON public.user_usage_counters
FOR ALL
USING (
  org_id = public.current_user_org_id()
  AND public.is_current_user_org_owner_or_admin()
)
WITH CHECK (
  org_id = public.current_user_org_id()
  AND public.is_current_user_org_owner_or_admin()
);

DROP POLICY IF EXISTS org_plan_profiles_select_policy ON public.org_plan_profiles;
CREATE POLICY org_plan_profiles_select_policy
ON public.org_plan_profiles
FOR SELECT
USING (
  org_id = public.current_user_org_id()
  AND public.can_current_user_view_org_usage()
);

DROP POLICY IF EXISTS org_plan_profiles_write_policy ON public.org_plan_profiles;
CREATE POLICY org_plan_profiles_write_policy
ON public.org_plan_profiles
FOR ALL
USING (
  org_id = public.current_user_org_id()
  AND public.is_current_user_org_owner_or_admin()
)
WITH CHECK (
  org_id = public.current_user_org_id()
  AND public.is_current_user_org_owner_or_admin()
);

DROP POLICY IF EXISTS onboarding_runs_select_policy ON public.onboarding_runs;
CREATE POLICY onboarding_runs_select_policy
ON public.onboarding_runs
FOR SELECT
USING (
  org_id = public.current_user_org_id()
  AND public.can_current_user_view_org_usage()
);

DROP POLICY IF EXISTS onboarding_runs_write_policy ON public.onboarding_runs;
CREATE POLICY onboarding_runs_write_policy
ON public.onboarding_runs
FOR ALL
USING (
  org_id = public.current_user_org_id()
  AND public.is_current_user_org_owner_or_admin()
)
WITH CHECK (
  org_id = public.current_user_org_id()
  AND public.is_current_user_org_owner_or_admin()
);

INSERT INTO public.ai_prompt_versions (
  org_id,
  name,
  version,
  scenario,
  provider_scope,
  system_prompt,
  developer_prompt,
  output_schema,
  is_active
)
SELECT
  o.id,
  'Onboarding Recommendation (DeepSeek)',
  'v11-deepseek',
  'onboarding_recommendation'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Help organizations finish onboarding with minimal effort and concrete actions.',
  'Use only provided org setup facts. Return strict JSON. Do not suggest actions beyond current permissions or disabled features.',
  '{"type":"object","required":["next_best_setup_steps","missing_foundations","recommended_demo_flow","recommended_team_actions","risks_if_skipped"]}'::jsonb,
  true
FROM public.organizations o
ON CONFLICT (org_id, scenario, version) DO NOTHING;

INSERT INTO public.ai_prompt_versions (
  org_id,
  name,
  version,
  scenario,
  provider_scope,
  system_prompt,
  developer_prompt,
  output_schema,
  is_active
)
SELECT
  o.id,
  'Usage Health Summary (DeepSeek)',
  'v11-deepseek',
  'usage_health_summary'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Summarize usage health and quota risks for B2B admin users.',
  'Use only provided usage metrics and feature flags. Return strict JSON. Keep recommendations actionable and conservative.',
  '{"type":"object","required":["usage_summary","hot_features","underused_features","quota_risks","recommended_adjustments"]}'::jsonb,
  true
FROM public.organizations o
ON CONFLICT (org_id, scenario, version) DO NOTHING;
