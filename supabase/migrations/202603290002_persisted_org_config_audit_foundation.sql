-- MOY phase-17: persisted org config audit + version snapshot foundation

CREATE TABLE IF NOT EXISTS public.org_config_audit_logs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
  actor_user_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE RESTRICT,
  target_type text NOT NULL,
  target_id text,
  target_key text,
  action_type text NOT NULL,
  before_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
  after_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
  diagnostics_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
  version_number bigint NOT NULL CHECK (version_number > 0),
  version_label text NOT NULL,
  snapshot_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT timezone('utc', now())
);

CREATE INDEX IF NOT EXISTS idx_org_config_audit_logs_org_created_at
  ON public.org_config_audit_logs (org_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_org_config_audit_logs_org_target_created_at
  ON public.org_config_audit_logs (org_id, target_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_org_config_audit_logs_org_target_key_version
  ON public.org_config_audit_logs (org_id, target_type, target_key, version_number DESC);

ALTER TABLE public.org_config_audit_logs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS org_config_audit_logs_select_policy ON public.org_config_audit_logs;
CREATE POLICY org_config_audit_logs_select_policy
ON public.org_config_audit_logs
FOR SELECT
USING (
  org_id = public.current_user_org_id()
  AND public.can_current_user_view_org_usage()
);

DROP POLICY IF EXISTS org_config_audit_logs_write_policy ON public.org_config_audit_logs;
CREATE POLICY org_config_audit_logs_write_policy
ON public.org_config_audit_logs
FOR ALL
USING (
  org_id = public.current_user_org_id()
  AND public.is_current_user_org_owner_or_admin()
)
WITH CHECK (
  org_id = public.current_user_org_id()
  AND public.is_current_user_org_owner_or_admin()
);
