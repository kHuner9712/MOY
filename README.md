# MOY (墨言 / Mate Of You) Web AI Workspace

MOY (Mate Of You) is 桐鸣科技 (Tongming Tech)'s B2B AI sales workspace for SMB teams.

Current stage: **v1.x Stable Release**  
Core stack: **Next.js + Supabase + DeepSeek**

---

## 1. What This Repo Delivers

This project is a runnable, extensible SaaS MVP with:

- Supabase Auth + Postgres + RLS
- Service-layer architecture (`services/*`)
- Provider-based AI layer (default DeepSeek)
- Prompt versioning + AI run audit + fallback
- Core workspace chain: capture / today / briefings / deals / touchpoints / outcomes / memory / manager views
- Organization productization: settings/team/ai/usage/onboarding/demo seed
- Import center: CSV/XLSX mapping/normalization/dedupe/partial execution/audit
- Mobile/PWA light interaction with offline draft sync
- Industry templates + scenario packs
- Commercialization and self-sales flywheel (public site + growth pipeline + trial conversion)
- **Automation ops layer**:
  - automation rule center
  - business event bus
  - customer health snapshots
  - renewal/retention watch skeleton
  - executive cockpit and executive briefs

---

## 2. Core Features (Latest Updates)

### A. Automation Rule Center

New page:

- `/settings/automation`

Capabilities:

- list built-in automation rules
- enable/disable rules (owner/admin)
- run rules manually (manager+)
- inspect recent run audits (`automation_rule_runs`)
- read rule conditions/actions and severity

### B. Executive Cockpit

New page:

- `/executive`

Key modules:

- open business events + status actions
- critical risks / trial stalled / deal blocked / renewal at risk
- customer health distribution
- deal/trial/team execution health blocks
- recommended management actions
- executive brief list and one-click generation
- renewal watch summary

### C. Business Event Bus

Unified event table: `business_events`.

Current supported event families include:

- `trial_stalled`
- `onboarding_stuck`
- `no_recent_touchpoint`
- `deal_blocked`
- `health_declined`
- `renewal_risk_detected`
- `manager_attention_escalated`
- `conversion_signal`

Event status flow:

- `open -> acknowledged -> resolved`
- `open -> ignored`

### D. Customer Health + Retention Signals

Health snapshots are persisted in `customer_health_snapshots` with:

- activity / engagement / progression / retention / expansion scores
- `overall_health_score`
- `health_band` (`healthy/watch/at_risk/critical`)
- `risk_flags` / `positive_signals` / summary

Retention watch skeleton is persisted in `renewal_watch_items` with:

- `renewal_status` (`watch/due_soon/at_risk/expansion_candidate/renewed/churned`)
- `renewal_due_at`
- recommendation summary

### E. Cross-module Hookback

Core signals are integrated back to:

- `/today` (operating event hints)
- `/briefings` (executive brief snapshot for manager scope)
- `/customers/[id]` (health + retention signals)
- `/deals/[id]` (ops events + recommended actions)
- `/manager/outcomes`, `/manager/rhythm`, `/manager/conversion` (executive cockpit entry points)

---

## 3. Data Model

New migration:

- `supabase/migrations/202603240001_automation_ops_executive_cockpit_layer.sql`

New tables:

- `automation_rules`
- `automation_rule_runs`
- `business_events`
- `customer_health_snapshots`
- `executive_briefs`
- `renewal_watch_items`

### Data Flow

1. `automation_rules` defines trigger conditions and suggested actions.
2. Manual run (or future scheduler) executes rules and writes `automation_rule_runs`.
3. Matched entities upsert into `business_events` (dedupe by org/entity/event_type when open).
4. Health engine computes `customer_health_snapshots`.
5. Renewal engine derives/updates `renewal_watch_items`.
6. Executive brief engine aggregates events/health/trial/deal summaries into `executive_briefs`.
7. Cockpit and manager pages read these outputs and expose operational actions.

---

## 4. Automation & Executive API

Automation:

- `GET /api/settings/automation`
- `POST /api/settings/automation`
- `POST /api/settings/automation/run`

Executive:

- `GET /api/executive/summary`
- `GET /api/executive/events`
- `GET /api/executive/health`
- `GET /api/executive/briefs`
- `POST /api/executive/briefs/generate`
- `POST /api/executive/events/[id]/ack`
- `POST /api/executive/events/[id]/resolve`
- `POST /api/executive/events/[id]/ignore`

Hookback APIs:

- `GET /api/customers/[id]/health`
- `GET /api/deals/[id]/ops-events`

---

## 5. Environment Variables

Copy `.env.example` to `.env.local`:

```bash
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=
NEXT_PUBLIC_APP_URL=http://localhost:3000
NEXT_PUBLIC_APP_VERSION=v1.0.0
SELF_SALES_ORG_ID=

AI_PROVIDER=deepseek
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_REASONER_MODEL=deepseek-reasoner
DEEPSEEK_STRICT_BETA_ENABLED=false
DEEPSEEK_JSON_MODE_ENABLED=true
DEEPSEEK_MAX_TOKENS=
DEEPSEEK_TEMPERATURE=
AI_ANALYSIS_TIMEOUT_MS=30000
AI_FALLBACK_TO_RULE_ENGINE=true
```

Important:

- `SELF_SALES_ORG_ID` is required for `/api/public/request-demo`, `/api/public/start-trial`, `/api/public/contact`.
- The public intake flow no longer falls back to "first organization" to avoid cross-org lead pollution.

Optional:

