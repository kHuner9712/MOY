# MOY Phase 16.5 Golden Path Smoke / Regression Checklist

## 1) Selected Golden Path

`Capture input -> rule-based risk hit -> work item creation -> manager/executive visibility -> outcome value proof`

Linked business objects:

1. `communication_inputs` from `/capture`
2. risk rules produce alert hits
3. alerts become actionable `work_items`
4. manager/executive sees events and summary (`business_events`, `executive_briefs`)
5. outcome and adoption metrics show business value (`action_outcomes`, `suggestion_adoptions`)

## 2) Why This Path

This is the most demo-ready end-to-end path in current MOY:

- Data entry exists and is fast (`/capture`)
- System intelligence is visible (rules/AI assist)
- System drives action (tasks, priority, owner action)
- Manager can see operational risk and what to do next
- Value can be shown by positive outcome and adoption linkage

It is the shortest path that crosses sales, manager, and executive value in one story.

## 3) Smoke Coverage Added

Added one chained smoke test that validates:

1. capture decision can go `auto` for high-confidence structured input
2. risk rules trigger (`no_followup_timeout`, `quoted_but_stalled`)
3. alert can be transformed into a work-item draft
4. task priority reaches `high/critical` under high-risk/high-value context
5. automation rule matching and action seeds are available
6. executive summary can be produced (fallback-safe)
7. outcome + adoption produce positive value indicators

## 4) Test Files Added/Modified

1. `tests/golden-path-smoke.test.ts` (new)
   - Adds `runGoldenPathSmokeTests(logPass)`
   - Covers capture -> risk rules -> task -> automation -> executive -> outcome chain

2. `tests/run-tests.mts` (updated)
   - Imports and executes `runGoldenPathSmokeTests(logPass)`
   - Keeps existing test runner and framework unchanged

## 5) Manual Pre-Release Checklist (Demo + RC)

### A. Sales Flow

- [ ] `/capture` can submit a quick note
- [ ] new followup/draft is visible after submission
- [ ] `/today` shows risk-driven tasks
- [ ] task quick actions (start/complete/snooze) update correctly

### B. Risk-to-Action Flow

- [ ] inactive high-risk customers trigger risk signal
- [ ] quoted-but-stalled deals trigger risk signal
- [ ] alerts can be converted/linked to work items
- [ ] task owner and due fields are complete

### C. Manager/Executive Visibility

- [ ] `/manager/*` or `/executive` shows related events
- [ ] event status transitions work (`acknowledged`, `resolved`, `ignored`)
- [ ] executive brief shows top risks and suggested actions

### D. Value Loop

- [ ] completed action can be captured as outcome
- [ ] adoption can be linked to outcome
- [ ] manager view reflects progress/risk change

### E. Stability Gates

- [ ] `npm run test` passes
- [ ] `npm run lint` passes
- [ ] `npm run build` passes
- [ ] key pages open on desktop and mobile (`/today`, `/capture`, `/briefings`, `/deals/[id]`)

