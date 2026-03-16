-- MOY phase-6: proactive work items + rhythm orchestration

do $$
begin
  if exists (select 1 from pg_type where typname = 'ai_scenario') then
    if not exists (
      select 1 from pg_enum
      where enumlabel = 'daily_work_plan_generation'
        and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'daily_work_plan_generation';
    end if;

    if not exists (
      select 1 from pg_enum
      where enumlabel = 'task_action_suggestion'
        and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'task_action_suggestion';
    end if;

    if not exists (
      select 1 from pg_enum
      where enumlabel = 'manager_team_rhythm_insight'
        and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'manager_team_rhythm_insight';
    end if;

    if not exists (
      select 1 from pg_enum
      where enumlabel = 'weekly_task_review'
        and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'weekly_task_review';
    end if;
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'work_item_source_type') then
    create type public.work_item_source_type as enum (
      'alert',
      'followup_due',
      'ai_suggested',
      'manager_assigned',
      'report_generated',
      'draft_confirmation',
      'manual'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'work_item_type') then
    create type public.work_item_type as enum (
      'followup_call',
      'send_quote',
      'confirm_decision_maker',
      'schedule_demo',
      'prepare_proposal',
      'revive_stalled_deal',
      'resolve_alert',
      'confirm_capture_draft',
      'review_customer',
      'manager_checkin'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'work_priority_band') then
    create type public.work_priority_band as enum ('low', 'medium', 'high', 'critical');
  end if;

  if not exists (select 1 from pg_type where typname = 'work_item_status') then
    create type public.work_item_status as enum ('todo', 'in_progress', 'done', 'snoozed', 'cancelled');
  end if;

  if not exists (select 1 from pg_type where typname = 'daily_plan_status') then
    create type public.daily_plan_status as enum ('draft', 'active', 'completed', 'archived');
  end if;

  if not exists (select 1 from pg_type where typname = 'plan_time_block') then
    create type public.plan_time_block as enum ('early_morning', 'morning', 'noon', 'afternoon', 'evening');
  end if;

  if not exists (select 1 from pg_type where typname = 'task_action_type') then
    create type public.task_action_type as enum (
      'created',
      'reprioritized',
      'started',
      'completed',
      'snoozed',
      'cancelled',
      'converted_to_followup',
      'converted_to_alert_resolution',
      'marked_blocked'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'work_agent_run_scope') then
    create type public.work_agent_run_scope as enum (
      'user_daily_plan',
      'manager_team_plan',
      'alert_reprioritization',
      'weekly_task_review'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'work_agent_run_status') then
    create type public.work_agent_run_status as enum ('queued', 'running', 'completed', 'failed');
  end if;
end $$;

create table if not exists public.work_items (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  owner_id uuid not null references public.profiles(id) on delete cascade,
  customer_id uuid references public.customers(id) on delete set null,
  opportunity_id uuid references public.opportunities(id) on delete set null,
  source_type public.work_item_source_type not null,
  work_type public.work_item_type not null,
  title text not null,
  description text not null default '',
  rationale text not null default '',
  priority_score numeric(6,2) not null default 50,
  priority_band public.work_priority_band not null default 'medium',
  status public.work_item_status not null default 'todo',
  scheduled_for date,
  due_at timestamptz,
  completed_at timestamptz,
  snoozed_until timestamptz,
  source_ref_type text,
  source_ref_id uuid,
  ai_generated boolean not null default false,
  ai_run_id uuid references public.ai_runs(id) on delete set null,
  created_by uuid not null references public.profiles(id),
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  constraint work_items_priority_score_check check (priority_score >= 0 and priority_score <= 100)
);

create table if not exists public.daily_work_plans (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  user_id uuid not null references public.profiles(id) on delete cascade,
  plan_date date not null,
  status public.daily_plan_status not null default 'draft',
  summary text,
  total_items int not null default 0,
  critical_items int not null default 0,
  focus_theme text,
  source_snapshot jsonb not null default '{}'::jsonb,
  generated_by uuid not null references public.profiles(id),
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  constraint daily_work_plans_unique_org_user_date unique (org_id, user_id, plan_date)
);

create table if not exists public.daily_work_plan_items (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  plan_id uuid not null references public.daily_work_plans(id) on delete cascade,
  work_item_id uuid not null references public.work_items(id) on delete cascade,
  sequence_no int not null,
  planned_time_block public.plan_time_block,
  recommendation_reason text not null default '',
  must_do boolean not null default false,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  constraint daily_work_plan_items_unique_plan_work_item unique (plan_id, work_item_id),
  constraint daily_work_plan_items_unique_plan_sequence unique (plan_id, sequence_no)
);

create table if not exists public.task_execution_logs (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  work_item_id uuid not null references public.work_items(id) on delete cascade,
  user_id uuid not null references public.profiles(id) on delete cascade,
  action_type public.task_action_type not null,
  action_note text,
  before_snapshot jsonb not null default '{}'::jsonb,
  after_snapshot jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.work_agent_runs (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  user_id uuid references public.profiles(id) on delete set null,
  run_scope public.work_agent_run_scope not null,
  status public.work_agent_run_status not null default 'queued',
  input_snapshot jsonb not null default '{}'::jsonb,
  output_snapshot jsonb not null default '{}'::jsonb,
  parsed_result jsonb not null default '{}'::jsonb,
  provider public.ai_provider,
  model text,
  result_source public.ai_result_source not null default 'provider',
  fallback_reason text,
  error_message text,
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz not null default timezone('utc', now())
);

create index if not exists idx_work_items_org_id on public.work_items(org_id);
create index if not exists idx_work_items_owner_id on public.work_items(owner_id);
create index if not exists idx_work_items_customer_id on public.work_items(customer_id);
create index if not exists idx_work_items_status on public.work_items(status);
create index if not exists idx_work_items_due_at on public.work_items(due_at);
create index if not exists idx_work_items_scheduled_for on public.work_items(scheduled_for);
create index if not exists idx_work_items_priority_band on public.work_items(priority_band);
create index if not exists idx_work_items_created_at on public.work_items(created_at desc);
create index if not exists idx_work_items_source_ref on public.work_items(source_ref_type, source_ref_id);

create index if not exists idx_daily_work_plans_org_id on public.daily_work_plans(org_id);
create index if not exists idx_daily_work_plans_user_id on public.daily_work_plans(user_id);
create index if not exists idx_daily_work_plans_plan_date on public.daily_work_plans(plan_date desc);
create index if not exists idx_daily_work_plans_status on public.daily_work_plans(status);
create index if not exists idx_daily_work_plans_created_at on public.daily_work_plans(created_at desc);

create index if not exists idx_daily_work_plan_items_org_id on public.daily_work_plan_items(org_id);
create index if not exists idx_daily_work_plan_items_plan_id on public.daily_work_plan_items(plan_id);
create index if not exists idx_daily_work_plan_items_work_item_id on public.daily_work_plan_items(work_item_id);
create index if not exists idx_daily_work_plan_items_sequence on public.daily_work_plan_items(sequence_no);

create index if not exists idx_task_execution_logs_org_id on public.task_execution_logs(org_id);
create index if not exists idx_task_execution_logs_work_item_id on public.task_execution_logs(work_item_id);
create index if not exists idx_task_execution_logs_user_id on public.task_execution_logs(user_id);
create index if not exists idx_task_execution_logs_created_at on public.task_execution_logs(created_at desc);

create index if not exists idx_work_agent_runs_org_id on public.work_agent_runs(org_id);
create index if not exists idx_work_agent_runs_user_id on public.work_agent_runs(user_id);
create index if not exists idx_work_agent_runs_scope on public.work_agent_runs(run_scope);
create index if not exists idx_work_agent_runs_status on public.work_agent_runs(status);
create index if not exists idx_work_agent_runs_created_at on public.work_agent_runs(created_at desc);

alter table public.work_items enable row level security;
alter table public.daily_work_plans enable row level security;
alter table public.daily_work_plan_items enable row level security;
alter table public.task_execution_logs enable row level security;
alter table public.work_agent_runs enable row level security;

drop trigger if exists set_work_items_updated_at on public.work_items;
create trigger set_work_items_updated_at
before update on public.work_items
for each row execute procedure public.set_updated_at();

drop trigger if exists set_daily_work_plans_updated_at on public.daily_work_plans;
create trigger set_daily_work_plans_updated_at
before update on public.daily_work_plans
for each row execute procedure public.set_updated_at();

drop trigger if exists set_daily_work_plan_items_updated_at on public.daily_work_plan_items;
create trigger set_daily_work_plan_items_updated_at
before update on public.daily_work_plan_items
for each row execute procedure public.set_updated_at();

drop policy if exists work_items_select_policy on public.work_items;
create policy work_items_select_policy
on public.work_items
for select
using (
  org_id = public.current_user_org_id()
  and (public.is_current_user_manager() or owner_id = auth.uid())
);

drop policy if exists work_items_insert_policy on public.work_items;
create policy work_items_insert_policy
on public.work_items
for insert
with check (
  org_id = public.current_user_org_id()
  and (public.is_current_user_manager() or owner_id = auth.uid())
);

drop policy if exists work_items_update_policy on public.work_items;
create policy work_items_update_policy
on public.work_items
for update
using (
  org_id = public.current_user_org_id()
  and (public.is_current_user_manager() or owner_id = auth.uid())
)
with check (
  org_id = public.current_user_org_id()
  and (public.is_current_user_manager() or owner_id = auth.uid())
);

drop policy if exists daily_work_plans_select_policy on public.daily_work_plans;
create policy daily_work_plans_select_policy
on public.daily_work_plans
for select
using (
  org_id = public.current_user_org_id()
  and (public.is_current_user_manager() or user_id = auth.uid())
);

drop policy if exists daily_work_plans_insert_policy on public.daily_work_plans;
create policy daily_work_plans_insert_policy
on public.daily_work_plans
for insert
with check (
  org_id = public.current_user_org_id()
  and (public.is_current_user_manager() or user_id = auth.uid())
);

drop policy if exists daily_work_plans_update_policy on public.daily_work_plans;
create policy daily_work_plans_update_policy
on public.daily_work_plans
for update
using (
  org_id = public.current_user_org_id()
  and (public.is_current_user_manager() or user_id = auth.uid())
)
with check (
  org_id = public.current_user_org_id()
  and (public.is_current_user_manager() or user_id = auth.uid())
);

drop policy if exists daily_work_plan_items_select_policy on public.daily_work_plan_items;
create policy daily_work_plan_items_select_policy
on public.daily_work_plan_items
for select
using (
  org_id = public.current_user_org_id()
  and exists (
    select 1
    from public.daily_work_plans p
    where p.id = daily_work_plan_items.plan_id
      and p.org_id = public.current_user_org_id()
      and (public.is_current_user_manager() or p.user_id = auth.uid())
  )
);

drop policy if exists daily_work_plan_items_insert_policy on public.daily_work_plan_items;
create policy daily_work_plan_items_insert_policy
on public.daily_work_plan_items
for insert
with check (
  org_id = public.current_user_org_id()
  and exists (
    select 1
    from public.daily_work_plans p
    where p.id = daily_work_plan_items.plan_id
      and p.org_id = public.current_user_org_id()
      and (public.is_current_user_manager() or p.user_id = auth.uid())
  )
);

drop policy if exists daily_work_plan_items_update_policy on public.daily_work_plan_items;
create policy daily_work_plan_items_update_policy
on public.daily_work_plan_items
for update
using (
  org_id = public.current_user_org_id()
  and exists (
    select 1
    from public.daily_work_plans p
    where p.id = daily_work_plan_items.plan_id
      and p.org_id = public.current_user_org_id()
      and (public.is_current_user_manager() or p.user_id = auth.uid())
  )
)
with check (
  org_id = public.current_user_org_id()
  and exists (
    select 1
    from public.daily_work_plans p
    where p.id = daily_work_plan_items.plan_id
      and p.org_id = public.current_user_org_id()
      and (public.is_current_user_manager() or p.user_id = auth.uid())
  )
);

drop policy if exists task_execution_logs_select_policy on public.task_execution_logs;
create policy task_execution_logs_select_policy
on public.task_execution_logs
for select
using (
  org_id = public.current_user_org_id()
  and (public.is_current_user_manager() or user_id = auth.uid())
);

drop policy if exists task_execution_logs_insert_policy on public.task_execution_logs;
create policy task_execution_logs_insert_policy
on public.task_execution_logs
for insert
with check (
  org_id = public.current_user_org_id()
  and (public.is_current_user_manager() or user_id = auth.uid())
);

drop policy if exists work_agent_runs_select_policy on public.work_agent_runs;
create policy work_agent_runs_select_policy
on public.work_agent_runs
for select
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or user_id = auth.uid()
    or user_id is null
  )
);

drop policy if exists work_agent_runs_insert_policy on public.work_agent_runs;
create policy work_agent_runs_insert_policy
on public.work_agent_runs
for insert
with check (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or user_id = auth.uid()
  )
);

drop policy if exists work_agent_runs_update_policy on public.work_agent_runs;
create policy work_agent_runs_update_policy
on public.work_agent_runs
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
  'Daily Work Plan Generation (DeepSeek)',
  'v6-deepseek',
  'daily_work_plan_generation'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Generate explainable daily sales work plans.',
  'Use only provided facts. Return strict JSON. Explain why each task should be prioritized now. Do not infer personality labels.',
  '{"type":"object","required":["focus_theme","must_do_item_ids","prioritized_items","recommended_time_blocks","plan_summary","caution_notes"]}'::jsonb,
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
  'Task Action Suggestion (DeepSeek)',
  'v6-deepseek',
  'task_action_suggestion'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Generate practical action suggestions for one sales task.',
  'Use only provided facts. Return strict JSON with why-now reasoning and risk-if-delayed. Do not make irreversible decisions for user.',
  '{"type":"object","required":["why_now","suggested_action","talk_track","risk_if_delayed","success_signal","estimated_effort"]}'::jsonb,
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
  'Manager Team Rhythm Insight (DeepSeek)',
  'v6-deepseek',
  'manager_team_rhythm_insight'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Produce manager-friendly team execution rhythm insight.',
  'Use factual task and quality data only. Focus on coaching and operating rhythm, not surveillance. Return strict JSON.',
  '{"type":"object","required":["team_execution_summary","overdue_patterns","under_attended_critical_customers","who_needs_support","which_actions_should_be_prioritized","managerial_actions"]}'::jsonb,
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
  'Weekly Task Review (DeepSeek)',
  'v6-deepseek',
  'weekly_task_review'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Review one week of execution tasks and provide next-week focus.',
  'Use provided facts only. Return strict JSON and explain carry-over reasons clearly.',
  '{"type":"object","required":["completion_summary","carry_over_reasons","execution_strengths","execution_gaps","next_week_focus"]}'::jsonb,
  true
from public.organizations o
on conflict (org_id, scenario, version) do nothing;
