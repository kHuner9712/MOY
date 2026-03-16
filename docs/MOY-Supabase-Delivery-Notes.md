# MOY Supabase + DeepSeek Delivery Notes

This document summarizes the latest project state after Supabase integration and DeepSeek-first AI upgrade.

## 1) Updated directory structure (key paths)

```text
MOY/
|- app/
|  |- api/
|  |  |- ai/
|  |  |  |- config-status/route.ts
|  |  |  |- customer-analysis/route.ts
|  |  |  |- followup-analysis/route.ts
|  |  |  `- runs/route.ts
|  |  `- alerts/
|  |     |- [id]/resolve/route.ts
|  |     `- run-scan/route.ts
|- lib/
|  |- supabase/
|  |- ai/
|  |  |- provider.ts
|  |  `- providers/deepseek.ts
|- services/
|  |- ai-analysis-service.ts
|  |- ai-prompt-service.ts
|  |- ai-run-service.ts
|  |- alert-rule-engine.ts
|  `- alert-workflow-service.ts
|- supabase/migrations/
|- scripts/seed-demo.ts
|- tests/
|- types/
|- .env.example
`- README.md
```

## 2) SQL files in `supabase/migrations`

1. `supabase/migrations/202603140001_base.sql`
2. `supabase/migrations/202603140002_ai_workflow.sql`
3. `supabase/migrations/202603140003_deepseek_provider.sql`

## 3) Added/refactored `services/*`

### Added
1. `services/ai-prompt-service.ts`
2. `services/ai-run-service.ts`
3. `services/alert-rule-engine.ts`
4. `services/alert-workflow-service.ts`
5. `services/ai-client-service.ts`

### Refactored
1. `services/auth-service.ts`
2. `services/customer-service.ts`
3. `services/followup-service.ts`
4. `services/opportunity-service.ts`
5. `services/alert-service.ts`
6. `services/ai-analysis-service.ts`
7. `services/mappers.ts`

## 4) `.env.example` (current)

```bash
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=

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

NEXT_PUBLIC_ENABLE_DEMO_AUTH=false
```

## 5) README initialization section highlights

`README.md` now includes:
1. Supabase environment and local setup
2. migration execution order (001 -> 002 -> 003)
3. demo seeding instructions
4. DeepSeek provider configuration
5. AI trigger flow + audit data flow
6. strict mode notes
7. fallback without DeepSeek key
8. troubleshooting checklist

## 6) Integration errors and fixes

1. PowerShell policy blocked `npm` script execution
- Fix: use `npm.cmd ...` commands.

2. `npm install server-only` failed with `EACCES` (network/permission)
- Fix: remove `import "server-only"` markers (non-functional runtime hint).

3. `spawn EPERM` during sandboxed `next build`
- Fix: run build with elevated execution mode.

4. TypeScript build error in `services/ai-run-service.ts`
- Fix: add explicit `AiRunRow` type to map callback parameter.

## 7) Final verification

- `npm.cmd run test` passed
- `npm.cmd run lint` passed
- `npm.cmd run build` passed

---

For full phase-3 details, see:
- `docs/MOY-Phase3-DeepSeek-Upgrade-Notes.md`
