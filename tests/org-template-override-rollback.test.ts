import assert from "node:assert/strict";

import {
  isOverrideDriftConflictError
} from "../lib/override-concurrency-guard";
import { listRecentOrgConfigAuditLogs } from "../services/org-config-audit-service";
import { upsertOrgTemplateOverride } from "../services/industry-template-service";
import {
  canExecuteOrgTemplateOverrideRollback,
  canPreviewOrgTemplateOverrideRollback,
  executeOrgTemplateOverrideRollback,
  previewOrgTemplateOverrideRollback
} from "../services/org-template-override-rollback-service";

interface MockRollbackDbState {
  overrideRow: Record<string, unknown> | null;
  auditRows: Array<Record<string, unknown>>;
  insertedAuditRows: Array<Record<string, unknown>>;
  auditTableUnavailable: boolean;
  nextOverrideId: number;
}

function asNumber(value: unknown, fallback: number): number {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
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

function sortByVersionDesc(rows: Array<Record<string, unknown>>): Array<Record<string, unknown>> {
  return [...rows].sort(
    (left, right) => asNumber(right.version_number, 0) - asNumber(left.version_number, 0)
  );
}

class MockRollbackDbQuery {
  private selectColumns = "*";
  private mode: "select" | "upsert" | "insert" = "select";
  private filters: Record<string, unknown> = {};
  private upsertPayload: Record<string, unknown> | null = null;
  private insertPayload: Record<string, unknown> | null = null;

  constructor(
    private readonly table: string,
    private readonly state: MockRollbackDbState
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

  order(): this {
    return this;
  }

  limit(count: number): Promise<{ data: Array<Record<string, unknown>> | null; error: { message: string } | null }> {
    if (this.table !== "org_config_audit_logs") {
      return Promise.resolve({
        data: [],
        error: null
      });
    }
    if (this.state.auditTableUnavailable) {
      return Promise.resolve({
        data: null,
        error: { message: 'relation "org_config_audit_logs" does not exist' }
      });
    }

    const filtered = this.state.auditRows.filter((row) => matchesFilters(row, this.filters));
    if (this.selectColumns === "version_number") {
      return Promise.resolve({
        data: sortByVersionDesc(filtered).slice(0, count).map((row) => ({
          version_number: row.version_number
        })),
        error: null
      });
    }

    return Promise.resolve({
      data: filtered.slice(0, count),
      error: null
    });
  }

  maybeSingle(): Promise<{ data: Record<string, unknown> | null; error: { message: string } | null }> {
    if (this.table === "org_config_audit_logs") {
      if (this.state.auditTableUnavailable) {
        return Promise.resolve({
          data: null,
          error: { message: 'relation "org_config_audit_logs" does not exist' }
        });
      }
      const row = this.state.auditRows.find((item) => matchesFilters(item, this.filters)) ?? null;
      return Promise.resolve({
        data: row,
        error: null
      });
    }

    if (this.table === "org_template_overrides") {
      const row =
        this.state.overrideRow && matchesFilters(this.state.overrideRow, this.filters)
          ? this.state.overrideRow
          : null;
      return Promise.resolve({
        data: row,
        error: null
      });
    }

    return Promise.resolve({
      data: null,
      error: { message: `unsupported_table:${this.table}` }
    });
  }

  upsert(payload: Record<string, unknown>): this {
    this.mode = "upsert";
    this.upsertPayload = payload;
    return this;
  }

  insert(payload: Record<string, unknown>): this {
    this.mode = "insert";
    this.insertPayload = payload;
    return this;
  }

  single(): Promise<{ data: Record<string, unknown> | null; error: { message: string } | null }> {
    if (this.mode === "upsert" && this.table === "org_template_overrides") {
      const payload = this.upsertPayload ?? {};
      const existing = this.state.overrideRow;
      const row = {
        id: existing?.id ?? `override-${++this.state.nextOverrideId}`,
        org_id: payload.org_id,
        template_id: payload.template_id,
        override_type: payload.override_type,
        override_payload: payload.override_payload ?? {},
        created_by: payload.created_by ?? "user-1",
        created_at: existing?.created_at ?? "2026-03-21T12:00:00.000Z",
        updated_at: "2026-03-21T13:00:00.000Z"
      };
      this.state.overrideRow = row;
      return Promise.resolve({
        data: row,
        error: null
      });
    }

    if (this.mode === "insert" && this.table === "org_config_audit_logs") {
      if (this.state.auditTableUnavailable) {
        return Promise.resolve({
          data: null,
          error: { message: 'relation "org_config_audit_logs" does not exist' }
        });
      }
      const payload = this.insertPayload ?? {};
      const row = {
        id: `audit-insert-${this.state.insertedAuditRows.length + 1}`,
        org_id: String(payload.org_id ?? "org-1"),
        actor_user_id: String(payload.actor_user_id ?? "user-1"),
        target_type: String(payload.target_type ?? "org_template_override"),
        target_id: (payload.target_id as string | null | undefined) ?? null,
        target_key: (payload.target_key as string | null | undefined) ?? null,
        action_type: String(payload.action_type ?? "rollback"),
        before_summary: payload.before_summary ?? {},
        after_summary: payload.after_summary ?? {},
        diagnostics_summary: payload.diagnostics_summary ?? {},
        version_number: asNumber(payload.version_number, 1),
        version_label: String(payload.version_label ?? "org_template_override:template-1:alert_rules:v1"),
        snapshot_summary: payload.snapshot_summary ?? {},
        created_at: "2026-03-21T13:30:00.000Z"
      };
      this.state.insertedAuditRows.push(row);
      this.state.auditRows.push(row);
      return Promise.resolve({
        data: row,
        error: null
      });
    }

    return Promise.resolve({
      data: null,
      error: { message: `single_not_supported:${this.table}:${this.mode}` }
    });
  }
}

function createMockRollbackSupabase(state: MockRollbackDbState): { from: (table: string) => MockRollbackDbQuery } {
  return {
    from: (table: string) => new MockRollbackDbQuery(table, state)
  };
}

function buildAuditRow(params: {
  id: string;
  targetKey: string;
  actionType?: string;
  versionNumber: number;
  versionLabel: string;
  normalizedPayload?: Record<string, unknown> | null;
  afterPayloadPreview?: Record<string, unknown> | null;
}): Record<string, unknown> {
  return {
    id: params.id,
    org_id: "org-1",
    actor_user_id: "user-1",
    target_type: "org_template_override",
    target_id: "override-1",
    target_key: params.targetKey,
    action_type: params.actionType ?? "update",
    before_summary: {
      payloadKeys: ["rules.no_followup_timeout"],
      payloadPreview: "{\"rules\":{\"no_followup_timeout\":7}}"
    },
    after_summary: params.afterPayloadPreview
      ? {
          payloadKeys: ["rules.no_followup_timeout"],
          payloadPreview: JSON.stringify(params.afterPayloadPreview)
        }
      : {
          payloadKeys: ["rules.no_followup_timeout"],
          payloadPreview: "{\"rules\":{\"no_followup_timeout\":4}}"
        },
    diagnostics_summary: {
      runtimeImpactSummary: "runtime_consumed",
      diagnostics: []
    },
    version_number: params.versionNumber,
    version_label: params.versionLabel,
    snapshot_summary: params.normalizedPayload
      ? {
          snapshot: {
            snapshotType: "org_template_override_normalized_payload_v1",
            targetType: "org_template_override",
            targetKey: params.targetKey,
            payloadSummary: {
              templateId: "template-1",
              overrideType: "alert_rules",
              normalizedPayload: params.normalizedPayload
            }
          }
        }
      : {},
    created_at: "2026-03-21T12:00:00.000Z"
  };
}

function buildBaseState(): MockRollbackDbState {
  return {
    overrideRow: {
      id: "override-1",
      org_id: "org-1",
      template_id: "template-1",
      override_type: "alert_rules",
      override_payload: {
        rules: {
          no_followup_timeout: 8
        }
      },
      created_by: "user-1",
      created_at: "2026-03-20T11:00:00.000Z",
      updated_at: "2026-03-20T11:00:00.000Z"
    },
    auditRows: [
      buildAuditRow({
        id: "audit-v2",
        targetKey: "template-1:alert_rules",
        versionNumber: 2,
        versionLabel: "org_template_override:template-1:alert_rules:v2",
        normalizedPayload: {
          rules: {
            no_followup_timeout: 4
          }
        }
      })
    ],
    insertedAuditRows: [],
    auditTableUnavailable: false,
    nextOverrideId: 1
  };
}

export async function runOrgTemplateOverrideRollbackTests(logPass: (name: string) => void): Promise<void> {
  const previewState = buildBaseState();
  const preview = await previewOrgTemplateOverrideRollback({
    supabase: createMockRollbackSupabase(previewState) as never,
    orgId: "org-1",
    templateId: "template-1",
    overrideType: "alert_rules",
    selector: {
      targetVersionLabel: "org_template_override:template-1:alert_rules:v2"
    }
  });
  assert.equal(preview.status, "allowed");
  assert.equal(preview.canExecute, true);
  assert.equal(preview.reason, null);
  assert.ok(preview.restorePlan.acceptedFields.includes("rules.no_followup_timeout"));
  assert.equal(preview.targetVersion.versionNumber, 2);
  assert.ok(preview.concurrency.baseline);
  assert.ok(preview.concurrency.expectedVersion?.compareToken);
  assert.equal(preview.concurrency.baseline?.targetKey, "template-1:alert_rules");
  logPass("org template override rollback: valid version can build allowed dry-run preview");

  const missingVersionPreview = await previewOrgTemplateOverrideRollback({
    supabase: createMockRollbackSupabase(buildBaseState()) as never,
    orgId: "org-1",
    templateId: "template-1",
    overrideType: "alert_rules",
    selector: {
      targetVersionLabel: "org_template_override:template-1:alert_rules:v99"
    }
  });
  assert.equal(missingVersionPreview.status, "rejected");
  assert.equal(missingVersionPreview.canExecute, false);
  assert.equal(missingVersionPreview.reason, "rollback_target_version_not_found");
  logPass("org template override rollback: missing target version is rejected with diagnostics");

  const forbiddenState = buildBaseState();
  forbiddenState.auditRows = [
    {
      ...buildAuditRow({
        id: "audit-customer-stage-v1",
        targetKey: "template-1:customer_stages",
        versionNumber: 1,
        versionLabel: "org_template_override:template-1:customer_stages:v1",
        normalizedPayload: {
          items: ["lead", "proposal", "won"]
        }
      }),
      snapshot_summary: {
        snapshot: {
          snapshotType: "org_template_override_normalized_payload_v1",
          targetType: "org_template_override",
          targetKey: "template-1:customer_stages",
          payloadSummary: {
            templateId: "template-1",
            overrideType: "customer_stages",
            normalizedPayload: {
              items: ["lead", "proposal", "won"]
            }
          }
        }
      }
    }
  ];
  const forbiddenPreview = await previewOrgTemplateOverrideRollback({
    supabase: createMockRollbackSupabase(forbiddenState) as never,
    orgId: "org-1",
    templateId: "template-1",
    overrideType: "customer_stages",
    selector: {
      targetVersionNumber: 1
    }
  });
  assert.equal(forbiddenPreview.status, "rejected");
  assert.equal(forbiddenPreview.reason, "rollback_forbidden_core_semantic_override");
  assert.equal(forbiddenPreview.restorePlan.forbiddenForRuntime, true);
  logPass("org template override rollback: forbidden override preview is rejected by guard strategy");

  assert.equal(canExecuteOrgTemplateOverrideRollback({ role: "manager", orgRole: "owner" }), true);
  assert.equal(canExecuteOrgTemplateOverrideRollback({ role: "manager", orgRole: "admin" }), true);
  assert.equal(canExecuteOrgTemplateOverrideRollback({ role: "manager", orgRole: "manager" }), false);
  assert.equal(canExecuteOrgTemplateOverrideRollback({ role: "sales", orgRole: "sales" }), false);
  assert.equal(canPreviewOrgTemplateOverrideRollback({ role: "manager", orgRole: "manager" }), true);
  assert.equal(canPreviewOrgTemplateOverrideRollback({ role: "sales", orgRole: "sales" }), false);
  logPass("org template override rollback: owner/admin execute boundary and manager preview boundary");

  const executeState = buildBaseState();
  const executePreview = await previewOrgTemplateOverrideRollback({
    supabase: createMockRollbackSupabase(executeState) as never,
    orgId: "org-1",
    templateId: "template-1",
    overrideType: "alert_rules",
    selector: {
      targetVersionLabel: "org_template_override:template-1:alert_rules:v2"
    }
  });
  assert.ok(executePreview.concurrency.expectedVersion);
  const execution = await executeOrgTemplateOverrideRollback({
    supabase: createMockRollbackSupabase(executeState) as never,
    orgId: "org-1",
    templateId: "template-1",
    overrideType: "alert_rules",
    selector: {
      targetVersionLabel: "org_template_override:template-1:alert_rules:v2"
    },
    expectedVersion: executePreview.concurrency.expectedVersion,
    actorUserId: "owner-1"
  });
  assert.equal(execution.status, "executed");
  assert.equal(execution.conflict, null);
  assert.equal(execution.writeResult?.persistedAudit.status, "persisted");
  assert.equal(executeState.insertedAuditRows.length, 1);
  assert.equal(executeState.insertedAuditRows[0]?.action_type, "rollback");
  const insertedDiagnostics = (executeState.insertedAuditRows[0]?.diagnostics_summary ??
    {}) as Record<string, unknown>;
  const insertedRollbackSource = (insertedDiagnostics.rollbackSource ?? {}) as Record<string, unknown>;
  assert.equal(
    insertedRollbackSource.sourceVersionLabel,
    "org_template_override:template-1:alert_rules:v2"
  );
  const currentOverridePayload = (executeState.overrideRow?.override_payload ?? {}) as Record<string, unknown>;
  const currentOverrideRules = (currentOverridePayload.rules ?? {}) as Record<string, unknown>;
  assert.equal(
    currentOverrideRules.no_followup_timeout,
    4
  );
  logPass("org template override rollback: guarded execution writes override and persists rollback audit record");

  const ignoredFieldState = buildBaseState();
  ignoredFieldState.auditRows = [
    buildAuditRow({
      id: "audit-v2-lossy",
      targetKey: "template-1:alert_rules",
      versionNumber: 2,
      versionLabel: "org_template_override:template-1:alert_rules:v2",
      normalizedPayload: null,
      afterPayloadPreview: {
        rules: {
          no_followup_timeout: 4,
          unknown_rule: 99
        }
      }
    })
  ];
  const ignoredFieldPreview = await previewOrgTemplateOverrideRollback({
    supabase: createMockRollbackSupabase(ignoredFieldState) as never,
    orgId: "org-1",
    templateId: "template-1",
    overrideType: "alert_rules",
    selector: {
      targetVersionNumber: 2
    }
  });
  const rejectedExecution = await executeOrgTemplateOverrideRollback({
    supabase: createMockRollbackSupabase(ignoredFieldState) as never,
    orgId: "org-1",
    templateId: "template-1",
    overrideType: "alert_rules",
    selector: {
      targetVersionNumber: 2
    },
    expectedVersion: ignoredFieldPreview.concurrency.expectedVersion,
    actorUserId: "owner-1"
  });
  assert.equal(rejectedExecution.status, "rejected");
  assert.equal(rejectedExecution.writeResult, null);
  assert.equal(rejectedExecution.conflict, null);
  assert.equal(rejectedExecution.preview.reason, "rollback_requires_lossless_payload_restore");
  assert.ok(
    rejectedExecution.preview.diagnostics.some((item) => item.includes("ignored_alert_rule_key:unknown_rule"))
  );
  logPass("org template override rollback: hardening still guards rollback execution and blocks lossy restore");

  const driftState = buildBaseState();
  const driftPreview = await previewOrgTemplateOverrideRollback({
    supabase: createMockRollbackSupabase(driftState) as never,
    orgId: "org-1",
    templateId: "template-1",
    overrideType: "alert_rules",
    selector: {
      targetVersionNumber: 2
    }
  });
  driftState.overrideRow = {
    ...(driftState.overrideRow ?? {}),
    override_payload: {
      rules: {
        no_followup_timeout: 12
      }
    },
    updated_at: "2026-03-21T14:00:00.000Z"
  };
  const driftExecution = await executeOrgTemplateOverrideRollback({
    supabase: createMockRollbackSupabase(driftState) as never,
    orgId: "org-1",
    templateId: "template-1",
    overrideType: "alert_rules",
    selector: {
      targetVersionNumber: 2
    },
    expectedVersion: driftPreview.concurrency.expectedVersion,
    actorUserId: "owner-1"
  });
  assert.equal(driftExecution.status, "conflict");
  assert.equal(driftExecution.writeResult, null);
  assert.equal(driftExecution.reason, "override_drift_conflict");
  assert.equal(driftExecution.conflict?.conflict, true);
  assert.ok(driftExecution.conflict?.currentVersion.compareToken);
  assert.ok(driftExecution.conflict?.expectedVersion.compareToken);
  assert.ok(
    driftExecution.diagnostics.some((item) => item.includes("concurrency_conflict:"))
  );
  logPass("org template override rollback: preview-execute drift mismatch is rejected with structured conflict");

  const writeGuardState = buildBaseState();
  const writeGuardPreview = await previewOrgTemplateOverrideRollback({
    supabase: createMockRollbackSupabase(writeGuardState) as never,
    orgId: "org-1",
    templateId: "template-1",
    overrideType: "alert_rules",
    selector: {
      targetVersionNumber: 2
    }
  });
  writeGuardState.overrideRow = {
    ...(writeGuardState.overrideRow ?? {}),
    override_payload: {
      rules: {
        no_followup_timeout: 9
      }
    },
    updated_at: "2026-03-21T15:00:00.000Z"
  };
  let writeGuardError: unknown = null;
  try {
    await upsertOrgTemplateOverride({
      supabase: createMockRollbackSupabase(writeGuardState) as never,
      orgId: "org-1",
      templateId: "template-1",
      overrideType: "alert_rules",
      overridePayload: {
        rules: {
          no_followup_timeout: 5
        }
      },
      expectedVersion: writeGuardPreview.concurrency.expectedVersion,
      actorUserId: "owner-1"
    });
  } catch (error) {
    writeGuardError = error;
  }
  assert.ok(writeGuardError);
  assert.equal(isOverrideDriftConflictError(writeGuardError), true);
  if (isOverrideDriftConflictError(writeGuardError)) {
    assert.equal(writeGuardError.conflict.conflict, true);
    assert.ok(writeGuardError.conflict.currentVersion.compareToken);
    assert.ok(writeGuardError.conflict.expectedVersion.compareToken);
    assert.ok(
      writeGuardError.conflict.diagnostics.some((item) => item.includes("concurrency_conflict:"))
    );
  }
  logPass("org template override rollback: direct override write path is guarded by expected version");

  const rollbackReadState = buildBaseState();
  rollbackReadState.auditRows.push({
    ...buildAuditRow({
      id: "audit-v3-rollback",
      targetKey: "template-1:alert_rules",
      actionType: "rollback",
      versionNumber: 3,
      versionLabel: "org_template_override:template-1:alert_rules:v3",
      normalizedPayload: {
        rules: {
          no_followup_timeout: 4
        }
      }
    }),
    diagnostics_summary: {
      runtimeImpactSummary: "runtime_consumed",
      rollbackSource: {
        sourceAuditId: "audit-v2",
        sourceVersionLabel: "org_template_override:template-1:alert_rules:v2",
        sourceVersionNumber: 2
      },
      diagnostics: ["rollback_recovered_from:org_template_override:template-1:alert_rules:v2"]
    }
  });
  const rollbackLogs = await listRecentOrgConfigAuditLogs({
    supabase: createMockRollbackSupabase(rollbackReadState) as never,
    orgId: "org-1",
    targetType: "org_template_override",
    actionType: "rollback",
    limit: 5
  });
  assert.equal(rollbackLogs.availability, "available");
  assert.equal(rollbackLogs.items.length, 1);
  assert.equal(rollbackLogs.items[0]?.actionType, "rollback");
  assert.equal(rollbackLogs.items[0]?.versionLabel, "org_template_override:template-1:alert_rules:v3");
  logPass("org template override rollback: runtime debug reader can load recent persisted rollback summaries");
}
