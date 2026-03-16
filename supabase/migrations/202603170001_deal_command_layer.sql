-- MOY phase-9: collaboration & deal command layer

do $$
begin
  if exists (select 1 from pg_type where typname = 'ai_scenario') then
    if not exists (
      select 1
      from pg_enum
      where enumlabel = 'deal_room_command_summary'
        and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'deal_room_command_summary';
    end if;

    if not exists (
      select 1
      from pg_enum
      where enumlabel = 'thread_summary'
        and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'thread_summary';
    end if;

    if not exists (
      select 1
      from pg_enum
      where enumlabel = 'decision_support'
        and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'decision_support';
    end if;

    if not exists (
      select 1
      from pg_enum
      where enumlabel = 'intervention_recommendation'
        and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'intervention_recommendation';
    end if;

    if not exists (
      select 1
      from pg_enum
      where enumlabel = 'deal_playbook_mapping'
        and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'deal_playbook_mapping';
    end if;
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'deal_room_status') then
    create type public.deal_room_status as enum (
      'active',
      'watchlist',
      'escalated',
      'blocked',
      'won',
      'lost',
      'archived'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'deal_room_priority_band') then
    create type public.deal_room_priority_band as enum (
      'normal',
      'important',
      'strategic',
      'critical'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'collaboration_thread_type') then
    create type public.collaboration_thread_type as enum (
      'strategy',
      'blocker',
      'quote_review',
      'next_step',
      'risk_discussion',
      'manager_intervention',
      'playbook_application'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'collaboration_thread_status') then
    create type public.collaboration_thread_status as enum ('open', 'resolved', 'archived');
  end if;

  if not exists (select 1 from pg_type where typname = 'collaboration_message_type') then
    create type public.collaboration_message_type as enum (
      'comment',
      'decision_note',
      'ai_summary',
      'system_event'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'decision_type') then
    create type public.decision_type as enum (
      'quote_strategy',
      'discount_exception',
      'trial_offer',
      'manager_intervention',
      'resource_support',
      'contract_risk',
      'stage_commitment'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'decision_status') then
    create type public.decision_status as enum (
      'proposed',
      'approved',
      'rejected',
      'superseded',
      'completed'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'deal_participant_role') then
    create type public.deal_participant_role as enum (
      'owner',
      'collaborator',
      'manager',
      'reviewer',
      'observer'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'deal_checkpoint_type') then
    create type public.deal_checkpoint_type as enum (
      'qualification',
      'need_confirmed',
      'proposal_sent',
      'quote_sent',
      'decision_maker_confirmed',
      'budget_confirmed',
      'trial_started',
      'contract_review',
      'closing'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'deal_checkpoint_status') then
    create type public.deal_checkpoint_status as enum ('pending', 'completed', 'blocked', 'skipped');
  end if;

  if not exists (select 1 from pg_type where typname = 'intervention_request_type') then
    create type public.intervention_request_type as enum (
      'manager_join_call',
      'pricing_support',
      'proposal_review',
      'objection_help',
      'contract_support',
      'executive_escalation'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'intervention_priority_band') then
    create type public.intervention_priority_band as enum ('low', 'medium', 'high', 'critical');
  end if;

  if not exists (select 1 from pg_type where typname = 'intervention_request_status') then
    create type public.intervention_request_status as enum ('open', 'accepted', 'declined', 'completed', 'expired');
  end if;
end $$;

create table if not exists public.deal_rooms (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  customer_id uuid not null references public.customers(id) on delete cascade,
  opportunity_id uuid references public.opportunities(id) on delete set null,
  owner_id uuid not null references public.profiles(id) on delete restrict,
  room_status public.deal_room_status not null default 'active',
  priority_band public.deal_room_priority_band not null default 'important',
  title text not null,
  command_summary text not null default '',
  current_goal text not null default '',
  current_blockers jsonb not null default '[]'::jsonb,
  next_milestone text,
  next_milestone_due_at timestamptz,
  manager_attention_needed boolean not null default false,
  source_snapshot jsonb not null default '{}'::jsonb,
  created_by uuid not null references public.profiles(id) on delete restrict,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.collaboration_threads (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  deal_room_id uuid not null references public.deal_rooms(id) on delete cascade,
  thread_type public.collaboration_thread_type not null,
  title text not null,
  status public.collaboration_thread_status not null default 'open',
  summary text not null default '',
  created_by uuid not null references public.profiles(id) on delete restrict,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.collaboration_messages (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  thread_id uuid not null references public.collaboration_threads(id) on delete cascade,
  author_user_id uuid not null references public.profiles(id) on delete restrict,
  message_type public.collaboration_message_type not null default 'comment',
  body_markdown text not null default '',
  mentions jsonb not null default '[]'::jsonb,
  source_ref_type text,
  source_ref_id uuid,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.decision_records (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  deal_room_id uuid not null references public.deal_rooms(id) on delete cascade,
  customer_id uuid not null references public.customers(id) on delete cascade,
  opportunity_id uuid references public.opportunities(id) on delete set null,
  decision_type public.decision_type not null,
  status public.decision_status not null default 'proposed',
  title text not null,
  context_summary text not null default '',
  options_considered jsonb not null default '[]'::jsonb,
  recommended_option text,
  decision_reason text,
  decided_by uuid references public.profiles(id) on delete set null,
  requested_by uuid not null references public.profiles(id) on delete restrict,
  due_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  constraint decision_records_completed_at_check check (
    (status = 'completed' and completed_at is not null)
    or status <> 'completed'
  )
);

create table if not exists public.deal_participants (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  deal_room_id uuid not null references public.deal_rooms(id) on delete cascade,
  user_id uuid not null references public.profiles(id) on delete cascade,
  role_in_room public.deal_participant_role not null default 'collaborator',
  is_active boolean not null default true,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  constraint deal_participants_unique unique (deal_room_id, user_id)
);

create table if not exists public.deal_checkpoints (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  deal_room_id uuid not null references public.deal_rooms(id) on delete cascade,
  checkpoint_type public.deal_checkpoint_type not null,
  status public.deal_checkpoint_status not null default 'pending',
  title text not null,
  description text not null default '',
  due_at timestamptz,
  completed_at timestamptz,
  owner_id uuid references public.profiles(id) on delete set null,
  evidence_snapshot jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  constraint deal_checkpoints_unique unique (deal_room_id, checkpoint_type),
  constraint deal_checkpoints_completed_at_check check (
    (status = 'completed' and completed_at is not null)
    or status <> 'completed'
  )
);

create table if not exists public.intervention_requests (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  deal_room_id uuid not null references public.deal_rooms(id) on delete cascade,
  requested_by uuid not null references public.profiles(id) on delete restrict,
  target_user_id uuid references public.profiles(id) on delete set null,
  request_type public.intervention_request_type not null,
  priority_band public.intervention_priority_band not null default 'medium',
  status public.intervention_request_status not null default 'open',
  request_summary text not null,
  context_snapshot jsonb not null default '{}'::jsonb,
  due_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  constraint intervention_requests_completed_at_check check (
    (status = 'completed' and completed_at is not null)
    or status <> 'completed'
  )
);

create index if not exists idx_deal_rooms_org_id on public.deal_rooms(org_id);
create index if not exists idx_deal_rooms_customer_id on public.deal_rooms(customer_id);
create index if not exists idx_deal_rooms_opportunity_id on public.deal_rooms(opportunity_id);
create index if not exists idx_deal_rooms_owner_id on public.deal_rooms(owner_id);
create index if not exists idx_deal_rooms_room_status on public.deal_rooms(room_status);
create index if not exists idx_deal_rooms_priority_band on public.deal_rooms(priority_band);
create index if not exists idx_deal_rooms_attention on public.deal_rooms(manager_attention_needed);
create index if not exists idx_deal_rooms_next_milestone_due on public.deal_rooms(next_milestone_due_at);
create index if not exists idx_deal_rooms_created_at on public.deal_rooms(created_at desc);

create index if not exists idx_collaboration_threads_org_id on public.collaboration_threads(org_id);
create index if not exists idx_collaboration_threads_deal_room_id on public.collaboration_threads(deal_room_id);
create index if not exists idx_collaboration_threads_thread_type on public.collaboration_threads(thread_type);
create index if not exists idx_collaboration_threads_status on public.collaboration_threads(status);
create index if not exists idx_collaboration_threads_created_at on public.collaboration_threads(created_at desc);

create index if not exists idx_collaboration_messages_org_id on public.collaboration_messages(org_id);
create index if not exists idx_collaboration_messages_thread_id on public.collaboration_messages(thread_id);
create index if not exists idx_collaboration_messages_author on public.collaboration_messages(author_user_id);
create index if not exists idx_collaboration_messages_message_type on public.collaboration_messages(message_type);
create index if not exists idx_collaboration_messages_created_at on public.collaboration_messages(created_at desc);

create index if not exists idx_decision_records_org_id on public.decision_records(org_id);
create index if not exists idx_decision_records_deal_room_id on public.decision_records(deal_room_id);
create index if not exists idx_decision_records_customer_id on public.decision_records(customer_id);
create index if not exists idx_decision_records_opportunity_id on public.decision_records(opportunity_id);
create index if not exists idx_decision_records_decision_type on public.decision_records(decision_type);
create index if not exists idx_decision_records_status on public.decision_records(status);
create index if not exists idx_decision_records_due_at on public.decision_records(due_at);
create index if not exists idx_decision_records_created_at on public.decision_records(created_at desc);

create index if not exists idx_deal_participants_org_id on public.deal_participants(org_id);
create index if not exists idx_deal_participants_deal_room_id on public.deal_participants(deal_room_id);
create index if not exists idx_deal_participants_user_id on public.deal_participants(user_id);
create index if not exists idx_deal_participants_is_active on public.deal_participants(is_active);
create index if not exists idx_deal_participants_created_at on public.deal_participants(created_at desc);

create index if not exists idx_deal_checkpoints_org_id on public.deal_checkpoints(org_id);
create index if not exists idx_deal_checkpoints_deal_room_id on public.deal_checkpoints(deal_room_id);
create index if not exists idx_deal_checkpoints_type on public.deal_checkpoints(checkpoint_type);
create index if not exists idx_deal_checkpoints_status on public.deal_checkpoints(status);
create index if not exists idx_deal_checkpoints_owner on public.deal_checkpoints(owner_id);
create index if not exists idx_deal_checkpoints_due_at on public.deal_checkpoints(due_at);
create index if not exists idx_deal_checkpoints_created_at on public.deal_checkpoints(created_at desc);

create index if not exists idx_intervention_requests_org_id on public.intervention_requests(org_id);
create index if not exists idx_intervention_requests_deal_room_id on public.intervention_requests(deal_room_id);
create index if not exists idx_intervention_requests_requested_by on public.intervention_requests(requested_by);
create index if not exists idx_intervention_requests_target_user_id on public.intervention_requests(target_user_id);
create index if not exists idx_intervention_requests_request_type on public.intervention_requests(request_type);
create index if not exists idx_intervention_requests_status on public.intervention_requests(status);
create index if not exists idx_intervention_requests_priority on public.intervention_requests(priority_band);
create index if not exists idx_intervention_requests_due_at on public.intervention_requests(due_at);
create index if not exists idx_intervention_requests_created_at on public.intervention_requests(created_at desc);

drop trigger if exists set_deal_rooms_updated_at on public.deal_rooms;
create trigger set_deal_rooms_updated_at
before update on public.deal_rooms
for each row execute procedure public.set_updated_at();

drop trigger if exists set_collaboration_threads_updated_at on public.collaboration_threads;
create trigger set_collaboration_threads_updated_at
before update on public.collaboration_threads
for each row execute procedure public.set_updated_at();

drop trigger if exists set_collaboration_messages_updated_at on public.collaboration_messages;
create trigger set_collaboration_messages_updated_at
before update on public.collaboration_messages
for each row execute procedure public.set_updated_at();

drop trigger if exists set_decision_records_updated_at on public.decision_records;
create trigger set_decision_records_updated_at
before update on public.decision_records
for each row execute procedure public.set_updated_at();

drop trigger if exists set_deal_participants_updated_at on public.deal_participants;
create trigger set_deal_participants_updated_at
before update on public.deal_participants
for each row execute procedure public.set_updated_at();

drop trigger if exists set_deal_checkpoints_updated_at on public.deal_checkpoints;
create trigger set_deal_checkpoints_updated_at
before update on public.deal_checkpoints
for each row execute procedure public.set_updated_at();

drop trigger if exists set_intervention_requests_updated_at on public.intervention_requests;
create trigger set_intervention_requests_updated_at
before update on public.intervention_requests
for each row execute procedure public.set_updated_at();

create or replace function public.can_access_deal_room(p_deal_room_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.deal_rooms dr
    where dr.id = p_deal_room_id
      and dr.org_id = public.current_user_org_id()
      and (
        public.is_current_user_manager()
        or dr.owner_id = auth.uid()
        or dr.created_by = auth.uid()
        or exists (
          select 1
          from public.deal_participants dp
          where dp.deal_room_id = dr.id
            and dp.user_id = auth.uid()
            and dp.is_active = true
            and dp.org_id = public.current_user_org_id()
        )
      )
  )
$$;

alter table public.deal_rooms enable row level security;
alter table public.collaboration_threads enable row level security;
alter table public.collaboration_messages enable row level security;
alter table public.decision_records enable row level security;
alter table public.deal_participants enable row level security;
alter table public.deal_checkpoints enable row level security;
alter table public.intervention_requests enable row level security;

drop policy if exists deal_rooms_select_policy on public.deal_rooms;
create policy deal_rooms_select_policy
on public.deal_rooms
for select
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or owner_id = auth.uid()
    or created_by = auth.uid()
    or exists (
      select 1
      from public.deal_participants dp
      where dp.deal_room_id = deal_rooms.id
        and dp.user_id = auth.uid()
        and dp.is_active = true
        and dp.org_id = public.current_user_org_id()
    )
  )
);

drop policy if exists deal_rooms_insert_policy on public.deal_rooms;
create policy deal_rooms_insert_policy
on public.deal_rooms
for insert
with check (
  org_id = public.current_user_org_id()
  and created_by = auth.uid()
  and (
    public.is_current_user_manager()
    or owner_id = auth.uid()
  )
);

drop policy if exists deal_rooms_update_policy on public.deal_rooms;
create policy deal_rooms_update_policy
on public.deal_rooms
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

drop policy if exists deal_rooms_delete_policy on public.deal_rooms;
create policy deal_rooms_delete_policy
on public.deal_rooms
for delete
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or owner_id = auth.uid()
    or created_by = auth.uid()
  )
);

drop policy if exists collaboration_threads_select_policy on public.collaboration_threads;
create policy collaboration_threads_select_policy
on public.collaboration_threads
for select
using (
  org_id = public.current_user_org_id()
  and public.can_access_deal_room(deal_room_id)
);

drop policy if exists collaboration_threads_insert_policy on public.collaboration_threads;
create policy collaboration_threads_insert_policy
on public.collaboration_threads
for insert
with check (
  org_id = public.current_user_org_id()
  and created_by = auth.uid()
  and public.can_access_deal_room(deal_room_id)
);

drop policy if exists collaboration_threads_update_policy on public.collaboration_threads;
create policy collaboration_threads_update_policy
on public.collaboration_threads
for update
using (
  org_id = public.current_user_org_id()
  and public.can_access_deal_room(deal_room_id)
  and (
    public.is_current_user_manager()
    or created_by = auth.uid()
    or exists (
      select 1
      from public.deal_rooms dr
      where dr.id = collaboration_threads.deal_room_id
        and dr.org_id = public.current_user_org_id()
        and (dr.owner_id = auth.uid() or dr.created_by = auth.uid())
    )
  )
)
with check (
  org_id = public.current_user_org_id()
  and public.can_access_deal_room(deal_room_id)
);

drop policy if exists collaboration_messages_select_policy on public.collaboration_messages;
create policy collaboration_messages_select_policy
on public.collaboration_messages
for select
using (
  org_id = public.current_user_org_id()
  and exists (
    select 1
    from public.collaboration_threads ct
    where ct.id = collaboration_messages.thread_id
      and ct.org_id = public.current_user_org_id()
      and public.can_access_deal_room(ct.deal_room_id)
  )
);

drop policy if exists collaboration_messages_insert_policy on public.collaboration_messages;
create policy collaboration_messages_insert_policy
on public.collaboration_messages
for insert
with check (
  org_id = public.current_user_org_id()
  and author_user_id = auth.uid()
  and exists (
    select 1
    from public.collaboration_threads ct
    where ct.id = collaboration_messages.thread_id
      and ct.org_id = public.current_user_org_id()
      and public.can_access_deal_room(ct.deal_room_id)
  )
);

drop policy if exists collaboration_messages_update_policy on public.collaboration_messages;
create policy collaboration_messages_update_policy
on public.collaboration_messages
for update
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or author_user_id = auth.uid()
  )
)
with check (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or author_user_id = auth.uid()
  )
);

