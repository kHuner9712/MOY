import assert from "node:assert/strict";

import { matchAutomationRuleTargets } from "../lib/automation-ops";
import { decideCaptureApplyMode } from "../lib/capture-flow";
import {
  buildCaptureBusinessEventPayload,
  buildCaptureBusinessEventTargets,
  type CaptureBusinessEventSignal
} from "../services/business-event-service";

export function runGoldenPathClosedLoopTraceTests(logPass: (name: string) => void): void {
  const applyMode = decideCaptureApplyMode({
    shouldCreateFollowup: true,
    hasMatchedCustomer: true,
    extractionConfidence: 0.93,
    matchConfidence: 0.9,
    hasSummary: true,
    hasNextStep: true
  });
  assert.equal(applyMode, "auto");

  const signal: CaptureBusinessEventSignal = {
    customerId: "customer-1",
    ownerId: "owner-1",
    communicationInputId: "input-1",
    followupId: "followup-1",
    lifecycle: "capture_auto_confirmed",
    extraction: {
      summary: "Customer accepted value framing, but still worries about budget cycle and asks for phased rollout.",
      buying_signals: ["Requested rollout timeline", "Asked for contract review call"],
      key_objections: ["Budget approval cycle"],
      uncertainty_notes: ["Procurement contact not confirmed"],
      should_trigger_alert_review: true
    }
  };

  const targets = buildCaptureBusinessEventTargets(signal);
  assert.equal(targets.length, 2);

  const conversion = targets.find((item) => item.eventType === "conversion_signal");
  const escalation = targets.find((item) => item.eventType === "manager_attention_escalated");
  assert.ok(conversion);
  assert.ok(escalation);

  assert.equal(conversion?.entityId, signal.customerId);
  assert.ok(conversion?.evidence.some((item) => item.includes("communication_input_id=input-1")));
  assert.ok(conversion?.evidence.some((item) => item.includes("followup_id=followup-1")));

  assert.equal(escalation?.severity, "critical");
  assert.ok(escalation?.evidence.some((item) => item.includes("should_trigger_alert_review=true")));

  const escalationPayload = buildCaptureBusinessEventPayload({
    input: signal,
    target: escalation as NonNullable<typeof escalation>
  });
  assert.equal(escalationPayload.source_ref_type, "communication_input");
  assert.equal(escalationPayload.source_ref_id, "input-1");
  assert.equal(escalationPayload.followup_id, "followup-1");
  assert.equal(escalationPayload.customer_id, "customer-1");

  const extractionSnapshot = (escalationPayload.extraction_snapshot ?? {}) as Record<string, unknown>;
  assert.equal(extractionSnapshot.buying_signal_count, 2);
  assert.equal(extractionSnapshot.objection_count, 1);
  assert.equal(extractionSnapshot.uncertainty_note_count, 1);
  assert.equal(extractionSnapshot.should_trigger_alert_review, true);

  const matchedManagerAttention = matchAutomationRuleTargets({
    ruleKey: "manager_attention_no_new_action",
    eventTargets: targets
  });
  assert.equal(matchedManagerAttention.length, 1);
  assert.equal(matchedManagerAttention[0]?.eventType, "manager_attention_escalated");

  const matchedHighRisk = matchAutomationRuleTargets({
    ruleKey: "high_risk_customer_inactivity",
    eventTargets: targets
  });
  assert.equal(matchedHighRisk.length, 0);

  logPass("golden path closed loop: capture trace to business event contracts");

  const noSignalTargets = buildCaptureBusinessEventTargets({
    ...signal,
    communicationInputId: "input-2",
    followupId: "followup-2",
    extraction: {
      summary: "Routine check-in and no blocking risks reported.",
      buying_signals: [],
      key_objections: [],
      uncertainty_notes: [],
      should_trigger_alert_review: false
    }
  });
  assert.equal(noSignalTargets.length, 0);

  logPass("golden path closed loop: deterministic no-signal branch");
}
