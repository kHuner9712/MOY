-- Phase 16: Automation Ops & Executive Cockpit Layer

-- ---------- Enums ----------
do $$
begin
  if not exists (select 1 from pg_type where typname = 'automation_rule_scope') then
    create type public.automation_rule_scope as enum (
      'customer_health',
      'deal_progress',
      'trial_conversion',
      'onboarding',
      'retention',
      'external_touchpoint',
      'manager_attention'
    );
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'automation_trigger_type') then
    create type public.automation_trigger_type as enum (
      'threshold',
      'inactivity',
      'missing_step',
      'health_score',
      'event_sequence'
    );
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'automation_rule_severity') then
    create type public.automation_rule_severity as enum ('info', 'warning', 'critical');
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'automation_rule_run_status') then
    create type public.automation_rule_run_status as enum ('running', 'completed', 'failed');
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'business_event_entity_type') then
    create type public.business_event_entity_type as enum (
      'customer',
      'opportunity',
      'deal_room',
      'trial_org',
      'work_item',
      'onboarding_run',
      'touchpoint'
    );
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'business_event_type') then
    create type public.business_event_type as enum (
      'first_value_reached',
      'health_declined',
      'renewal_risk_detected',
      'expansion_signal',
      'trial_stalled',
      'trial_activated',
      'onboarding_stuck',
      'deal_blocked',
      'no_recent_touchpoint',
      'manager_attention_escalated',
      'renewal_due_soon',
      'conversion_signal'
    );
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'business_event_severity') then
    create type public.business_event_severity as enum ('info', 'warning', 'critical');
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'business_event_status') then
    create type public.business_event_status as enum ('open', 'acknowledged', 'resolved', 'ignored');
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'customer_lifecycle_type') then
    create type public.customer_lifecycle_type as enum (
      'prospect',
      'active_customer',
      'trial_customer',
      'renewing_customer'
    );
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'customer_health_band') then
    create type public.customer_health_band as enum ('healthy', 'watch', 'at_risk', 'critical');
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'executive_brief_type') then
    create type public.executive_brief_type as enum (
      'executive_daily',
      'executive_weekly',
      'retention_watch',
      'trial_watch',
      'deal_watch'
    );
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'executive_brief_status') then
    create type public.executive_brief_status as enum ('generating', 'completed', 'failed');
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'renewal_watch_status') then
    create type public.renewal_watch_status as enum (
      'watch',
      'due_soon',
      'at_risk',
      'expansion_candidate',
      'renewed',
      'churned'
    );
  end if;
end $$;

-- ---------- AI scenario extension ----------
do $$
begin
  if exists (select 1 from pg_type where typname = 'ai_scenario') then
    begin
      alter type public.ai_scenario add value 'executive_brief_summary';
    exception when duplicate_object then null;
    end;
    begin
      alter type public.ai_scenario add value 'customer_health_summary';
    exception when duplicate_object then null;
    end;
    begin
      alter type public.ai_scenario add value 'automation_action_recommendation';
    exception when duplicate_object then null;
    end;
    begin
      alter type public.ai_scenario add value 'retention_watch_review';
    exception when duplicate_object then null;
    end;
  end if;
end $$;

-- ---------- Tables ----------
create table if not exists public.automation_rules (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  rule_key text not null,
  rule_name text not null,
  rule_scope public.automation_rule_scope not null,
  trigger_type public.automation_trigger_type not null,
  conditions_json jsonb not null default '{}'::jsonb,
  action_json jsonb not null default '{}'::jsonb,
  severity public.automation_rule_severity not null default 'warning',
  is_enabled boolean not null default true,
  created_by uuid references public.profiles(id) on delete set null,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  constraint automation_rules_unique_org_rule_key unique (org_id, rule_key)
);

