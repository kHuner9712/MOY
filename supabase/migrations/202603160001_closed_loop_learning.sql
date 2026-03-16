-- MOY phase-8: closed-loop learning layer (outcome capture, adoption tracking, playbook, outcome review)

do $$
begin
  if exists (select 1 from pg_type where typname = 'ai_scenario') then
    if not exists (
      select 1 from pg_enum
      where enumlabel = 'action_outcome_capture_assist'
        and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'action_outcome_capture_assist';
    end if;

    if not exists (
      select 1 from pg_enum
      where enumlabel = 'playbook_compile'
        and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'playbook_compile';
    end if;

    if not exists (
      select 1 from pg_enum
      where enumlabel = 'outcome_effectiveness_review'
        and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'outcome_effectiveness_review';
    end if;

    if not exists (
      select 1 from pg_enum
      where enumlabel = 'personal_effectiveness_update'
        and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'personal_effectiveness_update';
    end if;
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_type where typname = 'action_outcome_type') then
    create type public.action_outcome_type as enum (
      'followup_result',
      'quote_result',
      'meeting_result',
      'task_result',
      'manager_intervention_result'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'action_outcome_status') then
    create type public.action_outcome_status as enum (
      'positive_progress',
      'neutral',
      'stalled',
      'risk_increased',
      'closed_won',
      'closed_lost'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'action_outcome_sentiment_shift') then
    create type public.action_outcome_sentiment_shift as enum (
      'improved',
      'unchanged',
      'worsened',
      'unknown'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'action_outcome_usefulness_rating') then
    create type public.action_outcome_usefulness_rating as enum (
      'helpful',
      'somewhat_helpful',
      'not_helpful',
      'unknown'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'suggestion_target_type') then
    create type public.suggestion_target_type as enum (
      'prep_card',
      'content_draft',
      'task_action_suggestion',
      'morning_brief'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'suggestion_adoption_type') then
    create type public.suggestion_adoption_type as enum (
      'viewed',
      'copied',
      'edited',
      'adopted',
      'dismissed',
      'partially_used'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'suggestion_adoption_context') then
    create type public.suggestion_adoption_context as enum (
      'before_followup',
      'before_quote',
      'before_meeting',
      'during_task_execution',
      'after_review'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'playbook_scope_type') then
    create type public.playbook_scope_type as enum ('org', 'team', 'user');
  end if;

  if not exists (select 1 from pg_type where typname = 'playbook_type') then
    create type public.playbook_type as enum (
      'objection_handling',
      'customer_segment',
      'quote_strategy',
      'meeting_strategy',
      'followup_rhythm',
      'risk_recovery'
    );
  end if;

  if not exists (select 1 from pg_type where typname = 'playbook_status') then
    create type public.playbook_status as enum ('active', 'draft', 'archived');
  end if;

  if not exists (select 1 from pg_type where typname = 'outcome_review_scope') then
    create type public.outcome_review_scope as enum ('user', 'team', 'org');
  end if;

  if not exists (select 1 from pg_type where typname = 'outcome_review_status') then
    create type public.outcome_review_status as enum ('generating', 'completed', 'failed');
  end if;

  if not exists (select 1 from pg_type where typname = 'playbook_feedback_type') then
    create type public.playbook_feedback_type as enum (
      'useful',
      'not_useful',
      'outdated',
      'inaccurate',
      'adopted'
    );
  end if;
end $$;

create table if not exists public.action_outcomes (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  owner_id uuid not null references public.profiles(id) on delete cascade,
  customer_id uuid references public.customers(id) on delete set null,
  opportunity_id uuid references public.opportunities(id) on delete set null,
  work_item_id uuid references public.work_items(id) on delete set null,
  followup_id uuid references public.followups(id) on delete set null,
  communication_input_id uuid references public.communication_inputs(id) on delete set null,
  prep_card_id uuid references public.prep_cards(id) on delete set null,
  content_draft_id uuid references public.content_drafts(id) on delete set null,
  outcome_type public.action_outcome_type not null,
  result_status public.action_outcome_status not null default 'neutral',
  stage_changed boolean not null default false,
  old_stage public.customer_stage,
  new_stage public.customer_stage,
  customer_sentiment_shift public.action_outcome_sentiment_shift not null default 'unknown',
  key_outcome_summary text not null default '',
  new_objections jsonb not null default '[]'::jsonb,
  new_risks jsonb not null default '[]'::jsonb,
  next_step_defined boolean not null default false,
  next_step_text text,
  followup_due_at timestamptz,
  used_prep_card boolean not null default false,
  used_draft boolean not null default false,
  usefulness_rating public.action_outcome_usefulness_rating not null default 'unknown',
  notes text,
  created_by uuid not null references public.profiles(id),
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.suggestion_adoptions (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  user_id uuid not null references public.profiles(id) on delete cascade,
  target_type public.suggestion_target_type not null,
  target_id uuid not null,
  adoption_type public.suggestion_adoption_type not null,
  edit_distance_hint numeric(7,4),
  adoption_context public.suggestion_adoption_context not null,
  linked_outcome_id uuid references public.action_outcomes(id) on delete set null,
  created_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.playbooks (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  scope_type public.playbook_scope_type not null default 'team',
  owner_user_id uuid references public.profiles(id) on delete set null,
  playbook_type public.playbook_type not null,
  title text not null,
  summary text not null default '',
  status public.playbook_status not null default 'active',
  confidence_score numeric(5,4) not null default 0.6,
  applicability_notes text not null default '',
  source_snapshot jsonb not null default '{}'::jsonb,
  generated_by uuid not null references public.profiles(id),
  ai_run_id uuid references public.ai_runs(id) on delete set null,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  constraint playbooks_confidence_check check (confidence_score >= 0 and confidence_score <= 1)
);

create table if not exists public.playbook_entries (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  playbook_id uuid not null references public.playbooks(id) on delete cascade,
  entry_title text not null,
  entry_summary text not null default '',
  conditions jsonb not null default '{}'::jsonb,
  recommended_actions jsonb not null default '[]'::jsonb,
  caution_notes jsonb not null default '[]'::jsonb,
  evidence_snapshot jsonb not null default '{}'::jsonb,
  success_signal jsonb not null default '{}'::jsonb,
  failure_modes jsonb not null default '[]'::jsonb,
  confidence_score numeric(5,4) not null default 0.6,
  sort_order int not null default 100,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  constraint playbook_entries_confidence_check check (confidence_score >= 0 and confidence_score <= 1)
);

create table if not exists public.outcome_reviews (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  review_scope public.outcome_review_scope not null default 'team',
  target_user_id uuid references public.profiles(id) on delete set null,
  period_start date not null,
  period_end date not null,
  status public.outcome_review_status not null default 'generating',
  title text,
  executive_summary text,
  effective_patterns jsonb not null default '[]'::jsonb,
  ineffective_patterns jsonb not null default '[]'::jsonb,
  repeated_failures jsonb not null default '[]'::jsonb,
  coaching_actions jsonb not null default '[]'::jsonb,
  playbook_candidates jsonb not null default '[]'::jsonb,
  source_snapshot jsonb not null default '{}'::jsonb,
  generated_by uuid not null references public.profiles(id),
  ai_run_id uuid references public.ai_runs(id) on delete set null,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  constraint outcome_reviews_period_check check (period_end >= period_start)
);

create table if not exists public.playbook_feedback (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  user_id uuid not null references public.profiles(id) on delete cascade,
  playbook_id uuid not null references public.playbooks(id) on delete cascade,
  playbook_entry_id uuid references public.playbook_entries(id) on delete set null,
  feedback_type public.playbook_feedback_type not null,
  feedback_text text,
  created_at timestamptz not null default timezone('utc', now())
);

create index if not exists idx_action_outcomes_org_id on public.action_outcomes(org_id);
create index if not exists idx_action_outcomes_owner_id on public.action_outcomes(owner_id);
create index if not exists idx_action_outcomes_customer_id on public.action_outcomes(customer_id);
create index if not exists idx_action_outcomes_opportunity_id on public.action_outcomes(opportunity_id);
create index if not exists idx_action_outcomes_work_item_id on public.action_outcomes(work_item_id);
create index if not exists idx_action_outcomes_outcome_type on public.action_outcomes(outcome_type);
create index if not exists idx_action_outcomes_result_status on public.action_outcomes(result_status);
create index if not exists idx_action_outcomes_created_at on public.action_outcomes(created_at desc);

create index if not exists idx_suggestion_adoptions_org_id on public.suggestion_adoptions(org_id);
create index if not exists idx_suggestion_adoptions_user_id on public.suggestion_adoptions(user_id);
create index if not exists idx_suggestion_adoptions_target on public.suggestion_adoptions(target_type, target_id);
create index if not exists idx_suggestion_adoptions_context on public.suggestion_adoptions(adoption_context);
create index if not exists idx_suggestion_adoptions_outcome on public.suggestion_adoptions(linked_outcome_id);
create index if not exists idx_suggestion_adoptions_created_at on public.suggestion_adoptions(created_at desc);

create index if not exists idx_playbooks_org_id on public.playbooks(org_id);
create index if not exists idx_playbooks_scope_type on public.playbooks(scope_type);
create index if not exists idx_playbooks_owner_user_id on public.playbooks(owner_user_id);
create index if not exists idx_playbooks_playbook_type on public.playbooks(playbook_type);
create index if not exists idx_playbooks_status on public.playbooks(status);
create index if not exists idx_playbooks_created_at on public.playbooks(created_at desc);

create index if not exists idx_playbook_entries_org_id on public.playbook_entries(org_id);
create index if not exists idx_playbook_entries_playbook_id on public.playbook_entries(playbook_id);
create index if not exists idx_playbook_entries_confidence on public.playbook_entries(confidence_score desc);
create index if not exists idx_playbook_entries_sort_order on public.playbook_entries(sort_order);

create index if not exists idx_outcome_reviews_org_id on public.outcome_reviews(org_id);
create index if not exists idx_outcome_reviews_scope on public.outcome_reviews(review_scope);
create index if not exists idx_outcome_reviews_target_user on public.outcome_reviews(target_user_id);
create index if not exists idx_outcome_reviews_status on public.outcome_reviews(status);
create index if not exists idx_outcome_reviews_period on public.outcome_reviews(period_start, period_end);
create index if not exists idx_outcome_reviews_created_at on public.outcome_reviews(created_at desc);

create index if not exists idx_playbook_feedback_org_id on public.playbook_feedback(org_id);
create index if not exists idx_playbook_feedback_user_id on public.playbook_feedback(user_id);
create index if not exists idx_playbook_feedback_playbook_id on public.playbook_feedback(playbook_id);
create index if not exists idx_playbook_feedback_entry_id on public.playbook_feedback(playbook_entry_id);
create index if not exists idx_playbook_feedback_created_at on public.playbook_feedback(created_at desc);

alter table public.action_outcomes enable row level security;
alter table public.suggestion_adoptions enable row level security;
alter table public.playbooks enable row level security;
alter table public.playbook_entries enable row level security;
alter table public.outcome_reviews enable row level security;
alter table public.playbook_feedback enable row level security;

drop trigger if exists set_action_outcomes_updated_at on public.action_outcomes;
create trigger set_action_outcomes_updated_at
before update on public.action_outcomes
for each row execute procedure public.set_updated_at();

drop trigger if exists set_playbooks_updated_at on public.playbooks;
create trigger set_playbooks_updated_at
before update on public.playbooks
for each row execute procedure public.set_updated_at();

drop trigger if exists set_playbook_entries_updated_at on public.playbook_entries;
create trigger set_playbook_entries_updated_at
before update on public.playbook_entries
for each row execute procedure public.set_updated_at();

drop trigger if exists set_outcome_reviews_updated_at on public.outcome_reviews;
create trigger set_outcome_reviews_updated_at
before update on public.outcome_reviews
for each row execute procedure public.set_updated_at();

drop policy if exists action_outcomes_select_policy on public.action_outcomes;
create policy action_outcomes_select_policy
on public.action_outcomes
for select
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or owner_id = auth.uid()
    or created_by = auth.uid()
  )
);

drop policy if exists action_outcomes_insert_policy on public.action_outcomes;
create policy action_outcomes_insert_policy
on public.action_outcomes
for insert
with check (
  org_id = public.current_user_org_id()
  and created_by = auth.uid()
  and (
    public.is_current_user_manager()
    or owner_id = auth.uid()
  )
);

drop policy if exists action_outcomes_update_policy on public.action_outcomes;
create policy action_outcomes_update_policy
on public.action_outcomes
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

drop policy if exists suggestion_adoptions_select_policy on public.suggestion_adoptions;
create policy suggestion_adoptions_select_policy
on public.suggestion_adoptions
for select
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or user_id = auth.uid()
  )
);

drop policy if exists suggestion_adoptions_insert_policy on public.suggestion_adoptions;
create policy suggestion_adoptions_insert_policy
on public.suggestion_adoptions
for insert
with check (
  org_id = public.current_user_org_id()
  and user_id = auth.uid()
);

drop policy if exists suggestion_adoptions_update_policy on public.suggestion_adoptions;
create policy suggestion_adoptions_update_policy
on public.suggestion_adoptions
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

drop policy if exists playbooks_select_policy on public.playbooks;
create policy playbooks_select_policy
on public.playbooks
for select
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or owner_user_id = auth.uid()
    or scope_type in ('org', 'team')
  )
);

