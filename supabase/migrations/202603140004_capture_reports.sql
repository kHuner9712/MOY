-- MOY phase-4: capture inputs + generated reports
-- Adds multi-entry communication capture workflow and report generation persistence.

create extension if not exists pgcrypto;

do $$
begin
  if exists (select 1 from pg_type where typname = 'ai_scenario') then
    if not exists (
      select 1 from pg_enum
      where enumlabel = 'communication_extraction'
        and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'communication_extraction';
    end if;

    if not exists (
      select 1 from pg_enum
      where enumlabel = 'sales_daily_report'
        and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'sales_daily_report';
    end if;

    if not exists (
      select 1 from pg_enum
      where enumlabel = 'sales_weekly_report'
        and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'sales_weekly_report';
    end if;

    if not exists (
      select 1 from pg_enum
      where enumlabel = 'manager_daily_report'
        and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'manager_daily_report';
    end if;

    if not exists (
      select 1 from pg_enum
      where enumlabel = 'manager_weekly_report'
        and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'manager_weekly_report';
    end if;
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'communication_source_type') then
    create type public.communication_source_type as enum (
      'manual_note',
      'pasted_chat',
      'call_summary',
      'meeting_note',
      'voice_transcript',
      'imported_text'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'extraction_status') then
    create type public.extraction_status as enum ('pending', 'processing', 'completed', 'failed');
  end if;

  if not exists (select 1 from pg_type where typname = 'report_type') then
    create type public.report_type as enum ('sales_daily', 'sales_weekly', 'manager_daily', 'manager_weekly');
  end if;

  if not exists (select 1 from pg_type where typname = 'report_scope_type') then
    create type public.report_scope_type as enum ('self', 'team', 'org');
  end if;

  if not exists (select 1 from pg_type where typname = 'report_status') then
    create type public.report_status as enum ('generating', 'completed', 'failed');
  end if;

  if not exists (select 1 from pg_type where typname = 'followup_draft_status') then
    create type public.followup_draft_status as enum ('draft', 'confirmed');
  end if;
end $$;

alter table if exists public.ai_runs
  alter column customer_id drop not null;

create table if not exists public.communication_inputs (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  customer_id uuid references public.customers(id) on delete set null,
  owner_id uuid not null references public.profiles(id),
  source_type public.communication_source_type not null,
  title text,
  raw_content text not null,
  input_language text not null default 'zh-CN',
  occurred_at timestamptz not null default timezone('utc', now()),
  extracted_followup_id uuid references public.followups(id) on delete set null,
  extraction_status public.extraction_status not null default 'pending',
  extraction_error text,
  extracted_data jsonb not null default '{}'::jsonb,
  created_by uuid not null references public.profiles(id),
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  constraint communication_inputs_raw_content_check check (char_length(trim(raw_content)) > 0)
);

alter table if exists public.followups
  add column if not exists source_input_id uuid references public.communication_inputs(id) on delete set null,
  add column if not exists draft_status public.followup_draft_status not null default 'confirmed';

create table if not exists public.generated_reports (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  report_type public.report_type not null,
  target_user_id uuid references public.profiles(id) on delete set null,
  scope_type public.report_scope_type not null default 'self',
  period_start date not null,
  period_end date not null,
  status public.report_status not null default 'generating',
  title text,
  summary text,
  content_markdown text,
  metrics_snapshot jsonb not null default '{}'::jsonb,
  source_snapshot jsonb not null default '{}'::jsonb,
  generated_by uuid not null references public.profiles(id),
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  constraint generated_reports_period_check check (period_end >= period_start)
);

create index if not exists idx_followups_source_input_id on public.followups(source_input_id);
create index if not exists idx_followups_draft_status on public.followups(draft_status);

create index if not exists idx_comm_inputs_org_id on public.communication_inputs(org_id);
create index if not exists idx_comm_inputs_owner_id on public.communication_inputs(owner_id);
create index if not exists idx_comm_inputs_customer_id on public.communication_inputs(customer_id);
create index if not exists idx_comm_inputs_occurred_at on public.communication_inputs(occurred_at desc);
create index if not exists idx_comm_inputs_extraction_status on public.communication_inputs(extraction_status);
create index if not exists idx_comm_inputs_created_at on public.communication_inputs(created_at desc);

create index if not exists idx_generated_reports_org_id on public.generated_reports(org_id);
create index if not exists idx_generated_reports_target_user_id on public.generated_reports(target_user_id);
create index if not exists idx_generated_reports_report_type on public.generated_reports(report_type);
create index if not exists idx_generated_reports_period on public.generated_reports(period_start, period_end);
create index if not exists idx_generated_reports_status on public.generated_reports(status);
create index if not exists idx_generated_reports_created_at on public.generated_reports(created_at desc);

alter table public.communication_inputs enable row level security;
alter table public.generated_reports enable row level security;

drop trigger if exists set_communication_inputs_updated_at on public.communication_inputs;
create trigger set_communication_inputs_updated_at
before update on public.communication_inputs
for each row execute procedure public.set_updated_at();

drop trigger if exists set_generated_reports_updated_at on public.generated_reports;
create trigger set_generated_reports_updated_at
before update on public.generated_reports
for each row execute procedure public.set_updated_at();

drop policy if exists communication_inputs_select_policy on public.communication_inputs;
create policy communication_inputs_select_policy
on public.communication_inputs
for select
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or owner_id = auth.uid()
    or created_by = auth.uid()
    or (
      customer_id is not null
      and exists (
        select 1
        from public.customers c
        where c.id = communication_inputs.customer_id
          and c.owner_id = auth.uid()
          and c.org_id = public.current_user_org_id()
      )
    )
  )
);

drop policy if exists communication_inputs_insert_policy on public.communication_inputs;
create policy communication_inputs_insert_policy
on public.communication_inputs
for insert
with check (
  org_id = public.current_user_org_id()
  and created_by = auth.uid()
  and (
    public.is_current_user_manager()
    or owner_id = auth.uid()
  )
);

drop policy if exists communication_inputs_update_policy on public.communication_inputs;
create policy communication_inputs_update_policy
on public.communication_inputs
for update
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or owner_id = auth.uid()
    or created_by = auth.uid()
  )
)
with check (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or owner_id = auth.uid()
    or created_by = auth.uid()
  )
);

