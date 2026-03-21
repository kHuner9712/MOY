import test from "node:test";
import assert from "node:assert/strict";

import { getAlertDedupeDecision } from "../lib/alert-dedupe";

test("dedupe decision should upgrade severity and mark update", () => {
  const decision = getAlertDedupeDecision({
    existing: {
      source: "rule",
      severity: "warning",
      title: "旧标题",
      description: "旧描述",
      evidence: ["旧证据"],
      suggested_owner_action: ["旧动作"]
    },
    incoming: {
      source: "ai",
      level: "critical",
      title: "新标题",
      description: "新描述",
      evidence: ["新证据"],
      suggestedOwnerAction: ["新动作"]
    }
  });

  assert.equal(decision.shouldUpdate, true);
  assert.equal(decision.shouldUpgradeSeverity, true);
  assert.equal(decision.nextSource, "hybrid");
});

test("dedupe decision should skip update when content is unchanged", () => {
  const decision = getAlertDedupeDecision({
    existing: {
      source: "rule",
      severity: "warning",
      title: "同标题",
      description: "同描述",
      evidence: ["证据"],
      suggested_owner_action: ["动作"]
    },
    incoming: {
      source: "rule",
      level: "warning",
      title: "同标题",
      description: "同描述",
      evidence: ["证据"],
      suggestedOwnerAction: ["动作"]
    }
  });

  assert.equal(decision.shouldUpdate, false);
  assert.equal(decision.shouldUpgradeSeverity, false);
  assert.equal(decision.nextSource, "rule");
});
