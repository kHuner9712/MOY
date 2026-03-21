import assert from "node:assert/strict";

import {
  buildExpectedVersionFromConcurrencyBaseline,
  buildOverrideConcurrencyBaseline,
  validateOverrideExpectedVersion
} from "../lib/override-concurrency-guard";

export function runOverrideConcurrencyGuardTests(logPass: (name: string) => void): void {
  const baseline = buildOverrideConcurrencyBaseline({
    templateId: "template-1",
    overrideType: "alert_rules",
    auditAvailability: "available",
    currentVersionLabel: "org_template_override:template-1:alert_rules:v2",
    currentVersionNumber: 2,
    currentOverrideUpdatedAt: "2026-03-21T11:00:00.000Z",
    currentPayload: {
      rules: {
        no_followup_timeout: 4
      }
    },
    generatedAt: "2026-03-21T11:30:00.000Z"
  });
  const expected = buildExpectedVersionFromConcurrencyBaseline(baseline);
  const passResult = validateOverrideExpectedVersion({
    expectedVersion: expected,
    currentBaseline: baseline
  });
  assert.equal(passResult.conflict, false);
  assert.equal(passResult.info, null);
  logPass("override concurrency guard: baseline roundtrip validation passes");

  const driftedBaseline = buildOverrideConcurrencyBaseline({
    templateId: "template-1",
    overrideType: "alert_rules",
    auditAvailability: "available",
    currentVersionLabel: "org_template_override:template-1:alert_rules:v3",
    currentVersionNumber: 3,
    currentOverrideUpdatedAt: "2026-03-21T12:30:00.000Z",
    currentPayload: {
      rules: {
        no_followup_timeout: 6
      }
    },
    generatedAt: "2026-03-21T12:30:00.000Z"
  });
  const conflictResult = validateOverrideExpectedVersion({
    expectedVersion: expected,
    currentBaseline: driftedBaseline
  });
  assert.equal(conflictResult.conflict, true);
  assert.equal(conflictResult.info?.conflict, true);
  assert.ok(conflictResult.info?.currentVersion.compareToken);
  assert.ok(conflictResult.info?.expectedVersion.compareToken);
  assert.ok(
    conflictResult.info?.diagnostics.some((item) => item.includes("concurrency_conflict:"))
  );
  logPass("override concurrency guard: drift mismatch returns structured conflict diagnostics");
}
