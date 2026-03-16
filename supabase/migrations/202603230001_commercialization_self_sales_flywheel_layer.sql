-- Phase 15: Commercialization & Self-Sales Flywheel Layer

-- ---------- Enums ----------
do $$
begin
  if not exists (select 1 from pg_type where typname = 'inbound_lead_source') then
    create type public.inbound_lead_source as enum (
      'website_demo',
      'website_trial',
      'website_contact',
      'referral',
      'manual',
      'event',
      'content_download'
    );
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'inbound_lead_status') then
    create type public.inbound_lead_status as enum (
      'new',
      'qualified',
      'unqualified',
      'demo_scheduled',
      'trial_started',
      'converted_to_customer',
      'lost'
    );
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'demo_request_status') then
    create type public.demo_request_status as enum ('pending', 'scheduled', 'completed', 'no_show', 'cancelled');
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'demo_outcome_status') then
    create type public.demo_outcome_status as enum ('promising', 'neutral', 'not_fit', 'followup_needed');
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'trial_request_status') then
    create type public.trial_request_status as enum ('pending', 'approved', 'rejected', 'activated', 'expired');
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'trial_conversion_stage') then
    create type public.trial_conversion_stage as enum (
      'invited',
      'activated',
      'onboarding_started',
      'onboarding_completed',
      'first_value_seen',
      'active_trial',
      'conversion_discussion',
      'verbally_committed',
      'converted',
      'churned'
    );
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'conversion_event_type') then
    create type public.conversion_event_type as enum (
      'lead_created',
      'demo_requested',
      'demo_scheduled',
      'demo_completed',
      'trial_requested',
      'trial_approved',
      'trial_activated',
      'onboarding_completed',
      'first_deal_created',
      'first_brief_generated',
      'conversion_signal',
      'converted',
      'churn_risk'
    );
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'marketing_page_key') then
    create type public.marketing_page_key as enum ('home', 'product', 'industries', 'demo', 'trial', 'contact');
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'marketing_page_status') then
    create type public.marketing_page_status as enum ('draft', 'published');
  end if;
end $$;

-- ---------- AI scenario extension ----------
do $$
begin
  if exists (select 1 from pg_type where typname = 'ai_scenario') then
    begin
      alter type public.ai_scenario add value 'lead_qualification_assist';
    exception when duplicate_object then null;
    end;
    begin
      alter type public.ai_scenario add value 'trial_conversion_review';
    exception when duplicate_object then null;
    end;
    begin
      alter type public.ai_scenario add value 'growth_pipeline_summary';
    exception when duplicate_object then null;
    end;
  end if;
end $$;

