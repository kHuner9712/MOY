-- MOY phase-7: preparation layer (prep cards, morning briefs, content drafts)

do $$
begin
  if exists (select 1 from pg_type where typname = 'ai_scenario') then
    if not exists (
      select 1 from pg_enum
      where enumlabel = 'followup_prep_card'
        and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'followup_prep_card';
    end if;

    if not exists (
      select 1 from pg_enum
      where enumlabel = 'quote_prep_card'
        and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'quote_prep_card';
    end if;

    if not exists (
      select 1 from pg_enum
      where enumlabel = 'meeting_prep_card'
        and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'meeting_prep_card';
    end if;

    if not exists (
      select 1 from pg_enum
      where enumlabel = 'task_brief_card'
        and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'task_brief_card';
    end if;

    if not exists (
      select 1 from pg_enum
      where enumlabel = 'manager_attention_card'
        and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'manager_attention_card';
    end if;

    if not exists (
      select 1 from pg_enum
      where enumlabel = 'sales_morning_brief'
        and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'sales_morning_brief';
    end if;

    if not exists (
      select 1 from pg_enum
      where enumlabel = 'manager_morning_brief'
        and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'manager_morning_brief';
    end if;

    if not exists (
      select 1 from pg_enum
      where enumlabel = 'action_draft_generation'
        and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'action_draft_generation';
    end if;
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'prep_card_type') then
    create type public.prep_card_type as enum (
      'followup_prep',
      'quote_prep',
      'meeting_prep',
      'task_brief',
      'manager_attention'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'prep_card_status') then
    create type public.prep_card_status as enum ('draft', 'ready', 'stale', 'archived');
  end if;

  if not exists (select 1 from pg_type where typname = 'morning_brief_type') then
    create type public.morning_brief_type as enum ('sales_morning', 'manager_morning');
  end if;

  if not exists (select 1 from pg_type where typname = 'morning_brief_status') then
    create type public.morning_brief_status as enum ('generating', 'completed', 'failed');
  end if;

  if not exists (select 1 from pg_type where typname = 'content_draft_type') then
    create type public.content_draft_type as enum (
      'followup_message',
      'quote_explanation',
      'meeting_opening',
      'meeting_summary',
      'manager_checkin_note',
      'internal_update'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'content_draft_status') then
    create type public.content_draft_status as enum ('draft', 'adopted', 'discarded', 'archived');
  end if;

  if not exists (select 1 from pg_type where typname = 'prep_feedback_target_type') then
    create type public.prep_feedback_target_type as enum ('prep_card', 'content_draft', 'morning_brief');
  end if;

  if not exists (select 1 from pg_type where typname = 'prep_feedback_type') then
    create type public.prep_feedback_type as enum ('useful', 'not_useful', 'inaccurate', 'outdated', 'adopted');
  end if;
end $$;

create table if not exists public.prep_cards (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  owner_id uuid references public.profiles(id) on delete set null,
  customer_id uuid references public.customers(id) on delete set null,
  opportunity_id uuid references public.opportunities(id) on delete set null,
  work_item_id uuid references public.work_items(id) on delete set null,
  card_type public.prep_card_type not null,
  status public.prep_card_status not null default 'draft',
  title text not null,
  summary text not null default '',
  card_payload jsonb not null default '{}'::jsonb,
  source_snapshot jsonb not null default '{}'::jsonb,
  generated_by uuid not null references public.profiles(id),
  ai_run_id uuid references public.ai_runs(id) on delete set null,
  valid_until timestamptz,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.morning_briefs (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  target_user_id uuid references public.profiles(id) on delete set null,
  brief_type public.morning_brief_type not null,
  brief_date date not null,
  status public.morning_brief_status not null default 'generating',
  headline text,
  executive_summary text,
  brief_payload jsonb not null default '{}'::jsonb,
  source_snapshot jsonb not null default '{}'::jsonb,
  generated_by uuid not null references public.profiles(id),
  ai_run_id uuid references public.ai_runs(id) on delete set null,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  constraint morning_briefs_unique_scope unique (org_id, brief_type, brief_date, target_user_id)
);

create table if not exists public.content_drafts (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  owner_id uuid not null references public.profiles(id) on delete cascade,
  customer_id uuid references public.customers(id) on delete set null,
  opportunity_id uuid references public.opportunities(id) on delete set null,
  prep_card_id uuid references public.prep_cards(id) on delete set null,
  work_item_id uuid references public.work_items(id) on delete set null,
  draft_type public.content_draft_type not null,
  status public.content_draft_status not null default 'draft',
  title text not null,
  content_markdown text not null default '',
  content_text text not null default '',
  rationale text not null default '',
  source_snapshot jsonb not null default '{}'::jsonb,
  generated_by uuid not null references public.profiles(id),
  ai_run_id uuid references public.ai_runs(id) on delete set null,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.prep_feedback (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  user_id uuid not null references public.profiles(id) on delete cascade,
  target_type public.prep_feedback_target_type not null,
  target_id uuid not null,
  feedback_type public.prep_feedback_type not null,
  feedback_text text,
  created_at timestamptz not null default timezone('utc', now())
);

create index if not exists idx_prep_cards_org_id on public.prep_cards(org_id);
create index if not exists idx_prep_cards_owner_id on public.prep_cards(owner_id);
create index if not exists idx_prep_cards_customer_id on public.prep_cards(customer_id);
create index if not exists idx_prep_cards_opportunity_id on public.prep_cards(opportunity_id);
create index if not exists idx_prep_cards_work_item_id on public.prep_cards(work_item_id);
create index if not exists idx_prep_cards_card_type on public.prep_cards(card_type);
create index if not exists idx_prep_cards_status on public.prep_cards(status);
create index if not exists idx_prep_cards_created_at on public.prep_cards(created_at desc);

create index if not exists idx_morning_briefs_org_id on public.morning_briefs(org_id);
create index if not exists idx_morning_briefs_target_user_id on public.morning_briefs(target_user_id);
create index if not exists idx_morning_briefs_brief_type on public.morning_briefs(brief_type);
create index if not exists idx_morning_briefs_brief_date on public.morning_briefs(brief_date desc);
create index if not exists idx_morning_briefs_status on public.morning_briefs(status);
create index if not exists idx_morning_briefs_created_at on public.morning_briefs(created_at desc);

create index if not exists idx_content_drafts_org_id on public.content_drafts(org_id);
create index if not exists idx_content_drafts_owner_id on public.content_drafts(owner_id);
create index if not exists idx_content_drafts_customer_id on public.content_drafts(customer_id);
create index if not exists idx_content_drafts_opportunity_id on public.content_drafts(opportunity_id);
create index if not exists idx_content_drafts_work_item_id on public.content_drafts(work_item_id);
create index if not exists idx_content_drafts_prep_card_id on public.content_drafts(prep_card_id);
create index if not exists idx_content_drafts_draft_type on public.content_drafts(draft_type);
create index if not exists idx_content_drafts_status on public.content_drafts(status);
create index if not exists idx_content_drafts_created_at on public.content_drafts(created_at desc);

create index if not exists idx_prep_feedback_org_id on public.prep_feedback(org_id);
create index if not exists idx_prep_feedback_user_id on public.prep_feedback(user_id);
create index if not exists idx_prep_feedback_target on public.prep_feedback(target_type, target_id);
create index if not exists idx_prep_feedback_created_at on public.prep_feedback(created_at desc);

alter table public.prep_cards enable row level security;
alter table public.morning_briefs enable row level security;
alter table public.content_drafts enable row level security;
alter table public.prep_feedback enable row level security;

drop trigger if exists set_prep_cards_updated_at on public.prep_cards;
create trigger set_prep_cards_updated_at
before update on public.prep_cards
for each row execute procedure public.set_updated_at();

drop trigger if exists set_morning_briefs_updated_at on public.morning_briefs;
create trigger set_morning_briefs_updated_at
before update on public.morning_briefs
for each row execute procedure public.set_updated_at();

drop trigger if exists set_content_drafts_updated_at on public.content_drafts;
create trigger set_content_drafts_updated_at
before update on public.content_drafts
for each row execute procedure public.set_updated_at();

drop policy if exists prep_cards_select_policy on public.prep_cards;
create policy prep_cards_select_policy
on public.prep_cards
for select
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or owner_id = auth.uid()
  )
);

drop policy if exists prep_cards_insert_policy on public.prep_cards;
create policy prep_cards_insert_policy
on public.prep_cards
for insert
with check (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or owner_id = auth.uid()
  )
);

drop policy if exists prep_cards_update_policy on public.prep_cards;
create policy prep_cards_update_policy
on public.prep_cards
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

drop policy if exists morning_briefs_select_policy on public.morning_briefs;
create policy morning_briefs_select_policy
on public.morning_briefs
for select
using (
  org_id = public.current_user_org_id()
  and (
    (target_user_id is not null and target_user_id = auth.uid())
    or (public.is_current_user_manager() and brief_type = 'manager_morning')
    or (public.is_current_user_manager() and target_user_id is null)
  )
);

drop policy if exists morning_briefs_insert_policy on public.morning_briefs;
create policy morning_briefs_insert_policy
on public.morning_briefs
for insert
with check (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or target_user_id = auth.uid()
  )
);

drop policy if exists morning_briefs_update_policy on public.morning_briefs;
create policy morning_briefs_update_policy
on public.morning_briefs
for update
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or target_user_id = auth.uid()
  )
)
with check (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or target_user_id = auth.uid()
  )
);

drop policy if exists content_drafts_select_policy on public.content_drafts;
create policy content_drafts_select_policy
on public.content_drafts
for select
using (
  org_id = public.current_user_org_id()
  and (
    owner_id = auth.uid()
    or (
      public.is_current_user_manager()
      and draft_type in ('manager_checkin_note', 'internal_update')
    )
  )
);

drop policy if exists content_drafts_insert_policy on public.content_drafts;
create policy content_drafts_insert_policy
on public.content_drafts
for insert
with check (
  org_id = public.current_user_org_id()
  and (
    owner_id = auth.uid()
    or public.is_current_user_manager()
  )
);

drop policy if exists content_drafts_update_policy on public.content_drafts;
create policy content_drafts_update_policy
on public.content_drafts
for update
using (
  org_id = public.current_user_org_id()
  and (
    owner_id = auth.uid()
    or public.is_current_user_manager()
  )
)
with check (
  org_id = public.current_user_org_id()
  and (
    owner_id = auth.uid()
    or public.is_current_user_manager()
  )
);

drop policy if exists prep_feedback_select_policy on public.prep_feedback;
create policy prep_feedback_select_policy
on public.prep_feedback
for select
using (
  org_id = public.current_user_org_id()
  and (
    user_id = auth.uid()
    or public.is_current_user_manager()
  )
);

drop policy if exists prep_feedback_insert_policy on public.prep_feedback;
create policy prep_feedback_insert_policy
on public.prep_feedback
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
  'Followup Prep Card (DeepSeek)',
  'v7-deepseek',
  'followup_prep_card'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Prepare sales follow-up context cards before customer contact.',
  'Use only factual inputs. Return strict JSON. Explain why now, risks, and concrete talk-track guidance.',
  '{"type":"object","required":["current_state_summary","why_contact_now","contact_goal","recommended_angle","key_points_to_mention","likely_objections","suggested_talk_track","risk_notes","success_signal","missing_information"]}'::jsonb,
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
  'Quote Prep Card (DeepSeek)',
  'v7-deepseek',
  'quote_prep_card'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Prepare quote strategy cards before sending proposals.',
  'Use only factual inputs. Return strict JSON. Avoid promise language and surface missing data clearly.',
  '{"type":"object","required":["quote_context_summary","suggested_pricing_strategy","value_points_to_emphasize","objection_handling_notes","required_information_before_quote","next_step_after_quote","quote_risks"]}'::jsonb,
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
  'Meeting Prep Card (DeepSeek)',
  'v7-deepseek',
  'meeting_prep_card'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Prepare meeting intelligence cards before customer meetings.',
  'Use only factual inputs. Return strict JSON. Highlight must-ask questions, flow, and red flags.',
  '{"type":"object","required":["meeting_goal","participant_focus_hypothesis","must_ask_questions","must_cover_points","meeting_flow_suggestion","red_flags","post_meeting_actions"]}'::jsonb,
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
  'Task Brief Card (DeepSeek)',
  'v7-deepseek',
  'task_brief_card'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Generate task brief cards for immediate execution.',
  'Use only factual inputs. Return strict JSON. Focus on why this matters now and done definition.',
  '{"type":"object","required":["task_summary","why_this_matters","best_next_action","preparation_checklist","talk_track","done_definition"]}'::jsonb,
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
  'Manager Attention Card (DeepSeek)',
  'v7-deepseek',
  'manager_attention_card'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Generate manager intervention cards using team risk signals.',
  'Use only factual inputs. Return strict JSON. Use supportive operating language, not surveillance language.',
  '{"type":"object","required":["why_manager_should_intervene","intervention_goal","suggested_manager_action","expected_outcome","caution_notes"]}'::jsonb,
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
  'Sales Morning Brief (DeepSeek)',
  'v7-deepseek',
  'sales_morning_brief'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Generate concise and actionable sales morning briefs.',
  'Use only factual inputs. Return strict JSON. Prioritize today actions and preparation needs.',
  '{"type":"object","required":["headline","focus_theme","top_tasks","customers_to_prepare","top_risks","pending_drafts","memory_reminders","action_note","manager_actions"]}'::jsonb,
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
  'Manager Morning Brief (DeepSeek)',
  'v7-deepseek',
  'manager_morning_brief'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Generate manager morning operating intelligence briefs.',
  'Use only factual inputs. Return strict JSON. Focus on intervention priorities and support actions.',
  '{"type":"object","required":["headline","focus_theme","top_tasks","customers_to_prepare","top_risks","pending_drafts","memory_reminders","action_note","manager_actions"]}'::jsonb,
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
  'Action Draft Generation (DeepSeek)',
  'v7-deepseek',
  'action_draft_generation'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Generate editable action drafts for sales and manager users.',
  'Use only factual inputs. Return strict JSON. Keep wording professional and non-committal for pricing/contract terms.',
  '{"type":"object","required":["draft_title","draft_type","audience","purpose","content_text","content_markdown","rationale","caution_notes"]}'::jsonb,
  true
from public.organizations o
on conflict (org_id, scenario, version) do nothing;