drop policy if exists playbooks_insert_policy on public.playbooks;
create policy playbooks_insert_policy
on public.playbooks
for insert
with check (
  org_id = public.current_user_org_id()
  and generated_by = auth.uid()
  and (
    public.is_current_user_manager()
    or (
      scope_type = 'user'
      and owner_user_id = auth.uid()
    )
  )
);

drop policy if exists playbooks_update_policy on public.playbooks;
create policy playbooks_update_policy
on public.playbooks
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

drop policy if exists playbook_entries_select_policy on public.playbook_entries;
create policy playbook_entries_select_policy
on public.playbook_entries
for select
using (
  org_id = public.current_user_org_id()
  and exists (
    select 1
    from public.playbooks p
    where p.id = playbook_entries.playbook_id
      and p.org_id = public.current_user_org_id()
      and (
        public.is_current_user_manager()
        or p.owner_user_id = auth.uid()
        or p.scope_type in ('org', 'team')
      )
  )
);

drop policy if exists playbook_entries_insert_policy on public.playbook_entries;
create policy playbook_entries_insert_policy
on public.playbook_entries
for insert
with check (
  org_id = public.current_user_org_id()
  and exists (
    select 1
    from public.playbooks p
    where p.id = playbook_entries.playbook_id
      and p.org_id = public.current_user_org_id()
      and (
        public.is_current_user_manager()
        or p.generated_by = auth.uid()
      )
  )
);