```bash
NEXT_PUBLIC_ENABLE_DEMO_AUTH=false
NEXT_PUBLIC_DEMO_MANAGER_EMAIL=manager@demo.moy
NEXT_PUBLIC_DEMO_SALES_1_EMAIL=linyue@demo.moy
NEXT_PUBLIC_DEMO_SALES_2_EMAIL=chenhang@demo.moy
NEXT_PUBLIC_DEMO_SALES_3_EMAIL=wufan@demo.moy
NEXT_PUBLIC_DEMO_DEFAULT_PASSWORD=Demo#123456
```

---

## 6. Supabase Setup

Run migrations in order:

1. `202603140001_init_moy_schema.sql`
2. `202603140002_ai_workflow.sql`
3. `202603140003_deepseek_provider.sql`
4. `202603140004_capture_reports.sql`
5. `202603140005_memory_quality.sql`
6. `202603140006_work_rhythm.sql`
7. `202603150001_preparation_layer.sql`
8. `202603160001_closed_loop_learning.sql`
9. `202603170001_deal_command_layer.sql`
10. `202603180001_external_touchpoint_layer.sql`
11. `202603190001_organization_productization_layer.sql`
12. `202603200001_data_import_migration_layer.sql`
13. `202603210001_mobile_pwa_light_interaction_layer.sql`
14. `202603220001_industry_template_scenario_pack_layer.sql`
15. `202603230001_commercialization_self_sales_flywheel_layer.sql`
16. `202603240001_automation_ops_executive_cockpit_layer.sql`
17. `202603240002_phase16_5_rc_audit_fixes.sql`
18. `202603250001_attribution_layer.sql`
19. `202603250002_notification_layer.sql`
20. `202603260001_onboarding_enhancement_layer.sql`

Seed demo:

```bash
npm install
npm run seed:demo
```


---

## 7. Run Locally

```bash
npm install
npm run dev
```

Open:

- public site: `http://localhost:3000`
- login: `http://localhost:3000/login`

Validation:

```bash
npm run test
npm run lint
npm run build
```

---

## 8. Automation + Executive Main Chain

### Rule execution chain

1. Open `/settings/automation`.
2. Enable/disable rules (owner/admin).
3. Click `Run Rules`.
4. System writes:
   - `automation_rule_runs`
   - `business_events` (deduped open events)
   - action artifacts where configured (work items / interventions / brief triggers)
5. Open `/executive` to review current risk/opportunity queue.

### Event handling chain

1. In `/executive`, process events:
   - `Ack`
   - `Resolve`
   - `Ignore`
2. Event status is persisted to `business_events.status`.
3. Related pages reflect updated signal state.

### Executive brief chain

1. Generate brief from `/executive`.
2. Engine aggregates events + health + trial/deal signals.
3. Writes `executive_briefs` (`completed` or `failed` with fallback content).

---

## 9. Customer Health + Retention Logic

Health snapshot uses multi-signal scoring (rule-first):

- followup freshness and cadence
- external touchpoint recency
- progression and outcome movement
- unresolved alerts and blocked states
- trial/onboarding usage signals (for self-sales/trial tracks)

Output:

- `overall_health_score` (0-100)
- health band
- risk/positive signal arrays
- concise summary

Retention watch derives from:

- health band and trend
- renewal due proximity
- risk accumulation and low external activity
- expansion candidates with positive momentum

---

## 10. Fallback Behavior

- `executive_brief_summary` failure -> rule-based executive brief fallback
- `customer_health_summary` failure -> rule-based health summary fallback
- `automation_action_recommendation` failure -> static suggested action fallback
- `retention_watch_review` failure -> rule-based retention watch summary fallback
- rule-run partial failures -> run-level audit keeps failed rules isolated, successful rules still commit

Principle: AI failure never blocks core write path.

---

## 11. Access Control Notes

- Owner/Admin:
  - manage automation rules
  - full executive cockpit scope
- Manager:
  - view executive scope
  - run rules and process events in authorized scope
- Sales:
  - no full executive cockpit
  - receives scoped health/event summaries through owned entities

All new tables are protected by RLS policies in migration.

---

## 12. Core Services Overview

Added:

- `services/automation-rule-service.ts`
- `services/business-event-service.ts`
- `services/customer-health-service.ts`
- `services/executive-brief-service.ts`
- `services/renewal-watch-service.ts`
- `services/executive-cockpit-service.ts`
- `services/executive-client-service.ts`

Support libs:

- `lib/automation-ops.ts`
- `lib/renewal-watch.ts`
- `lib/automation-fallback.ts`

---

## 13. Manual Verification Checklist

1. Configure env and run all migrations through `202603240001`.
2. Login as manager/admin and open `/settings/automation`.
3. Verify built-in rules load, and toggling works for owner/admin only.
4. Trigger `Run Rules`; verify:
   - `automation_rule_runs` row created
   - `business_events` rows created/updated
5. Open `/executive`; verify:
   - summary cards populate
   - open events list loads
   - event actions (`ack/resolve/ignore`) persist
6. Generate an executive brief and verify `executive_briefs` writes.
7. Open `/customers/[id]` and verify health/retention signal panel.
8. Open `/deals/[id]` and verify ops event + recommended action panel.
9. Open `/briefings` and `/today` (manager scope) and verify executive signal hookbacks.
10. Disable DeepSeek key and confirm all fallbacks still produce usable outputs.

---

## 14. Tests

Core functional tests added in:

- `tests/automation-ops-layer.test.ts`

Coverage includes:

- automation rule matching
- business event dedupe and status flow
- customer health fallback
- executive brief fallback
- rule-to-action linkage
- renewal watch derivation and health band mapping

Run:

```bash
npm run test
```
