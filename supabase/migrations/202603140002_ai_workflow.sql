-- MOY phase-3 AI workflow schema
-- Covers: AI audit tables, prompt versioning, alert scan runs, and alert enrichment.

create extension if not exists pgcrypto;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'ai_run_status') then
    create type public.ai_run_status as enum ('queued', 'running', 'completed', 'failed');
  end if;

  if not exists (select 1 from pg_type where typname = 'ai_trigger_source') then
    create type public.ai_trigger_source as enum ('manual', 'followup_submit', 'nightly_scan', 'alert_regen', 'manager_review');
  end if;

  if not exists (select 1 from pg_type where typname = 'ai_scenario') then
    create type public.ai_scenario as enum ('followup_analysis', 'customer_health', 'leak_risk');
  end if;

  if not exists (select 1 from pg_type where typname = 'alert_source') then
    create type public.alert_source as enum ('rule', 'ai', 'hybrid');
  end if;

  if not exists (select 1 from pg_type where typname = 'alert_rule_run_status') then
    create type public.alert_rule_run_status as enum ('running', 'completed', 'failed');
  end if;

  if not exists (select 1 from pg_type where typname = 'ai_feedback_rating') then
    create type public.ai_feedback_rating as enum ('helpful', 'not_helpful', 'partially_helpful');
  end if;
end $$;

do $$
begin
  if not exists (
    select 1 from pg_enum
    where enumlabel = 'no_followup_timeout'
      and enumtypid = 'public.alert_rule_type'::regtype
  ) then
    alter type public.alert_rule_type add value 'no_followup_timeout';
  end if;

  if not exists (
    select 1 from pg_enum
    where enumlabel = 'positive_reply_but_no_progress'
      and enumtypid = 'public.alert_rule_type'::regtype
  ) then
    alter type public.alert_rule_type add value 'positive_reply_but_no_progress';
  end if;

  if not exists (
    select 1 from pg_enum
    where enumlabel = 'no_decision_maker'
      and enumtypid = 'public.alert_rule_type'::regtype
  ) then
    alter type public.alert_rule_type add value 'no_decision_maker';
  end if;

  if not exists (
    select 1 from pg_enum
    where enumlabel = 'ai_detected'
      and enumtypid = 'public.alert_rule_type'::regtype
  ) then
    alter type public.alert_rule_type add value 'ai_detected';
  end if;
end $$;

create table if not exists public.ai_prompt_versions (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  name text not null,
  version text not null,
  scenario public.ai_scenario not null,
  system_prompt text not null,
  developer_prompt text not null,
  output_schema jsonb not null default '{}'::jsonb,
  is_active boolean not null default true,
  created_at timestamptz not null default timezone('utc', now()),
  unique (org_id, scenario, version)
);

create table if not exists public.ai_runs (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  customer_id uuid not null references public.customers(id) on delete cascade,
  followup_id uuid references public.followups(id) on delete set null,
  triggered_by_user_id uuid references public.profiles(id) on delete set null,
  trigger_source public.ai_trigger_source not null default 'manual',
  scenario public.ai_scenario not null,
  model text not null,
  prompt_version text not null,
  status public.ai_run_status not null default 'queued',
  input_snapshot jsonb,
  output_snapshot jsonb,
  parsed_result jsonb,
  error_message text,
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.alert_rule_runs (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  rule_name text not null,
  status public.alert_rule_run_status not null default 'running',
  scanned_count int not null default 0,
  created_alert_count int not null default 0,
  deduped_alert_count int not null default 0,
  resolved_alert_count int not null default 0,
  started_at timestamptz not null default timezone('utc', now()),
  completed_at timestamptz,
  error_message text,
  created_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.ai_feedback (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  ai_run_id uuid not null references public.ai_runs(id) on delete cascade,
  user_id uuid not null references public.profiles(id) on delete cascade,
  rating public.ai_feedback_rating not null,
  feedback_text text,
  created_at timestamptz not null default timezone('utc', now())
);

alter table public.alerts
  add column if not exists source public.alert_source not null default 'rule',
  add column if not exists evidence jsonb not null default '[]'::jsonb,
  add column if not exists suggested_owner_action text[] not null default '{}'::text[],
  add column if not exists ai_run_id uuid references public.ai_runs(id) on delete set null,
  add column if not exists last_triggered_at timestamptz not null default timezone('utc', now());

create index if not exists idx_ai_prompt_versions_org_scenario
  on public.ai_prompt_versions(org_id, scenario, is_active, created_at desc);

create index if not exists idx_ai_runs_org_id on public.ai_runs(org_id);
create index if not exists idx_ai_runs_customer_id on public.ai_runs(customer_id);
create index if not exists idx_ai_runs_followup_id on public.ai_runs(followup_id);
create index if not exists idx_ai_runs_status on public.ai_runs(status);
create index if not exists idx_ai_runs_trigger_source on public.ai_runs(trigger_source);
create index if not exists idx_ai_runs_created_at on public.ai_runs(created_at desc);

create index if not exists idx_alert_rule_runs_org_id on public.alert_rule_runs(org_id);
create index if not exists idx_alert_rule_runs_created_at on public.alert_rule_runs(created_at desc);
create index if not exists idx_alert_rule_runs_status on public.alert_rule_runs(status);

create index if not exists idx_ai_feedback_org_id on public.ai_feedback(org_id);
create index if not exists idx_ai_feedback_ai_run_id on public.ai_feedback(ai_run_id);
create index if not exists idx_ai_feedback_user_id on public.ai_feedback(user_id);
create index if not exists idx_ai_feedback_created_at on public.ai_feedback(created_at desc);

create index if not exists idx_alerts_source on public.alerts(source);
create index if not exists idx_alerts_rule_type_status on public.alerts(rule_type, status);
create index if not exists idx_alerts_ai_run_id on public.alerts(ai_run_id);
create index if not exists idx_alerts_last_triggered_at on public.alerts(last_triggered_at desc);

alter table public.ai_prompt_versions enable row level security;
alter table public.ai_runs enable row level security;
alter table public.alert_rule_runs enable row level security;
alter table public.ai_feedback enable row level security;

drop policy if exists ai_prompt_versions_select_policy on public.ai_prompt_versions;
create policy ai_prompt_versions_select_policy
on public.ai_prompt_versions
for select
using (
  org_id = public.current_user_org_id()
);

drop policy if exists ai_prompt_versions_manage_policy on public.ai_prompt_versions;
create policy ai_prompt_versions_manage_policy
on public.ai_prompt_versions
for all
using (
  org_id = public.current_user_org_id()
  and public.is_current_user_manager()
)
with check (
  org_id = public.current_user_org_id()
  and public.is_current_user_manager()
);

drop policy if exists ai_runs_select_policy on public.ai_runs;
create policy ai_runs_select_policy
on public.ai_runs
for select
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or triggered_by_user_id = auth.uid()
    or exists (
      select 1
      from public.customers c
      where c.id = ai_runs.customer_id
        and c.owner_id = auth.uid()
        and c.org_id = public.current_user_org_id()
    )
  )
);

