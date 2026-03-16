-- MOY (Mate Of You) - Supabase bootstrap schema
-- Covers: enums, tables, indexes, helper functions, triggers, and RLS policies

create extension if not exists pgcrypto;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'app_role') then
    create type public.app_role as enum ('sales', 'manager');
  end if;

  if not exists (select 1 from pg_type where typname = 'customer_stage') then
    create type public.customer_stage as enum ('lead', 'initial_contact', 'needs_confirmed', 'proposal', 'negotiation', 'won', 'lost');
  end if;

  if not exists (select 1 from pg_type where typname = 'opportunity_stage') then
    create type public.opportunity_stage as enum ('discovery', 'qualification', 'proposal', 'business_review', 'negotiation', 'won', 'lost');
  end if;

  if not exists (select 1 from pg_type where typname = 'risk_level') then
    create type public.risk_level as enum ('low', 'medium', 'high');
  end if;

  if not exists (select 1 from pg_type where typname = 'alert_severity') then
    create type public.alert_severity as enum ('info', 'warning', 'critical');
  end if;

  if not exists (select 1 from pg_type where typname = 'alert_status') then
    create type public.alert_status as enum ('open', 'watching', 'resolved');
  end if;

  if not exists (select 1 from pg_type where typname = 'communication_type') then
    create type public.communication_type as enum ('phone', 'wechat', 'email', 'meeting', 'other');
  end if;

  if not exists (select 1 from pg_type where typname = 'alert_rule_type') then
    create type public.alert_rule_type as enum (
      'no_followup_overdue',
      'active_response_no_quote',
      'quoted_but_stalled',
      'missing_decision_maker',
      'high_probability_stalled'
    );
  end if;
end $$;

