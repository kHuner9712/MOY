-- MOY phase-5: personal work memory + manager operating quality

do $$
begin
  if exists (select 1 from pg_type where typname = 'ai_scenario') then
    if not exists (
      select 1 from pg_enum
      where enumlabel = 'sales_memory_compile'
        and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'sales_memory_compile';
    end if;

    if not exists (
      select 1 from pg_enum
      where enumlabel = 'manager_quality_insight'
        and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'manager_quality_insight';
    end if;

    if not exists (
      select 1 from pg_enum
      where enumlabel = 'user_coaching_report'
        and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'user_coaching_report';
    end if;
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'memory_item_type') then
    create type public.memory_item_type as enum (
      'customer_preference',
      'communication_pattern',
      'objection_pattern',
      'tactic_pattern',
      'followup_rhythm',
      'risk_pattern',
      'coaching_hint'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'memory_item_status') then
    create type public.memory_item_status as enum ('active', 'hidden', 'rejected');
  end if;

  if not exists (select 1 from pg_type where typname = 'quality_period_type') then
    create type public.quality_period_type as enum ('daily', 'weekly', 'monthly');
  end if;

  if not exists (select 1 from pg_type where typname = 'coaching_report_scope') then
    create type public.coaching_report_scope as enum ('user', 'team');
  end if;

  if not exists (select 1 from pg_type where typname = 'coaching_report_status') then
    create type public.coaching_report_status as enum ('generating', 'completed', 'failed');
  end if;

  if not exists (select 1 from pg_type where typname = 'memory_feedback_type') then
    create type public.memory_feedback_type as enum ('accurate', 'inaccurate', 'outdated', 'useful', 'not_useful');
  end if;
end $$;

create table if not exists public.user_memory_profiles (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  user_id uuid not null references public.profiles(id) on delete cascade,
  memory_version text not null default 'v1',
  summary text not null default '',
  preferred_customer_types jsonb not null default '[]'::jsonb,
  preferred_communication_styles jsonb not null default '[]'::jsonb,
  common_objections jsonb not null default '[]'::jsonb,
  effective_tactics jsonb not null default '[]'::jsonb,
  common_followup_rhythm jsonb not null default '[]'::jsonb,
  quoting_style_notes jsonb not null default '[]'::jsonb,
  risk_blind_spots jsonb not null default '[]'::jsonb,
  manager_coaching_focus jsonb not null default '[]'::jsonb,
  confidence_score numeric(5,4) not null default 0.6,
  source_window_days int not null default 60,
  last_compiled_at timestamptz,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  constraint user_memory_profiles_confidence_check check (confidence_score >= 0 and confidence_score <= 1),
  constraint user_memory_profiles_unique_org_user unique (org_id, user_id)
);

create table if not exists public.user_memory_items (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  user_id uuid not null references public.profiles(id) on delete cascade,
  memory_type public.memory_item_type not null,
  title text not null,
  description text not null,
  evidence_snapshot jsonb not null default '{}'::jsonb,
  confidence_score numeric(5,4) not null default 0.6,
  source_count int not null default 0,
  status public.memory_item_status not null default 'active',
  created_by_system boolean not null default true,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  constraint user_memory_items_confidence_check check (confidence_score >= 0 and confidence_score <= 1),
  constraint user_memory_items_source_count_check check (source_count >= 0)
);

create table if not exists public.behavior_quality_snapshots (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  user_id uuid not null references public.profiles(id) on delete cascade,
  snapshot_date date not null,
  period_type public.quality_period_type not null,
  assigned_customer_count int not null default 0,
  active_customer_count int not null default 0,
  followup_count int not null default 0,
  on_time_followup_rate numeric(5,4) not null default 0,
  overdue_followup_rate numeric(5,4) not null default 0,
  followup_completeness_score numeric(5,2) not null default 0,
  stage_progression_score numeric(5,2) not null default 0,
  risk_response_score numeric(5,2) not null default 0,
  high_value_focus_score numeric(5,2) not null default 0,
  activity_quality_score numeric(5,2) not null default 0,
  shallow_activity_ratio numeric(5,4) not null default 0,
  stalled_customer_count int not null default 0,
  high_risk_unhandled_count int not null default 0,
  summary text not null default '',
  metrics_snapshot jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  constraint behavior_quality_snapshots_unique_org_user_period unique (org_id, user_id, snapshot_date, period_type)
);

create table if not exists public.coaching_reports (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  report_scope public.coaching_report_scope not null default 'user',
  target_user_id uuid references public.profiles(id) on delete set null,
  period_start date not null,
  period_end date not null,
  status public.coaching_report_status not null default 'generating',
  title text,
  executive_summary text,
  strengths jsonb not null default '[]'::jsonb,
  weaknesses jsonb not null default '[]'::jsonb,
  coaching_actions jsonb not null default '[]'::jsonb,
  replicable_patterns jsonb not null default '[]'::jsonb,
  risk_warnings jsonb not null default '[]'::jsonb,
  content_markdown text,
  source_snapshot jsonb not null default '{}'::jsonb,
  generated_by uuid not null references public.profiles(id),
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  constraint coaching_reports_period_check check (period_end >= period_start)
);

create table if not exists public.memory_feedback (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  user_id uuid not null references public.profiles(id) on delete cascade,
  memory_item_id uuid not null references public.user_memory_items(id) on delete cascade,
  feedback_type public.memory_feedback_type not null,
  feedback_text text,
  created_at timestamptz not null default timezone('utc', now())
);

create index if not exists idx_user_memory_profiles_org_id on public.user_memory_profiles(org_id);
create index if not exists idx_user_memory_profiles_user_id on public.user_memory_profiles(user_id);
create index if not exists idx_user_memory_profiles_last_compiled on public.user_memory_profiles(last_compiled_at desc);

create index if not exists idx_user_memory_items_org_id on public.user_memory_items(org_id);
create index if not exists idx_user_memory_items_user_id on public.user_memory_items(user_id);
create index if not exists idx_user_memory_items_type_status on public.user_memory_items(memory_type, status);
create index if not exists idx_user_memory_items_created_at on public.user_memory_items(created_at desc);

create index if not exists idx_behavior_quality_org_id on public.behavior_quality_snapshots(org_id);
create index if not exists idx_behavior_quality_user_id on public.behavior_quality_snapshots(user_id);
create index if not exists idx_behavior_quality_snapshot_date on public.behavior_quality_snapshots(snapshot_date desc);
create index if not exists idx_behavior_quality_period on public.behavior_quality_snapshots(period_type);
create index if not exists idx_behavior_quality_created_at on public.behavior_quality_snapshots(created_at desc);

create index if not exists idx_coaching_reports_org_id on public.coaching_reports(org_id);
create index if not exists idx_coaching_reports_target_user on public.coaching_reports(target_user_id);
create index if not exists idx_coaching_reports_status on public.coaching_reports(status);
create index if not exists idx_coaching_reports_created_at on public.coaching_reports(created_at desc);
create index if not exists idx_coaching_reports_period on public.coaching_reports(period_start, period_end);

create index if not exists idx_memory_feedback_org_id on public.memory_feedback(org_id);
create index if not exists idx_memory_feedback_user_id on public.memory_feedback(user_id);
create index if not exists idx_memory_feedback_memory_item on public.memory_feedback(memory_item_id);
create index if not exists idx_memory_feedback_created_at on public.memory_feedback(created_at desc);

alter table public.user_memory_profiles enable row level security;
alter table public.user_memory_items enable row level security;
alter table public.behavior_quality_snapshots enable row level security;
alter table public.coaching_reports enable row level security;
alter table public.memory_feedback enable row level security;

drop trigger if exists set_user_memory_profiles_updated_at on public.user_memory_profiles;
create trigger set_user_memory_profiles_updated_at
before update on public.user_memory_profiles
for each row execute procedure public.set_updated_at();

drop trigger if exists set_user_memory_items_updated_at on public.user_memory_items;
create trigger set_user_memory_items_updated_at
before update on public.user_memory_items
for each row execute procedure public.set_updated_at();

drop trigger if exists set_behavior_quality_snapshots_updated_at on public.behavior_quality_snapshots;
create trigger set_behavior_quality_snapshots_updated_at
before update on public.behavior_quality_snapshots
for each row execute procedure public.set_updated_at();

drop trigger if exists set_coaching_reports_updated_at on public.coaching_reports;
create trigger set_coaching_reports_updated_at
before update on public.coaching_reports
for each row execute procedure public.set_updated_at();

drop policy if exists user_memory_profiles_select_policy on public.user_memory_profiles;
create policy user_memory_profiles_select_policy
on public.user_memory_profiles
for select
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or user_id = auth.uid()
  )
);