-- ---------- Tables ----------
create table if not exists public.inbound_leads (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  lead_source public.inbound_lead_source not null,
  company_name text not null,
  contact_name text not null,
  email text not null,
  phone text,
  industry_hint text,
  team_size_hint text,
  use_case_hint text,
  source_campaign text,
  landing_page text,
  status public.inbound_lead_status not null default 'new',
  assigned_owner_id uuid references public.profiles(id) on delete set null,
  converted_customer_id uuid references public.customers(id) on delete set null,
  converted_opportunity_id uuid references public.opportunities(id) on delete set null,
  notes text,
  payload_snapshot jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.demo_requests (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  lead_id uuid not null references public.inbound_leads(id) on delete cascade,
  requested_by_email text not null,
  requested_at timestamptz not null default timezone('utc', now()),
  preferred_time_text text,
  demo_status public.demo_request_status not null default 'pending',
  scheduled_event_id uuid references public.calendar_events(id) on delete set null,
  owner_id uuid references public.profiles(id) on delete set null,
  demo_summary text,
  outcome_status public.demo_outcome_status,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.trial_requests (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  lead_id uuid not null references public.inbound_leads(id) on delete cascade,
  requested_by_email text not null,
  requested_at timestamptz not null default timezone('utc', now()),
  requested_template_id uuid references public.industry_templates(id) on delete set null,
  request_status public.trial_request_status not null default 'pending',
  target_org_id uuid references public.organizations(id) on delete set null,
  activation_token text,
  activation_started_at timestamptz,
  activated_at timestamptz,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.trial_conversion_tracks (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  target_org_id uuid not null references public.organizations(id) on delete cascade,
  lead_id uuid references public.inbound_leads(id) on delete set null,
  owner_id uuid not null references public.profiles(id) on delete cascade,
  current_stage public.trial_conversion_stage not null default 'invited',
  activation_score integer not null default 0,
  engagement_score integer not null default 0,
  conversion_readiness_score integer not null default 0,
  risk_flags jsonb not null default '[]'::jsonb,
  next_action text,
  next_action_due_at timestamptz,
  summary text,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  constraint trial_conversion_tracks_unique_target_org unique (org_id, target_org_id)
);

create table if not exists public.conversion_events (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  target_org_id uuid references public.organizations(id) on delete set null,
  lead_id uuid references public.inbound_leads(id) on delete set null,
  event_type public.conversion_event_type not null,
  event_summary text not null default '',
  event_payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.marketing_pages (
  id uuid primary key default gen_random_uuid(),
  page_key public.marketing_page_key not null unique,
  status public.marketing_page_status not null default 'draft',
  title text not null,
  subtitle text not null default '',
  content_payload jsonb not null default '{}'::jsonb,
  seo_payload jsonb not null default '{}'::jsonb,
  updated_by uuid references public.profiles(id) on delete set null,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.lead_assignment_rules (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  rule_name text not null,
  source_filter text[] not null default '{}',
  industry_filter text[] not null default '{}',
  team_size_filter text[] not null default '{}',
  assign_to_user_id uuid not null references public.profiles(id) on delete cascade,
  priority integer not null default 100,
  is_active boolean not null default true,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

-- ---------- Indexes ----------
create index if not exists idx_inbound_leads_org_id on public.inbound_leads(org_id);
create index if not exists idx_inbound_leads_source on public.inbound_leads(lead_source);
create index if not exists idx_inbound_leads_status on public.inbound_leads(status);
create index if not exists idx_inbound_leads_owner on public.inbound_leads(assigned_owner_id);
create index if not exists idx_inbound_leads_created_at on public.inbound_leads(created_at desc);

create index if not exists idx_demo_requests_org_id on public.demo_requests(org_id);
create index if not exists idx_demo_requests_lead_id on public.demo_requests(lead_id);
create index if not exists idx_demo_requests_status on public.demo_requests(demo_status);
create index if not exists idx_demo_requests_owner on public.demo_requests(owner_id);
create index if not exists idx_demo_requests_requested_at on public.demo_requests(requested_at desc);

create index if not exists idx_trial_requests_org_id on public.trial_requests(org_id);
create index if not exists idx_trial_requests_lead_id on public.trial_requests(lead_id);
create index if not exists idx_trial_requests_status on public.trial_requests(request_status);
create index if not exists idx_trial_requests_target_org on public.trial_requests(target_org_id);
create index if not exists idx_trial_requests_requested_at on public.trial_requests(requested_at desc);

create index if not exists idx_trial_conversion_tracks_org_id on public.trial_conversion_tracks(org_id);
create index if not exists idx_trial_conversion_tracks_target_org on public.trial_conversion_tracks(target_org_id);
create index if not exists idx_trial_conversion_tracks_owner on public.trial_conversion_tracks(owner_id);
create index if not exists idx_trial_conversion_tracks_stage on public.trial_conversion_tracks(current_stage);
create index if not exists idx_trial_conversion_tracks_created_at on public.trial_conversion_tracks(created_at desc);

create index if not exists idx_conversion_events_org_id on public.conversion_events(org_id);
create index if not exists idx_conversion_events_target_org on public.conversion_events(target_org_id);
create index if not exists idx_conversion_events_lead_id on public.conversion_events(lead_id);
create index if not exists idx_conversion_events_type on public.conversion_events(event_type);
create index if not exists idx_conversion_events_created_at on public.conversion_events(created_at desc);

create index if not exists idx_marketing_pages_status on public.marketing_pages(status);
create index if not exists idx_marketing_pages_updated_at on public.marketing_pages(updated_at desc);

create index if not exists idx_lead_assignment_rules_org_id on public.lead_assignment_rules(org_id);
create index if not exists idx_lead_assignment_rules_active on public.lead_assignment_rules(is_active);
create index if not exists idx_lead_assignment_rules_priority on public.lead_assignment_rules(priority asc);
create index if not exists idx_lead_assignment_rules_created_at on public.lead_assignment_rules(created_at desc);

-- ---------- updated_at triggers ----------
do $$
begin
  if exists (select 1 from pg_proc where proname = 'set_updated_at') then
    drop trigger if exists trg_inbound_leads_updated_at on public.inbound_leads;
    create trigger trg_inbound_leads_updated_at
    before update on public.inbound_leads
    for each row execute function public.set_updated_at();

    drop trigger if exists trg_demo_requests_updated_at on public.demo_requests;
    create trigger trg_demo_requests_updated_at
    before update on public.demo_requests
    for each row execute function public.set_updated_at();

    drop trigger if exists trg_trial_requests_updated_at on public.trial_requests;
    create trigger trg_trial_requests_updated_at
    before update on public.trial_requests
    for each row execute function public.set_updated_at();

    drop trigger if exists trg_trial_conversion_tracks_updated_at on public.trial_conversion_tracks;
    create trigger trg_trial_conversion_tracks_updated_at
    before update on public.trial_conversion_tracks
    for each row execute function public.set_updated_at();

    drop trigger if exists trg_marketing_pages_updated_at on public.marketing_pages;
    create trigger trg_marketing_pages_updated_at
    before update on public.marketing_pages
    for each row execute function public.set_updated_at();

    drop trigger if exists trg_lead_assignment_rules_updated_at on public.lead_assignment_rules;
    create trigger trg_lead_assignment_rules_updated_at
    before update on public.lead_assignment_rules
    for each row execute function public.set_updated_at();
  end if;
end $$;

-- ---------- RLS ----------
alter table public.inbound_leads enable row level security;
alter table public.demo_requests enable row level security;
alter table public.trial_requests enable row level security;
alter table public.trial_conversion_tracks enable row level security;
alter table public.conversion_events enable row level security;
alter table public.marketing_pages enable row level security;
alter table public.lead_assignment_rules enable row level security;

drop policy if exists inbound_leads_select_policy on public.inbound_leads;
create policy inbound_leads_select_policy
on public.inbound_leads
for select
using (
  org_id = public.current_user_org_id()
  and (
    public.can_current_user_view_org_usage()
    or assigned_owner_id = auth.uid()
  )
);

drop policy if exists inbound_leads_admin_write_policy on public.inbound_leads;
create policy inbound_leads_admin_write_policy
on public.inbound_leads
for all
using (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
)
with check (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
);

drop policy if exists inbound_leads_owner_update_policy on public.inbound_leads;
create policy inbound_leads_owner_update_policy
on public.inbound_leads
for update
using (
  org_id = public.current_user_org_id()
  and assigned_owner_id = auth.uid()
)
with check (
  org_id = public.current_user_org_id()
  and assigned_owner_id = auth.uid()
);

drop policy if exists demo_requests_select_policy on public.demo_requests;
create policy demo_requests_select_policy
on public.demo_requests
for select
using (
  org_id = public.current_user_org_id()
  and (
    public.can_current_user_view_org_usage()
    or owner_id = auth.uid()
    or exists (
      select 1
      from public.inbound_leads l
      where l.id = demo_requests.lead_id
        and l.assigned_owner_id = auth.uid()
    )
  )
);

drop policy if exists demo_requests_admin_write_policy on public.demo_requests;
create policy demo_requests_admin_write_policy
on public.demo_requests
for all
using (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
)
with check (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
);

drop policy if exists demo_requests_owner_update_policy on public.demo_requests;
create policy demo_requests_owner_update_policy
on public.demo_requests
for update
using (
  org_id = public.current_user_org_id()
  and owner_id = auth.uid()
)
with check (
  org_id = public.current_user_org_id()
  and owner_id = auth.uid()
);

drop policy if exists trial_requests_select_policy on public.trial_requests;
create policy trial_requests_select_policy
on public.trial_requests
for select
using (
  org_id = public.current_user_org_id()
  and (
    public.can_current_user_view_org_usage()
    or exists (
      select 1
      from public.inbound_leads l
      where l.id = trial_requests.lead_id
        and l.assigned_owner_id = auth.uid()
    )
  )
);

drop policy if exists trial_requests_admin_write_policy on public.trial_requests;
create policy trial_requests_admin_write_policy
on public.trial_requests
for all
using (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
)
with check (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
);

drop policy if exists trial_conversion_tracks_select_policy on public.trial_conversion_tracks;
create policy trial_conversion_tracks_select_policy
on public.trial_conversion_tracks
for select
using (
  org_id = public.current_user_org_id()
  and (
    public.can_current_user_view_org_usage()
    or owner_id = auth.uid()
  )
);

drop policy if exists trial_conversion_tracks_admin_write_policy on public.trial_conversion_tracks;
create policy trial_conversion_tracks_admin_write_policy
on public.trial_conversion_tracks
for all
using (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
)
with check (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
);

drop policy if exists trial_conversion_tracks_owner_update_policy on public.trial_conversion_tracks;
create policy trial_conversion_tracks_owner_update_policy
on public.trial_conversion_tracks
for update
using (
  org_id = public.current_user_org_id()
  and owner_id = auth.uid()
)
with check (
  org_id = public.current_user_org_id()
  and owner_id = auth.uid()
);

drop policy if exists conversion_events_select_policy on public.conversion_events;
create policy conversion_events_select_policy
on public.conversion_events
for select
using (
  org_id = public.current_user_org_id()
  and (
    public.can_current_user_view_org_usage()
    or exists (
      select 1
      from public.inbound_leads l
      where l.id = conversion_events.lead_id
        and l.assigned_owner_id = auth.uid()
    )
  )
);

drop policy if exists conversion_events_admin_write_policy on public.conversion_events;
create policy conversion_events_admin_write_policy
on public.conversion_events
for all
using (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
)
with check (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
);

drop policy if exists marketing_pages_select_policy on public.marketing_pages;
create policy marketing_pages_select_policy
on public.marketing_pages
for select
using (true);

drop policy if exists marketing_pages_write_policy on public.marketing_pages;
create policy marketing_pages_write_policy
on public.marketing_pages
for all
using (public.is_current_user_org_owner_or_admin())
with check (public.is_current_user_org_owner_or_admin());

drop policy if exists lead_assignment_rules_select_policy on public.lead_assignment_rules;
create policy lead_assignment_rules_select_policy
on public.lead_assignment_rules
for select
using (
  org_id = public.current_user_org_id()
  and public.can_current_user_view_org_usage()
);

drop policy if exists lead_assignment_rules_write_policy on public.lead_assignment_rules;
create policy lead_assignment_rules_write_policy
on public.lead_assignment_rules
for all
using (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
)
with check (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
);

-- ---------- Seed marketing pages ----------
insert into public.marketing_pages (page_key, status, title, subtitle, content_payload, seo_payload)
values
  ('home', 'published', 'MOY 墨言', '销售团队的 AI 作战工作台', '{"hero_cta":["申请 Demo","开始试用"],"sections":["capture","today","deal_room","briefings","industry_templates","pwa","imports"]}'::jsonb, '{"keywords":["sales ai","crm","b2b","moy"]}'::jsonb),
  ('product', 'published', '产品能力', '从线索进入到成交复盘的全链路执行系统', '{"pillars":["capture","preparation","execution","deal_command","closed_loop","commercialization"]}'::jsonb, '{"keywords":["sales workspace","deal command","trial conversion"]}'::jsonb),
  ('industries', 'published', '行业方案', '六套行业模板快速落地', '{"templates":["generic","b2b_software","education_training","manufacturing","channel_sales","consulting_services"]}'::jsonb, '{"keywords":["industry template","sales playbook"]}'::jsonb),
  ('demo', 'published', '申请 Demo', '提交需求，24 小时内由顾问联系', '{"form":"request-demo"}'::jsonb, '{}'::jsonb),
  ('trial', 'published', '开始试用', '行业模板 + onboarding 快速起飞', '{"form":"start-trial"}'::jsonb, '{}'::jsonb),
  ('contact', 'published', '联系我们', '获取方案建议与迁移支持', '{"form":"contact"}'::jsonb, '{}'::jsonb)
on conflict (page_key) do update set
  status = excluded.status,
  title = excluded.title,
  subtitle = excluded.subtitle,
  content_payload = excluded.content_payload,
  seo_payload = excluded.seo_payload,
  updated_at = timezone('utc', now());