create table if not exists public.organizations (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  slug text not null unique,
  created_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  org_id uuid not null references public.organizations(id) on delete cascade,
  display_name text not null,
  role public.app_role not null default 'sales',
  is_active boolean not null default true,
  title text,
  team_name text,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.customers (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  owner_id uuid not null references public.profiles(id),
  name text not null,
  company_name text not null,
  contact_name text not null,
  phone text,
  email text,
  source_channel text,
  current_stage public.customer_stage not null default 'lead',
  last_followup_at timestamptz,
  next_followup_at timestamptz,
  win_probability int not null default 30 check (win_probability >= 0 and win_probability <= 100),
  risk_level public.risk_level not null default 'low',
  tags text[] not null default '{}'::text[],
  ai_summary text,
  ai_suggestion text,
  ai_risk_judgement text,
  has_decision_maker boolean not null default true,
  created_by uuid not null references public.profiles(id),
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.followups (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  customer_id uuid not null references public.customers(id) on delete cascade,
  owner_id uuid not null references public.profiles(id),
  communication_type public.communication_type not null,
  summary text not null,
  customer_needs text not null,
  objections text,
  next_step text not null,
  next_followup_at timestamptz,
  needs_ai_analysis boolean not null default false,
  ai_summary text,
  ai_suggestion text,
  ai_risk_level public.risk_level,
  ai_leak_risk boolean,
  created_by uuid not null references public.profiles(id),
  created_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.opportunities (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  customer_id uuid not null references public.customers(id) on delete cascade,
  owner_id uuid not null references public.profiles(id),
  title text not null,
  amount numeric(14, 2) not null default 0 check (amount >= 0),
  stage public.opportunity_stage not null default 'discovery',
  risk_level public.risk_level not null default 'low',
  expected_close_date date,
  last_activity_at timestamptz,
  created_by uuid not null references public.profiles(id),
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.alerts (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  customer_id uuid references public.customers(id) on delete cascade,
  opportunity_id uuid references public.opportunities(id) on delete cascade,
  owner_id uuid references public.profiles(id),
  rule_type public.alert_rule_type not null,
  severity public.alert_severity not null default 'warning',
  status public.alert_status not null default 'open',
  title text not null,
  description text,
  due_at timestamptz,
  resolved_at timestamptz,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  constraint alerts_target_check check (customer_id is not null or opportunity_id is not null)
);

create index if not exists idx_profiles_org_id on public.profiles(org_id);
create index if not exists idx_profiles_role on public.profiles(role);

create index if not exists idx_customers_org_id on public.customers(org_id);
create index if not exists idx_customers_owner_id on public.customers(owner_id);
create index if not exists idx_customers_stage on public.customers(current_stage);
create index if not exists idx_customers_next_followup on public.customers(next_followup_at);
create index if not exists idx_customers_last_followup on public.customers(last_followup_at);
create index if not exists idx_customers_created_at on public.customers(created_at desc);

create index if not exists idx_followups_org_id on public.followups(org_id);
create index if not exists idx_followups_customer_id on public.followups(customer_id);
create index if not exists idx_followups_owner_id on public.followups(owner_id);
create index if not exists idx_followups_created_at on public.followups(created_at desc);
create index if not exists idx_followups_next_followup_at on public.followups(next_followup_at);

create index if not exists idx_opportunities_org_id on public.opportunities(org_id);
create index if not exists idx_opportunities_owner_id on public.opportunities(owner_id);
create index if not exists idx_opportunities_stage on public.opportunities(stage);
create index if not exists idx_opportunities_last_activity on public.opportunities(last_activity_at);
create index if not exists idx_opportunities_created_at on public.opportunities(created_at desc);

create index if not exists idx_alerts_org_id on public.alerts(org_id);
create index if not exists idx_alerts_owner_id on public.alerts(owner_id);
create index if not exists idx_alerts_customer_id on public.alerts(customer_id);
create index if not exists idx_alerts_opportunity_id on public.alerts(opportunity_id);
create index if not exists idx_alerts_status on public.alerts(status);
create index if not exists idx_alerts_severity on public.alerts(severity);
create index if not exists idx_alerts_due_at on public.alerts(due_at);
create index if not exists idx_alerts_created_at on public.alerts(created_at desc);

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = timezone('utc', now());
  return new;
end;
$$;

drop trigger if exists set_profiles_updated_at on public.profiles;
create trigger set_profiles_updated_at
before update on public.profiles
for each row execute procedure public.set_updated_at();

drop trigger if exists set_customers_updated_at on public.customers;
create trigger set_customers_updated_at
before update on public.customers
for each row execute procedure public.set_updated_at();

drop trigger if exists set_opportunities_updated_at on public.opportunities;
create trigger set_opportunities_updated_at
before update on public.opportunities
for each row execute procedure public.set_updated_at();

drop trigger if exists set_alerts_updated_at on public.alerts;
create trigger set_alerts_updated_at
before update on public.alerts
for each row execute procedure public.set_updated_at();

create or replace function public.current_user_org_id()
returns uuid
language sql
stable
security definer
set search_path = public
as $$
  select p.org_id
  from public.profiles p
  where p.id = auth.uid()
    and p.is_active = true
  limit 1
$$;

create or replace function public.current_user_role()
returns public.app_role
language sql
stable
security definer
set search_path = public
as $$
  select p.role
  from public.profiles p
  where p.id = auth.uid()
    and p.is_active = true
  limit 1
$$;

create or replace function public.is_current_user_manager()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select coalesce(public.current_user_role() = 'manager', false)
$$;

create or replace function public.sync_customer_after_followup()
returns trigger
language plpgsql
set search_path = public
as $$
begin
  update public.customers c
     set last_followup_at = new.created_at,
         next_followup_at = coalesce(new.next_followup_at, c.next_followup_at),
         ai_summary = case when new.needs_ai_analysis and new.ai_summary is not null then new.ai_summary else c.ai_summary end,
         ai_suggestion = case when new.needs_ai_analysis and new.ai_suggestion is not null then new.ai_suggestion else c.ai_suggestion end,
         ai_risk_judgement = case
           when new.needs_ai_analysis and new.ai_leak_risk = true then coalesce(new.ai_suggestion, c.ai_risk_judgement)
           when new.needs_ai_analysis and new.ai_risk_level is not null then 'AI 风险级别：' || new.ai_risk_level::text
           else c.ai_risk_judgement
         end,
         risk_level = case when new.needs_ai_analysis and new.ai_risk_level is not null then new.ai_risk_level else c.risk_level end,
         updated_at = timezone('utc', now())
   where c.id = new.customer_id;

  return new;
end;
$$;

drop trigger if exists sync_customer_after_followup_trigger on public.followups;
create trigger sync_customer_after_followup_trigger
after insert on public.followups
for each row execute procedure public.sync_customer_after_followup();

alter table public.organizations enable row level security;
alter table public.profiles enable row level security;
alter table public.customers enable row level security;
alter table public.followups enable row level security;
alter table public.opportunities enable row level security;
alter table public.alerts enable row level security;

drop policy if exists organizations_select_own on public.organizations;
create policy organizations_select_own
on public.organizations
for select
using (id = public.current_user_org_id());

drop policy if exists profiles_select_same_org on public.profiles;
create policy profiles_select_same_org
on public.profiles
for select
using (
  auth.uid() is not null
  and org_id = public.current_user_org_id()
);

drop policy if exists profiles_update_self_or_manager on public.profiles;
create policy profiles_update_self_or_manager
on public.profiles
for update
using (
  org_id = public.current_user_org_id()
  and (id = auth.uid() or public.is_current_user_manager())
)
with check (
  org_id = public.current_user_org_id()
  and (id = auth.uid() or public.is_current_user_manager())
);

drop policy if exists customers_select_policy on public.customers;
create policy customers_select_policy
on public.customers
for select
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or owner_id = auth.uid()
    or created_by = auth.uid()
  )
);

drop policy if exists customers_insert_policy on public.customers;
create policy customers_insert_policy
on public.customers
for insert
with check (
  org_id = public.current_user_org_id()
  and created_by = auth.uid()
  and (
    public.is_current_user_manager()
    or owner_id = auth.uid()
  )
);

drop policy if exists customers_update_policy on public.customers;
create policy customers_update_policy
on public.customers
for update
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or owner_id = auth.uid()
  )
)
with check (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or owner_id = auth.uid()
  )
);

