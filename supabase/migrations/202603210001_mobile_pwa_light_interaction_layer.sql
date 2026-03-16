-- Phase 13: Mobile Web / PWA Light Interaction Layer

-- ---------- Enums ----------
do $$
begin
  if not exists (select 1 from pg_type where typname = 'mobile_draft_type') then
    create type public.mobile_draft_type as enum ('capture', 'outcome', 'email_draft', 'touchpoint_note');
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'mobile_draft_sync_status') then
    create type public.mobile_draft_sync_status as enum ('pending', 'synced', 'failed', 'discarded');
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'mobile_install_type') then
    create type public.mobile_install_type as enum ('browser', 'pwa');
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'offline_action_type') then
    create type public.offline_action_type as enum (
      'create_capture_draft',
      'create_outcome_draft',
      'save_email_draft',
      'quick_complete_task',
      'snooze_task'
    );
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'offline_action_queue_status') then
    create type public.offline_action_queue_status as enum ('queued', 'processing', 'done', 'failed');
  end if;
end $$;

-- ---------- AI scenario extension ----------
do $$
begin
  if exists (select 1 from pg_type where typname = 'ai_scenario') then
    begin
      alter type public.ai_scenario add value 'mobile_quick_capture_refine';
    exception when duplicate_object then
      null;
    end;
    begin
      alter type public.ai_scenario add value 'mobile_brief_compact_summary';
    exception when duplicate_object then
      null;
    end;
  end if;
end $$;

-- ---------- Tables ----------
create table if not exists public.mobile_draft_sync_jobs (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null,
  user_id uuid not null,
  draft_type public.mobile_draft_type not null,
  local_draft_id text not null,
  sync_status public.mobile_draft_sync_status not null default 'pending',
  target_entity_type text null,
  target_entity_id uuid null,
  summary text null,
  payload_snapshot jsonb not null default '{}'::jsonb,
  error_message text null,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.mobile_device_sessions (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null,
  user_id uuid not null,
  device_label text not null,
  install_type public.mobile_install_type not null default 'browser',
  last_seen_at timestamptz not null default timezone('utc', now()),
  app_version text null,
  push_capable boolean not null default false,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.offline_action_queue (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null,
  user_id uuid not null,
  action_type public.offline_action_type not null,
  action_payload jsonb not null default '{}'::jsonb,
  queue_status public.offline_action_queue_status not null default 'queued',
  target_entity_type text null,
  target_entity_id uuid null,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

-- ---------- Indexes ----------
create index if not exists idx_mobile_draft_sync_jobs_org_id on public.mobile_draft_sync_jobs(org_id);
create index if not exists idx_mobile_draft_sync_jobs_user_id on public.mobile_draft_sync_jobs(user_id);
create index if not exists idx_mobile_draft_sync_jobs_status on public.mobile_draft_sync_jobs(sync_status);
create index if not exists idx_mobile_draft_sync_jobs_created_at on public.mobile_draft_sync_jobs(created_at desc);

create index if not exists idx_mobile_device_sessions_org_id on public.mobile_device_sessions(org_id);
create index if not exists idx_mobile_device_sessions_user_id on public.mobile_device_sessions(user_id);
create index if not exists idx_mobile_device_sessions_last_seen_at on public.mobile_device_sessions(last_seen_at desc);
create index if not exists idx_mobile_device_sessions_created_at on public.mobile_device_sessions(created_at desc);

create index if not exists idx_offline_action_queue_org_id on public.offline_action_queue(org_id);
create index if not exists idx_offline_action_queue_user_id on public.offline_action_queue(user_id);
create index if not exists idx_offline_action_queue_status on public.offline_action_queue(queue_status);
create index if not exists idx_offline_action_queue_created_at on public.offline_action_queue(created_at desc);

-- ---------- updated_at trigger ----------
do $$
begin
  if exists (select 1 from pg_proc where proname = 'set_updated_at') then
    drop trigger if exists trg_mobile_draft_sync_jobs_updated_at on public.mobile_draft_sync_jobs;
    create trigger trg_mobile_draft_sync_jobs_updated_at
    before update on public.mobile_draft_sync_jobs
    for each row execute function public.set_updated_at();

    drop trigger if exists trg_mobile_device_sessions_updated_at on public.mobile_device_sessions;
    create trigger trg_mobile_device_sessions_updated_at
    before update on public.mobile_device_sessions
    for each row execute function public.set_updated_at();

    drop trigger if exists trg_offline_action_queue_updated_at on public.offline_action_queue;
    create trigger trg_offline_action_queue_updated_at
    before update on public.offline_action_queue
    for each row execute function public.set_updated_at();
  end if;
end $$;

-- ---------- RLS ----------
alter table public.mobile_draft_sync_jobs enable row level security;
alter table public.mobile_device_sessions enable row level security;
alter table public.offline_action_queue enable row level security;

drop policy if exists mobile_draft_sync_jobs_select_policy on public.mobile_draft_sync_jobs;
create policy mobile_draft_sync_jobs_select_policy on public.mobile_draft_sync_jobs
for select
using (
  org_id = public.current_user_org_id()
  and user_id = auth.uid()
);

drop policy if exists mobile_draft_sync_jobs_write_policy on public.mobile_draft_sync_jobs;
create policy mobile_draft_sync_jobs_write_policy on public.mobile_draft_sync_jobs
for all
using (
  org_id = public.current_user_org_id()
  and user_id = auth.uid()
)
with check (
  org_id = public.current_user_org_id()
  and user_id = auth.uid()
);

drop policy if exists mobile_device_sessions_select_policy on public.mobile_device_sessions;
create policy mobile_device_sessions_select_policy on public.mobile_device_sessions
for select
using (
  org_id = public.current_user_org_id()
  and user_id = auth.uid()
);

drop policy if exists mobile_device_sessions_write_policy on public.mobile_device_sessions;
create policy mobile_device_sessions_write_policy on public.mobile_device_sessions
for all
using (
  org_id = public.current_user_org_id()
  and user_id = auth.uid()
)
with check (
  org_id = public.current_user_org_id()
  and user_id = auth.uid()
);

drop policy if exists offline_action_queue_select_policy on public.offline_action_queue;
create policy offline_action_queue_select_policy on public.offline_action_queue
for select
using (
  org_id = public.current_user_org_id()
  and user_id = auth.uid()
);

drop policy if exists offline_action_queue_write_policy on public.offline_action_queue;
create policy offline_action_queue_write_policy on public.offline_action_queue
for all
using (
  org_id = public.current_user_org_id()
  and user_id = auth.uid()
)
with check (
  org_id = public.current_user_org_id()
  and user_id = auth.uid()
);
