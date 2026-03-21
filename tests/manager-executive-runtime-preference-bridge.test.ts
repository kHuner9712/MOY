import assert from "node:assert/strict";

import type { BusinessEvent } from "../types/automation";
import {
  applyExecutiveEventPreference,
  applyManagerActionPreference,
  applyReportFocusOverlay,
  buildManagerVisibilityRuntimeContext,
  buildResolvedIndustryTemplateContext
} from "../services/template-org-runtime-bridge-service";

function buildEvent(overrides: Partial<BusinessEvent>): BusinessEvent {
  const now = new Date().toISOString();
  return {
    id: "event-default",
    orgId: "org-1",
    entityType: "customer",
    entityId: "customer-1",
    eventType: "no_recent_touchpoint",
    severity: "warning",
    eventSummary: "Default warning event",
    eventPayload: {},
    status: "open",
    createdAt: now,
    updatedAt: now,
    ...overrides
  };
}

export function runManagerExecutiveRuntimePreferenceBridgeTests(logPass: (name: string) => void): void {
  const saasTemplateContext = buildResolvedIndustryTemplateContext({
    templateKey: "saas_subscription"
  });
  const runtimeVisibilityContext = buildManagerVisibilityRuntimeContext({
    context: saasTemplateContext,
    reportType: "manager_weekly"
  });

  assert.equal(runtimeVisibilityContext.fallbackToBase, false);
  assert.equal(runtimeVisibilityContext.templateKey, "saas_subscription");
  assert.ok(runtimeVisibilityContext.managerFocusMetricPriority.includes("trial_activation_rate_7d"));
  assert.ok(runtimeVisibilityContext.reportMetricPriority.length > 0);
  logPass("manager-executive runtime: context resolution from template + org overlay");

  const warningEvents: BusinessEvent[] = [
    buildEvent({
      id: "event-no-touchpoint",
      eventType: "no_recent_touchpoint",
      severity: "warning",
      createdAt: "2026-03-20T00:00:00.000Z"
    }),
    buildEvent({
      id: "event-trial-stalled",
      eventType: "trial_stalled",
      severity: "warning",
      createdAt: "2026-03-20T00:00:01.000Z"
    }),
    buildEvent({
      id: "event-renewal-risk",
      eventType: "renewal_risk_detected",
      severity: "warning",
      createdAt: "2026-03-20T00:00:02.000Z"
    })
  ];
  const preferredEvents = applyExecutiveEventPreference({
    events: warningEvents,
    context: runtimeVisibilityContext
  });
  const weightById = new Map(
    warningEvents.map((item) => [item.id, runtimeVisibilityContext.eventTypePreferenceWeights[item.eventType] ?? 0])
  );
  const topWeight = Math.max(...Array.from(weightById.values()));
  assert.equal(weightById.get(preferredEvents[0]?.id ?? ""), topWeight);
  logPass("manager-executive runtime: executive risk ordering consumes runtime preference");

  const metricsSnapshot = {
    open_alerts: 5,
    high_risk_alerts: 2,
    ai_runs_count: 11
  };
  const sourceSnapshot = {
    period: {
      start: "2026-03-14",
      end: "2026-03-20"
    }
  };
  const reportOverlay = applyReportFocusOverlay({
    reportType: "manager_weekly",
    metricsSnapshot,
    sourceSnapshot,
    context: runtimeVisibilityContext
  });
  assert.deepEqual(reportOverlay.metricsSnapshot, metricsSnapshot);
  assert.ok("runtime_preference_overlay" in reportOverlay.sourceSnapshot);
  assert.ok(
    Array.isArray(
      (reportOverlay.sourceSnapshot.runtime_preference_overlay as Record<string, unknown>).highlight_metric_keys
    )
  );
  logPass("manager-executive runtime: report focus overlay keeps base metric semantics");

  const actionOverlay = applyManagerActionPreference({
    actions: ["Run weekly review with team"],
    context: runtimeVisibilityContext,
    limit: 2
  });
  assert.ok(actionOverlay.some((item) => item.startsWith("Priority action: ")));
  assert.ok(actionOverlay.includes("Run weekly review with team"));
  logPass("manager-executive runtime: manager action priority overlay");

  const fallbackTemplateContext = buildResolvedIndustryTemplateContext({
    templateKey: "unknown_template"
  });
  const fallbackRuntimeContext = buildManagerVisibilityRuntimeContext({
    context: fallbackTemplateContext,
    reportType: "sales_daily"
  });
  const fallbackEvents = applyExecutiveEventPreference({
    events: warningEvents,
    context: fallbackRuntimeContext
  });
  assert.deepEqual(
    fallbackEvents.map((item) => item.id),
    warningEvents.map((item) => item.id)
  );
  const fallbackActions = applyManagerActionPreference({
    actions: ["Keep base manager review path"],
    context: fallbackRuntimeContext
  });
  assert.deepEqual(fallbackActions, ["Keep base manager review path"]);
  logPass("manager-executive runtime: fallback context keeps default behavior");
}
