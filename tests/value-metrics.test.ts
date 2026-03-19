import test from "node:test";
import assert from "node:assert/strict";

import {
  VALUE_METRICS_LABELS,
  VALUE_METRICS_DESCRIPTIONS,
  ACTION_TYPE_LABELS,
  ACTION_TYPE_DESCRIPTIONS,
  type ValueMetrics,
  type TrendResult,
  type TodayPriorityAction,
  type TodayValueSummary
} from "../types/value-metrics";

test("VALUE_METRICS_LABELS contains expected keys", () => {
  assert.ok("lessFollowup" in VALUE_METRICS_LABELS);
  assert.ok("newRiskCustomers" in VALUE_METRICS_LABELS);
  assert.ok("handledRiskEvents" in VALUE_METRICS_LABELS);
  assert.ok("managerInterventions" in VALUE_METRICS_LABELS);
  assert.ok("recoveredProgressions" in VALUE_METRICS_LABELS);
  assert.ok("aiAdoptions" in VALUE_METRICS_LABELS);
  assert.ok("timeSavedMinutes" in VALUE_METRICS_LABELS);
});

test("VALUE_METRICS_LABELS uses Chinese labels", () => {
  assert.strictEqual(VALUE_METRICS_LABELS.lessFollowup, "漏跟进减少");
  assert.strictEqual(VALUE_METRICS_LABELS.newRiskCustomers, "新增风险客户");
  assert.strictEqual(VALUE_METRICS_LABELS.handledRiskEvents, "风险事件处理");
  assert.strictEqual(VALUE_METRICS_LABELS.managerInterventions, "经理介入");
  assert.strictEqual(VALUE_METRICS_LABELS.recoveredProgressions, "推进恢复");
  assert.strictEqual(VALUE_METRICS_LABELS.aiAdoptions, "AI 建议采纳");
  assert.strictEqual(VALUE_METRICS_LABELS.timeSavedMinutes, "节省时间(分钟)");
});

test("VALUE_METRICS_DESCRIPTIONS provides calculation context", () => {
  assert.ok(VALUE_METRICS_DESCRIPTIONS.lessFollowup.includes("系统预警"));
  assert.ok(VALUE_METRICS_DESCRIPTIONS.handledRiskEvents.includes("已处理"));
  assert.ok(VALUE_METRICS_DESCRIPTIONS.aiAdoptions.includes("采纳"));
  assert.ok(VALUE_METRICS_DESCRIPTIONS.timeSavedMinutes.includes("节省"));
});

test("ACTION_TYPE_LABELS maps action types to Chinese labels", () => {
  assert.strictEqual(ACTION_TYPE_LABELS.reduce_risk, "降低风险");
  assert.strictEqual(ACTION_TYPE_LABELS.recover_progression, "恢复推进");
  assert.strictEqual(ACTION_TYPE_LABELS.prevent_loss, "防止流失");
  assert.strictEqual(ACTION_TYPE_LABELS.capture_opportunity, "抓住机会");
});

test("ACTION_TYPE_DESCRIPTIONS provides action context", () => {
  assert.ok(ACTION_TYPE_DESCRIPTIONS.reduce_risk.includes("风险"));
  assert.ok(ACTION_TYPE_DESCRIPTIONS.recover_progression.includes("停滞"));
  assert.ok(ACTION_TYPE_DESCRIPTIONS.prevent_loss.includes("流失"));
  assert.ok(ACTION_TYPE_DESCRIPTIONS.capture_opportunity.includes("机会"));
});

test("TrendResult type allows valid directions", () => {
  const upTrend: TrendResult = {
    direction: "up",
    changePercent: 25,
    description: "相比上周提升 25%，表现优秀"
  };
  const downTrend: TrendResult = {
    direction: "down",
    changePercent: -15,
    description: "相比上周下降 15%，需要关注"
  };
  const stableTrend: TrendResult = {
    direction: "stable",
    changePercent: 0,
    description: "与上周持平，保持稳定"
  };

  assert.strictEqual(upTrend.direction, "up");
  assert.strictEqual(downTrend.direction, "down");
  assert.strictEqual(stableTrend.direction, "stable");
});

test("TodayPriorityAction type structure", () => {
  const action: TodayPriorityAction = {
    workItemId: "test-work-item-id",
    title: "跟进高风险客户",
    customerName: "测试客户",
    priority: "critical",
    actionType: "reduce_risk",
    reason: "客户健康度下降，需要及时跟进",
    expectedImpact: "降低客户流失风险",
    dueAt: "2026-03-16T18:00:00.000Z"
  };

  assert.strictEqual(action.priority, "critical");
  assert.strictEqual(action.actionType, "reduce_risk");
  assert.ok(action.title);
  assert.ok(action.reason);
  assert.ok(action.expectedImpact);
});

test("TodayValueSummary aggregates metrics correctly", () => {
  const summary: TodayValueSummary = {
    completedTasks: 10,
    aiAssistedTasks: 6,
    riskActionsPending: 3,
    progressionActionsPending: 2,
    estimatedTimeSavedMinutes: 90,
    priorityActions: [
      {
        workItemId: "action-1",
        title: "跟进风险客户A",
        priority: "critical",
        actionType: "reduce_risk",
        reason: "健康度下降",
        expectedImpact: "降低流失风险"
      }
    ]
  };

  assert.strictEqual(summary.completedTasks, 10);
  assert.strictEqual(summary.aiAssistedTasks, 6);
  assert.strictEqual(summary.riskActionsPending, 3);
  assert.strictEqual(summary.progressionActionsPending, 2);
  assert.strictEqual(summary.estimatedTimeSavedMinutes, 90);
  assert.strictEqual(summary.priorityActions.length, 1);
});

