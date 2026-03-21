import assert from "node:assert/strict";

import {
  buildRollbackExecutePayloadFromPreview,
  extractTemplateOverrideConflictPayload,
  resolveTemplateOverrideEditorAccess
} from "../lib/template-override-editor-ui";
import type { TemplateOverrideRollbackPreviewPayload } from "../services/settings-client-service";
import type { OrgTemplateOverrideType } from "../types/productization";

function buildRollbackPreview(
  overrideType: OrgTemplateOverrideType
): TemplateOverrideRollbackPreviewPayload["preview"] {
  return {
    generatedAt: "2026-03-22T08:00:00.000Z",
    status: "allowed",
    canExecute: true,
    reason: null,
    diagnostics: [],
    request: {
      orgId: "org-1",
      templateId: "template-1",
      overrideType
    },
    targetVersion: {
      auditId: "audit-1",
      versionLabel: "org_template_override:template-1:alert_rules:v2",
      versionNumber: 2,
      actionType: "update",
      createdAt: "2026-03-22T07:00:00.000Z"
    },
    currentValue: {
      exists: true,
      targetId: "override-1",
      summary: {
        payloadKeys: ["rules.no_followup_timeout"],
        payloadPreview: "{\"rules\":{\"no_followup_timeout\":7}}"
      }
    },
    targetValue: {
      summary: {
        payloadKeys: ["rules.no_followup_timeout"],
        payloadPreview: "{\"rules\":{\"no_followup_timeout\":4}}"
      },
      normalizedPayload: {
        rules: {
          no_followup_timeout: 4
        }
      }
    },
    restorePlan: {
      acceptedFields: ["rules.no_followup_timeout"],
      ignoredFields: [],
      runtimeImpactSummary: "runtime_consumed",
      acceptedForRuntime: true,
      forbiddenForRuntime: false
    },
    concurrency: {
      baseline: {
        generatedAt: "2026-03-22T08:00:00.000Z",
        targetKey: "template-1:alert_rules",
        auditAvailability: "available",
        currentVersionLabel: "org_template_override:template-1:alert_rules:v3",
        currentVersionNumber: 3,
        currentOverrideUpdatedAt: "2026-03-22T07:30:00.000Z",
        currentPayloadHash: "hash-1",
        compareToken: "ovc_v1_token"
      },
      expectedVersion: {
        compareToken: "ovc_v1_token",
        versionLabel: "org_template_override:template-1:alert_rules:v3",
        versionNumber: 3,
        overrideUpdatedAt: "2026-03-22T07:30:00.000Z",
        payloadHash: "hash-1"
      },
      note: "preview baseline ready"
    }
  };
}

export function runTemplateOverrideEditorUiTests(logPass: (name: string) => void): void {
  const owner = resolveTemplateOverrideEditorAccess({
    user: {
      role: "manager",
      orgRole: "owner"
    },
    templateCenterRole: "owner"
  });
  assert.equal(owner.canAccess, true);
  assert.equal(owner.canPreview, true);
  assert.equal(owner.canWrite, true);
  assert.equal(owner.canExecuteRollback, true);

  const admin = resolveTemplateOverrideEditorAccess({
    user: {
      role: "manager",
      orgRole: "admin"
    },
    templateCenterRole: "admin"
  });
  assert.equal(admin.canWrite, true);
  assert.equal(admin.canExecuteRollback, true);

  const manager = resolveTemplateOverrideEditorAccess({
    user: {
      role: "manager",
      orgRole: "manager"
    },
    templateCenterRole: "manager"
  });
  assert.equal(manager.canAccess, true);
  assert.equal(manager.canPreview, true);
  assert.equal(manager.canWrite, false);
  assert.equal(manager.canExecuteRollback, false);

  const sales = resolveTemplateOverrideEditorAccess({
    user: {
      role: "sales",
      orgRole: "sales"
    },
    templateCenterRole: "sales"
  });
  assert.equal(sales.canAccess, false);
  assert.equal(sales.canPreview, false);
  assert.equal(sales.canWrite, false);
  assert.equal(sales.canExecuteRollback, false);
  logPass("template override editor ui: owner/admin writable, manager read-only, sales denied");

  const conflict = extractTemplateOverrideConflictPayload({
    conflict: true,
    conflictReason: "compare_token_mismatch",
    currentVersion: {
      compareToken: "current-token"
    },
    expectedVersion: {
      compareToken: "expected-token"
    },
    diagnostics: ["concurrency_conflict:compare_token_mismatch"]
  });
  assert.equal(conflict?.conflict, true);
  assert.equal(conflict?.conflictReason, "compare_token_mismatch");
  assert.equal(conflict?.diagnostics.length, 1);
  assert.equal(extractTemplateOverrideConflictPayload({ conflict: false }), null);
  logPass("template override editor ui: conflict payload extraction is stable");

  const rollbackPayload = buildRollbackExecutePayloadFromPreview({
    templateId: "template-1",
    overrideType: "alert_rules",
    preview: buildRollbackPreview("alert_rules")
  });
  assert.ok(rollbackPayload);
  assert.equal(rollbackPayload?.targetAuditId, "audit-1");
  assert.equal(rollbackPayload?.targetVersionNumber, 2);
  assert.equal(rollbackPayload?.expectedVersion.compareToken, "ovc_v1_token");
  logPass("template override editor ui: rollback execute payload can be built from preview");

  const missingExpected = buildRollbackExecutePayloadFromPreview({
    templateId: "template-1",
    overrideType: "alert_rules",
    preview: {
      ...buildRollbackPreview("alert_rules"),
      concurrency: {
        baseline: null,
        expectedVersion: null,
        note: "missing"
      }
    }
  });
  assert.equal(missingExpected, null);
  logPass("template override editor ui: rollback execute payload rejects incomplete preview");
}
