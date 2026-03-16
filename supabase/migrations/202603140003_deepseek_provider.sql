-- MOY phase-3.1: DeepSeek-first provider architecture
-- Adds provider-level auditability and fallback tracking.

do $$
begin
  if not exists (select 1 from pg_type where typname = 'ai_provider') then
    create type public.ai_provider as enum ('deepseek', 'openai', 'qwen', 'zhipu');
  end if;

  if not exists (select 1 from pg_type where typname = 'ai_provider_scope') then
    create type public.ai_provider_scope as enum ('deepseek', 'universal');
  end if;

  if not exists (select 1 from pg_type where typname = 'ai_result_source') then
    create type public.ai_result_source as enum ('provider', 'fallback');
  end if;
end $$;

do $$
begin
  if exists (select 1 from pg_type where typname = 'ai_scenario') then
    if not exists (
      select 1
      from pg_enum
      where enumlabel = 'leak_risk_inference'
        and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'leak_risk_inference';
    end if;

    if not exists (
      select 1
      from pg_enum
      where enumlabel = 'manager_summary'
        and enumtypid = 'public.ai_scenario'::regtype
    ) then
      alter type public.ai_scenario add value 'manager_summary';
    end if;
  end if;
end $$;

do $$
begin
  if exists (select 1 from pg_type where typname = 'alert_source') then
    if not exists (
      select 1
      from pg_enum
      where enumlabel = 'fallback'
        and enumtypid = 'public.alert_source'::regtype
    ) then
      alter type public.alert_source add value 'fallback';
    end if;
  end if;
end $$;

alter table if exists public.ai_runs
  add column if not exists provider public.ai_provider not null default 'deepseek',
  add column if not exists latency_ms int,
  add column if not exists result_source public.ai_result_source not null default 'provider',
  add column if not exists fallback_reason text;

alter table if exists public.ai_prompt_versions
  add column if not exists provider_scope public.ai_provider_scope not null default 'universal';

create index if not exists idx_ai_runs_provider_status_created
  on public.ai_runs(provider, status, created_at desc);

create index if not exists idx_ai_runs_result_source_created
  on public.ai_runs(result_source, created_at desc);

create index if not exists idx_ai_prompt_versions_provider_scope
  on public.ai_prompt_versions(provider_scope, scenario, is_active, created_at desc);

-- New DeepSeek-ready prompt versions (idempotent).
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
  o.id as org_id,
  'Followup Analysis (DeepSeek)' as name,
  'v2-deepseek' as version,
  'followup_analysis'::public.ai_scenario as scenario,
  'deepseek'::public.ai_provider_scope as provider_scope,
  'You are MOY AI analyst for B2B sales followups.' as system_prompt,
  'Return strict JSON only. Use only provided facts. Explicitly say uncertainty if data is insufficient.' as developer_prompt,
  '{"type":"object","required":["customer_status_summary","key_needs","key_objections","buying_signals","risk_level","leak_risk","leak_reasons","next_best_actions","recommended_next_followup_at","manager_attention_needed","confidence_score","reasoning_brief"]}'::jsonb as output_schema,
  true as is_active
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
  o.id as org_id,
  'Customer Health (DeepSeek)' as name,
  'v2-deepseek' as version,
  'customer_health'::public.ai_scenario as scenario,
  'deepseek'::public.ai_provider_scope as provider_scope,
  'You are MOY AI analyst for customer health review.' as system_prompt,
  'Return strict JSON only. Base analysis on provided evidence and progression records only.' as developer_prompt,
  '{"type":"object","required":["stage_fit_assessment","momentum_score","relationship_score","decision_clarity_score","budget_clarity_score","timeline_clarity_score","overall_risk_level","stall_signals","suggested_strategy","summary"]}'::jsonb as output_schema,
  true as is_active
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
  o.id as org_id,
  'Leak Risk Inference (DeepSeek)' as name,
  'v2-deepseek' as version,
  'leak_risk_inference'::public.ai_scenario as scenario,
  'deepseek'::public.ai_provider_scope as provider_scope,
  'You are MOY AI leakage risk inference assistant.' as system_prompt,
  'Return strict JSON only. Decide whether to create or update an alert with concise evidence.' as developer_prompt,
  '{"type":"object","required":["should_create_alert","severity","primary_rule_type","title","description","evidence","suggested_owner_action","due_at"]}'::jsonb as output_schema,
  true as is_active
from public.organizations o
on conflict (org_id, scenario, version) do nothing;

