-- Phase 12: Data Import & Migration Layer

-- ---------- Enums ----------
do $$
begin
  if not exists (select 1 from pg_type where typname = 'import_type') then
    create type public.import_type as enum ('customers', 'opportunities', 'followups', 'mixed');
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'import_source_type') then
    create type public.import_source_type as enum ('csv', 'xlsx', 'manual_table', 'demo_bootstrap');
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'import_job_status') then
    create type public.import_job_status as enum (
      'uploaded',
      'parsing',
      'mapping',
      'validating',
      'preview_ready',
      'importing',
      'completed',
      'failed',
      'cancelled'
    );
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'import_row_status') then
    create type public.import_row_status as enum (
      'pending',
      'valid',
      'invalid',
      'duplicate_candidate',
      'merge_candidate',
      'imported',
      'skipped',
      'failed'
    );
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'import_merge_resolution') then
    create type public.import_merge_resolution as enum ('create_new', 'merge_existing', 'skip');
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'import_entity_type') then
    create type public.import_entity_type as enum ('customer', 'opportunity', 'followup', 'mixed');
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'dedupe_resolution_status') then
    create type public.dedupe_resolution_status as enum ('pending', 'confirmed', 'ignored');
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'dedupe_resolution_action') then
    create type public.dedupe_resolution_action as enum ('create_new', 'merge', 'skip');
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'import_audit_event_type') then
    create type public.import_audit_event_type as enum (
      'uploaded',
      'parsed',
      'mapping_saved',
      'validation_run',
      'dedupe_reviewed',
      'import_started',
      'row_imported',
      'row_failed',
      'completed',
      'cancelled'
    );
  end if;
end $$;

-- ---------- AI scenario extension ----------
do $$
begin
  if exists (select 1 from pg_type where typname = 'ai_scenario') then
    begin
      alter type public.ai_scenario add value 'import_column_mapping_assist';
    exception when duplicate_object then
      null;
    end;
    begin
      alter type public.ai_scenario add value 'import_review_summary';
    exception when duplicate_object then
      null;
    end;
  end if;
end $$;

