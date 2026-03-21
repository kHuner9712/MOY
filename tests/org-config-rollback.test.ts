import assert from "node:assert/strict";

import {
  canExecuteOrgConfigRollback,
  canPreviewOrgConfigRollback,
  executeOrgConfigRollback,
  previewOrgConfigRollback
} from "../services/org-config-rollback-service";
import { DEFAULT_FEATURE_FLAGS } from "../services/org-feature-service";
import { listRecentOrgConfigAuditLogs } from "../services/org-config-audit-service";

interface MockOrgConfigRollbackState {
  nowTick: number;
  orgSettingsRow: Record<string, unknown>;
  orgAiSettingsRow: Record<string, unknown>;
  featureFlagRows: Array<Record<string, unknown>>;
  auditRows: Array<Record<string, unknown>>;
}

function asNumber(value: unknown, fallback = 0): number {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function nextIso(state: MockOrgConfigRollbackState): string {
  state.nowTick += 1;
  const base = new Date("2026-03-23T08:00:00.000Z").getTime();
  return new Date(base + state.nowTick * 60 * 1000).toISOString();
}

function matchesFilters(row: Record<string, unknown>, filters: Record<string, unknown>): boolean {
  for (const [key, value] of Object.entries(filters)) {
    if (value === null) {
      if (row[key] !== null && row[key] !== undefined) return false;
      continue;
    }
    if (row[key] !== value) return false;
  }
  return true;
}

function cloneRow<T extends Record<string, unknown>>(row: T): T {
  return JSON.parse(JSON.stringify(row)) as T;
}

class MockOrgConfigRollbackQuery {
  private mode: "select" | "update" | "upsert" | "insert" = "select";
  private selectColumns = "*";
  private filters: Record<string, unknown> = {};
  private orderBy: { column: string; ascending: boolean } | null = null;
  private updatePayload: Record<string, unknown> | null = null;
  private upsertPayload: Record<string, unknown> | null = null;
  private insertPayload: Record<string, unknown> | Array<Record<string, unknown>> | null = null;

  constructor(
    private readonly table: string,
    private readonly state: MockOrgConfigRollbackState
  ) {}

  select(columns: string): this {
    this.selectColumns = columns;
    return this;
  }

  eq(field: string, value: unknown): this {
    this.filters[field] = value;
    return this;
  }

  is(field: string, value: unknown): this {
    this.filters[field] = value;
    return this;
  }

  order(column: string, params?: { ascending?: boolean }): this {
    this.orderBy = {
      column,
      ascending: Boolean(params?.ascending ?? false)
    };
    return this;
  }

  update(payload: Record<string, unknown>): this {
    this.mode = "update";
    this.updatePayload = payload;
    return this;
  }

  upsert(payload: Record<string, unknown>): this {
    this.mode = "upsert";
    this.upsertPayload = payload;
    return this;
  }

  insert(payload: Record<string, unknown> | Array<Record<string, unknown>>): this {
    this.mode = "insert";
    this.insertPayload = payload;
    return this;
  }

  private getTableRows(): Array<Record<string, unknown>> {
    if (this.table === "org_settings") return [this.state.orgSettingsRow];
    if (this.table === "org_ai_settings") return [this.state.orgAiSettingsRow];
    if (this.table === "org_feature_flags") return this.state.featureFlagRows;
    if (this.table === "org_config_audit_logs") return this.state.auditRows;
    return [];
  }

  private applyFilters(rows: Array<Record<string, unknown>>): Array<Record<string, unknown>> {
    return rows.filter((row) => matchesFilters(row, this.filters));
  }

  private applyOrder(rows: Array<Record<string, unknown>>): Array<Record<string, unknown>> {
    if (!this.orderBy) return rows;
    const sorted = [...rows].sort((left, right) => {
      const leftValue = left[this.orderBy?.column ?? ""];
      const rightValue = right[this.orderBy?.column ?? ""];
      if (typeof leftValue === "number" || typeof rightValue === "number") {
        return asNumber(leftValue) - asNumber(rightValue);
      }
      return String(leftValue ?? "").localeCompare(String(rightValue ?? ""));
    });
    return this.orderBy.ascending ? sorted : sorted.reverse();
  }

  private executeSelect(limitCount?: number): {
    data: Array<Record<string, unknown>>;
    error: { message: string } | null;
  } {
    const filtered = this.applyOrder(this.applyFilters(this.getTableRows()));
    const limited = typeof limitCount === "number" ? filtered.slice(0, limitCount) : filtered;
    if (this.table === "org_config_audit_logs" && this.selectColumns === "version_number") {
      return {
        data: limited.map((row) => ({
          version_number: row.version_number
        })),
        error: null
      };
    }
    return {
      data: limited.map((row) => cloneRow(row)),
      error: null
    };
  }

  limit(count: number): Promise<{ data: Array<Record<string, unknown>> | null; error: { message: string } | null }> {
    const result = this.executeSelect(count);
    return Promise.resolve({
      data: result.data,
      error: result.error
    });
  }

  maybeSingle(): Promise<{ data: Record<string, unknown> | null; error: { message: string } | null }> {
    const result = this.executeSelect(1);
    return Promise.resolve({
      data: result.data[0] ?? null,
      error: result.error
    });
  }

  single(): Promise<{ data: Record<string, unknown> | null; error: { message: string } | null }> {
    if (this.mode === "update") {
      if (this.table === "org_settings") {
        Object.assign(this.state.orgSettingsRow, this.updatePayload ?? {});
        this.state.orgSettingsRow.updated_at = nextIso(this.state);
        return Promise.resolve({ data: cloneRow(this.state.orgSettingsRow), error: null });
      }
      if (this.table === "org_ai_settings") {
        Object.assign(this.state.orgAiSettingsRow, this.updatePayload ?? {});
        this.state.orgAiSettingsRow.updated_at = nextIso(this.state);
        return Promise.resolve({ data: cloneRow(this.state.orgAiSettingsRow), error: null });
      }
    }

    if (this.mode === "upsert" && this.table === "org_feature_flags") {
      const payload = this.upsertPayload ?? {};
      const existing = this.state.featureFlagRows.find(
        (row) => row.org_id === payload.org_id && row.feature_key === payload.feature_key
      );
      if (existing) {
        existing.is_enabled = payload.is_enabled;
        existing.config_json = payload.config_json ?? {};
        existing.updated_at = nextIso(this.state);
        return Promise.resolve({ data: cloneRow(existing), error: null });
      }
      const inserted = {
        id: `feature-${this.state.featureFlagRows.length + 1}`,
        org_id: payload.org_id,
        feature_key: payload.feature_key,
        is_enabled: payload.is_enabled ?? false,
        config_json: payload.config_json ?? {},
        created_at: nextIso(this.state),
        updated_at: nextIso(this.state)
      };
      this.state.featureFlagRows.push(inserted);
      return Promise.resolve({ data: cloneRow(inserted), error: null });
    }

    if (this.mode === "insert" && this.table === "org_config_audit_logs") {
      const payload = Array.isArray(this.insertPayload) ? this.insertPayload[0] ?? {} : this.insertPayload ?? {};
      const row = {
        id: `audit-${this.state.auditRows.length + 1}`,
        org_id: String(payload.org_id ?? "org-1"),
        actor_user_id: String(payload.actor_user_id ?? "user-1"),
        target_type: String(payload.target_type ?? "org_settings"),
        target_id: (payload.target_id as string | null | undefined) ?? null,
        target_key: (payload.target_key as string | null | undefined) ?? null,
        action_type: String(payload.action_type ?? "update"),
        before_summary: payload.before_summary ?? {},
        after_summary: payload.after_summary ?? {},
        diagnostics_summary: payload.diagnostics_summary ?? {},
        version_number: asNumber(payload.version_number, 1),
        version_label: String(payload.version_label ?? "org_settings:default:v1"),
        snapshot_summary: payload.snapshot_summary ?? {},
        created_at: nextIso(this.state)
      };
      this.state.auditRows.push(row);
      return Promise.resolve({
        data: cloneRow(row),
        error: null
      });
    }

    if (this.mode === "insert" && this.table === "org_feature_flags") {
      const rows = Array.isArray(this.insertPayload) ? this.insertPayload : [this.insertPayload ?? {}];
      for (const item of rows) {
        this.state.featureFlagRows.push({
          id: `feature-${this.state.featureFlagRows.length + 1}`,
          org_id: item.org_id,
          feature_key: item.feature_key,
          is_enabled: item.is_enabled ?? false,
          config_json: item.config_json ?? {},
          created_at: nextIso(this.state),
          updated_at: nextIso(this.state)
        });
      }
      return Promise.resolve({ data: cloneRow(this.state.featureFlagRows[0] ?? {}), error: null });
    }

    const result = this.executeSelect(1);
    return Promise.resolve({
      data: result.data[0] ?? null,
      error: result.error
    });
  }

  then<TResult1 = { data: Array<Record<string, unknown>> | null; error: { message: string } | null }, TResult2 = never>(
    onfulfilled?: ((value: { data: Array<Record<string, unknown>> | null; error: { message: string } | null }) => TResult1 | PromiseLike<TResult1>) | null,
    onrejected?: ((reason: unknown) => TResult2 | PromiseLike<TResult2>) | null
  ): Promise<TResult1 | TResult2> {
    const result = this.executeSelect();
    return Promise.resolve({
      data: result.data,
      error: result.error
    }).then(onfulfilled ?? undefined, onrejected ?? undefined);
  }
}

function createMockSupabase(state: MockOrgConfigRollbackState): { from: (table: string) => MockOrgConfigRollbackQuery } {
  return {
    from: (table: string) => new MockOrgConfigRollbackQuery(table, state)
  };
}

function createAuditRow(params: {
  id: string;
  targetType: "org_settings" | "org_ai_settings" | "org_feature_flags";
  versionNumber: number;
  versionLabel: string;
  afterPayload: Record<string, unknown>;
  snapshotPatch?: Record<string, unknown>;
  actionType?: string;
}): Record<string, unknown> {
  return {
    id: params.id,
    org_id: "org-1",
    actor_user_id: "user-1",
    target_type: params.targetType,
    target_id: params.targetType === "org_feature_flags" ? null : `${params.targetType}-1`,
    target_key: "default",
    action_type: params.actionType ?? "update",
    before_summary: {
      payloadPreview: "{}"
    },
    after_summary: {
      payloadPreview: JSON.stringify(params.afterPayload)
    },
    diagnostics_summary: {
      diagnostics: [],
      runtimeImpactSummary: "runtime_consumed"
    },
    version_number: params.versionNumber,
    version_label: params.versionLabel,
    snapshot_summary: {
      snapshot: {
        snapshotType: `${params.targetType}_normalized_patch_v1`,
        targetType: params.targetType,
        targetKey: "default",
        payloadSummary: {
          normalizedPatch: params.snapshotPatch ?? params.afterPayload
        }
      }
    },
    created_at: "2026-03-23T08:00:00.000Z"
  };
}

function createInitialState(): MockOrgConfigRollbackState {
  return {
    nowTick: 0,
    orgSettingsRow: {
      id: "org-settings-1",
      org_id: "org-1",
      org_display_name: "MOY",
      brand_name: "MOY",
      industry_hint: "saas",
      timezone: "Asia/Shanghai",
      locale: "zh-CN",
      default_customer_stages: ["lead", "contacted", "qualified"],
      default_opportunity_stages: ["discovery", "proposal", "won"],
      default_alert_rules: {
        no_followup_timeout: 7,
        quoted_but_stalled: 10,
        high_probability_stalled: 5
      },
      default_followup_sla_days: 3,
      onboarding_completed: false,
      onboarding_step_state: {
        org_profile: true
      },
      created_at: "2026-03-23T08:00:00.000Z",
      updated_at: "2026-03-23T08:00:00.000Z"
    },
    orgAiSettingsRow: {
      id: "org-ai-settings-1",
      org_id: "org-1",
      provider: "deepseek",
      model_default: "deepseek-chat",
      model_reasoning: "deepseek-reasoner",
      fallback_mode: "provider_then_rules",
      auto_analysis_enabled: true,
      auto_plan_enabled: true,
      auto_brief_enabled: true,
      auto_touchpoint_review_enabled: true,
      human_review_required_for_sensitive_actions: true,
      max_daily_ai_runs: 300,
      max_monthly_ai_runs: 5000,
      created_at: "2026-03-23T08:00:00.000Z",
      updated_at: "2026-03-23T08:00:00.000Z"
    },
    featureFlagRows: Object.entries(DEFAULT_FEATURE_FLAGS).map(([featureKey, isEnabled], index) => ({
      id: `feature-${index + 1}`,
      org_id: "org-1",
      feature_key: featureKey,
      is_enabled: isEnabled,
      config_json: {},
      created_at: "2026-03-23T08:00:00.000Z",
      updated_at: "2026-03-23T08:00:00.000Z"
    })),
    auditRows: [
      createAuditRow({
        id: "audit-org-settings-v1",
        targetType: "org_settings",
        versionNumber: 1,
        versionLabel: "org_settings:default:v1",
        afterPayload: {
          orgDisplayName: "MOY Rollback",
          brandName: "MOY",
          timezone: "Asia/Shanghai",
          locale: "zh-CN",
          defaultAlertRules: {
            no_followup_timeout: 6,
            quoted_but_stalled: 10,
            high_probability_stalled: 5
          },
          defaultFollowupSlaDays: 3
        }
      }),
      createAuditRow({
        id: "audit-org-ai-v1",
        targetType: "org_ai_settings",
        versionNumber: 1,
        versionLabel: "org_ai_settings:default:v1",
        afterPayload: {
          modelDefault: "deepseek-chat",
          modelReasoning: "deepseek-reasoner",
          fallbackMode: "provider_then_rules",
          humanReviewRequiredForSensitiveActions: true,
          apiKey: "sk-live-123"
        }
      }),
      createAuditRow({
        id: "audit-org-flags-v1",
        targetType: "org_feature_flags",
        versionNumber: 1,
        versionLabel: "org_feature_flags:default:v1",
        afterPayload: {
          ai_auto_analysis: false,
          ai_auto_planning: true,
          ai_morning_brief: true,
          ai_deal_command: true,
          external_touchpoints: true,
          prep_cards: true,
          playbooks: true,
          manager_quality_view: true,
          outcome_learning: true,
          demo_seed_tools: true
        }
      })
    ]
  };
}

export async function runOrgConfigRollbackTests(logPass: (name: string) => void): Promise<void> {
  assert.equal(canPreviewOrgConfigRollback({ role: "manager", orgRole: "manager" }), true);
  assert.equal(canExecuteOrgConfigRollback({ role: "manager", orgRole: "manager" }), false);
  assert.equal(canExecuteOrgConfigRollback({ role: "manager", orgRole: "owner" }), true);
  assert.equal(canPreviewOrgConfigRollback({ role: "sales", orgRole: "sales" }), false);
  logPass("org config rollback: manager preview boundary and owner/admin execute boundary");

  const orgSettingsState = createInitialState();
  const orgSettingsPreview = await previewOrgConfigRollback({
    supabase: createMockSupabase(orgSettingsState) as never,
    orgId: "org-1",
    targetType: "org_settings",
    selector: {
      targetVersionLabel: "org_settings:default:v1"
    }
  });
  assert.equal(orgSettingsPreview.status, "allowed");
  assert.equal(orgSettingsPreview.canExecute, true);
  assert.equal(orgSettingsPreview.reason, null);
  assert.ok(orgSettingsPreview.concurrency.expectedVersion?.compareToken);
  assert.ok(orgSettingsPreview.restorePlan.acceptedFields.length > 0);

  const orgSettingsExecution = await executeOrgConfigRollback({
    supabase: createMockSupabase(orgSettingsState) as never,
    orgId: "org-1",
    targetType: "org_settings",
    selector: {
      targetAuditId: "audit-org-settings-v1"
    },
    expectedVersion: orgSettingsPreview.concurrency.expectedVersion,
    actorUserId: "owner-1"
  });
  assert.equal(orgSettingsExecution.status, "executed");
  assert.equal(orgSettingsExecution.conflict, null);
  assert.equal(orgSettingsExecution.writeResult?.targetType, "org_settings");
  assert.equal(orgSettingsExecution.writeResult?.persistedAuditStatus, "persisted");
  assert.ok(orgSettingsState.auditRows.some((item) => item.action_type === "rollback" && item.target_type === "org_settings"));
  const rollbackLogs = await listRecentOrgConfigAuditLogs({
    supabase: createMockSupabase(orgSettingsState) as never,
    orgId: "org-1",
    targetType: "org_settings",
    actionType: "rollback",
    limit: 3
  });
  assert.equal(rollbackLogs.availability, "available");
  assert.equal(rollbackLogs.items[0]?.actionType, "rollback");
  logPass("org config rollback: org_settings preview+execute works and persists rollback audit");

  const aiState = createInitialState();
  const aiRejectedPreview = await previewOrgConfigRollback({
    supabase: createMockSupabase(aiState) as never,
    orgId: "org-1",
    targetType: "org_ai_settings",
    selector: {
      targetVersionNumber: 1
    }
  });
  assert.equal(aiRejectedPreview.status, "rejected");
  assert.equal(aiRejectedPreview.canExecute, false);
  assert.equal(aiRejectedPreview.reason, "rollback_requires_lossless_payload_restore");
  const aiTargetSummary = aiRejectedPreview.targetValue.summary;
  assert.equal(aiTargetSummary?.hasSensitiveRedaction, true);
  assert.ok(aiTargetSummary?.payloadPreview.includes("***REDACTED***"));
  assert.ok(!aiTargetSummary?.payloadPreview.includes("sk-live-123"));
  logPass("org config rollback: ai preview rejects lossy payload and redacts sensitive summary fields");

  const featureFlagState = createInitialState();
  const flagsPreview = await previewOrgConfigRollback({
    supabase: createMockSupabase(featureFlagState) as never,
    orgId: "org-1",
    targetType: "org_feature_flags",
    selector: {
      targetVersionLabel: "org_feature_flags:default:v1"
    }
  });
  assert.equal(flagsPreview.status, "allowed");
  featureFlagState.featureFlagRows = featureFlagState.featureFlagRows.map((item) =>
    item.feature_key === "ai_auto_analysis"
      ? {
          ...item,
          is_enabled: !Boolean(item.is_enabled),
          updated_at: "2026-03-23T09:00:00.000Z"
        }
      : item
  );
  const conflictExecution = await executeOrgConfigRollback({
    supabase: createMockSupabase(featureFlagState) as never,
    orgId: "org-1",
    targetType: "org_feature_flags",
    selector: {
      targetVersionLabel: "org_feature_flags:default:v1"
    },
    expectedVersion: flagsPreview.concurrency.expectedVersion,
    actorUserId: "owner-1"
  });
  assert.equal(conflictExecution.status, "conflict");
  assert.equal(conflictExecution.reason, "org_config_drift_conflict");
  assert.equal(conflictExecution.conflict?.conflict, true);
  assert.ok(conflictExecution.diagnostics.some((item) => item.includes("concurrency_conflict:")));
  logPass("org config rollback: baseline drift rejects rollback execution with structured conflict");
}