drop policy if exists playbook_entries_update_policy on public.playbook_entries;
create policy playbook_entries_update_policy
on public.playbook_entries
for update
using (
  org_id = public.current_user_org_id()
  and exists (
    select 1
    from public.playbooks p
    where p.id = playbook_entries.playbook_id
      and p.org_id = public.current_user_org_id()
      and (
        public.is_current_user_manager()
        or p.generated_by = auth.uid()
      )
  )
)
with check (
  org_id = public.current_user_org_id()
  and exists (
    select 1
    from public.playbooks p
    where p.id = playbook_entries.playbook_id
      and p.org_id = public.current_user_org_id()
      and (
        public.is_current_user_manager()
        or p.generated_by = auth.uid()
      )
  )
);

drop policy if exists outcome_reviews_select_policy on public.outcome_reviews;
create policy outcome_reviews_select_policy
on public.outcome_reviews
for select
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or target_user_id = auth.uid()
    or generated_by = auth.uid()
  )
);

drop policy if exists outcome_reviews_insert_policy on public.outcome_reviews;
create policy outcome_reviews_insert_policy
on public.outcome_reviews
for insert
with check (
  org_id = public.current_user_org_id()
  and generated_by = auth.uid()
  and (
    public.is_current_user_manager()
    or (
      review_scope = 'user'
      and target_user_id = auth.uid()
    )
  )
);

