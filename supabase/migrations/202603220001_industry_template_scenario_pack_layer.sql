-- Phase 14: Industry Template & Scenario Pack Layer

-- ---------- Enums ----------
do $$
begin
  if not exists (select 1 from pg_type where typname = 'industry_family') then
    create type public.industry_family as enum (
      'generic',
      'b2b_software',
      'education_training',
      'manufacturing',
      'channel_sales',
      'consulting_services'
    );
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'template_status') then
    create type public.template_status as enum ('active', 'draft', 'archived');
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'org_template_assignment_status') then
    create type public.org_template_assignment_status as enum ('active', 'pending_preview', 'archived');
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'template_apply_mode') then
    create type public.template_apply_mode as enum ('onboarding_default', 'demo_seed', 'manual_apply', 'trial_bootstrap');
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'scenario_pack_type') then
    create type public.scenario_pack_type as enum (
      'objections',
      'decision_chain',
      'quote_strategy',
      'meeting_goals',
      'risk_signals',
      'manager_interventions',
      'followup_patterns'
    );
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'org_template_override_type') then
    create type public.org_template_override_type as enum (
      'customer_stages',
      'opportunity_stages',
      'alert_rules',
      'checkpoints',
      'playbook_seed',
      'prep_preferences',
      'brief_preferences',
      'demo_seed_profile'
    );
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'template_application_run_type') then
    create type public.template_application_run_type as enum ('preview', 'apply', 'reapply', 'demo_seed_apply');
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'template_application_run_status') then
    create type public.template_application_run_status as enum ('queued', 'running', 'completed', 'failed');
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'template_apply_strategy') then
    create type public.template_apply_strategy as enum ('additive_only', 'merge_prefer_existing', 'template_override_existing');
  end if;
end $$;

-- ---------- AI scenarios extension ----------
do $$
begin
  if exists (select 1 from pg_type where typname = 'ai_scenario') then
    begin
      alter type public.ai_scenario add value 'template_fit_recommendation';
    exception when duplicate_object then null;
    end;
    begin
      alter type public.ai_scenario add value 'template_application_summary';
    exception when duplicate_object then null;
    end;
    begin
      alter type public.ai_scenario add value 'industry_seed_customization';
    exception when duplicate_object then null;
    end;
  end if;
end $$;