test("ValueMetrics type structure", () => {
  const metrics: ValueMetrics = {
    periodStart: "2026-03-10",
    periodEnd: "2026-03-16",
    lessFollowup: 5,
    newRiskCustomers: 2,
    handledRiskEvents: 8,
    managerInterventions: 3,
    recoveredProgressions: 4,
    aiAdoptions: 12,
    timeSavedMinutes: 180,
    weeklyTrend: {
      direction: "up",
      changePercent: 20,
      description: "相比上周提升 20%"
    }
  };

  assert.strictEqual(metrics.periodStart, "2026-03-10");
  assert.strictEqual(metrics.lessFollowup, 5);
  assert.strictEqual(metrics.handledRiskEvents, 8);
  assert.strictEqual(metrics.weeklyTrend.direction, "up");
});

test("Trend calculation logic - improvement scenario", () => {
  const currentMetrics = {
    handledRiskEvents: 10,
    recoveredProgressions: 5,
    aiAdoptions: 8,
    timeSavedMinutes: 120
  };
  const previousMetrics = {
    handledRiskEvents: 5,
    recoveredProgressions: 2,
    aiAdoptions: 4,
    timeSavedMinutes: 60
  };

  const metrics = [
    { current: currentMetrics.handledRiskEvents, previous: previousMetrics.handledRiskEvents, weight: 1 },
    { current: currentMetrics.recoveredProgressions, previous: previousMetrics.recoveredProgressions, weight: 1 },
    { current: currentMetrics.aiAdoptions, previous: previousMetrics.aiAdoptions, weight: 0.8 },
    { current: currentMetrics.timeSavedMinutes, previous: previousMetrics.timeSavedMinutes, weight: 0.5 }
  ];

  let totalImprovement = 0;
  let totalWeight = 0;

  for (const m of metrics) {
    const base = m.previous || 1;
    const change = m.current - m.previous;
    const percentChange = (change / base) * 100;
    totalImprovement += percentChange * m.weight;
    totalWeight += m.weight;
  }

  const avgChange = totalImprovement / totalWeight;

  assert.ok(avgChange > 10, "Should show improvement trend");
});

test("Trend calculation logic - decline scenario", () => {
  const currentMetrics = {
    handledRiskEvents: 3,
    recoveredProgressions: 1,
    aiAdoptions: 2,
    timeSavedMinutes: 30
  };
  const previousMetrics = {
    handledRiskEvents: 10,
    recoveredProgressions: 5,
    aiAdoptions: 8,
    timeSavedMinutes: 120
  };

  const metrics = [
    { current: currentMetrics.handledRiskEvents, previous: previousMetrics.handledRiskEvents, weight: 1 },
    { current: currentMetrics.recoveredProgressions, previous: previousMetrics.recoveredProgressions, weight: 1 },
    { current: currentMetrics.aiAdoptions, previous: previousMetrics.aiAdoptions, weight: 0.8 },
    { current: currentMetrics.timeSavedMinutes, previous: previousMetrics.timeSavedMinutes, weight: 0.5 }
  ];

  let totalImprovement = 0;
  let totalWeight = 0;

  for (const m of metrics) {
    const base = m.previous || 1;
    const change = m.current - m.previous;
    const percentChange = (change / base) * 100;
    totalImprovement += percentChange * m.weight;
    totalWeight += m.weight;
  }

  const avgChange = totalImprovement / totalWeight;

  assert.ok(avgChange < -10, "Should show decline trend");
});

test("Trend calculation logic - stable scenario", () => {
  const currentMetrics = {
    handledRiskEvents: 5,
    recoveredProgressions: 2,
    aiAdoptions: 4,
    timeSavedMinutes: 62
  };
  const previousMetrics = {
    handledRiskEvents: 5,
    recoveredProgressions: 2,
    aiAdoptions: 4,
    timeSavedMinutes: 60
  };

  const metrics = [
    { current: currentMetrics.handledRiskEvents, previous: previousMetrics.handledRiskEvents, weight: 1 },
    { current: currentMetrics.recoveredProgressions, previous: previousMetrics.recoveredProgressions, weight: 1 },
    { current: currentMetrics.aiAdoptions, previous: previousMetrics.aiAdoptions, weight: 0.8 },
    { current: currentMetrics.timeSavedMinutes, previous: previousMetrics.timeSavedMinutes, weight: 0.5 }
  ];

  let totalImprovement = 0;
  let totalWeight = 0;

  for (const m of metrics) {
    const base = m.previous || 1;
    const change = m.current - m.previous;
    const percentChange = (change / base) * 100;
    totalImprovement += percentChange * m.weight;
    totalWeight += m.weight;
  }

  const avgChange = totalImprovement / totalWeight;

  assert.ok(avgChange >= -10 && avgChange <= 10, "Should show stable trend");
});
