-- MOY phase-10: external touchpoint layer

do $$
begin
  if exists (select 1 from pg_type where typname = 'ai_scenario') then
    if not exists (
      select 1 from pg_enum where enumlabel = 'email_draft_generation' and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'email_draft_generation';
    end if;

    if not exists (
      select 1 from pg_enum where enumlabel = 'meeting_agenda_generation' and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'meeting_agenda_generation';
    end if;

    if not exists (
      select 1 from pg_enum where enumlabel = 'meeting_followup_summary' and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'meeting_followup_summary';
    end if;

    if not exists (
      select 1 from pg_enum where enumlabel = 'document_asset_summary' and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'document_asset_summary';
    end if;

    if not exists (
      select 1 from pg_enum where enumlabel = 'external_touchpoint_review' and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'external_touchpoint_review';
    end if;
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'external_provider_type') then
    create type public.external_provider_type as enum ('email', 'calendar', 'storage');
  end if;

  if not exists (select 1 from pg_type where typname = 'external_provider_name') then
    create type public.external_provider_name as enum (
      'gmail',
      'outlook',
      'google_calendar',
      'google_drive',
      'dropbox',
      'manual_upload'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'external_connection_status') then
    create type public.external_connection_status as enum ('connected', 'disconnected', 'error');
  end if;

  if not exists (select 1 from pg_type where typname = 'email_thread_status') then
    create type public.email_thread_status as enum ('open', 'waiting_reply', 'replied', 'archived');
  end if;

  if not exists (select 1 from pg_type where typname = 'email_sentiment_hint') then
    create type public.email_sentiment_hint as enum ('positive', 'neutral', 'negative', 'unknown');
  end if;

  if not exists (select 1 from pg_type where typname = 'email_message_direction') then
    create type public.email_message_direction as enum ('inbound', 'outbound', 'draft');
  end if;

  if not exists (select 1 from pg_type where typname = 'email_message_status') then
    create type public.email_message_status as enum ('draft', 'sent', 'received', 'failed');
  end if;

  if not exists (select 1 from pg_type where typname = 'email_message_source_type') then
    create type public.email_message_source_type as enum ('imported', 'manual', 'ai_generated');
  end if;

  if not exists (select 1 from pg_type where typname = 'calendar_event_type') then
    create type public.calendar_event_type as enum (
      'customer_meeting',
      'demo',
      'proposal_review',
      'internal_strategy',
      'manager_intervention'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'calendar_meeting_status') then
    create type public.calendar_meeting_status as enum ('scheduled', 'completed', 'cancelled', 'no_show');
  end if;

  if not exists (select 1 from pg_type where typname = 'document_asset_source_type') then
    create type public.document_asset_source_type as enum ('upload', 'email_attachment', 'generated', 'imported');
  end if;

  if not exists (select 1 from pg_type where typname = 'document_asset_type') then
    create type public.document_asset_type as enum (
      'proposal',
      'quote',
      'contract_draft',
      'meeting_note',
      'case_study',
      'product_material',
      'other'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'touchpoint_type') then
    create type public.touchpoint_type as enum ('email', 'meeting', 'document');
  end if;

  if not exists (select 1 from pg_type where typname = 'touchpoint_event_type') then
    create type public.touchpoint_event_type as enum (
      'email_received',
      'email_sent',
      'draft_created',
      'meeting_scheduled',
      'meeting_completed',
      'document_uploaded',
      'document_reviewed',
      'attachment_extracted'
    );
  end if;
end $$;

create table if not exists public.external_accounts (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  user_id uuid not null references public.profiles(id) on delete cascade,
  provider_type public.external_provider_type not null,
  provider_name public.external_provider_name not null,
  account_label text not null,
  connection_status public.external_connection_status not null default 'connected',
  metadata jsonb not null default '{}'::jsonb,
  connected_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.email_threads (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  owner_id uuid not null references public.profiles(id) on delete restrict,
  customer_id uuid references public.customers(id) on delete set null,
  opportunity_id uuid references public.opportunities(id) on delete set null,
  deal_room_id uuid references public.deal_rooms(id) on delete set null,
  external_account_id uuid references public.external_accounts(id) on delete set null,
  external_thread_ref text,
  subject text not null,
  participants jsonb not null default '[]'::jsonb,
  latest_message_at timestamptz,
  thread_status public.email_thread_status not null default 'open',
  sentiment_hint public.email_sentiment_hint not null default 'unknown',
  summary text not null default '',
  source_snapshot jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.email_messages (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  thread_id uuid not null references public.email_threads(id) on delete cascade,
  sender_user_id uuid references public.profiles(id) on delete set null,
  direction public.email_message_direction not null,
  external_message_ref text,
  message_subject text not null default '',
  message_body_text text not null default '',
  message_body_markdown text not null default '',
  sent_at timestamptz,
  received_at timestamptz,
  status public.email_message_status not null default 'draft',
  source_type public.email_message_source_type not null default 'manual',
  ai_run_id uuid references public.ai_runs(id) on delete set null,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.calendar_events (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  owner_id uuid not null references public.profiles(id) on delete restrict,
  customer_id uuid references public.customers(id) on delete set null,
  opportunity_id uuid references public.opportunities(id) on delete set null,
  deal_room_id uuid references public.deal_rooms(id) on delete set null,
  external_account_id uuid references public.external_accounts(id) on delete set null,
  external_event_ref text,
  event_type public.calendar_event_type not null,
  title text not null,
  description text not null default '',
  attendees jsonb not null default '[]'::jsonb,
  start_at timestamptz not null,
  end_at timestamptz not null,
  meeting_status public.calendar_meeting_status not null default 'scheduled',
  agenda_summary text not null default '',
  notes_summary text not null default '',
  source_snapshot jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  constraint calendar_events_time_check check (end_at >= start_at)
);

create table if not exists public.document_assets (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  owner_id uuid not null references public.profiles(id) on delete restrict,
  customer_id uuid references public.customers(id) on delete set null,
  opportunity_id uuid references public.opportunities(id) on delete set null,
  deal_room_id uuid references public.deal_rooms(id) on delete set null,
  source_type public.document_asset_source_type not null default 'upload',
  document_type public.document_asset_type not null default 'other',
  title text not null,
  file_name text not null,
  mime_type text not null default 'text/plain',
  storage_path text,
  extracted_text text not null default '',
  summary text not null default '',
  tags jsonb not null default '[]'::jsonb,
  linked_prep_card_id uuid references public.prep_cards(id) on delete set null,
  linked_draft_id uuid references public.content_drafts(id) on delete set null,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.external_touchpoint_events (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  owner_id uuid references public.profiles(id) on delete set null,
  customer_id uuid references public.customers(id) on delete set null,
  opportunity_id uuid references public.opportunities(id) on delete set null,
  deal_room_id uuid references public.deal_rooms(id) on delete set null,
  touchpoint_type public.touchpoint_type not null,
  event_type public.touchpoint_event_type not null,
  related_ref_type text,
  related_ref_id uuid,
  event_summary text not null default '',
  event_payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now())
);

create index if not exists idx_external_accounts_org_id on public.external_accounts(org_id);
create index if not exists idx_external_accounts_user_id on public.external_accounts(user_id);
create index if not exists idx_external_accounts_provider_type on public.external_accounts(provider_type);
create index if not exists idx_external_accounts_status on public.external_accounts(connection_status);
create index if not exists idx_external_accounts_created_at on public.external_accounts(connected_at desc);

create index if not exists idx_email_threads_org_id on public.email_threads(org_id);
create index if not exists idx_email_threads_owner_id on public.email_threads(owner_id);
create index if not exists idx_email_threads_customer_id on public.email_threads(customer_id);
create index if not exists idx_email_threads_opportunity_id on public.email_threads(opportunity_id);
create index if not exists idx_email_threads_deal_room_id on public.email_threads(deal_room_id);
create index if not exists idx_email_threads_external_account on public.email_threads(external_account_id);
create index if not exists idx_email_threads_latest_message on public.email_threads(latest_message_at desc);
create index if not exists idx_email_threads_status on public.email_threads(thread_status);
create index if not exists idx_email_threads_updated_at on public.email_threads(updated_at desc);

create index if not exists idx_email_messages_org_id on public.email_messages(org_id);
create index if not exists idx_email_messages_thread_id on public.email_messages(thread_id);
create index if not exists idx_email_messages_sender on public.email_messages(sender_user_id);
create index if not exists idx_email_messages_direction on public.email_messages(direction);
create index if not exists idx_email_messages_status on public.email_messages(status);
create index if not exists idx_email_messages_sent_at on public.email_messages(sent_at desc);
create index if not exists idx_email_messages_received_at on public.email_messages(received_at desc);
create index if not exists idx_email_messages_created_at on public.email_messages(created_at desc);

create index if not exists idx_calendar_events_org_id on public.calendar_events(org_id);
create index if not exists idx_calendar_events_owner_id on public.calendar_events(owner_id);
create index if not exists idx_calendar_events_customer_id on public.calendar_events(customer_id);
create index if not exists idx_calendar_events_opportunity_id on public.calendar_events(opportunity_id);
create index if not exists idx_calendar_events_deal_room_id on public.calendar_events(deal_room_id);
create index if not exists idx_calendar_events_external_account on public.calendar_events(external_account_id);
create index if not exists idx_calendar_events_start_at on public.calendar_events(start_at);
create index if not exists idx_calendar_events_status on public.calendar_events(meeting_status);
create index if not exists idx_calendar_events_created_at on public.calendar_events(created_at desc);

create index if not exists idx_document_assets_org_id on public.document_assets(org_id);
create index if not exists idx_document_assets_owner_id on public.document_assets(owner_id);
create index if not exists idx_document_assets_customer_id on public.document_assets(customer_id);
create index if not exists idx_document_assets_opportunity_id on public.document_assets(opportunity_id);
create index if not exists idx_document_assets_deal_room_id on public.document_assets(deal_room_id);
create index if not exists idx_document_assets_document_type on public.document_assets(document_type);
create index if not exists idx_document_assets_source_type on public.document_assets(source_type);
create index if not exists idx_document_assets_created_at on public.document_assets(created_at desc);

create index if not exists idx_touchpoint_events_org_id on public.external_touchpoint_events(org_id);
create index if not exists idx_touchpoint_events_owner_id on public.external_touchpoint_events(owner_id);
create index if not exists idx_touchpoint_events_customer_id on public.external_touchpoint_events(customer_id);
create index if not exists idx_touchpoint_events_opportunity_id on public.external_touchpoint_events(opportunity_id);
create index if not exists idx_touchpoint_events_deal_room_id on public.external_touchpoint_events(deal_room_id);
create index if not exists idx_touchpoint_events_type on public.external_touchpoint_events(touchpoint_type);
create index if not exists idx_touchpoint_events_event_type on public.external_touchpoint_events(event_type);
create index if not exists idx_touchpoint_events_created_at on public.external_touchpoint_events(created_at desc);

drop trigger if exists set_external_accounts_updated_at on public.external_accounts;
create trigger set_external_accounts_updated_at
before update on public.external_accounts
for each row execute procedure public.set_updated_at();

drop trigger if exists set_email_threads_updated_at on public.email_threads;
create trigger set_email_threads_updated_at
before update on public.email_threads
for each row execute procedure public.set_updated_at();

drop trigger if exists set_email_messages_updated_at on public.email_messages;
create trigger set_email_messages_updated_at
before update on public.email_messages
for each row execute procedure public.set_updated_at();

drop trigger if exists set_calendar_events_updated_at on public.calendar_events;
create trigger set_calendar_events_updated_at
before update on public.calendar_events
for each row execute procedure public.set_updated_at();

drop trigger if exists set_document_assets_updated_at on public.document_assets;
create trigger set_document_assets_updated_at
before update on public.document_assets
for each row execute procedure public.set_updated_at();

create or replace function public.can_access_touchpoint_customer(p_customer_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select
    case
      when p_customer_id is null then true
      else exists (
        select 1
        from public.customers c
        where c.id = p_customer_id
          and c.org_id = public.current_user_org_id()
          and (
            public.is_current_user_manager()
            or c.owner_id = auth.uid()
          )
      )
    end
$$;

alter table public.external_accounts enable row level security;
alter table public.email_threads enable row level security;
alter table public.email_messages enable row level security;
alter table public.calendar_events enable row level security;
alter table public.document_assets enable row level security;
alter table public.external_touchpoint_events enable row level security;

drop policy if exists external_accounts_select_policy on public.external_accounts;
create policy external_accounts_select_policy
on public.external_accounts
for select
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or user_id = auth.uid()
  )
);

drop policy if exists external_accounts_insert_policy on public.external_accounts;
create policy external_accounts_insert_policy
on public.external_accounts
for insert
with check (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or user_id = auth.uid()
  )
);

drop policy if exists external_accounts_update_policy on public.external_accounts;
create policy external_accounts_update_policy
on public.external_accounts
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

drop policy if exists email_threads_select_policy on public.email_threads;
create policy email_threads_select_policy
on public.email_threads
for select
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or owner_id = auth.uid()
    or public.can_access_touchpoint_customer(customer_id)
    or (deal_room_id is not null and public.can_access_deal_room(deal_room_id))
  )
);

drop policy if exists email_threads_insert_policy on public.email_threads;
create policy email_threads_insert_policy
on public.email_threads
for insert
with check (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or owner_id = auth.uid()
  )
  and public.can_access_touchpoint_customer(customer_id)
  and (
    deal_room_id is null
    or public.can_access_deal_room(deal_room_id)
  )
);

drop policy if exists email_threads_update_policy on public.email_threads;
create policy email_threads_update_policy
on public.email_threads
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

drop policy if exists email_messages_select_policy on public.email_messages;
create policy email_messages_select_policy
on public.email_messages
for select
using (
  org_id = public.current_user_org_id()
  and exists (
    select 1
    from public.email_threads t
    where t.id = email_messages.thread_id
      and t.org_id = public.current_user_org_id()
      and (
        public.is_current_user_manager()
        or t.owner_id = auth.uid()
        or public.can_access_touchpoint_customer(t.customer_id)
        or (t.deal_room_id is not null and public.can_access_deal_room(t.deal_room_id))
      )
  )
);

drop policy if exists email_messages_insert_policy on public.email_messages;
create policy email_messages_insert_policy
on public.email_messages
for insert
with check (
  org_id = public.current_user_org_id()
  and (
    sender_user_id is null
    or sender_user_id = auth.uid()
    or public.is_current_user_manager()
  )
  and exists (
    select 1
    from public.email_threads t
    where t.id = email_messages.thread_id
      and t.org_id = public.current_user_org_id()
      and (
        public.is_current_user_manager()
        or t.owner_id = auth.uid()
        or public.can_access_touchpoint_customer(t.customer_id)
        or (t.deal_room_id is not null and public.can_access_deal_room(t.deal_room_id))
      )
  )
);

drop policy if exists email_messages_update_policy on public.email_messages;
create policy email_messages_update_policy
on public.email_messages
for update
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or sender_user_id = auth.uid()
    or sender_user_id is null
  )
)
with check (
  org_id = public.current_user_org_id()
);

