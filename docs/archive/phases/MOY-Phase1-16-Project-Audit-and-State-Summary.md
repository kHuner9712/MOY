# MOY Phase 1-16 Project Audit & State Summary

## 1. Phase 1-16 Stage Overview
The MOY project has completed 16 core functionality phases, evolving into a robust B2B AI sales workspace. It features a complete architecture based on Next.js App Router, Supabase, and DeepSeek AI integration. The system encompasses end-to-end sales workflow management, from inbound lead capture to post-sales health tracking and executive insights.

## 2. Core Capability Map
- **Capture:** Omnichannel communication extraction (phone, chat, email) with AI-powered intent and objection analysis.
- **Workflow & Today Plan:** Daily prioritization engine driven by follow-up cadence, risk levels, and AI suggestions.
- **Deal Command:** Deal room enablement with structured checkpoints, playbook mapping, and decision support.
- **Memory & Quality:** User memory compilation from interactions, paired with behavior quality tracking for coaching.
- **Automation Ops:** Rule-based event bus for automated alerts, notifications, and workflow interventions.
- **Executive Cockpit:** High-level operational health views, cohort tracking, and automated executive briefs.

## 3. Key Pages & Role Entrances
| Page | Primary Role | Function |
|---|---|---|
| `/capture` | Sales / Manager | Quick entry for communication records and meeting notes. |
| `/today` | Sales | Daily prioritized task and follow-up list. |
| `/deals` | Sales / Manager | Deal room for structured opportunity advancement. |
| `/imports` | Manager / Admin | Data migration center with deduplication and normalization. |
| `/manager` | Manager | Team execution rhythm, operating quality, and outcome reviews. |
| `/executive` | Admin / Owner | High-level business event processing, health metrics, and renewal watch. |
| `/settings/automation` | Admin / Owner | Automation rules management. |

## 4. Data Layer Overview
- **Core Entities:** `customers`, `opportunities`, `followup_records`, `work_items`, `alerts`.
- **Phase 16 Additions:** `automation_rules`, `automation_rule_runs`, `business_events`, `customer_health_snapshots`, `renewal_watch_items`, `executive_briefs`.
- Data access is strictly controlled via Supabase RLS, primarily segmented by `org_id` and relationship to the `owner_id` or `manager` role.

## 5. Advanced Capabilities Overview
- **AI Engine:** Pluggable AI provider layer (defaulting to DeepSeek) for extracting insights, drafting follow-ups, generating meeting briefs, and reasoning about customer health. Features strict JSON mode and schema validation.
- **Productization & Growth:** Self-sales flywheel with public demo/trial requests, template application, and organization-based usage metering/entitlements.
- **Mobile/PWA:** Light interaction layer optimized for mobile draft capture and offline-sync capabilities.

## 6. Audit Conclusion (Phase 16.5 RC)
**Risk Level: LOW-MEDIUM**
The project is generally in a healthy, cohesive state and is structurally ready to advance to Phase 17. The service layer is well-disciplined, the API uses a unified response pattern, and the test suite covers a large portion of the core logic. However, a few lingering issues and undocumented changes from bridging phases need to be addressed to ensure absolute stability.

## 7. Key Findings (P0/P1)
- **[P0] Corrupted Scratch File:** A junk file (`services/value`) containing mixed Chinese markdown and code was left in the services directory. (Fixed)
- **[P1] Undocumented Migrations:** Three post-Phase-16 migrations (`attribution_layer`, `notification_layer`, `onboarding_enhancement_layer`) were present but undocumented in the README. 
- **[P1] RLS Token Claim Pattern:** The `notification_layer` migration uses `auth.jwt -> 'org_id'` for RLS. Supabase standard claims do not natively include `org_id` unless a custom JWT or hook is implemented. This could lead to access denial or bypass.
- **[P1] Test Runner Import Failure:** The test runner `run-tests.mts` fails to execute currently due to missing `.ts` extensions in internal imports (e.g., `lib/env`), causing a Node.js module resolution error in `node --experimental-strip-types`.

## 8. Recommendations Before Phase 17
1. **Verify Notification RLS:** Confirm whether `org_id` is actually injected into the Supabase JWT. If not, rewrite the RLS policies in `notifications` to use a `user_id` -> `org_id` lookup via `profiles` or `org_memberships`.
2. **Fix Test Suite Imports:** Perform a pass over all imports in `tests/run-tests.mts` and related files to ensure fully qualified ESM imports (with `.ts` extensions) are used so the tests can run in CI.
3. **Role Granularity Review:** `lib/server-auth.ts` forcefully maps owner/admin/manager all to `'manager'`. If Phase 17 introduces owner-only or admin-only destructive actions, this flattening will mask those privileges and require refactoring.