drop policy if exists user_memory_profiles_insert_policy on public.user_memory_profiles;
create policy user_memory_profiles_insert_policy
on public.user_memory_profiles
for insert
with check (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or user_id = auth.uid()
  )
);

drop policy if exists user_memory_profiles_update_policy on public.user_memory_profiles;
create policy user_memory_profiles_update_policy
on public.user_memory_profiles
for update
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or user_id = auth.uid()
  )
)
with check (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or user_id = auth.uid()
  )
);

drop policy if exists user_memory_items_select_policy on public.user_memory_items;
create policy user_memory_items_select_policy
on public.user_memory_items
for select
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or user_id = auth.uid()
  )
);

drop policy if exists user_memory_items_insert_policy on public.user_memory_items;
create policy user_memory_items_insert_policy
on public.user_memory_items
for insert
with check (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or user_id = auth.uid()
  )
);

drop policy if exists user_memory_items_update_policy on public.user_memory_items;
create policy user_memory_items_update_policy
on public.user_memory_items
for update
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or user_id = auth.uid()
  )
)
with check (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or user_id = auth.uid()
  )
);

drop policy if exists behavior_quality_snapshots_select_policy on public.behavior_quality_snapshots;
create policy behavior_quality_snapshots_select_policy
on public.behavior_quality_snapshots
for select
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or user_id = auth.uid()
  )
);

