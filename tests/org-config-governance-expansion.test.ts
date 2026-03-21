import assert from "node:assert/strict";

import {
  buildExpectedVersionFromOrgConfigBaseline,
  isOrgConfigDriftConflictError
} from "../lib/override-concurrency-guard";
import {
  listRecentOrgConfigAuditLogs
} from "../services/org-config-audit-service";
import {
  governedUpdateOrgFeatureFlags,
  governedUpdateOrgSettings
} from "../services/org-config-governance-service";
import { DEFAULT_FEATURE_FLAGS } from "../services/org-feature-service";
import { buildOrgConfigGovernanceSummary } from "../services/runtime-explain-debug-service";

interface MockOrgConfigDbState {
  nowTick: number;
  orgSettingsRow: Record<string, unknown>;
  orgAiSettingsRow: Record<string, unknown>;
  featureFlagRows: Array<Record<string, unknown>>;
  auditRows: Array<Record<string, unknown>>;
}

function nextIso(state: MockOrgConfigDbState): string {
  state.nowTick += 1;
  const base = new Date("2026-03-22T08:00:00.000Z").getTime();
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

function asNumber(value: unknown, fallback = 0): number {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function cloneRow<T extends Record<string, unknown>>(row: T): T {
  return JSON.parse(JSON.stringify(row)) as T;
}

class MockOrgConfigDbQuery {
  private mode: "select" | "update" | "upsert" | "insert" = "select";
  private selectColumns = "*";
  private filters: Record<string, unknown> = {};
  private orderBy: { column: string; ascending: boolean } | null = null;
  private updatePayload: Record<string, unknown> | null = null;
  private upsertPayload: Record<string, unknown> | null = null;
  private insertPayload: Record<string, unknown> | Array<Record<string, unknown>> | null = null;

  constructor(
    private readonly table: string,
    private readonly state: MockOrgConfigDbState
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
      return Promise.resolve({ data: cloneRow(row), error: null });
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

function createMockSupabase(state: MockOrgConfigDbState): { from: (table: string) => MockOrgConfigDbQuery } {
  return {
    from: (table: string) => new MockOrgConfigDbQuery(table, state)
  };
}

function createInitialState(): MockOrgConfigDbState {
  const featureFlagRows = Object.entries(DEFAULT_FEATURE_FLAGS).map(([featureKey, isEnabled], index) => ({
    id: `feature-${index + 1}`,
    org_id: "org-1",
    feature_key: featureKey,
    is_enabled: isEnabled,
    config_json: {},
    created_at: "2026-03-22T08:00:00.000Z",
    updated_at: "2026-03-22T08:00:00.000Z"
  }));

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
      created_at: "2026-03-22T08:00:00.000Z",
      updated_at: "2026-03-22T08:00:00.000Z"
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
      created_at: "2026-03-22T08:00:00.000Z",
      updated_at: "2026-03-22T08:00:00.000Z"
    },
    featureFlagRows,
    auditRows: []
  };
}

export async function runOrgConfigGovernanceExpansionTests(logPass: (name: string) => void): Promise<void> {
  const state = createInitialState();
  const supabase = createMockSupabase(state);

  const orgSettingsWrite = await governedUpdateOrgSettings({
    supabase: supabase as never,
    orgId: "org-1",
    actorUserId: "user-1",
    patch: {
      defaultAlertRules: {
        no_followup_timeout: 6,
        unsupported_rule: 99
      } as Record<string, number>,
      defaultCustomerStages: ["lead", "proposal", "won", "custom_stage"],
      // @ts-expect-error test unknown field hardening
      unknownField: "ignored"
    },
    expectedVersion: null
  });

  assert.equal(orgSettingsWrite.writeDiagnostics.acceptedForWrite, true);
  assert.ok(orgSettingsWrite.writeDiagnostics.acceptedFields.includes("defaultAlertRules"));
  assert.ok(orgSettingsWrite.writeDiagnostics.ignoredFields.includes("defaultCustomerStages"));
  assert.ok(orgSettingsWrite.writeDiagnostics.forbiddenFields.includes("defaultCustomerStages"));
  assert.ok(
    orgSettingsWrite.writeDiagnostics.diagnostics.some((item) =>
      item.includes("forbidden_core_semantic_value:defaultCustomerStages")
    )
  );
  assert.equal(orgSettingsWrite.persistedAudit.status, "persisted");
  assert.equal(orgSettingsWrite.persistedAudit.record?.targetType, "org_settings");
  logPass("org config governance expansion: org_settings write path produces diagnostics and persisted audit");

  let conflictCaught = false;
  try {
    await governedUpdateOrgSettings({
      supabase: supabase as never,
      orgId: "org-1",
      actorUserId: "user-1",
      patch: {
        defaultFollowupSlaDays: 5
      },
      expectedVersion: buildExpectedVersionFromOrgConfigBaseline(orgSettingsWrite.concurrency.beforeWrite)
    });
  } catch (error) {
    conflictCaught = isOrgConfigDriftConflictError(error);
    if (isOrgConfigDriftConflictError(error)) {
      assert.equal(error.conflict.conflict, true);
      assert.ok(error.conflict.diagnostics.some((item) => item.includes("concurrency_conflict:")));
      assert.ok(error.conflict.currentVersion.compareToken);
      assert.ok(error.conflict.expectedVersion.compareToken);
    }
  }
  assert.equal(conflictCaught, true);
  logPass("org config governance expansion: expectedVersion mismatch rejects org_settings write with structured conflict");

  const featureFlagsWrite = await governedUpdateOrgFeatureFlags({
    supabase: supabase as never,
    orgId: "org-1",
    actorUserId: "user-1",
    patch: {
      ai_auto_analysis: false,
      // @ts-expect-error test unknown feature hardening
      unknown_feature: true
    },
    expectedVersion: null
  });
  assert.equal(featureFlagsWrite.writeDiagnostics.acceptedForWrite, true);
  assert.ok(featureFlagsWrite.writeDiagnostics.acceptedFields.includes("ai_auto_analysis"));
  assert.ok(featureFlagsWrite.writeDiagnostics.ignoredFields.includes("unknown_feature"));
  assert.equal(featureFlagsWrite.payload.featureFlagMap.ai_auto_analysis, false);
  assert.equal(featureFlagsWrite.persistedAudit.status, "persisted");
  assert.equal(featureFlagsWrite.persistedAudit.record?.targetType, "org_feature_flags");
  logPass("org config governance expansion: org_feature_flags write path persists audit and surfaces ignored field diagnostics");

  const [orgSettingsAudits, orgAiSettingsAudits, orgFeatureFlagsAudits] = await Promise.all([
    listRecentOrgConfigAuditLogs({
      supabase: supabase as never,
      orgId: "org-1",
      targetType: "org_settings",
      limit: 3
    }),
    listRecentOrgConfigAuditLogs({
      supabase: supabase as never,
      orgId: "org-1",
      targetType: "org_ai_settings",
      limit: 3
    }),
    listRecentOrgConfigAuditLogs({
      supabase: supabase as never,
      orgId: "org-1",
      targetType: "org_feature_flags",
      limit: 3
    })
  ]);
  const governanceSummary = buildOrgConfigGovernanceSummary({
    orgSettingsAudits,
    orgAiSettingsAudits,
    orgFeatureFlagsAudits
  });
  const settingsSummary = governanceSummary.items.find((item) => item.targetType === "org_settings");
  const flagsSummary = governanceSummary.items.find((item) => item.targetType === "org_feature_flags");
  assert.equal(settingsSummary?.hasPersistedAudit, true);
  assert.equal(flagsSummary?.hasPersistedAudit, true);
  assert.ok((settingsSummary?.diagnosticsPreview.length ?? 0) > 0);
  assert.ok((flagsSummary?.diagnosticsPreview.length ?? 0) > 0);
  logPass("org config governance expansion: runtime debug reader can aggregate recent governance summaries for org config targets");
}