drop policy if exists customers_delete_policy on public.customers;
create policy customers_delete_policy
on public.customers
for delete
using (
  org_id = public.current_user_org_id()
  and public.is_current_user_manager()
);

drop policy if exists followups_select_policy on public.followups;
create policy followups_select_policy
on public.followups
for select
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or owner_id = auth.uid()
    or created_by = auth.uid()
    or exists (
      select 1
      from public.customers c
      where c.id = followups.customer_id
        and c.owner_id = auth.uid()
        and c.org_id = public.current_user_org_id()
    )
  )
);

drop policy if exists followups_insert_policy on public.followups;
create policy followups_insert_policy
on public.followups
for insert
with check (
  org_id = public.current_user_org_id()
  and created_by = auth.uid()
  and (
    public.is_current_user_manager()
    or exists (
      select 1
      from public.customers c
      where c.id = followups.customer_id
        and c.owner_id = auth.uid()
        and c.org_id = public.current_user_org_id()
    )
  )
);

drop policy if exists followups_update_policy on public.followups;
create policy followups_update_policy
on public.followups
for update
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or created_by = auth.uid()
  )
)
with check (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or created_by = auth.uid()
  )
);

drop policy if exists opportunities_select_policy on public.opportunities;
create policy opportunities_select_policy
on public.opportunities
for select
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or owner_id = auth.uid()
    or created_by = auth.uid()
  )
);

drop policy if exists opportunities_insert_policy on public.opportunities;
create policy opportunities_insert_policy
on public.opportunities
for insert
with check (
  org_id = public.current_user_org_id()
  and created_by = auth.uid()
  and (
    public.is_current_user_manager()
    or owner_id = auth.uid()
  )
);

drop policy if exists opportunities_update_policy on public.opportunities;
create policy opportunities_update_policy
on public.opportunities
for update
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or owner_id = auth.uid()
  )
)
with check (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or owner_id = auth.uid()
  )
);

drop policy if exists alerts_select_policy on public.alerts;
create policy alerts_select_policy
on public.alerts
for select
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or owner_id = auth.uid()
    or (customer_id is not null and exists (
      select 1
      from public.customers c
      where c.id = alerts.customer_id
        and c.owner_id = auth.uid()
        and c.org_id = public.current_user_org_id()
    ))
    or (opportunity_id is not null and exists (
      select 1
      from public.opportunities o
      where o.id = alerts.opportunity_id
        and o.owner_id = auth.uid()
        and o.org_id = public.current_user_org_id()
    ))
  )
);

drop policy if exists alerts_insert_policy on public.alerts;
create policy alerts_insert_policy
on public.alerts
for insert
with check (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or owner_id = auth.uid()
  )
);

drop policy if exists alerts_update_policy on public.alerts;
create policy alerts_update_policy
on public.alerts
for update
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or owner_id = auth.uid()
    or (customer_id is not null and exists (
      select 1
      from public.customers c
      where c.id = alerts.customer_id
        and c.owner_id = auth.uid()
        and c.org_id = public.current_user_org_id()
    ))
    or (opportunity_id is not null and exists (
      select 1
      from public.opportunities o
      where o.id = alerts.opportunity_id
        and o.owner_id = auth.uid()
        and o.org_id = public.current_user_org_id()
    ))
  )
)
with check (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or owner_id = auth.uid()
    or (customer_id is not null and exists (
      select 1
      from public.customers c
      where c.id = alerts.customer_id
        and c.owner_id = auth.uid()
        and c.org_id = public.current_user_org_id()
    ))
    or (opportunity_id is not null and exists (
      select 1
      from public.opportunities o
      where o.id = alerts.opportunity_id
        and o.owner_id = auth.uid()
        and o.org_id = public.current_user_org_id()
    ))
  )
);