drop policy if exists behavior_quality_snapshots_insert_policy on public.behavior_quality_snapshots;
create policy behavior_quality_snapshots_insert_policy
on public.behavior_quality_snapshots
for insert
with check (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or user_id = auth.uid()
  )
);

drop policy if exists behavior_quality_snapshots_update_policy on public.behavior_quality_snapshots;
create policy behavior_quality_snapshots_update_policy
on public.behavior_quality_snapshots
for update
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or user_id = auth.uid()
  )
)
with check (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or user_id = auth.uid()
  )
);

drop policy if exists coaching_reports_select_policy on public.coaching_reports;
create policy coaching_reports_select_policy
on public.coaching_reports
for select
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or target_user_id = auth.uid()
    or generated_by = auth.uid()
  )
);

drop policy if exists coaching_reports_insert_policy on public.coaching_reports;
create policy coaching_reports_insert_policy
on public.coaching_reports
for insert
with check (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or (
      report_scope = 'user'
      and target_user_id = auth.uid()
      and generated_by = auth.uid()
    )
  )
);

drop policy if exists coaching_reports_update_policy on public.coaching_reports;
create policy coaching_reports_update_policy
on public.coaching_reports
for update
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or generated_by = auth.uid()
  )
)
with check (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or generated_by = auth.uid()
  )
);

drop policy if exists memory_feedback_select_policy on public.memory_feedback;
create policy memory_feedback_select_policy
on public.memory_feedback
for select
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or user_id = auth.uid()
  )
);

drop policy if exists memory_feedback_insert_policy on public.memory_feedback;
create policy memory_feedback_insert_policy
on public.memory_feedback
for insert
with check (
  org_id = public.current_user_org_id()
  and user_id = auth.uid()
);

insert into public.ai_prompt_versions (
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
select
  o.id,
  'Sales Memory Compile (DeepSeek)',
  'v5-deepseek',
  'sales_memory_compile'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Compile business work memory for one salesperson based on factual activity data.',
  'Only use provided facts. Do not infer personality traits. Do not apply labels to personal character. Return strict JSON.',
  '{"type":"object","required":["summary","preferred_customer_types","preferred_communication_styles","common_objections","effective_tactics","common_followup_rhythm","quoting_style_notes","risk_blind_spots","manager_coaching_focus","memory_items","confidence_score"]}'::jsonb,
  true
from public.organizations o
on conflict (org_id, scenario, version) do nothing;

insert into public.ai_prompt_versions (
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
select
  o.id,
  'Manager Quality Insight (DeepSeek)',
  'v5-deepseek',
  'manager_quality_insight'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Produce manager-facing operating quality insights from sales team quality snapshots.',
  'Focus on coaching and operating rhythm, not surveillance. Avoid personal judgement. Return strict JSON.',
  '{"type":"object","required":["executive_summary","replicable_patterns","needs_coaching","management_actions","risk_warnings"]}'::jsonb,
  true
from public.organizations o
on conflict (org_id, scenario, version) do nothing;

insert into public.ai_prompt_versions (
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
select
  o.id,
  'User Coaching Report (DeepSeek)',
  'v5-deepseek',
  'user_coaching_report'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Generate coaching reports for sales execution quality.',
  'Only use supplied evidence. No personality judgement. Provide concrete coaching actions. Return strict JSON.',
  '{"type":"object","required":["title","executive_summary","strengths","weaknesses","coaching_actions","replicable_patterns","risk_warnings","content_markdown"]}'::jsonb,
  true
from public.organizations o
on conflict (org_id, scenario, version) do nothing;
