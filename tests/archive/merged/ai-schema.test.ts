import test from "node:test";
import assert from "node:assert/strict";

import { followupAnalysisResultSchema, leakAlertInferenceResultSchema } from "../types/ai";

test("followup analysis schema should parse valid payload", () => {
  const parsed = followupAnalysisResultSchema.parse({
    customer_status_summary: "客户态度积极，等待预算确认。",
    key_needs: ["试点上线计划"],
    key_objections: ["预算审批周期长"],
    buying_signals: ["客户要求下周演示"],
    risk_level: "medium",
    leak_risk: "medium",
    leak_reasons: ["超过 5 天未推进"],
    next_best_actions: ["安排决策人会议"],
    recommended_next_followup_at: new Date().toISOString(),
    manager_attention_needed: false,
    confidence_score: 0.78,
    reasoning_brief: "推进存在延迟，但意向仍在。"
  });

  assert.equal(parsed.risk_level, "medium");
});

test("leak inference schema should reject unknown rule type", () => {
  assert.throws(() => {
    leakAlertInferenceResultSchema.parse({
      should_create_alert: true,
      severity: "warning",
      primary_rule_type: "unknown_rule",
      title: "风险",
      description: "描述",
      evidence: [],
      suggested_owner_action: [],
      due_at: null
    });
  });
});
