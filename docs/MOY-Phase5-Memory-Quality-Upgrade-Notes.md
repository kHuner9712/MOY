# MOY Phase 5 Upgrade Notes

Date: 2026-03-14  
Scope: Personal Work Memory + Manager Operating Quality

## 1. Directory Changes

New/updated key modules in this phase:

- `supabase/migrations/202603140005_memory_quality.sql`
- `types/memory.ts`
- `types/quality.ts`
- `lib/behavior-quality.ts`
- `lib/memory-feedback.ts`
- `lib/memory-fallback.ts`
- `lib/coaching-fallback.ts`
- `services/user-memory-service.ts`
- `services/memory-compile-service.ts`
- `services/behavior-quality-service.ts`
- `services/manager-insight-service.ts`
- `services/coaching-report-service.ts`
- `services/memory-client-service.ts`
- `services/manager-quality-client-service.ts`
- `services/coaching-report-client-service.ts`
- `hooks/use-user-memory.ts`
- `hooks/use-manager-quality.ts`
- `hooks/use-coaching-reports.ts`
- `app/api/memory/refresh/route.ts`
- `app/api/memory/profile/route.ts`
- `app/api/memory/items/[id]/feedback/route.ts`
- `app/api/manager/quality/route.ts`
- `app/api/reports/coaching-generate/route.ts`
- `app/(app)/memory/page.tsx`
- `app/(app)/manager/quality/page.tsx`
- `README.md` (full Phase 5 rewrite)

Enhanced existing pages:

- `app/(app)/dashboard/page.tsx`
- `app/(app)/capture/page.tsx`
- `app/(app)/customers/[id]/page.tsx`
- `app/(app)/manager/page.tsx`
- `app/(app)/reports/page.tsx`

---

## 2. Migration Details

Migration file:
- `supabase/migrations/202603140005_memory_quality.sql`

### 2.1 Enum updates
- `ai_scenario` adds:
  - `sales_memory_compile`
  - `manager_quality_insight`
  - `user_coaching_report`
- New enums:
  - `memory_item_type`
  - `memory_item_status`
  - `quality_period_type`
  - `coaching_report_scope`
  - `coaching_report_status`
  - `memory_feedback_type`

### 2.2 New tables
- `user_memory_profiles`
- `user_memory_items`
- `behavior_quality_snapshots`
- `coaching_reports`
- `memory_feedback`

### 2.3 RLS and indexing
- RLS enabled for all new tables
- Role scope:
  - sales: own profile/items/feedback and personal reports
  - manager: org-level quality/coaching visibility
- Indexes added on:
  - `org_id`, `user_id`, `snapshot_date`, `period_type`, `status`, `created_at` and other query-critical fields

### 2.4 Prompt seeds
Inserted prompt versions for:
- `sales_memory_compile`
- `manager_quality_insight`
- `user_coaching_report`

---

## 3. Service Layer Changes

### 3.1 Memory services
- `services/user-memory-service.ts`
  - get profile/items
  - upsert profile
  - replace system-generated memory items
  - write feedback and map item status

- `services/memory-compile-service.ts`
  - aggregate 30/60/90-day style behavior data
  - invoke DeepSeek scenario `sales_memory_compile`
  - zod-validate output
  - persist profile + items + ai run
  - fallback to deterministic memory summary on AI failure

### 3.2 Quality services
- `services/behavior-quality-service.ts`
  - compile explainable quality metrics
  - upsert `behavior_quality_snapshots`

- `services/manager-insight-service.ts`
  - load team snapshots
  - run `manager_quality_insight`
  - fallback and audit run status

### 3.3 Coaching services
- `services/coaching-report-service.ts`
  - generate user/team coaching reports
  - run `user_coaching_report`
  - fallback report when AI fails
  - persist to `coaching_reports` + `ai_runs`

---

## 4. API Additions

- `POST /api/memory/refresh`
- `GET /api/memory/profile`
- `POST /api/memory/items/[id]/feedback`
- `GET /api/manager/quality`
- `POST /api/reports/coaching-generate`

Permission model:
- sales: self-only memory and personal coaching scope
- manager: org-level quality and coaching scope

Unified response shape:
- `success`
- `data`
- `error`

---

## 5. UI Additions

### 5.1 `/memory`
- work memory profile view
- memory item list
- manual refresh
- item feedback: useful / inaccurate / hide

### 5.2 `/manager/quality`
- period switch (weekly/monthly)
- team quality overview
- coaching focus and risk lists
- matrix/ranking style quality visibility

### 5.3 Existing pages enhanced
- dashboard: memory summary and correction suggestions
- capture: memory-aware action hints
- customer detail: memory-enhanced AI suggestions
- manager page: quality entry and coaching focus
- reports: coaching report generation entry + list

---

## 6. Error Handling and Fallback

### 6.1 Runtime issues fixed during integration
1. Type mismatch in `services/manager-insight-service.ts` fallback input  
   - Cause: fallback expected different metric field names
   - Fix: aligned fallback inputs with actual row fields:
     - `activityQualityScore`
     - `shallowActivityRatio`
     - `highRiskUnhandledCount`

2. `Json` typing mismatch in `services/user-memory-service.ts` evidence payload  
   - Cause: nested evidence object inferred wider than Supabase `Json`
   - Fix: explicit cast to insert type-compatible payload

### 6.2 Fallback policies
- Memory compile failure:
  - keep previous profile
  - write fallback memory result
  - mark ai run with fallback reason
- Coaching report failure:
  - generate deterministic baseline coaching report
  - persist report and audit trail

---

## 7. Validation Results

Executed after final update:

- `npm.cmd run test` -> PASS
- `npm.cmd run lint` -> PASS
- `npm.cmd run build` -> PASS

Build includes new pages and APIs:
- `/memory`
- `/manager/quality`
- `/api/memory/*`
- `/api/manager/quality`
- `/api/reports/coaching-generate`

---

## 8. Notes for Handover

- README now documents full Phase 5 setup, data flow, metrics, fallback, and manual QA steps.
- No change required to existing Supabase/DeepSeek foundation; this phase is additive and backward-compatible with prior modules.
- Existing RLS model remains consistent with org-first multi-tenant boundaries.