drop policy if exists calendar_events_select_policy on public.calendar_events;
create policy calendar_events_select_policy
on public.calendar_events
for select
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or owner_id = auth.uid()
    or public.can_access_touchpoint_customer(customer_id)
    or (deal_room_id is not null and public.can_access_deal_room(deal_room_id))
  )
);

drop policy if exists calendar_events_insert_policy on public.calendar_events;
create policy calendar_events_insert_policy
on public.calendar_events
for insert
with check (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or owner_id = auth.uid()
  )
  and public.can_access_touchpoint_customer(customer_id)
  and (
    deal_room_id is null
    or public.can_access_deal_room(deal_room_id)
  )
);

drop policy if exists calendar_events_update_policy on public.calendar_events;
create policy calendar_events_update_policy
on public.calendar_events
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

drop policy if exists document_assets_select_policy on public.document_assets;
create policy document_assets_select_policy
on public.document_assets
for select
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or owner_id = auth.uid()
    or public.can_access_touchpoint_customer(customer_id)
    or (deal_room_id is not null and public.can_access_deal_room(deal_room_id))
  )
);

drop policy if exists document_assets_insert_policy on public.document_assets;
create policy document_assets_insert_policy
on public.document_assets
for insert
with check (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or owner_id = auth.uid()
  )
  and public.can_access_touchpoint_customer(customer_id)
  and (
    deal_room_id is null
    or public.can_access_deal_room(deal_room_id)
  )
);