drop policy if exists generated_reports_select_policy on public.generated_reports;
create policy generated_reports_select_policy
on public.generated_reports
for select
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or generated_by = auth.uid()
    or target_user_id = auth.uid()
  )
);

drop policy if exists generated_reports_insert_policy on public.generated_reports;
create policy generated_reports_insert_policy
on public.generated_reports
for insert
with check (
  org_id = public.current_user_org_id()
  and generated_by = auth.uid()
  and (
    public.is_current_user_manager()
    or (
      report_type in ('sales_daily', 'sales_weekly')
      and scope_type = 'self'
      and coalesce(target_user_id, auth.uid()) = auth.uid()
    )
  )
);

drop policy if exists generated_reports_update_policy on public.generated_reports;
create policy generated_reports_update_policy
on public.generated_reports
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
  'Communication Extraction (DeepSeek)',
  'v4-deepseek',
  'communication_extraction'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Extract structured sales facts from raw communication text.',
  'Return strict JSON only. Do not fabricate facts. Mark uncertainty clearly when information is missing.',
  '{"type":"object","required":["matched_customer_name","confidence_of_match","communication_type","summary","key_needs","key_objections","buying_signals","mentioned_budget","mentioned_timeline","decision_makers","next_step","recommended_next_followup_at","should_create_followup","should_update_opportunity","should_trigger_alert_review","structured_tags","uncertainty_notes"]}'::jsonb,
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
  'Sales Daily Report (DeepSeek)',
  'v4-deepseek',
  'sales_daily_report'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Generate concise daily sales report for one salesperson.',
  'Return strict JSON only. Keep conclusions evidence-based and actionable.',
  '{"type":"object","required":["title","summary","key_metrics","risk_list","recommended_actions","content_markdown"]}'::jsonb,
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
  'Sales Weekly Report (DeepSeek)',
  'v4-deepseek',
  'sales_weekly_report'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Generate concise weekly sales report for one salesperson.',
  'Return strict JSON only. Keep conclusions evidence-based and actionable.',
  '{"type":"object","required":["title","summary","key_metrics","risk_list","recommended_actions","content_markdown"]}'::jsonb,
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
  'Manager Daily Report (DeepSeek)',
  'v4-deepseek',
  'manager_daily_report'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Generate concise daily report for sales managers.',
  'Return strict JSON only. Highlight management actions, team risks, and concrete priorities.',
  '{"type":"object","required":["title","summary","key_metrics","risk_list","recommended_actions","content_markdown"]}'::jsonb,
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
  'Manager Weekly Report (DeepSeek)',
  'v4-deepseek',
  'manager_weekly_report'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Generate concise weekly report for sales managers.',
  'Return strict JSON only. Highlight team execution quality, risks, and management actions.',
  '{"type":"object","required":["title","summary","key_metrics","risk_list","recommended_actions","content_markdown"]}'::jsonb,
  true
from public.organizations o
on conflict (org_id, scenario, version) do nothing;