drop policy if exists decision_records_select_policy on public.decision_records;
create policy decision_records_select_policy
on public.decision_records
for select
using (
  org_id = public.current_user_org_id()
  and public.can_access_deal_room(deal_room_id)
);

drop policy if exists decision_records_insert_policy on public.decision_records;
create policy decision_records_insert_policy
on public.decision_records
for insert
with check (
  org_id = public.current_user_org_id()
  and requested_by = auth.uid()
  and public.can_access_deal_room(deal_room_id)
);

drop policy if exists decision_records_update_policy on public.decision_records;
create policy decision_records_update_policy
on public.decision_records
for update
using (
  org_id = public.current_user_org_id()
  and public.can_access_deal_room(deal_room_id)
  and (
    public.is_current_user_manager()
    or requested_by = auth.uid()
    or decided_by = auth.uid()
  )
)
with check (
  org_id = public.current_user_org_id()
  and public.can_access_deal_room(deal_room_id)
);

drop policy if exists deal_participants_select_policy on public.deal_participants;
create policy deal_participants_select_policy
on public.deal_participants
for select
using (
  org_id = public.current_user_org_id()
  and public.can_access_deal_room(deal_room_id)
);

drop policy if exists deal_participants_insert_policy on public.deal_participants;
create policy deal_participants_insert_policy
on public.deal_participants
for insert
with check (
  org_id = public.current_user_org_id()
  and public.can_access_deal_room(deal_room_id)
  and (
    public.is_current_user_manager()
    or exists (
      select 1
      from public.deal_rooms dr
      where dr.id = deal_participants.deal_room_id
        and dr.org_id = public.current_user_org_id()
        and (dr.owner_id = auth.uid() or dr.created_by = auth.uid())
    )
  )
);

