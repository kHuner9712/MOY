import assert from "node:assert/strict";

import {
  extractOrgConfigConflictPayload,
  resolveOrgConfigEditorAccess
} from "../lib/org-config-editor-ui";
import {
  buildOrgConfigEditorSectionState,
  previewOrgConfigWrite
} from "../services/org-config-editor-service";
import type {
  LatestOrgConfigAuditVersionResult,
  RecentOrgConfigAuditLogResult
} from "../services/org-config-audit-service";
import type { OrgConfigAuditLog } from "../types/productization";

function buildAuditLog(overrides: Partial<OrgConfigAuditLog> = {}): OrgConfigAuditLog {
  return {
    id: "audit-1",
    orgId: "org-1",
    actorUserId: "user-1",
    targetType: "org_settings",
    targetId: "org-settings-1",
    targetKey: "default",
    actionType: "update",
    beforeSummary: {},
    afterSummary: {},
    diagnosticsSummary: {
      diagnostics: ["ignored_unknown_field:unknownField"],
      acceptedFields: ["defaultAlertRules"],
      ignoredFields: ["unknownField"],
      forbiddenFields: [],
      runtimeImpactSummary: "runtime_partial_ignored"
    },
    versionNumber: 2,
    versionLabel: "org_settings:default:v2",
    snapshotSummary: {},
    createdAt: "2026-03-22T09:00:00.000Z",
    ...overrides
  };
}

export function runOrgConfigEditorUiTests(logPass: (name: string) => void): void {
  const owner = resolveOrgConfigEditorAccess({
    user: { role: "manager", orgRole: "owner" }
  });
  const admin = resolveOrgConfigEditorAccess({
    user: { role: "manager", orgRole: "admin" }
  });
  const manager = resolveOrgConfigEditorAccess({
    user: { role: "manager", orgRole: "manager" }
  });
  const sales = resolveOrgConfigEditorAccess({
    user: { role: "sales", orgRole: "sales" }
  });
  const viewer = resolveOrgConfigEditorAccess({
    user: { role: "sales", orgRole: "viewer" }
  });
  assert.equal(owner.canAccess, true);
  assert.equal(owner.canWrite, true);
  assert.equal(admin.canAccess, true);
  assert.equal(admin.canWrite, true);
  assert.equal(manager.canAccess, true);
  assert.equal(manager.canWrite, false);
  assert.equal(sales.canAccess, false);
  assert.equal(viewer.canAccess, false);
  logPass("org config editor ui: owner/admin writable, manager read-only, sales/viewer denied");

  const preview = previewOrgConfigWrite({
    targetType: "org_settings",
    patch: {
      defaultAlertRules: {
        no_followup_timeout: 4,
        unsupported_rule: 99
      },
      unknownField: "ignored"
    }
  });
  assert.equal(preview.targetType, "org_settings");
  assert.ok(preview.writeDiagnostics.acceptedFields.includes("defaultAlertRules"));
  assert.ok(preview.writeDiagnostics.ignoredFields.includes("unknownField"));
  assert.ok(preview.writeDiagnostics.diagnostics.some((item) => item.includes("ignored_alert_rule_key:unsupported_rule")));
  assert.ok(preview.diagnosticsSummary.runtimeImpactSummary.length > 0);
  logPass("org config editor ui: preview exposes accepted/ignored/diagnostics summary");

  const conflict = extractOrgConfigConflictPayload({
    conflict: true,
    conflictReason: "compare_token_mismatch",
    currentVersion: { compareToken: "current_token" },
    expectedVersion: { compareToken: "expected_token" },
    diagnostics: ["concurrency_conflict:compare_token_mismatch:expected=expected_token:current=current_token"]
  });
  assert.equal(conflict?.conflict, true);
  assert.equal(conflict?.conflictReason, "compare_token_mismatch");
  assert.equal(String(conflict?.currentVersion?.compareToken), "current_token");
  assert.equal(String(conflict?.expectedVersion?.compareToken), "expected_token");
  assert.ok((conflict?.diagnostics.length ?? 0) > 0);
  logPass("org config editor ui: conflict payload is recognized for frontend conflict banner");

  const latestVersion: LatestOrgConfigAuditVersionResult = {
    availability: "available",
    item: {
      id: "audit-1",
      versionLabel: "org_settings:default:v2",
      versionNumber: 2,
      createdAt: "2026-03-22T09:00:00.000Z"
    },
    note: "latest exists"
  };
  const recentAvailable: RecentOrgConfigAuditLogResult = {
    availability: "available",
    note: "latest 1",
    items: [buildAuditLog()]
  };
  const sectionWithHistory = buildOrgConfigEditorSectionState({
    targetType: "org_settings",
    targetKey: "default",
    currentPayload: {
      defaultAlertRules: {
        no_followup_timeout: 4
      }
    },
    updatedAt: "2026-03-22T09:00:00.000Z",
    latestVersion,
    recentAudits: recentAvailable
  });
  assert.equal(sectionWithHistory.recentAudits.availability, "available");
  assert.equal(sectionWithHistory.recentAudits.items.length, 1);
  assert.ok(sectionWithHistory.recentAudits.items[0]?.diagnosticsPreview.length);
  assert.ok(sectionWithHistory.expectedVersion.compareToken);
  logPass("org config editor ui: recent persisted audit summary is available for display");

  const noHistoryVersion: LatestOrgConfigAuditVersionResult = {
    availability: "empty",
    item: null,
    note: "no version"
  };
  const recentEmpty: RecentOrgConfigAuditLogResult = {
    availability: "empty",
    note: "no history",
    items: []
  };
  const sectionWithoutHistory = buildOrgConfigEditorSectionState({
    targetType: "org_feature_flags",
    targetKey: "default",
    currentPayload: {
      ai_auto_analysis: true
    },
    updatedAt: null,
    latestVersion: noHistoryVersion,
    recentAudits: recentEmpty
  });
  assert.equal(sectionWithoutHistory.recentAudits.availability, "empty");
  assert.equal(sectionWithoutHistory.recentAudits.items.length, 0);
  assert.equal(sectionWithoutHistory.latestDiagnosticsSummary, null);
  assert.ok(sectionWithoutHistory.expectedVersion.compareToken);
  logPass("org config editor ui: no history state degrades gracefully");
}