drop policy if exists ai_runs_insert_policy on public.ai_runs;
create policy ai_runs_insert_policy
on public.ai_runs
for insert
with check (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or triggered_by_user_id = auth.uid()
    or exists (
      select 1
      from public.customers c
      where c.id = ai_runs.customer_id
        and c.owner_id = auth.uid()
        and c.org_id = public.current_user_org_id()
    )
  )
);

drop policy if exists ai_runs_update_policy on public.ai_runs;
create policy ai_runs_update_policy
on public.ai_runs
for update
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or triggered_by_user_id = auth.uid()
  )
)
with check (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or triggered_by_user_id = auth.uid()
  )
);

drop policy if exists alert_rule_runs_select_policy on public.alert_rule_runs;
create policy alert_rule_runs_select_policy
on public.alert_rule_runs
for select
using (
  org_id = public.current_user_org_id()
  and public.is_current_user_manager()
);

drop policy if exists alert_rule_runs_manage_policy on public.alert_rule_runs;
create policy alert_rule_runs_manage_policy
on public.alert_rule_runs
for all
using (
  org_id = public.current_user_org_id()
  and public.is_current_user_manager()
)
with check (
  org_id = public.current_user_org_id()
  and public.is_current_user_manager()
);

drop policy if exists ai_feedback_select_policy on public.ai_feedback;
create policy ai_feedback_select_policy
on public.ai_feedback
for select
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or user_id = auth.uid()
  )
);

drop policy if exists ai_feedback_insert_policy on public.ai_feedback;
create policy ai_feedback_insert_policy
on public.ai_feedback
for insert
with check (
  org_id = public.current_user_org_id()
  and user_id = auth.uid()
);

-- Seed minimal prompt versions for every existing org (idempotent)
insert into public.ai_prompt_versions (
  org_id,
  name,
  version,
  scenario,
  system_prompt,
  developer_prompt,
  output_schema,
  is_active
)
select
  o.id as org_id,
  'Followup Analysis' as name,
  'v1' as version,
  'followup_analysis'::public.ai_scenario as scenario,
  'You are MOY AI analyst for B2B sales followups.' as system_prompt,
  'Return strict JSON only. Use only provided facts. If uncertain, say so in reasoning_brief.' as developer_prompt,
  '{"type":"object","required":["customer_status_summary","key_needs","key_objections","buying_signals","risk_level","leak_risk","leak_reasons","next_best_actions","recommended_next_followup_at","manager_attention_needed","confidence_score","reasoning_brief"]}'::jsonb as output_schema,
  true as is_active
from public.organizations o
on conflict (org_id, scenario, version) do nothing;

insert into public.ai_prompt_versions (
  org_id,
  name,
  version,
  scenario,
  system_prompt,
  developer_prompt,
  output_schema,
  is_active
)
select
  o.id as org_id,
  'Customer Health' as name,
  'v1' as version,
  'customer_health'::public.ai_scenario as scenario,
  'You are MOY AI analyst for customer health review.' as system_prompt,
  'Return strict JSON only. Base analysis on records and progression evidence.' as developer_prompt,
  '{"type":"object","required":["stage_fit_assessment","momentum_score","relationship_score","decision_clarity_score","budget_clarity_score","timeline_clarity_score","overall_risk_level","stall_signals","suggested_strategy","summary"]}'::jsonb as output_schema,
  true as is_active
from public.organizations o
on conflict (org_id, scenario, version) do nothing;

insert into public.ai_prompt_versions (
  org_id,
  name,
  version,
  scenario,
  system_prompt,
  developer_prompt,
  output_schema,
  is_active
)
select
  o.id as org_id,
  'Leak Risk Inference' as name,
  'v1' as version,
  'leak_risk'::public.ai_scenario as scenario,
  'You are MOY AI risk sentinel for sales leakage alerts.' as system_prompt,
  'Return strict JSON only. Judge whether alert should be created or upgraded.' as developer_prompt,
  '{"type":"object","required":["should_create_alert","severity","primary_rule_type","title","description","evidence","suggested_owner_action","due_at"]}'::jsonb as output_schema,
  true as is_active
from public.organizations o
on conflict (org_id, scenario, version) do nothing;