-- ---------- Tables ----------
create table if not exists public.industry_templates (
  id uuid primary key default gen_random_uuid(),
  template_key text not null unique,
  display_name text not null,
  industry_family public.industry_family not null,
  status public.template_status not null default 'active',
  summary text not null default '',
  template_payload jsonb not null default '{}'::jsonb,
  is_system_template boolean not null default true,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.org_template_assignments (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  template_id uuid not null references public.industry_templates(id) on delete restrict,
  assignment_status public.org_template_assignment_status not null default 'active',
  apply_mode public.template_apply_mode not null default 'manual_apply',
  apply_strategy public.template_apply_strategy not null default 'merge_prefer_existing',
  applied_by uuid not null references public.profiles(id) on delete restrict,
  applied_at timestamptz not null default timezone('utc', now()),
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.scenario_packs (
  id uuid primary key default gen_random_uuid(),
  template_id uuid not null references public.industry_templates(id) on delete cascade,
  pack_type public.scenario_pack_type not null,
  title text not null,
  summary text not null default '',
  pack_payload jsonb not null default '{}'::jsonb,
  status public.template_status not null default 'active',
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.org_template_overrides (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  template_id uuid not null references public.industry_templates(id) on delete restrict,
  override_type public.org_template_override_type not null,
  override_payload jsonb not null default '{}'::jsonb,
  created_by uuid not null references public.profiles(id) on delete restrict,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.template_application_runs (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  template_id uuid not null references public.industry_templates(id) on delete restrict,
  initiated_by uuid not null references public.profiles(id) on delete restrict,
  run_type public.template_application_run_type not null,
  apply_mode public.template_apply_mode not null default 'manual_apply',
  apply_strategy public.template_apply_strategy not null default 'merge_prefer_existing',
  status public.template_application_run_status not null default 'queued',
  summary text not null default '',
  diff_snapshot jsonb not null default '{}'::jsonb,
  result_snapshot jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.seeded_playbook_templates (
  id uuid primary key default gen_random_uuid(),
  template_id uuid not null references public.industry_templates(id) on delete cascade,
  playbook_type public.playbook_type not null,
  title text not null,
  summary text not null default '',
  payload jsonb not null default '{}'::jsonb,
  status public.template_status not null default 'active',
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

-- Unique constraints required by upsert/update flows
do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'uq_org_template_overrides_org_template_type') then
    alter table public.org_template_overrides
      add constraint uq_org_template_overrides_org_template_type
      unique (org_id, template_id, override_type);
  end if;

  if not exists (select 1 from pg_constraint where conname = 'uq_scenario_packs_template_pack_type_title') then
    alter table public.scenario_packs
      add constraint uq_scenario_packs_template_pack_type_title
      unique (template_id, pack_type, title);
  end if;

  if not exists (select 1 from pg_constraint where conname = 'uq_seeded_playbook_templates_template_title') then
    alter table public.seeded_playbook_templates
      add constraint uq_seeded_playbook_templates_template_title
      unique (template_id, title);
  end if;
end $$;

-- ---------- Indexes ----------
create index if not exists idx_industry_templates_template_key on public.industry_templates(template_key);
create index if not exists idx_industry_templates_family on public.industry_templates(industry_family);
create index if not exists idx_industry_templates_status on public.industry_templates(status);
create index if not exists idx_industry_templates_created_at on public.industry_templates(created_at desc);

create index if not exists idx_org_template_assignments_org_id on public.org_template_assignments(org_id);
create index if not exists idx_org_template_assignments_template_id on public.org_template_assignments(template_id);
create index if not exists idx_org_template_assignments_status on public.org_template_assignments(assignment_status);
create index if not exists idx_org_template_assignments_created_at on public.org_template_assignments(created_at desc);

create index if not exists idx_scenario_packs_template_id on public.scenario_packs(template_id);
create index if not exists idx_scenario_packs_pack_type on public.scenario_packs(pack_type);
create index if not exists idx_scenario_packs_status on public.scenario_packs(status);
create index if not exists idx_scenario_packs_created_at on public.scenario_packs(created_at desc);

create index if not exists idx_org_template_overrides_org_id on public.org_template_overrides(org_id);
create index if not exists idx_org_template_overrides_template_id on public.org_template_overrides(template_id);
create index if not exists idx_org_template_overrides_type on public.org_template_overrides(override_type);
create index if not exists idx_org_template_overrides_created_at on public.org_template_overrides(created_at desc);

create index if not exists idx_template_application_runs_org_id on public.template_application_runs(org_id);
create index if not exists idx_template_application_runs_template_id on public.template_application_runs(template_id);
create index if not exists idx_template_application_runs_status on public.template_application_runs(status);
create index if not exists idx_template_application_runs_created_at on public.template_application_runs(created_at desc);

create index if not exists idx_seeded_playbook_templates_template_id on public.seeded_playbook_templates(template_id);
create index if not exists idx_seeded_playbook_templates_type on public.seeded_playbook_templates(playbook_type);
create index if not exists idx_seeded_playbook_templates_status on public.seeded_playbook_templates(status);
create index if not exists idx_seeded_playbook_templates_created_at on public.seeded_playbook_templates(created_at desc);

-- ---------- updated_at triggers ----------
do $$
begin
  if exists (select 1 from pg_proc where proname = 'set_updated_at') then
    drop trigger if exists trg_industry_templates_updated_at on public.industry_templates;
    create trigger trg_industry_templates_updated_at
    before update on public.industry_templates
    for each row execute function public.set_updated_at();

    drop trigger if exists trg_org_template_assignments_updated_at on public.org_template_assignments;
    create trigger trg_org_template_assignments_updated_at
    before update on public.org_template_assignments
    for each row execute function public.set_updated_at();

    drop trigger if exists trg_scenario_packs_updated_at on public.scenario_packs;
    create trigger trg_scenario_packs_updated_at
    before update on public.scenario_packs
    for each row execute function public.set_updated_at();

    drop trigger if exists trg_org_template_overrides_updated_at on public.org_template_overrides;
    create trigger trg_org_template_overrides_updated_at
    before update on public.org_template_overrides
    for each row execute function public.set_updated_at();

    drop trigger if exists trg_template_application_runs_updated_at on public.template_application_runs;
    create trigger trg_template_application_runs_updated_at
    before update on public.template_application_runs
    for each row execute function public.set_updated_at();

    drop trigger if exists trg_seeded_playbook_templates_updated_at on public.seeded_playbook_templates;
    create trigger trg_seeded_playbook_templates_updated_at
    before update on public.seeded_playbook_templates
    for each row execute function public.set_updated_at();
  end if;
end $$;

-- ---------- RLS ----------
alter table public.industry_templates enable row level security;
alter table public.org_template_assignments enable row level security;
alter table public.scenario_packs enable row level security;
alter table public.org_template_overrides enable row level security;
alter table public.template_application_runs enable row level security;
alter table public.seeded_playbook_templates enable row level security;

drop policy if exists industry_templates_select_policy on public.industry_templates;
create policy industry_templates_select_policy
on public.industry_templates
for select
using (auth.uid() is not null);

drop policy if exists scenario_packs_select_policy on public.scenario_packs;
create policy scenario_packs_select_policy
on public.scenario_packs
for select
using (auth.uid() is not null);

drop policy if exists seeded_playbook_templates_select_policy on public.seeded_playbook_templates;
create policy seeded_playbook_templates_select_policy
on public.seeded_playbook_templates
for select
using (auth.uid() is not null);

drop policy if exists org_template_assignments_select_policy on public.org_template_assignments;
create policy org_template_assignments_select_policy
on public.org_template_assignments
for select
using (
  org_id = public.current_user_org_id()
  and public.can_current_user_view_org_usage()
);

drop policy if exists org_template_assignments_write_policy on public.org_template_assignments;
create policy org_template_assignments_write_policy
on public.org_template_assignments
for all
using (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
)
with check (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
);

drop policy if exists org_template_overrides_select_policy on public.org_template_overrides;
create policy org_template_overrides_select_policy
on public.org_template_overrides
for select
using (
  org_id = public.current_user_org_id()
  and public.can_current_user_view_org_usage()
);

drop policy if exists org_template_overrides_write_policy on public.org_template_overrides;
create policy org_template_overrides_write_policy
on public.org_template_overrides
for all
using (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
)
with check (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
);

drop policy if exists template_application_runs_select_policy on public.template_application_runs;
create policy template_application_runs_select_policy
on public.template_application_runs
for select
using (
  org_id = public.current_user_org_id()
  and public.can_current_user_view_org_usage()
);

drop policy if exists template_application_runs_write_policy on public.template_application_runs;
create policy template_application_runs_write_policy
on public.template_application_runs
for all
using (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
)
with check (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
);

-- ---------- Seed templates ----------
insert into public.industry_templates (
  template_key,
  display_name,
  industry_family,
  status,
  summary,
  template_payload,
  is_system_template
)
values
  (
    'generic',
    'Generic B2B',
    'generic',
    'active',
    'Balanced default template for SMB B2B selling motions.',
    '{
      "customer_stages": ["lead", "initial_contact", "needs_confirmed", "proposal", "negotiation", "won", "lost"],
      "opportunity_stages": ["discovery", "qualification", "proposal", "business_review", "negotiation", "won", "lost"],
      "default_alert_rules": {"no_followup_timeout": 7, "quoted_but_stalled": 10, "high_probability_stalled": 5},
      "suggested_checkpoints": ["need_confirmed", "quote_sent", "decision_maker_confirmed", "closing"],
      "manager_attention_signals": ["high_risk_customer", "overdue_followup", "stalled_quote"],
      "prep_preferences": ["value-first opening", "clear next step owner", "2-3 day followup rhythm"],
      "brief_preferences": ["today priorities", "high-risk customers", "drafts pending confirmation"],
      "recommended_onboarding_path": ["configure org profile", "import first customers", "generate first today plan", "create first deal room"],
      "demo_seed_profile": "generic_demo"
    }'::jsonb,
    true
  ),
  (
    'b2b_software',
    'B2B Software',
    'b2b_software',
    'active',
    'Trial and procurement driven software sales template.',
    '{"demo_seed_profile":"software_demo"}'::jsonb,
    true
  ),
  (
    'education_training',
    'Education & Training',
    'education_training',
    'active',
    'Fast conversion template for trial-class and enrollment motions.',
    '{"demo_seed_profile":"education_demo"}'::jsonb,
    true
  ),
  (
    'manufacturing',
    'Manufacturing / Industrial',
    'manufacturing',
    'active',
    'Long-cycle manufacturing template with technical and procurement checkpoints.',
    '{"demo_seed_profile":"manufacturing_demo"}'::jsonb,
    true
  ),
  (
    'channel_sales',
    'Channel / Distribution',
    'channel_sales',
    'active',
    'Channel recruitment template with policy and region governance.',
    '{"demo_seed_profile":"channel_demo"}'::jsonb,
    true
  ),
  (
    'consulting_services',
    'Consulting / Services',
    'consulting_services',
    'active',
    'Consulting template with diagnosis-first and boundary alignment discipline.',
    '{"demo_seed_profile":"consulting_demo"}'::jsonb,
    true
  )
on conflict (template_key) do update
set
  display_name = excluded.display_name,
  industry_family = excluded.industry_family,
  status = excluded.status,
  summary = excluded.summary,
  template_payload = excluded.template_payload,
  is_system_template = excluded.is_system_template,
  updated_at = timezone('utc', now());
