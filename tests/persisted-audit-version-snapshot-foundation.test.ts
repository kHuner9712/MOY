import assert from "node:assert/strict";

import {
  buildOrgTemplateOverrideWriteAuditDraft,
  prepareOrgTemplateOverrideWrite
} from "../lib/org-override-write-governance";
import {
  buildOrgTemplateOverrideAuditRecord,
  listRecentOrgConfigAuditLogs,
  persistOrgConfigAuditRecord
} from "../services/org-config-audit-service";

interface MockAuditDbState {
  latestVersionRows: Array<{ version_number: number }>;
  listRows: Array<Record<string, unknown>>;
  insertRow: Record<string, unknown> | null;
  versionQueryError: string | null;
  listQueryError: string | null;
  insertError: string | null;
  insertedPayloads: Array<Record<string, unknown>>;
}

class MockAuditDbQuery {
  private mode: "select" | "insert" = "select";
  private selectColumns = "*";
  private pendingInsertPayload: Record<string, unknown> | null = null;

  constructor(
    private readonly state: MockAuditDbState
  ) {}

  select(columns: string): this {
    this.selectColumns = columns;
    return this;
  }

  eq(): this {
    return this;
  }

  is(): this {
    return this;
  }

  order(): this {
    return this;
  }

  limit(): Promise<{ data: unknown[] | null; error: { message: string } | null }> {
    if (this.selectColumns === "version_number") {
      return Promise.resolve({
        data: this.state.latestVersionRows,
        error: this.state.versionQueryError ? { message: this.state.versionQueryError } : null
      });
    }
    return Promise.resolve({
      data: this.state.listRows,
      error: this.state.listQueryError ? { message: this.state.listQueryError } : null
    });
  }

  insert(payload: Record<string, unknown>): this {
    this.mode = "insert";
    this.pendingInsertPayload = payload;
    this.state.insertedPayloads.push(payload);
    return this;
  }

  single(): Promise<{ data: Record<string, unknown> | null; error: { message: string } | null }> {
    if (this.mode !== "insert") {
      return Promise.resolve({
        data: null,
        error: { message: "single_without_insert" }
      });
    }
    if (this.state.insertError) {
      return Promise.resolve({
        data: null,
        error: { message: this.state.insertError }
      });
    }

    if (this.state.insertRow) {
      return Promise.resolve({
        data: this.state.insertRow,
        error: null
      });
    }

    const payload = this.pendingInsertPayload ?? {};
    return Promise.resolve({
      data: {
        id: "audit-generated",
        org_id: String(payload.org_id ?? "org-1"),
        actor_user_id: String(payload.actor_user_id ?? "user-1"),
        target_type: String(payload.target_type ?? "org_template_override"),
        target_id: (payload.target_id as string | null | undefined) ?? null,
        target_key: (payload.target_key as string | null | undefined) ?? null,
        action_type: String(payload.action_type ?? "update"),
        before_summary: payload.before_summary ?? {},
        after_summary: payload.after_summary ?? {},
        diagnostics_summary: payload.diagnostics_summary ?? {},
        version_number: Number(payload.version_number ?? 1),
        version_label: String(payload.version_label ?? "org_template_override:template-1:alert_rules:v1"),
        snapshot_summary: payload.snapshot_summary ?? {},
        created_at: "2026-03-21T13:00:00.000Z"
      },
      error: null
    });
  }
}

function createMockAuditSupabase(state: MockAuditDbState): { from: () => MockAuditDbQuery } {
  return {
    from: () => new MockAuditDbQuery(state)
  };
}

