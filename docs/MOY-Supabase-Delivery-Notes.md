# MOY Supabase + DeepSeek Delivery Notes

> ⚠️ **已归档 (Archived)**
>
> 本文档已停止更新，其核心内容已被 [README.md](../README.md) 完整覆盖。
>
> 迁移记录：
> - 环境变量配置 → README.md Section 5
> - Migration 说明 → README.md Section 6
> - DeepSeek 配置 → README.md Section 5
> - AI 触发流程 → README.md Section 10

---

## 历史信息保留

### .env.example (历史版本)

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

### Migration 执行顺序 (历史)

1. `202603140001_init_moy_schema.sql`
2. `202603140002_ai_workflow.sql`
3. `202603140003_deepseek_provider.sql`

---

_最后更新：2026-03-14（已归档）_