drop policy if exists document_assets_update_policy on public.document_assets;
create policy document_assets_update_policy
on public.document_assets
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

drop policy if exists external_touchpoint_events_select_policy on public.external_touchpoint_events;
create policy external_touchpoint_events_select_policy
on public.external_touchpoint_events
for select
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or owner_id = auth.uid()
    or owner_id is null
    or public.can_access_touchpoint_customer(customer_id)
    or (deal_room_id is not null and public.can_access_deal_room(deal_room_id))
  )
);

drop policy if exists external_touchpoint_events_insert_policy on public.external_touchpoint_events;
create policy external_touchpoint_events_insert_policy
on public.external_touchpoint_events
for insert
with check (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or owner_id = auth.uid()
    or owner_id is null
  )
  and public.can_access_touchpoint_customer(customer_id)
  and (
    deal_room_id is null
    or public.can_access_deal_room(deal_room_id)
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
  'Email Draft Generation (DeepSeek)',
  'v10-deepseek',
  'email_draft_generation'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Generate professional and editable sales email drafts.',
  'Use only provided business facts. Return strict JSON. Never promise price/contract/delivery commitments that are not explicitly confirmed.',
  '{"type":"object","required":["subject","opening","body","cta","caution_notes"]}'::jsonb,
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
  'Meeting Agenda Generation (DeepSeek)',
  'v10-deepseek',
  'meeting_agenda_generation'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Generate practical customer meeting agendas for sales execution.',
  'Use only provided facts and return strict JSON. Keep agenda concise, executable and risk-aware.',
  '{"type":"object","required":["meeting_goal","agenda_points","must_cover","risk_notes","expected_next_step"]}'::jsonb,
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
  'Meeting Followup Summary (DeepSeek)',
  'v10-deepseek',
  'meeting_followup_summary'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Summarize meeting results and propose next actions.',
  'Use only provided facts and return strict JSON. Explicitly mark uncertainty when notes are insufficient.',
  '{"type":"object","required":["meeting_summary","decisions_made","next_actions","followup_message_draft_hint","checkpoint_update_hint"]}'::jsonb,
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
  'Document Asset Summary (DeepSeek)',
  'v10-deepseek',
  'document_asset_summary'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Summarize uploaded sales documents and identify action implications.',
  'Use only extracted document text and known context. Return strict JSON and avoid legal or contract claims beyond provided facts.',
  '{"type":"object","required":["document_type_guess","summary","risk_flags","recommended_actions","related_checkpoint_hint"]}'::jsonb,
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
  'External Touchpoint Review (DeepSeek)',
  'v10-deepseek',
  'external_touchpoint_review'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Review external touchpoint progress for sales deals.',
  'Use only observed email/meeting/document events and deal context. Return strict JSON with explicit recommended next moves.',
  '{"type":"object","required":["external_progress_assessment","stalled_touchpoints","missing_touchpoints","recommended_next_moves"]}'::jsonb,
  true
from public.organizations o
on conflict (org_id, scenario, version) do nothing;