export async function runPersistedAuditVersionSnapshotFoundationTests(logPass: (name: string) => void): Promise<void> {
  const accepted = prepareOrgTemplateOverrideWrite({
    overrideType: "alert_rules",
    overridePayload: {
      rules: {
        no_followup_timeout: 4,
        ignored_unknown_rule: 99
      }
    }
  });
  const acceptedDraft = buildOrgTemplateOverrideWriteAuditDraft({
    orgId: "org-1",
    actorUserId: "user-1",
    templateId: "template-1",
    targetId: "override-1",
    overrideType: "alert_rules",
    beforePayload: {
      rules: {
        no_followup_timeout: 7
      }
    },
    afterPayload: accepted.writeDiagnostics.normalizedPayload,
    writeDiagnostics: accepted.writeDiagnostics,
    happenedAt: "2026-03-21T12:00:00.000Z"
  });
  const acceptedRecordDraft = buildOrgTemplateOverrideAuditRecord({
    orgId: "org-1",
    actorUserId: "user-1",
    templateId: "template-1",
    targetId: "override-1",
    overrideType: "alert_rules",
    actionType: "update",
    auditDraft: acceptedDraft,
    writeDiagnostics: accepted.writeDiagnostics
  });

  const persistedState: MockAuditDbState = {
    latestVersionRows: [{ version_number: 1 }],
    listRows: [],
    insertRow: null,
    versionQueryError: null,
    listQueryError: null,
    insertError: null,
    insertedPayloads: []
  };
  const persistedSupabase = createMockAuditSupabase(persistedState);
  const persistedResult = await persistOrgConfigAuditRecord({
    supabase: persistedSupabase as never,
    recordDraft: acceptedRecordDraft
  });
  assert.equal(persistedResult.status, "persisted");
  assert.equal(persistedResult.record?.versionNumber, 2);
  assert.equal(persistedResult.record?.versionLabel, "org_template_override:template-1:alert_rules:v2");
  assert.equal(persistedState.insertedPayloads.length, 1);
  assert.equal(persistedState.insertedPayloads[0]?.version_number, 2);
  assert.ok(
    JSON.stringify(persistedState.insertedPayloads[0]?.snapshot_summary ?? {}).includes(
      "org_template_override_normalized_payload_v1"
    )
  );
  logPass("persisted audit foundation: valid override write persists audit record with version label");

  const unavailablePersistResult = await persistOrgConfigAuditRecord({
    supabase: createMockAuditSupabase({
      latestVersionRows: [],
      listRows: [],
      insertRow: null,
      versionQueryError: 'relation "org_config_audit_logs" does not exist',
      listQueryError: null,
      insertError: null,
      insertedPayloads: []
    }) as never,
    recordDraft: acceptedRecordDraft
  });
  assert.equal(unavailablePersistResult.status, "not_available");
  assert.equal(unavailablePersistResult.record, null);
  logPass("persisted audit foundation: missing audit table returns not_available without crashing write path");

  const forbidden = prepareOrgTemplateOverrideWrite({
    overrideType: "customer_stages",
    overridePayload: {
      items: ["lead", "proposal", "won"]
    }
  });
  const forbiddenDraft = buildOrgTemplateOverrideWriteAuditDraft({
    orgId: "org-1",
    actorUserId: "user-1",
    templateId: "template-1",
    targetId: "override-2",
    overrideType: "customer_stages",
    beforePayload: null,
    afterPayload: forbidden.writeDiagnostics.normalizedPayload,
    writeDiagnostics: forbidden.writeDiagnostics
  });
  const forbiddenRecordDraft = buildOrgTemplateOverrideAuditRecord({
    orgId: "org-1",
    actorUserId: "user-1",
    templateId: "template-1",
    targetId: "override-2",
    overrideType: "customer_stages",
    actionType: "create",
    auditDraft: forbiddenDraft,
    writeDiagnostics: forbidden.writeDiagnostics
  });
  assert.equal(forbiddenRecordDraft.diagnosticsSummary.runtimeImpactSummary, "runtime_ignored_forbidden_core_semantics");
  assert.equal(forbiddenRecordDraft.diagnosticsSummary.ignoredByRuntime, true);
  assert.equal(forbiddenRecordDraft.diagnosticsSummary.forbiddenForRuntime, true);
  logPass("persisted audit foundation: forbidden override diagnostics are embedded in audit summary");

  const listState: MockAuditDbState = {
    latestVersionRows: [],
    listRows: [
      {
        id: "audit-1",
        org_id: "org-1",
        actor_user_id: "user-1",
        target_type: "org_template_override",
        target_id: "override-1",
        target_key: "template-1:alert_rules",
        action_type: "update",
        before_summary: { payloadKeys: ["rules.no_followup_timeout"] },
        after_summary: { payloadKeys: ["rules.no_followup_timeout"] },
        diagnostics_summary: {
          runtimeImpactSummary: "runtime_consumed",
          diagnostics: ["ignored_alert_rule_key:ignored_unknown_rule"]
        },
        version_number: 2,
        version_label: "org_template_override:template-1:alert_rules:v2",
        snapshot_summary: { snapshotType: "org_template_override_normalized_payload_v1" },
        created_at: "2026-03-21T13:00:00.000Z"
      }
    ],
    insertRow: null,
    versionQueryError: null,
    listQueryError: null,
    insertError: null,
    insertedPayloads: []
  };
  const availableLogs = await listRecentOrgConfigAuditLogs({
    supabase: createMockAuditSupabase(listState) as never,
    orgId: "org-1",
    limit: 5
  });
  assert.equal(availableLogs.availability, "available");
  assert.equal(availableLogs.items.length, 1);
  assert.equal(availableLogs.items[0]?.targetType, "org_template_override");
  assert.equal(availableLogs.items[0]?.versionLabel, "org_template_override:template-1:alert_rules:v2");
  logPass("persisted audit foundation: runtime debug reader can load recent persisted audit summaries");

  const emptyLogs = await listRecentOrgConfigAuditLogs({
    supabase: createMockAuditSupabase({
      latestVersionRows: [],
      listRows: [],
      insertRow: null,
      versionQueryError: null,
      listQueryError: null,
      insertError: null,
      insertedPayloads: []
    }) as never,
    orgId: "org-1",
    limit: 5
  });
  assert.equal(emptyLogs.availability, "empty");
  assert.equal(emptyLogs.items.length, 0);
  logPass("persisted audit foundation: no history falls back to empty state");

  const unavailableLogs = await listRecentOrgConfigAuditLogs({
    supabase: createMockAuditSupabase({
      latestVersionRows: [],
      listRows: [],
      insertRow: null,
      versionQueryError: null,
      listQueryError: 'relation "org_config_audit_logs" does not exist',
      insertError: null,
      insertedPayloads: []
    }) as never,
    orgId: "org-1",
    limit: 5
  });
  assert.equal(unavailableLogs.availability, "not_available");
  assert.equal(unavailableLogs.items.length, 0);
  logPass("persisted audit foundation: missing audit table falls back to not_available");
}