drop policy if exists outcome_reviews_update_policy on public.outcome_reviews;
create policy outcome_reviews_update_policy
on public.outcome_reviews
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

drop policy if exists playbook_feedback_select_policy on public.playbook_feedback;
create policy playbook_feedback_select_policy
on public.playbook_feedback
for select
using (
  org_id = public.current_user_org_id()
  and (
    public.is_current_user_manager()
    or user_id = auth.uid()
  )
);

drop policy if exists playbook_feedback_insert_policy on public.playbook_feedback;
create policy playbook_feedback_insert_policy
on public.playbook_feedback
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
  'Action Outcome Capture Assist (DeepSeek)',
  'v8-deepseek',
  'action_outcome_capture_assist'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Assist sales users in capturing concise and factual action outcomes.',
  'Use only provided facts. Do not infer personality. Keep output concise, evidence-based, and JSON-only.',
  '{"type":"object","required":["outcome_type","result_status","stage_changed","old_stage","new_stage","customer_sentiment_shift","key_outcome_summary","new_objections","new_risks","next_step_defined","next_step_text","followup_due_at","used_prep_card","used_draft","usefulness_rating","notes"]}'::jsonb,
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
  'Playbook Compile (DeepSeek)',
  'v8-deepseek',
  'playbook_compile'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Compile practical sales playbooks from factual team outcomes.',
  'Only use factual samples. Avoid causal overclaim. Output JSON with clear applicability and caution notes.',
  '{"type":"object","required":["playbook_type","title","summary","confidence_score","applicability_notes","entries"]}'::jsonb,
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
  'Outcome Effectiveness Review (DeepSeek)',
  'v8-deepseek',
  'outcome_effectiveness_review'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Review outcome effectiveness patterns for manager-friendly coaching decisions.',
  'Do not use punitive language. Use sample-based findings only and clearly note uncertainty. Return strict JSON.',
  '{"type":"object","required":["title","executive_summary","effective_patterns","ineffective_patterns","repeated_failures","coaching_actions","playbook_candidates"]}'::jsonb,
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
  'Personal Effectiveness Update (DeepSeek)',
  'v8-deepseek',
  'personal_effectiveness_update'::public.ai_scenario,
  'deepseek'::public.ai_provider_scope,
  'You are MOY AI. Update personal suggestion effectiveness weighting for one sales user.',
  'Do not infer personality. Use only adoption and outcome facts. Return strict JSON and include uncertainty notes.',
  '{"type":"object","required":["summary","helpful_suggestion_patterns","ineffective_suggestion_patterns","rhythm_adjustments","coaching_focus_updates","confidence_score"]}'::jsonb,
  true
from public.organizations o
on conflict (org_id, scenario, version) do nothing;