-- ---------- Tables ----------
create table if not exists public.import_jobs (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null,
  initiated_by uuid not null,
  import_type public.import_type not null,
  source_type public.import_source_type not null,
  file_name text not null,
  storage_path text null,
  job_status public.import_job_status not null default 'uploaded',
  total_rows integer not null default 0,
  valid_rows integer not null default 0,
  invalid_rows integer not null default 0,
  duplicate_rows integer not null default 0,
  imported_rows integer not null default 0,
  skipped_rows integer not null default 0,
  merged_rows integer not null default 0,
  error_rows integer not null default 0,
  summary text null,
  detail_snapshot jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.import_job_columns (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null,
  import_job_id uuid not null references public.import_jobs(id) on delete cascade,
  source_column_name text not null,
  source_column_index integer not null,
  detected_type text null,
  mapped_target_entity public.import_entity_type null,
  mapped_target_field text null,
  mapping_confidence numeric(5,4) null,
  normalization_rule jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.import_job_rows (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null,
  import_job_id uuid not null references public.import_jobs(id) on delete cascade,
  source_row_no integer not null,
  raw_payload jsonb not null default '{}'::jsonb,
  normalized_payload jsonb not null default '{}'::jsonb,
  row_status public.import_row_status not null default 'pending',
  validation_errors jsonb not null default '[]'::jsonb,
  duplicate_candidates jsonb not null default '[]'::jsonb,
  merge_resolution public.import_merge_resolution null,
  imported_entity_type public.import_entity_type null,
  imported_entity_id uuid null,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.import_templates (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null,
  template_name text not null,
  import_type public.import_type not null,
  column_mapping jsonb not null default '{}'::jsonb,
  normalization_config jsonb not null default '{}'::jsonb,
  is_default boolean not null default false,
  created_by uuid not null,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.dedupe_match_groups (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null,
  import_job_id uuid not null references public.import_jobs(id) on delete cascade,
  entity_type public.import_entity_type not null,
  source_row_ids jsonb not null default '[]'::jsonb,
  existing_entity_ids jsonb not null default '[]'::jsonb,
  match_reason text not null,
  confidence_score numeric(5,4) not null default 0,
  resolution_status public.dedupe_resolution_status not null default 'pending',
  resolution_action public.dedupe_resolution_action null,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.import_audit_events (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null,
  import_job_id uuid not null references public.import_jobs(id) on delete cascade,
  actor_user_id uuid null,
  event_type public.import_audit_event_type not null,
  event_summary text not null,
  event_payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now())
);

-- ---------- Indexes ----------
create index if not exists idx_import_jobs_org_id on public.import_jobs(org_id);
create index if not exists idx_import_jobs_status on public.import_jobs(job_status);
create index if not exists idx_import_jobs_type on public.import_jobs(import_type);
create index if not exists idx_import_jobs_created_at on public.import_jobs(created_at desc);

create index if not exists idx_import_job_columns_org_id on public.import_job_columns(org_id);
create index if not exists idx_import_job_columns_job_id on public.import_job_columns(import_job_id);

create index if not exists idx_import_job_rows_org_id on public.import_job_rows(org_id);
create index if not exists idx_import_job_rows_job_id on public.import_job_rows(import_job_id);
create index if not exists idx_import_job_rows_status on public.import_job_rows(row_status);
create index if not exists idx_import_job_rows_created_at on public.import_job_rows(created_at desc);

create index if not exists idx_import_templates_org_id on public.import_templates(org_id);
create index if not exists idx_import_templates_type on public.import_templates(import_type);
create index if not exists idx_import_templates_created_at on public.import_templates(created_at desc);

create index if not exists idx_dedupe_match_groups_org_id on public.dedupe_match_groups(org_id);
create index if not exists idx_dedupe_match_groups_job_id on public.dedupe_match_groups(import_job_id);
create index if not exists idx_dedupe_match_groups_status on public.dedupe_match_groups(resolution_status);

create index if not exists idx_import_audit_events_org_id on public.import_audit_events(org_id);
create index if not exists idx_import_audit_events_job_id on public.import_audit_events(import_job_id);
create index if not exists idx_import_audit_events_created_at on public.import_audit_events(created_at desc);

-- ---------- updated_at trigger ----------
do $$
begin
  if exists (select 1 from pg_proc where proname = 'set_updated_at') then
    drop trigger if exists trg_import_jobs_updated_at on public.import_jobs;
    create trigger trg_import_jobs_updated_at
    before update on public.import_jobs
    for each row execute function public.set_updated_at();

    drop trigger if exists trg_import_job_columns_updated_at on public.import_job_columns;
    create trigger trg_import_job_columns_updated_at
    before update on public.import_job_columns
    for each row execute function public.set_updated_at();

    drop trigger if exists trg_import_job_rows_updated_at on public.import_job_rows;
    create trigger trg_import_job_rows_updated_at
    before update on public.import_job_rows
    for each row execute function public.set_updated_at();

    drop trigger if exists trg_import_templates_updated_at on public.import_templates;
    create trigger trg_import_templates_updated_at
    before update on public.import_templates
    for each row execute function public.set_updated_at();

    drop trigger if exists trg_dedupe_match_groups_updated_at on public.dedupe_match_groups;
    create trigger trg_dedupe_match_groups_updated_at
    before update on public.dedupe_match_groups
    for each row execute function public.set_updated_at();
  end if;
end $$;

-- ---------- RLS ----------
alter table public.import_jobs enable row level security;
alter table public.import_job_columns enable row level security;
alter table public.import_job_rows enable row level security;
alter table public.import_templates enable row level security;
alter table public.dedupe_match_groups enable row level security;
alter table public.import_audit_events enable row level security;

drop policy if exists import_jobs_select_policy on public.import_jobs;
create policy import_jobs_select_policy on public.import_jobs
for select
using (
  org_id = public.current_user_org_id()
  and public.current_user_membership_role() in ('owner', 'admin', 'manager')
);

drop policy if exists import_jobs_write_policy on public.import_jobs;
create policy import_jobs_write_policy on public.import_jobs
for all
using (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
)
with check (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
);

drop policy if exists import_job_columns_select_policy on public.import_job_columns;
create policy import_job_columns_select_policy on public.import_job_columns
for select
using (
  org_id = public.current_user_org_id()
  and public.current_user_membership_role() in ('owner', 'admin', 'manager')
);

drop policy if exists import_job_columns_write_policy on public.import_job_columns;
create policy import_job_columns_write_policy on public.import_job_columns
for all
using (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
)
with check (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
);

drop policy if exists import_job_rows_select_policy on public.import_job_rows;
create policy import_job_rows_select_policy on public.import_job_rows
for select
using (
  org_id = public.current_user_org_id()
  and public.current_user_membership_role() in ('owner', 'admin', 'manager')
);

drop policy if exists import_job_rows_write_policy on public.import_job_rows;
create policy import_job_rows_write_policy on public.import_job_rows
for all
using (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
)
with check (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
);

drop policy if exists import_templates_select_policy on public.import_templates;
create policy import_templates_select_policy on public.import_templates
for select
using (
  org_id = public.current_user_org_id()
  and public.current_user_membership_role() in ('owner', 'admin', 'manager')
);

drop policy if exists import_templates_write_policy on public.import_templates;
create policy import_templates_write_policy on public.import_templates
for all
using (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
)
with check (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
);

drop policy if exists dedupe_match_groups_select_policy on public.dedupe_match_groups;
create policy dedupe_match_groups_select_policy on public.dedupe_match_groups
for select
using (
  org_id = public.current_user_org_id()
  and public.current_user_membership_role() in ('owner', 'admin', 'manager')
);

drop policy if exists dedupe_match_groups_write_policy on public.dedupe_match_groups;
create policy dedupe_match_groups_write_policy on public.dedupe_match_groups
for all
using (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
)
with check (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
);

drop policy if exists import_audit_events_select_policy on public.import_audit_events;
create policy import_audit_events_select_policy on public.import_audit_events
for select
using (
  org_id = public.current_user_org_id()
  and public.current_user_membership_role() in ('owner', 'admin', 'manager')
);

drop policy if exists import_audit_events_write_policy on public.import_audit_events;
create policy import_audit_events_write_policy on public.import_audit_events
for all
using (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
)
with check (
  org_id = public.current_user_org_id()
  and public.is_current_user_org_owner_or_admin()
);