drop policy if exists deal_participants_update_policy on public.deal_participants;
create policy deal_participants_update_policy
on public.deal_participants
for update
using (
  org_id = public.current_user_org_id()
  and public.can_access_deal_room(deal_room_id)
  and (
    public.is_current_user_manager()
    or user_id = auth.uid()
    or exists (
      select 1
      from public.deal_rooms dr
      where dr.id = deal_participants.deal_room_id
        and dr.org_id = public.current_user_org_id()
        and (dr.owner_id = auth.uid() or dr.created_by = auth.uid())
    )
  )
)
with check (
  org_id = public.current_user_org_id()
  and public.can_access_deal_room(deal_room_id)
);

drop policy if exists deal_checkpoints_select_policy on public.deal_checkpoints;
create policy deal_checkpoints_select_policy
on public.deal_checkpoints
for select
using (
  org_id = public.current_user_org_id()
  and public.can_access_deal_room(deal_room_id)
);

drop policy if exists deal_checkpoints_insert_policy on public.deal_checkpoints;
create policy deal_checkpoints_insert_policy
on public.deal_checkpoints
for insert
with check (
  org_id = public.current_user_org_id()
  and public.can_access_deal_room(deal_room_id)
  and (
    public.is_current_user_manager()
    or owner_id is null
    or owner_id = auth.uid()
  )
);

