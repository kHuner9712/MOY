import assert from "node:assert/strict";

import {
  buildOrgOverrideWriteDiagnosticsSummary,
  buildOrgTemplateOverrideWriteAuditDraft,
  prepareOrgTemplateOverrideWrite
} from "../lib/org-override-write-governance";
import { canManageOrgCustomization, canManageTemplates } from "../lib/role-capability";

export function runOrgOverrideWritePathGovernanceTests(logPass: (name: string) => void): void {
  const acceptedRuntimeOverride = prepareOrgTemplateOverrideWrite({
    overrideType: "alert_rules",
    overridePayload: {
      rules: {
        no_followup_timeout: 4,
        ignored_unknown_rule: 99
      }
    }
  });
  assert.equal(acceptedRuntimeOverride.writeDiagnostics.acceptedForWrite, true);
  assert.equal(acceptedRuntimeOverride.writeDiagnostics.acceptedForRuntime, true);
  assert.equal(acceptedRuntimeOverride.writeDiagnostics.runtimeImpactSummary, "runtime_consumed");
  assert.deepEqual(acceptedRuntimeOverride.writeDiagnostics.normalizedPayload, {
    rules: {
      no_followup_timeout: 4
    }
  });
  assert.ok(acceptedRuntimeOverride.writeDiagnostics.ignoredFields.includes("rules.ignored_unknown_rule"));
  const acceptedSummary = buildOrgOverrideWriteDiagnosticsSummary([acceptedRuntimeOverride.writeDiagnostics]);
  assert.equal(acceptedSummary.acceptedOverrides.length, 1);
  assert.equal(acceptedSummary.ignoredOverrides.length, 0);
  assert.equal(acceptedSummary.runtimeImpactCounters.runtimeConsumed, 1);
  assert.ok(acceptedSummary.diagnostics.includes("alert_rules:ignored_alert_rule_key:ignored_unknown_rule"));
  logPass("org override write governance: accepted runtime override exposes ignored fields and diagnostics");

  const forbiddenRuntimeOverride = prepareOrgTemplateOverrideWrite({
    overrideType: "customer_stages",
    overridePayload: {
      items: ["lead", "proposal", "won"]
    }
  });
  assert.equal(forbiddenRuntimeOverride.writeDiagnostics.acceptedForWrite, true);
  assert.equal(forbiddenRuntimeOverride.writeDiagnostics.acceptedForRuntime, false);
  assert.equal(forbiddenRuntimeOverride.writeDiagnostics.forbiddenForRuntime, true);
  assert.equal(forbiddenRuntimeOverride.writeDiagnostics.ignoredByRuntime, true);
  assert.equal(
    forbiddenRuntimeOverride.writeDiagnostics.runtimeImpactSummary,
    "runtime_ignored_forbidden_core_semantics"
  );
  const forbiddenSummary = buildOrgOverrideWriteDiagnosticsSummary([forbiddenRuntimeOverride.writeDiagnostics]);
  assert.equal(forbiddenSummary.ignoredOverrides.length, 1);
  assert.equal(forbiddenSummary.forbiddenOverrides.length, 1);
  assert.equal(forbiddenSummary.runtimeImpactCounters.runtimeIgnored, 1);
  logPass("org override write governance: forbidden override is explicitly marked ignored by runtime");

  const rejectedByPayload = prepareOrgTemplateOverrideWrite({
    overrideType: "brief_preferences",
    overridePayload: {
      items: []
    }
  });
  assert.equal(rejectedByPayload.writeDiagnostics.acceptedForWrite, false);
  assert.equal(rejectedByPayload.writeDiagnostics.runtimeImpactSummary, "write_rejected");

  const rejectedByType = prepareOrgTemplateOverrideWrite({
    overrideType: "unsupported_override_type" as never,
    overridePayload: {
      any: "value"
    }
  });
  assert.equal(rejectedByType.writeDiagnostics.acceptedForWrite, false);
  assert.equal(rejectedByType.writeDiagnostics.reason, "unknown_override_type");

  const rejectedSummary = buildOrgOverrideWriteDiagnosticsSummary([
    rejectedByPayload.writeDiagnostics,
    rejectedByType.writeDiagnostics
  ]);
  assert.equal(rejectedSummary.rejectedOverrides.length, 2);
  assert.equal(rejectedSummary.runtimeImpactCounters.writeRejected, 2);
  logPass("org override write governance: invalid payload/type are rejected before write");

  const auditDraft = buildOrgTemplateOverrideWriteAuditDraft({
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
    afterPayload: acceptedRuntimeOverride.writeDiagnostics.normalizedPayload,
    writeDiagnostics: acceptedRuntimeOverride.writeDiagnostics,
    happenedAt: "2026-03-21T12:00:00.000Z"
  });
  assert.equal(auditDraft.version, 1);
  assert.equal(auditDraft.orgId, "org-1");
  assert.equal(auditDraft.actorUserId, "user-1");
  assert.equal(auditDraft.targetRef.overrideType, "alert_rules");
  assert.equal(auditDraft.happenedAt, "2026-03-21T12:00:00.000Z");
  assert.ok(auditDraft.beforeSummary?.payloadKeys.includes("rules.no_followup_timeout"));
  assert.ok(auditDraft.afterSummary?.payloadKeys.includes("rules.no_followup_timeout"));
  assert.equal(auditDraft.diagnosticsSummary.runtimeImpactSummary, "runtime_consumed");
  logPass("org override write governance: audit draft summary is deterministic");

  const owner = { role: "manager" as const, orgRole: "owner" as const };
  const admin = { role: "manager" as const, orgRole: "admin" as const };
  const manager = { role: "manager" as const, orgRole: "manager" as const };
  const sales = { role: "sales" as const, orgRole: "sales" as const };
  const viewer = { role: "sales" as const, orgRole: "viewer" as const };
  assert.equal(canManageTemplates(owner), true);
  assert.equal(canManageTemplates(admin), true);
  assert.equal(canManageTemplates(manager), false);
  assert.equal(canManageTemplates(sales), false);
  assert.equal(canManageTemplates(viewer), false);
  assert.equal(canManageOrgCustomization(owner), true);
  assert.equal(canManageOrgCustomization(admin), true);
  assert.equal(canManageOrgCustomization(manager), false);
  assert.equal(canManageOrgCustomization(sales), false);
  assert.equal(canManageOrgCustomization(viewer), false);
  logPass("org override write governance: owner/admin and low-role write boundary");
}