create table if not exists public.automation_rule_runs (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  rule_id uuid not null references public.automation_rules(id) on delete cascade,
  run_status public.automation_rule_run_status not null default 'running',
  matched_count integer not null default 0,
  created_action_count integer not null default 0,
  summary text,
  detail_snapshot jsonb not null default '{}'::jsonb,
  started_at timestamptz not null default timezone('utc', now()),
  completed_at timestamptz,
  created_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.business_events (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  entity_type public.business_event_entity_type not null,
  entity_id uuid not null,
  event_type public.business_event_type not null,
  severity public.business_event_severity not null default 'warning',
  event_summary text not null,
  event_payload jsonb not null default '{}'::jsonb,
  status public.business_event_status not null default 'open',
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.customer_health_snapshots (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  customer_id uuid not null references public.customers(id) on delete cascade,
  snapshot_date date not null,
  lifecycle_type public.customer_lifecycle_type not null,
  activity_score integer not null default 0,
  engagement_score integer not null default 0,
  progression_score integer not null default 0,
  retention_score integer not null default 0,
  expansion_score integer not null default 0,
  overall_health_score integer not null default 0,
  health_band public.customer_health_band not null default 'watch',
  risk_flags jsonb not null default '[]'::jsonb,
  positive_signals jsonb not null default '[]'::jsonb,
  summary text,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  constraint customer_health_snapshots_unique_daily unique (org_id, customer_id, snapshot_date)
);

create table if not exists public.executive_briefs (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  brief_type public.executive_brief_type not null,
  target_user_id uuid references public.profiles(id) on delete set null,
  status public.executive_brief_status not null default 'generating',
  headline text,
  summary text,
  brief_payload jsonb not null default '{}'::jsonb,
  source_snapshot jsonb not null default '{}'::jsonb,
  ai_run_id uuid references public.ai_runs(id) on delete set null,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.renewal_watch_items (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  customer_id uuid not null references public.customers(id) on delete cascade,
  owner_id uuid references public.profiles(id) on delete set null,
  renewal_status public.renewal_watch_status not null default 'watch',
  renewal_due_at timestamptz,
  product_scope text,
  health_snapshot_id uuid references public.customer_health_snapshots(id) on delete set null,
  recommendation_summary text,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  constraint renewal_watch_items_unique_customer unique (org_id, customer_id)
);

-- ---------- Indexes ----------
create index if not exists idx_automation_rules_org_scope_enabled on public.automation_rules(org_id, rule_scope, is_enabled);
create index if not exists idx_automation_rules_created_at on public.automation_rules(created_at desc);

create index if not exists idx_automation_rule_runs_org_rule on public.automation_rule_runs(org_id, rule_id);
create index if not exists idx_automation_rule_runs_status on public.automation_rule_runs(run_status);
create index if not exists idx_automation_rule_runs_created_at on public.automation_rule_runs(created_at desc);

create index if not exists idx_business_events_org_type_status on public.business_events(org_id, event_type, status);
create index if not exists idx_business_events_org_entity on public.business_events(org_id, entity_type, entity_id);
create index if not exists idx_business_events_created_at on public.business_events(created_at desc);

create index if not exists idx_customer_health_snapshots_org_customer on public.customer_health_snapshots(org_id, customer_id);
create index if not exists idx_customer_health_snapshots_date on public.customer_health_snapshots(snapshot_date desc);
create index if not exists idx_customer_health_snapshots_band on public.customer_health_snapshots(health_band);
create index if not exists idx_customer_health_snapshots_org_band on public.customer_health_snapshots(org_id, health_band, snapshot_date desc);

create index if not exists idx_executive_briefs_org_type on public.executive_briefs(org_id, brief_type);
create index if not exists idx_executive_briefs_status on public.executive_briefs(status);
create index if not exists idx_executive_briefs_created_at on public.executive_briefs(created_at desc);

create index if not exists idx_renewal_watch_items_org_customer on public.renewal_watch_items(org_id, customer_id);
create index if not exists idx_renewal_watch_items_status on public.renewal_watch_items(renewal_status);
create index if not exists idx_renewal_watch_items_due_at on public.renewal_watch_items(renewal_due_at asc);
create index if not exists idx_renewal_watch_items_created_at on public.renewal_watch_items(created_at desc);

-- ---------- updated_at triggers ----------
do $$
begin
  if exists (select 1 from pg_proc where proname = 'set_updated_at') then
    drop trigger if exists trg_automation_rules_updated_at on public.automation_rules;
    create trigger trg_automation_rules_updated_at
    before update on public.automation_rules
    for each row execute function public.set_updated_at();

    drop trigger if exists trg_business_events_updated_at on public.business_events;
    create trigger trg_business_events_updated_at
    before update on public.business_events
    for each row execute function public.set_updated_at();

    drop trigger if exists trg_customer_health_snapshots_updated_at on public.customer_health_snapshots;
    create trigger trg_customer_health_snapshots_updated_at
    before update on public.customer_health_snapshots
    for each row execute function public.set_updated_at();

    drop trigger if exists trg_executive_briefs_updated_at on public.executive_briefs;
    create trigger trg_executive_briefs_updated_at
    before update on public.executive_briefs
    for each row execute function public.set_updated_at();

    drop trigger if exists trg_renewal_watch_items_updated_at on public.renewal_watch_items;
    create trigger trg_renewal_watch_items_updated_at
    before update on public.renewal_watch_items
    for each row execute function public.set_updated_at();
  end if;
end $$;

-- ---------- RLS ----------
alter table public.automation_rules enable row level security;
alter table public.automation_rule_runs enable row level security;
alter table public.business_events enable row level security;
alter table public.customer_health_snapshots enable row level security;
alter table public.executive_briefs enable row level security;
alter table public.renewal_watch_items enable row level security;

-- automation_rules
DROP POLICY if exists automation_rules_select_policy on public.automation_rules;
create policy automation_rules_select_policy
on public.automation_rules
for select
using (
  org_id = public.current_user_org_id()
  and public.can_current_user_view_org_usage()
);

DROP POLICY if exists automation_rules_write_policy on public.automation_rules;
create policy automation_rules_write_policy
on public.automation_rules
for all
using (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
)
with check (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
);

-- automation_rule_runs
DROP POLICY if exists automation_rule_runs_select_policy on public.automation_rule_runs;
create policy automation_rule_runs_select_policy
on public.automation_rule_runs
for select
using (
  org_id = public.current_user_org_id()
  and public.can_current_user_view_org_usage()
);

DROP POLICY if exists automation_rule_runs_write_policy on public.automation_rule_runs;
create policy automation_rule_runs_write_policy
on public.automation_rule_runs
for all
using (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
)
with check (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
);

-- business_events
DROP POLICY if exists business_events_select_policy on public.business_events;
create policy business_events_select_policy
on public.business_events
for select
using (
  org_id = public.current_user_org_id()
  and (
    public.can_current_user_view_org_usage()
    or exists (
      select 1
      from public.customers c
      where c.id = business_events.entity_id
        and business_events.entity_type = 'customer'
        and c.org_id = public.current_user_org_id()
        and c.owner_id = auth.uid()
    )
    or exists (
      select 1
      from public.opportunities o
      where o.id = business_events.entity_id
        and business_events.entity_type = 'opportunity'
        and o.org_id = public.current_user_org_id()
        and o.owner_id = auth.uid()
    )
    or exists (
      select 1
      from public.deal_rooms dr
      where dr.id = business_events.entity_id
        and business_events.entity_type = 'deal_room'
        and dr.org_id = public.current_user_org_id()
        and dr.owner_id = auth.uid()
    )
  )
);

DROP POLICY if exists business_events_write_policy on public.business_events;
create policy business_events_write_policy
on public.business_events
for all
using (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
)
with check (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
);

-- customer_health_snapshots
DROP POLICY if exists customer_health_snapshots_select_policy on public.customer_health_snapshots;
create policy customer_health_snapshots_select_policy
on public.customer_health_snapshots
for select
using (
  org_id = public.current_user_org_id()
  and (
    public.can_current_user_view_org_usage()
    or exists (
      select 1
      from public.customers c
      where c.id = customer_health_snapshots.customer_id
        and c.org_id = public.current_user_org_id()
        and c.owner_id = auth.uid()
    )
  )
);

DROP POLICY if exists customer_health_snapshots_write_policy on public.customer_health_snapshots;
create policy customer_health_snapshots_write_policy
on public.customer_health_snapshots
for all
using (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
)
with check (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
);

-- executive_briefs
DROP POLICY if exists executive_briefs_select_policy on public.executive_briefs;
create policy executive_briefs_select_policy
on public.executive_briefs
for select
using (
  org_id = public.current_user_org_id()
  and (
    public.can_current_user_view_org_usage()
    or target_user_id = auth.uid()
  )
);

DROP POLICY if exists executive_briefs_write_policy on public.executive_briefs;
create policy executive_briefs_write_policy
on public.executive_briefs
for all
using (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
)
with check (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
);

-- renewal_watch_items
DROP POLICY if exists renewal_watch_items_select_policy on public.renewal_watch_items;
create policy renewal_watch_items_select_policy
on public.renewal_watch_items
for select
using (
  org_id = public.current_user_org_id()
  and (
    public.can_current_user_view_org_usage()
    or owner_id = auth.uid()
    or exists (
      select 1
      from public.customers c
      where c.id = renewal_watch_items.customer_id
        and c.org_id = public.current_user_org_id()
        and c.owner_id = auth.uid()
    )
  )
);

DROP POLICY if exists renewal_watch_items_write_policy on public.renewal_watch_items;
create policy renewal_watch_items_write_policy
on public.renewal_watch_items
for all
using (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
)
with check (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
);