drop policy if exists deal_checkpoints_update_policy on public.deal_checkpoints;
create policy deal_checkpoints_update_policy
on public.deal_checkpoints
for update
using (
  org_id = public.current_user_org_id()
  and public.can_access_deal_room(deal_room_id)
  and (
    public.is_current_user_manager()
    or owner_id = auth.uid()
    or exists (
      select 1
      from public.deal_rooms dr
      where dr.id = deal_checkpoints.deal_room_id
        and dr.org_id = public.current_user_org_id()
        and (dr.owner_id = auth.uid() or dr.created_by = auth.uid())
    )
  )
)
with check (
  org_id = public.current_user_org_id()
  and public.can_access_deal_room(deal_room_id)
);

drop policy if exists intervention_requests_select_policy on public.intervention_requests;
create policy intervention_requests_select_policy
on public.intervention_requests
for select
using (
  org_id = public.current_user_org_id()
  and public.can_access_deal_room(deal_room_id)
);

drop policy if exists intervention_requests_insert_policy on public.intervention_requests;
create policy intervention_requests_insert_policy
on public.intervention_requests
for insert
with check (
  org_id = public.current_user_org_id()
  and requested_by = auth.uid()
  and public.can_access_deal_room(deal_room_id)
);

drop policy if exists intervention_requests_update_policy on public.intervention_requests;
create policy intervention_requests_update_policy
on public.intervention_requests
for update
using (
  org_id = public.current_user_org_id()
  and public.can_access_deal_room(deal_room_id)
  and (
    public.is_current_user_manager()
    or requested_by = auth.uid()
    or target_user_id = auth.uid()
  )
)
with check (
  org_id = public.current_user_org_id()
  and public.can_access_deal_room(deal_room_id)
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
  'Deal Room Command Summary (DeepSeek)',
  'v9-deepseek',
  'deal_room_command_summary'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Produce command summaries for key deal rooms.',
  'Use only provided business facts. Return strict JSON. Explain blockers, next moves, manager attention reason, and missing information without exaggerating deal probability.',
  '{"type":"object","required":["command_summary","current_goal_refinement","key_blockers","recommended_next_moves","manager_attention_reason","missing_information"]}'::jsonb,
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
  'Thread Summary (DeepSeek)',
  'v9-deepseek',
  'thread_summary'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Summarize collaboration threads for sales execution.',
  'Use only provided thread messages. Return strict JSON. Keep unresolved questions and next action explicit, and mark whether a decision is needed.',
  '{"type":"object","required":["summary","open_questions","recommended_next_action","decision_needed"]}'::jsonb,
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
  'Decision Support (DeepSeek)',
  'v9-deepseek',
  'decision_support'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Provide decision support for quote/discount/trial/contract decisions.',
  'Use only provided facts and options. Return strict JSON. Avoid over-claiming causality. Clearly state caution notes and follow-up actions.',
  '{"type":"object","required":["options_assessment","recommended_option","pros_cons","caution_notes","followup_actions"]}'::jsonb,
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
  'Intervention Recommendation (DeepSeek)',
  'v9-deepseek',
  'intervention_recommendation'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Recommend whether and how manager should intervene on key deals.',
  'Use only provided facts. Return strict JSON. Explain why now, intervention goal, and expected shift in supportive language.',
  '{"type":"object","required":["whether_to_intervene","why_now","intervention_goal","suggested_manager_action","expected_shift"]}'::jsonb,
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
  'Deal Playbook Mapping (DeepSeek)',
  'v9-deepseek',
  'deal_playbook_mapping'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Map active deal signals to relevant team/user playbooks.',
  'Use only provided facts. Return strict JSON. Do not overstate certainty, and provide explicit applicability reasons and suggested application.',
  '{"type":"object","required":["relevant_playbooks","applicability_reason","suggested_application"]}'::jsonb,
  true
from public.organizations o
on conflict (org_id, scenario, version) do nothing;
