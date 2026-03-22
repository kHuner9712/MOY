import assert from "node:assert/strict";

import { buildFallbackDailyPlan } from "../lib/daily-plan-fallback";
import {
  buildFallbackDealPlaybookMapping,
  buildFallbackDealRoomCommandSummary,
  buildFallbackDecisionSupport,
  buildFallbackInterventionRecommendation,
  buildFallbackThreadSummary
} from "../lib/deal-command-fallback";
import { deriveDecisionApprovalLinkage } from "../lib/deal-decision-linkage";
import { buildBlockedCheckpointLinkage } from "../lib/deal-checkpoint-linkage";
import { computeBehaviorQualityMetrics, calculateShallowActivityRatio } from "../lib/behavior-quality";
import { computeOutcomeOverview } from "../lib/closed-loop";
import { buildFallbackUserCoachingReport } from "../lib/coaching-fallback";
import { decideCaptureApplyMode } from "../lib/capture-flow";
import { getAlertDedupeDecision } from "../lib/alert-dedupe";
import { evaluateAlertRules } from "../lib/alert-rules";
import { normalizeDeepSeekContent, parseDeepSeekJsonText } from "../lib/ai/providers/deepseek";
import { buildFallbackOutcomeAssist } from "../lib/outcome-fallback";
import { buildFallbackOutcomeReview, buildFallbackPersonalEffectivenessUpdate } from "../lib/outcome-review-fallback";
import { buildFallbackPlaybook } from "../lib/playbook-fallback";
import {
  buildFallbackDocumentSummary,
  buildFallbackEmailDraft,
  buildFallbackExternalTouchpointReview,
  buildFallbackMeetingAgenda,
  evaluateNoRecentTouchpoint,
  evaluateWaitingReplyNeed
} from "../lib/external-touchpoint-fallback";
import { pickAutoCompletableTaskIdsAfterFollowup } from "../lib/work-item-linkage";
import { buildFallbackTeamRhythmInsight } from "../lib/team-rhythm-fallback";
import { computeTaskPriority } from "../lib/task-priority";
import { resolveWorkItemTransition } from "../lib/work-item-state";
import { buildWorkItemDraftFromAlert } from "../lib/work-item-builder";
import { deriveMemoryItemStatusFromFeedback } from "../lib/memory-feedback";
import { buildFallbackMemoryCompileResult } from "../lib/memory-fallback";
import { buildFallbackActionDraft, buildFallbackFollowupPrepCard, buildFallbackMorningBrief } from "../lib/preparation-fallback";
import { buildBriefingHubView, mapDraftCoverageByWorkItem, mapPrepCoverageByWorkItem } from "../lib/briefing-hub";
import { deriveContentDraftStatusFromFeedback, derivePrepCardStatusFromFeedback } from "../lib/preparation-feedback";
import { isInterventionStatusTransitionAllowed } from "../lib/intervention-request-flow";
import { buildFallbackReport } from "../lib/report-fallback";
import { mapFeedbackToAdoptionType } from "../lib/suggestion-adoption";
import { summarizeDemoSeedSteps } from "../lib/demo-seed-summary";
import { deriveAiActionAccess, deriveFeatureAccess } from "../lib/feature-access-utils";
import {
  canProcessDocumentsByEntitlement,
  canRunAiByEntitlement,
  hasSeatCapacity
} from "../lib/plan-entitlement-utils";
import {
  canViewOrgUsage,
  isOrgAdminRole,
  isSeatStatusTransitionAllowed
} from "../lib/org-membership-utils";
import { buildFallbackOnboardingRecommendation, buildFallbackUsageHealthSummary } from "../lib/productization-fallback";
import { runImportLayerTests } from "./import-layer.test";
import { runMobileLayerTests } from "./mobile-layer.test";
import { runTemplateLayerTests } from "./template-layer.test";
import { runIndustryTemplateFrameworkTests } from "./industry-template-framework.test";
import { runEnterpriseCustomizationFrameworkTests } from "./enterprise-customization-framework.test";
import { runTemplateOrgRuntimeBridgeTests } from "./template-org-runtime-bridge.test";
import { runManagerExecutiveRuntimePreferenceBridgeTests } from "./manager-executive-runtime-preference-bridge.test";
import { runOrgRuntimeConfigReadPathTests } from "./org-runtime-config-read-path.test";
import { runRuntimeConfigExplainHardeningTests } from "./runtime-config-explain-hardening.test";
import { runCommercializationLayerTests } from "./commercialization-layer.test";
import { runCommercialEntrySystemTests } from "./commercial-entry-system.test";
import { runCommercialReadinessGateTests } from "./commercial-readiness-gate.test";
import { runAutomationOpsLayerTests } from "./automation-ops-layer.test";
import { runGoldenPathClosedLoopTraceTests } from "./golden-path-closed-loop-trace.test";
import { runWorkItemActionHubTests } from "./work-item-action-hub.test";
import { runGoldenPathSmokeTests } from "./golden-path-smoke.test";
import { runRolePermissionModelTests } from "./role-permission-model.test";
import { runOrgOverrideWritePathGovernanceTests } from "./org-override-write-path-governance.test";
import { runOverrideConcurrencyGuardTests } from "./override-concurrency-guard.test";
import { runRuntimeExplainDebugPanelTests } from "./runtime-explain-debug-panel.test";
import { runPersistedAuditVersionSnapshotFoundationTests } from "./persisted-audit-version-snapshot-foundation.test";
import { runOrgTemplateOverrideRollbackTests } from "./org-template-override-rollback.test";
import { runOrgConfigRollbackTests } from "./org-config-rollback.test";
import { runTemplateOverrideEditorUiTests } from "./template-override-editor-ui.test";
import { runOrgConfigGovernanceExpansionTests } from "./org-config-governance-expansion.test";
import { runOrgConfigEditorUiTests } from "./org-config-editor-ui.test";
import { runConfigOperationsHubTests } from "./config-operations-hub.test";
import { runConfigTimelineDiffViewerTests } from "./config-timeline-diff-viewer.test";
import { pickBestCustomerMatch, scoreCustomerCandidate } from "../services/customer-match-service";
import {
  communicationExtractionResultSchema,
  followupAnalysisResultSchema,
  leakAlertInferenceResultSchema
} from "../types/ai";
import type { Customer } from "../types/customer";
import type { FollowupRecord } from "../types/followup";
import type { Opportunity } from "../types/opportunity";
import type { ContentDraft, MorningBrief, PrepCard } from "../types/preparation";
import type { EntitlementStatus } from "../types/productization";

function logPass(name: string): void {
  console.log(`PASS ${name}`);
}

function buildCustomer(overrides: Partial<Customer> = {}): Customer {
  return {
    id: "c-1",
    customerName: "张三",
    companyName: "示例公司",
    contactName: "张三",
    phone: "13800000000",
    email: "zhangsan@example.com",
    sourceChannel: "官网",
    stage: "proposal",
    ownerId: "u-1",
    ownerName: "销售A",
    lastFollowupAt: new Date(Date.now() - 8 * 24 * 60 * 60 * 1000).toISOString(),
    nextFollowupAt: new Date().toISOString(),
    winProbability: 80,
    riskLevel: "high",
    tags: [],
    aiSummary: "",
    aiSuggestion: "",
    aiRiskJudgement: "",
    stalledDays: 8,
    hasDecisionMaker: false,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    ...overrides
  };
}

function buildFollowup(overrides: Partial<FollowupRecord> = {}): FollowupRecord {
  return {
    id: "f-1",
    customerId: "c-1",
    ownerId: "u-1",
    ownerName: "销售A",
    method: "wechat",
    summary: "客户反馈积极，希望尽快推进试用。",
    customerNeeds: "希望一周内验证试点效果",
    objections: "",
    nextPlan: "安排试用",
    nextFollowupAt: new Date().toISOString(),
    needsAiAnalysis: true,
    sourceInputId: null,
    draftStatus: "confirmed",
    createdAt: new Date().toISOString(),
    ...overrides
  };
}

function buildOpportunity(overrides: Partial<Opportunity> = {}): Opportunity {
  return {
    id: "o-1",
    customerId: "c-1",
    customerName: "示例公司",
    name: "商机A",
    expectedAmount: 100000,
    stage: "proposal",
    ownerId: "u-1",
    ownerName: "销售A",
    lastProgressAt: new Date(Date.now() - 10 * 24 * 60 * 60 * 1000).toISOString(),
    riskLevel: "high",
    closeDate: "2026-04-01",
    ...overrides
  };
}

function runAlertRuleTests(): void {
  const customer = buildCustomer();
  const hits = evaluateAlertRules({
    now: new Date(),
    customer,
    followups: [buildFollowup()],
    opportunities: [buildOpportunity()]
  });

  const ruleTypes = hits.map((item) => item.ruleType);
  assert.ok(ruleTypes.includes("no_followup_timeout"));
  assert.ok(ruleTypes.includes("quoted_but_stalled"));
  logPass("alert rules hit detection");

  const wonHits = evaluateAlertRules({
    now: new Date(),
    customer: buildCustomer({ stage: "won", stalledDays: 0 }),
    followups: [buildFollowup()],
    opportunities: [buildOpportunity()]
  });
  assert.equal(wonHits.length, 0);
  logPass("alert rules skip won customer");
}

function runSchemaTests(): void {
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
  logPass("followup schema parse");

  const extraction = communicationExtractionResultSchema.parse({
    matched_customer_name: "星河制造",
    confidence_of_match: 0.86,
    communication_type: "phone",
    summary: "客户认可方案方向，但希望拆分报价。",
    key_needs: ["分阶段报价", "下周复盘"],
    key_objections: ["担心总价过高"],
    buying_signals: ["愿意安排决策人会议"],
    mentioned_budget: "预算约 20 万",
    mentioned_timeline: "下月启动",
    decision_makers: ["李总", "老板娘"],
    next_step: "发送分阶段报价并约下周二复盘",
    recommended_next_followup_at: new Date().toISOString(),
    should_create_followup: true,
    should_update_opportunity: true,
    should_trigger_alert_review: false,
    structured_tags: ["预算敏感", "高意向"],
    uncertainty_notes: []
  });
  assert.equal(extraction.communication_type, "phone");
  logPass("communication extraction schema parse");

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
  logPass("leak schema invalid rule rejection");
}

function runDedupeTests(): void {
  const upgraded = getAlertDedupeDecision({
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

  assert.equal(upgraded.shouldUpdate, true);
  assert.equal(upgraded.shouldUpgradeSeverity, true);
  assert.equal(upgraded.nextSource, "hybrid");
  logPass("dedupe upgrade decision");

  const unchanged = getAlertDedupeDecision({
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
  assert.equal(unchanged.shouldUpdate, false);
  assert.equal(unchanged.shouldUpgradeSeverity, false);
  assert.equal(unchanged.nextSource, "rule");
  logPass("dedupe unchanged decision");
}

function runProviderParseTests(): void {
  const content = normalizeDeepSeekContent([
    { text: "{\"ok\":true" },
    { text: ",\"value\":123}" }
  ]);
  assert.equal(content, "{\"ok\":true,\"value\":123}");

  const parsed = parseDeepSeekJsonText(content);
  assert.deepEqual(parsed, { ok: true, value: 123 });
  assert.equal(parseDeepSeekJsonText("not-json"), null);
  logPass("provider output parse");
}

function runCustomerMatchTests(): void {
  const exact = scoreCustomerCandidate({
    hint: "星河制造",
    customerName: "星河制造",
    companyName: "星河制造有限公司",
    contactName: "李总",
    ownerBoost: true
  });
  assert.ok(exact > 0.75);

  const weak = scoreCustomerCandidate({
    hint: "星河制造",
    customerName: "天海电子",
    companyName: "天海电子有限公司",
    contactName: "王总"
  });
  assert.ok(weak < 0.4);

  const picked = pickBestCustomerMatch([
    { id: "c-1", name: "星河制造", companyName: "星河制造", contactName: "李总", ownerId: "u-1", score: 0.88 },
    { id: "c-2", name: "天海电子", companyName: "天海电子", contactName: "王总", ownerId: "u-2", score: 0.41 }
  ]);

  assert.equal(picked.best?.id, "c-1");
  assert.ok(picked.confidence > 0.9);
  logPass("customer match scoring and pick");
}

function runCaptureDecisionTests(): void {
  const auto = decideCaptureApplyMode({
    shouldCreateFollowup: true,
    hasMatchedCustomer: true,
    extractionConfidence: 0.92,
    matchConfidence: 0.88,
    hasSummary: true,
    hasNextStep: true
  });
  assert.equal(auto, "auto");

  const manual = decideCaptureApplyMode({
    shouldCreateFollowup: true,
    hasMatchedCustomer: true,
    extractionConfidence: 0.6,
    matchConfidence: 0.85,
    hasSummary: true,
    hasNextStep: false
  });
  assert.equal(manual, "manual");

  const none = decideCaptureApplyMode({
    shouldCreateFollowup: true,
    hasMatchedCustomer: false,
    extractionConfidence: 0.9,
    matchConfidence: 0.2,
    hasSummary: true,
    hasNextStep: true
  });
  assert.equal(none, "none");
  logPass("capture apply mode decision");
}

function runReportFallbackTests(): void {
  const report = buildFallbackReport({
    reportType: "manager_weekly",
    periodStart: "2026-03-08",
    periodEnd: "2026-03-14",
    metricsSnapshot: {
      new_customers: 6,
      followups_count: 18,
      communication_inputs_count: 21,
      high_risk_alerts: 3,
      high_risk_customers: 4,
      pending_drafts: 5,
      open_alerts: 7
    },
    sourceSnapshot: {}
  });

  assert.ok(report.title.includes("规则回退"));
  assert.ok(report.summary.includes("2026-03-08"));
  assert.ok(report.key_metrics.length >= 4);
  assert.ok(report.content_markdown.includes("风险清单"));
  logPass("report fallback builder");
}

function runBehaviorQualityTests(): void {
  const metrics = computeBehaviorQualityMetrics({
    periodStart: "2026-03-08",
    periodEnd: "2026-03-14",
    customers: [
      {
        id: "c-1",
        stage: "proposal",
        winProbability: 80,
        lastFollowupAt: new Date(Date.now() - 8 * 24 * 60 * 60 * 1000).toISOString(),
        nextFollowupAt: new Date().toISOString()
      },
      {
        id: "c-2",
        stage: "negotiation",
        winProbability: 75,
        lastFollowupAt: new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString(),
        nextFollowupAt: new Date().toISOString()
      }
    ],
    followups: [
      {
        id: "f-1",
        customerId: "c-1",
        createdAt: "2026-03-10T09:00:00.000Z",
        summary: "客户认可方向，计划推进预算审批",
        customerNeeds: "需要分阶段报价",
        objections: "预算敏感",
        nextPlan: "周三提交分阶段报价",
        nextFollowupAt: "2026-03-12T09:00:00.000Z",
        draftStatus: "confirmed"
      },
      {
        id: "f-2",
        customerId: "c-2",
        createdAt: "2026-03-11T09:00:00.000Z",
        summary: "已安排决策人会议",
        customerNeeds: "关注实施时间",
        objections: "",
        nextPlan: "准备会议材料",
        nextFollowupAt: "2026-03-13T09:00:00.000Z",
        draftStatus: "confirmed"
      }
    ],
    opportunities: [
      {
        id: "o-1",
        customerId: "c-1",
        stage: "proposal",
        updatedAt: "2026-03-12T08:00:00.000Z"
      }
    ],
    alerts: [
      {
        id: "a-1",
        customerId: "c-1",
        severity: "warning",
        status: "resolved",
        createdAt: "2026-03-09T09:00:00.000Z",
        updatedAt: "2026-03-10T09:00:00.000Z"
      }
    ]
  });

  assert.ok(metrics.activityQualityScore > 0);
  assert.ok(metrics.followupCompletenessScore > 0);
  assert.ok(metrics.stageProgressionScore >= 0);
  logPass("behavior quality metrics");
}

function runShallowActivityTests(): void {
  const ratio = calculateShallowActivityRatio([
    {
      id: "f-1",
      customerId: "c-1",
      createdAt: new Date().toISOString(),
      summary: "ok",
      customerNeeds: "",
      objections: "",
      nextPlan: "跟进",
      nextFollowupAt: null,
      draftStatus: "draft"
    },
    {
      id: "f-2",
      customerId: "c-1",
      createdAt: new Date().toISOString(),
      summary: "客户明确预算范围并同意安排下周评审会议",
      customerNeeds: "分阶段部署计划",
      objections: "预算审批流程",
      nextPlan: "发报价并约评审会",
      nextFollowupAt: new Date().toISOString(),
      draftStatus: "confirmed"
    }
  ]);

  assert.ok(ratio > 0 && ratio < 1);
  logPass("shallow activity ratio");
}

function runMemoryFeedbackTests(): void {
  assert.equal(deriveMemoryItemStatusFromFeedback("useful"), null);
  assert.equal(deriveMemoryItemStatusFromFeedback("accurate"), null);
  assert.equal(deriveMemoryItemStatusFromFeedback("inaccurate"), "rejected");
  assert.equal(deriveMemoryItemStatusFromFeedback("not_useful"), "hidden");
  assert.equal(deriveMemoryItemStatusFromFeedback("outdated"), "hidden");
  logPass("memory feedback status mapping");
}

function runMemoryFallbackTests(): void {
  const result = buildFallbackMemoryCompileResult({
    stageTop: ["proposal（3）"],
    sourceTypeTop: ["manual_note（4）"],
    communicationTop: ["phone（5）"],
    objectionTop: ["预算（4）"],
    tacticsTop: ["分阶段报价（3）"],
    rhythmTop: ["平均 3 天进行二次跟进"],
    riskTop: ["high_probability_stalled:critical（2）"],
    coachingTop: ["提升高风险客户响应"],
    confidence: 0.62
  });

  assert.ok(result.summary.includes("规则"));
  assert.ok(result.memory_items.length > 0);
  assert.equal(result.confidence_score, 0.62);
  logPass("memory compile fallback");
}

function runCoachingFallbackTests(): void {
  const result = buildFallbackUserCoachingReport({
    userName: "销售A",
    periodStart: "2026-03-08",
    periodEnd: "2026-03-14",
    quality: {
      activityQualityScore: 58,
      shallowActivityRatio: 0.48,
      riskResponseScore: 40,
      highRiskUnhandledCount: 3,
      followupCompletenessScore: 61
    }
  });

  assert.ok(result.title.includes("规则回退"));
  assert.ok(result.coaching_actions.length >= 2);
  assert.ok(result.content_markdown.includes("建议动作"));
  logPass("coaching report fallback");
}

function runTaskPriorityTests(): void {
  const result = computeTaskPriority({
    workType: "resolve_alert",
    customerValueScore: 82,
    riskLevel: "high",
    dueAt: new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString(),
    scheduledFor: new Date().toISOString().slice(0, 10),
    customerStage: "negotiation",
    lastFollowupAt: new Date(Date.now() - 8 * 24 * 60 * 60 * 1000).toISOString(),
    highProbabilityOpportunity: true,
    managerFlagged: true,
    rhythmFit: "good",
    backlogSize: 6
  });

  assert.ok(result.priorityScore >= 85);
  assert.equal(result.priorityBand, "critical");
  assert.ok(result.rationale.length > 0);
  logPass("task priority score");
}

function runDailyPlanFallbackTests(): void {
  const fallback = buildFallbackDailyPlan({
    sortedItems: [
      {
        id: "w-1",
        orgId: "o-1",
        ownerId: "u-1",
        ownerName: "Sales A",
        customerId: "c-1",
        customerName: "Client A",
        opportunityId: null,
        sourceType: "followup_due",
        workType: "followup_call",
        title: "Task 1",
        description: "Call customer",
        rationale: "Due today",
        priorityScore: 90,
        priorityBand: "critical",
        status: "todo",
        scheduledFor: "2026-03-14",
        dueAt: new Date().toISOString(),
        completedAt: null,
        snoozedUntil: null,
        sourceRefType: "customer",
        sourceRefId: "c-1",
        aiGenerated: false,
        aiRunId: null,
        createdBy: "u-1",
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString()
      }
    ],
    userName: "Sales A",
    planDate: "2026-03-14"
  });

  assert.ok(fallback.focus_theme.length > 0);
  assert.ok(fallback.prioritized_items.length > 0);
  assert.ok(fallback.must_do_item_ids.length > 0);
  logPass("daily plan fallback");
}

function runWorkItemTransitionTests(): void {
  const started = resolveWorkItemTransition("todo", "start");
  assert.equal(started.valid, true);
  assert.equal(started.nextStatus, "in_progress");

  const done = resolveWorkItemTransition("in_progress", "complete");
  assert.equal(done.valid, true);
  assert.equal(done.nextStatus, "done");

  const resumed = resolveWorkItemTransition("snoozed", "start");
  assert.equal(resumed.valid, true);
  assert.equal(resumed.nextStatus, "todo");

  const invalid = resolveWorkItemTransition("done", "start");
  assert.equal(invalid.valid, false);
  logPass("work item state transition");
}

function runAlertToWorkItemTests(): void {
  const draft = buildWorkItemDraftFromAlert({
    id: "a-1",
    customerId: "c-1",
    customerName: "Client A",
    ownerId: "u-1",
    ownerName: "Sales A",
    ruleType: "quoted_but_stalled",
    source: "rule",
    level: "warning",
    status: "open",
    title: "Quoted but stalled",
    message: "No progress after quotation",
    evidence: ["No update for 9 days"],
    suggestedOwnerAction: ["Follow up decision maker"],
    dueAt: null,
    resolvedAt: null,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    opportunityId: null
  });

  assert.equal(draft.sourceType, "alert");
  assert.equal(draft.workType, "resolve_alert");
  assert.equal(draft.sourceRefType, "alert");
  assert.equal(draft.sourceRefId, "a-1");
  logPass("alert to work item draft");
}

function runFollowupTaskLinkageTests(): void {
  const ids = pickAutoCompletableTaskIdsAfterFollowup({
    customerId: "c-1",
    tasks: [
      { id: "w-1", customer_id: "c-1", status: "todo", work_type: "followup_call" },
      { id: "w-2", customer_id: "c-1", status: "in_progress", work_type: "review_customer" },
      { id: "w-3", customer_id: "c-2", status: "todo", work_type: "followup_call" },
      { id: "w-4", customer_id: "c-1", status: "done", work_type: "followup_call" }
    ]
  });

  assert.deepEqual(ids, ["w-1", "w-2"]);
  logPass("followup auto-complete linkage");
}

function runManagerRhythmFallbackTests(): void {
  const result = buildFallbackTeamRhythmInsight({
    rows: [
      {
        userId: "u-1",
        userName: "Sales A",
        todoCount: 4,
        inProgressCount: 2,
        doneCount: 6,
        overdueCount: 1,
        criticalOpenCount: 1,
        completionRate: 0.55,
        overdueRate: 0.15,
        backlogScore: 5.8
      },
      {
        userId: "u-2",
        userName: "Sales B",
        todoCount: 9,
        inProgressCount: 4,
        doneCount: 2,
        overdueCount: 5,
        criticalOpenCount: 3,
        completionRate: 0.13,
        overdueRate: 0.42,
        backlogScore: 14.1
      }
    ],
    overdueTasks: 6,
    unattendedCriticalCustomers: ["Client X"]
  });

  assert.ok(result.team_execution_summary.length > 0);
  assert.ok(result.who_needs_support.length > 0);
  assert.ok(result.managerial_actions.length > 0);
  logPass("manager rhythm fallback");
}

function runPrepCardFallbackTests(): void {
  const result = buildFallbackFollowupPrepCard({
    customerName: "Client A",
    stage: "proposal",
    riskLevel: "high",
    nextFollowupAt: new Date().toISOString()
  });

  assert.ok(result.current_state_summary.includes("Client A"));
  assert.ok(result.suggested_talk_track.length > 0);
  assert.ok(result.success_signal.length > 0);
  logPass("prep card fallback");
}

function runMorningBriefFallbackTests(): void {
  const result = buildFallbackMorningBrief({
    briefType: "sales_morning",
    topTasks: ["Call Client A"],
    topRisks: ["Quoted but stalled"],
    customersToPrepare: ["Client A"],
    pendingDraftCount: 2
  });

  assert.ok(result.headline.length > 0);
  assert.equal(result.top_tasks[0], "Call Client A");
  assert.ok(result.action_note.length > 0);
  logPass("morning brief fallback");
}

function runContentDraftFallbackTests(): void {
  const result = buildFallbackActionDraft({
    draftType: "followup_message",
    customerName: "Client A",
    taskTitle: "Follow up today"
  });

  assert.equal(result.draft_type, "followup_message");
  assert.ok(result.content_text.includes("Client A"));
  assert.ok(result.content_markdown.includes("next step"));
  logPass("content draft fallback");
}

function runTaskPrepAssociationTests(): void {
  const now = new Date().toISOString();
  const prepCards: PrepCard[] = [
    {
      id: "p-1",
      orgId: "o-1",
      ownerId: "u-1",
      customerId: "c-1",
      opportunityId: null,
      workItemId: "w-1",
      cardType: "task_brief",
      status: "ready",
      title: "Task Brief A",
      summary: "A",
      cardPayload: {},
      sourceSnapshot: {},
      generatedBy: "u-1",
      aiRunId: null,
      validUntil: null,
      createdAt: now,
      updatedAt: now
    },
    {
      id: "p-2",
      orgId: "o-1",
      ownerId: "u-1",
      customerId: "c-2",
      opportunityId: null,
      workItemId: "w-2",
      cardType: "task_brief",
      status: "ready",
      title: "Task Brief B",
      summary: "B",
      cardPayload: {},
      sourceSnapshot: {},
      generatedBy: "u-1",
      aiRunId: null,
      validUntil: null,
      createdAt: now,
      updatedAt: now
    }
  ];

  const drafts: ContentDraft[] = [
    {
      id: "d-1",
      orgId: "o-1",
      ownerId: "u-1",
      customerId: "c-1",
      opportunityId: null,
      prepCardId: "p-1",
      workItemId: "w-1",
      draftType: "followup_message",
      status: "draft",
      title: "Draft A",
      contentMarkdown: "draft",
      contentText: "draft",
      rationale: "context",
      sourceSnapshot: {},
      generatedBy: "u-1",
      aiRunId: null,
      createdAt: now,
      updatedAt: now
    }
  ];

  const prepCoverage = mapPrepCoverageByWorkItem(prepCards, ["w-1", "w-3"]);
  const draftCoverage = mapDraftCoverageByWorkItem(drafts, ["w-1", "w-2"]);

  assert.equal(prepCoverage["w-1"]?.id, "p-1");
  assert.equal(prepCoverage["w-3"], undefined);
  assert.equal(draftCoverage["w-1"]?.length, 1);
  assert.equal(draftCoverage["w-2"], undefined);
  logPass("task to prep/draft coverage mapping");
}

function runPrepFeedbackLogicTests(): void {
  assert.equal(derivePrepCardStatusFromFeedback("outdated"), "stale");
  assert.equal(derivePrepCardStatusFromFeedback("inaccurate"), "archived");
  assert.equal(derivePrepCardStatusFromFeedback("useful"), null);
  assert.equal(deriveContentDraftStatusFromFeedback("adopted"), "adopted");
  assert.equal(deriveContentDraftStatusFromFeedback("not_useful"), "discarded");
  assert.equal(deriveContentDraftStatusFromFeedback("useful"), null);
  logPass("prep feedback status mapping");
}

function runBriefingsAggregationTests(): void {
  const older = "2026-03-13T08:00:00.000Z";
  const newer = "2026-03-14T08:00:00.000Z";
  const morningBrief: MorningBrief = {
    id: "mb-1",
    orgId: "o-1",
    targetUserId: "u-1",
    briefType: "sales_morning",
    briefDate: "2026-03-14",
    status: "completed",
    headline: "Headline",
    executiveSummary: "Summary",
    briefPayload: {},
    sourceSnapshot: {},
    generatedBy: "u-1",
    aiRunId: "ar-1",
    createdAt: newer,
    updatedAt: newer
  };
  const view = buildBriefingHubView({
    morningBrief,
    prepCards: [
      {
        id: "p-old",
        orgId: "o-1",
        ownerId: "u-1",
        customerId: null,
        opportunityId: null,
        workItemId: null,
        cardType: "followup_prep",
        status: "ready",
        title: "Old",
        summary: "Old",
        cardPayload: {},
        sourceSnapshot: {},
        generatedBy: "u-1",
        aiRunId: null,
        validUntil: null,
        createdAt: older,
        updatedAt: older
      },
      {
        id: "p-new",
        orgId: "o-1",
        ownerId: "u-1",
        customerId: null,
        opportunityId: null,
        workItemId: null,
        cardType: "followup_prep",
        status: "ready",
        title: "New",
        summary: "New",
        cardPayload: {},
        sourceSnapshot: {},
        generatedBy: "u-1",
        aiRunId: null,
        validUntil: null,
        createdAt: newer,
        updatedAt: newer
      }
    ],
    contentDrafts: []
  });

  assert.equal(view.morningBrief?.id, "mb-1");
  assert.equal(view.prepCards[0]?.id, "p-new");
  assert.equal(view.prepCards[1]?.id, "p-old");
  logPass("briefings aggregation");
}

function runOutcomeFallbackTests(): void {
  const draft = buildFallbackOutcomeAssist({
    preferredOutcomeType: "task_result",
    previousStage: "proposal",
    nextStage: "negotiation",
    summaryHint: "Task completed with decision-maker alignment.",
    usedPrepCard: true,
    usedDraft: true
  });
  assert.equal(draft.result_status, "positive_progress");
  assert.equal(draft.stage_changed, true);
  assert.equal(draft.used_prep_card, true);
  assert.equal(draft.used_draft, true);
  logPass("outcome capture fallback");
}

function runSuggestionAdoptionMappingTests(): void {
  assert.equal(mapFeedbackToAdoptionType("adopted"), "adopted");
  assert.equal(mapFeedbackToAdoptionType("useful"), "partially_used");
  assert.equal(mapFeedbackToAdoptionType("not_useful"), "dismissed");
  assert.equal(mapFeedbackToAdoptionType("inaccurate"), "dismissed");
  assert.equal(mapFeedbackToAdoptionType("outdated"), "edited");
  logPass("suggestion adoption mapping");
}

function runAdoptionOutcomeLinkageTests(): void {
  const metrics = computeOutcomeOverview({
    outcomes: [
      { id: "o-1", resultStatus: "positive_progress", outcomeType: "followup_result" },
      { id: "o-2", resultStatus: "stalled", outcomeType: "task_result" },
      { id: "o-3", resultStatus: "positive_progress", outcomeType: "quote_result" }
    ],
    adoptions: [
      { id: "a-1", linkedOutcomeId: "o-1", adoptionType: "adopted" },
      { id: "a-2", linkedOutcomeId: "o-2", adoptionType: "dismissed" },
      { id: "a-3", linkedOutcomeId: "o-3", adoptionType: "partially_used" }
    ]
  });
  assert.equal(metrics.totalOutcomes, 3);
  assert.ok(metrics.positiveRate > 0.6);
  assert.ok(metrics.adoptionRate > 0.5);
  assert.ok(metrics.adoptionPositiveRate > 0.9);
  logPass("adoption outcome linkage");
}

function runPlaybookFallbackTests(): void {
  const playbook = buildFallbackPlaybook({
    title: "Fallback Playbook",
    playbookType: "followup_rhythm",
    effectivePatternHints: ["Follow up in 48 hours"],
    ineffectivePatternHints: ["No next-step owner"]
  });
  assert.equal(playbook.playbook_type, "followup_rhythm");
  assert.ok(playbook.entries.length > 0);
  assert.ok(playbook.entries[0].recommended_actions.length > 0);
  logPass("playbook fallback");
}

function runOutcomeReviewFallbackTests(): void {
  const review = buildFallbackOutcomeReview({
    periodLabel: "2026-03-01~2026-03-31",
    positiveRate: 0.42,
    adoptionRate: 0.35,
    repeatedFailures: ["task_result:stalled x3"]
  });
  const personal = buildFallbackPersonalEffectivenessUpdate({
    positiveRateAfterAdoption: 0.55,
    positiveRateWithoutAdoption: 0.31
  });

  assert.ok(review.title.includes("fallback"));
  assert.ok(review.coaching_actions.length > 0);
  assert.ok(personal.helpful_suggestion_patterns.length > 0);
  assert.ok(personal.summary.length > 0);
  logPass("outcome review fallback");
}

function runDealCommandFallbackTests(): void {
  const summary = buildFallbackDealRoomCommandSummary({
    room: {
      title: "Strategic Deal A",
      currentGoal: "Send aligned quote package",
      nextMilestone: "Quote review completed",
      managerAttentionNeeded: true
    },
    blockers: ["Decision owner unclear", "Budget window not confirmed"],
    openTasks: 5,
    overdueTasks: 2,
    openInterventions: 1
  });
  assert.ok(summary.command_summary.includes("Strategic Deal A"));
  assert.ok(summary.key_blockers.length > 0);
  logPass("deal room fallback summary");

  const threadSummary = buildFallbackThreadSummary({
    threadTitle: "Quote Review Thread",
    recentMessages: [
      { body: "Need pricing boundary before customer meeting", type: "comment" },
      { body: "Manager can join call on Friday", type: "system_event" }
    ]
  });
  assert.ok(threadSummary.summary.length > 0);
  assert.ok(threadSummary.open_questions.length > 0);
  logPass("thread summary fallback");

  const decisionSupport = buildFallbackDecisionSupport({
    decisionType: "discount_exception",
    options: ["5% limited-time discount", "No discount but phased rollout"],
    knownRisks: ["Approval delay", "Margin pressure"]
  });
  assert.equal(decisionSupport.options_assessment.length, 2);
  assert.ok(decisionSupport.followup_actions.length > 0);
  logPass("decision support fallback");

  const intervention = buildFallbackInterventionRecommendation({
    managerAttentionNeeded: true,
    blockerCount: 2,
    overdueTaskCount: 3
  });
  assert.equal(intervention.whether_to_intervene, true);
  assert.ok(intervention.suggested_manager_action.length > 0);
  logPass("intervention recommendation fallback");

  const now = new Date().toISOString();
  const mapping = buildFallbackDealPlaybookMapping({
    room: {
      id: "dr-1",
      title: "Strategic Deal A",
      currentGoal: "Close quote checkpoint"
    },
    playbooks: [
      {
        playbook: {
          id: "pb-1",
          orgId: "org-1",
          scopeType: "team",
          ownerUserId: null,
          playbookType: "quote_strategy",
          title: "Quote Strategy Pattern",
          summary: "Use phased quote with value anchors.",
          status: "active",
          confidenceScore: 0.72,
          applicabilityNotes: "For budget-sensitive mid-market accounts",
          sourceSnapshot: {},
          generatedBy: "system",
          aiRunId: null,
          createdAt: now,
          updatedAt: now
        },
        entries: [
          {
            id: "pbe-1",
            orgId: "org-1",
            playbookId: "pb-1",
            entryTitle: "Anchor business value first",
            entrySummary: "Lead with ROI and staged commitment.",
            conditions: {},
            recommendedActions: ["State ROI first", "Offer phased option"],
            cautionNotes: ["Do not over-discount too early"],
            evidenceSnapshot: {},
            successSignal: {},
            failureModes: ["Price-first conversation"],
            confidenceScore: 0.71,
            sortOrder: 1,
            createdAt: now,
            updatedAt: now
          }
        ]
      }
    ]
  });
  assert.ok(mapping.relevant_playbooks.length > 0);
  assert.ok(mapping.suggested_application.length > 0);
  logPass("deal playbook mapping result");
}

function runDecisionLinkageTests(): void {
  const quote = deriveDecisionApprovalLinkage("quote_strategy");
  assert.equal(quote.workType, "send_quote");
  assert.equal(quote.checkpointType, "quote_sent");

  const contract = deriveDecisionApprovalLinkage("contract_risk");
  assert.equal(contract.workType, "prepare_proposal");
  assert.equal(contract.checkpointType, "contract_review");

  const stageCommitment = deriveDecisionApprovalLinkage("stage_commitment");
  assert.equal(stageCommitment.workType, "revive_stalled_deal");
  assert.equal(stageCommitment.checkpointType, "closing");
  logPass("decision workitem/checkpoint linkage");
}

function runInterventionFlowTests(): void {
  assert.equal(isInterventionStatusTransitionAllowed("open", "accepted"), true);
  assert.equal(isInterventionStatusTransitionAllowed("accepted", "completed"), true);
  assert.equal(isInterventionStatusTransitionAllowed("completed", "open"), false);
  assert.equal(isInterventionStatusTransitionAllowed("declined", "open"), true);
  logPass("intervention request status flow");
}

function runCheckpointLinkageTests(): void {
  const linkage = buildBlockedCheckpointLinkage({
    checkpointTitle: "Budget Confirmed",
    checkpointType: "budget_confirmed",
    checkpointDescription: "No budget owner yet",
    checkpointDueAt: "2026-03-20T10:00:00.000Z",
    dealRoomTitle: "Strategic Deal A"
  });

  assert.equal(linkage.roomPatch.managerAttentionNeeded, true);
  assert.equal(linkage.roomPatch.roomStatus, "blocked");
  assert.equal(linkage.alert.ruleType, "ai_detected");
  assert.equal(linkage.alert.level, "critical");
  assert.ok(linkage.alert.evidence.some((item) => item.includes("checkpoint=budget_confirmed")));
  logPass("checkpoint blocked linkage");
}

function runTouchpointFallbackTests(): void {
  const email = buildFallbackEmailDraft({
    customerName: "Example Corp",
    context: "followup"
  });
  assert.ok(email.subject.length > 0);
  assert.ok(email.body.length > 0);
  logPass("email draft fallback");

  const agenda = buildFallbackMeetingAgenda({
    customerName: "Example Corp",
    meetingType: "proposal_review"
  });
  assert.ok(agenda.meeting_goal.length > 0);
  assert.ok(agenda.agenda_points.length >= 3);
  logPass("meeting agenda fallback");

  const doc = buildFallbackDocumentSummary({
    fileName: "quote-v2.md",
    extractedText: "Quote details for staged rollout and pricing boundary."
  });
  assert.equal(doc.document_type_guess, "quote");
  assert.ok(doc.recommended_actions.length > 0);
  logPass("document summary fallback");

  const review = buildFallbackExternalTouchpointReview({
    totalEvents: 12,
    waitingReplyThreads: 4,
    scheduledMeetings: 3,
    highPriorityDealWithoutTouchpoint: 2
  });
  assert.ok(review.external_progress_assessment.length > 0);
  assert.ok(review.recommended_next_moves.length > 0);
  logPass("touchpoint review fallback");

  const waitingReplyHit = evaluateWaitingReplyNeed({
    threadStatus: "waiting_reply",
    latestMessageAt: new Date(Date.now() - 31 * 60 * 60 * 1000).toISOString(),
    thresholdHours: 24
  });
  assert.equal(waitingReplyHit, true);

  const waitingReplySkip = evaluateWaitingReplyNeed({
    threadStatus: "replied",
    latestMessageAt: new Date().toISOString(),
    thresholdHours: 24
  });
  assert.equal(waitingReplySkip, false);
  logPass("waiting reply rule");

  const noRecentHit = evaluateNoRecentTouchpoint({
    latestTouchpointAt: new Date(Date.now() - 11 * 24 * 60 * 60 * 1000).toISOString(),
    dealPriorityBand: "critical",
    thresholdDays: 10
  });
  assert.equal(noRecentHit, true);

  const noRecentSkip = evaluateNoRecentTouchpoint({
    latestTouchpointAt: new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString(),
    dealPriorityBand: "important",
    thresholdDays: 10
  });
  assert.equal(noRecentSkip, false);
  logPass("no recent touchpoint rule");
}

function buildEntitlement(overrides: Partial<EntitlementStatus> = {}): EntitlementStatus {
  return {
    planTier: "trial",
    status: "active",
    seatLimit: 10,
    seatUsed: 3,
    aiRunLimitMonthly: 1000,
    aiRunUsedMonthly: 120,
    documentLimitMonthly: 300,
    documentUsedMonthly: 20,
    touchpointLimitMonthly: 1500,
    touchpointUsedMonthly: 100,
    remainingAiRunsMonthly: 880,
    quotaNearLimit: false,
    quotaExceeded: false,
    advancedFeaturesEnabled: true,
    ...overrides
  };
}

function runProductizationFallbackTests(): void {
  const checklist = {
    items: [
      { key: "org_profile", title: "Configure organization profile", completed: true, detail: "" },
      { key: "ai_setup", title: "Configure AI provider", completed: false, detail: "" },
      { key: "team_invite", title: "Invite members", completed: false, detail: "" }
    ],
    completedCount: 1,
    totalCount: 3,
    progress: 33,
    completed: false
  };

  const onboarding = buildFallbackOnboardingRecommendation({
    checklist,
    featureFlags: {
      ai_auto_analysis: true,
      ai_auto_planning: true,
      ai_morning_brief: false
    },
    hasAiConfigured: false
  });
  assert.ok(onboarding.nextBestSetupSteps.length > 0);
  assert.ok(onboarding.risksIfSkipped.some((item) => item.includes("fallback")));
  logPass("onboarding checklist fallback recommendation");

  const usageSummary = buildFallbackUsageHealthSummary({
    entitlement: buildEntitlement({
      quotaNearLimit: true,
      aiRunUsedMonthly: 920,
      remainingAiRunsMonthly: 80
    }),
    monthlyUsage: {
      aiRunsCount: 920,
      prepCardsCount: 2,
      draftsCount: 1,
      reportsCount: 2,
      touchpointEventsCount: 60,
      documentProcessedCount: 18,
      workPlanGenerationsCount: 8
    }
  });
  assert.ok(usageSummary.quotaRisks.length > 0);
  assert.ok(usageSummary.recommendedAdjustments.length > 0);
  logPass("usage summary fallback");
}

function runFeatureAccessLogicTests(): void {
  const disabled = deriveFeatureAccess({
    featureKey: "ai_auto_planning",
    featureEnabled: false,
    planStatus: "active"
  });
  assert.equal(disabled.allowed, false);
  assert.ok(disabled.reason?.includes("disabled"));

  const aiFallback = deriveAiActionAccess({
    featureAllowed: true,
    featureReason: null,
    providerConfigured: false,
    providerReason: "missing_key",
    quotaAllowed: true,
    quotaReason: null
  });
  assert.equal(aiFallback.allowed, false);
  assert.equal(aiFallback.fallbackOnly, true);
  logPass("feature flag disable and ai fallback gating");
}

function runEntitlementTests(): void {
  const aiAllowed = canRunAiByEntitlement(buildEntitlement());
  assert.equal(aiAllowed.allowed, true);

  const aiBlocked = canRunAiByEntitlement(
    buildEntitlement({
      aiRunLimitMonthly: 500,
      aiRunUsedMonthly: 500
    })
  );
  assert.equal(aiBlocked.allowed, false);

  const docsBlocked = canProcessDocumentsByEntitlement(
    buildEntitlement({
      documentLimitMonthly: 10,
      documentUsedMonthly: 10
    })
  );
  assert.equal(docsBlocked.allowed, false);

  const seatBlocked = hasSeatCapacity(
    buildEntitlement({
      seatLimit: 5,
      seatUsed: 5
    })
  );
  assert.equal(seatBlocked.allowed, false);
  logPass("plan entitlement and quota checks");
}

function runMembershipFlowTests(): void {
  assert.equal(isOrgAdminRole("owner"), true);
  assert.equal(isOrgAdminRole("sales"), false);
  assert.equal(canViewOrgUsage("manager"), true);
  assert.equal(canViewOrgUsage("viewer"), false);

  assert.equal(isSeatStatusTransitionAllowed("invited", "active"), true);
  assert.equal(isSeatStatusTransitionAllowed("active", "suspended"), true);
  assert.equal(isSeatStatusTransitionAllowed("suspended", "active"), true);
  assert.equal(isSeatStatusTransitionAllowed("removed", "active"), false);
  logPass("invite role and seat status flow");
}

function runDemoSeedSummaryTests(): void {
  const partial = summarizeDemoSeedSteps([
    { name: "customers", success: true, inserted: 6, message: "ok" },
    { name: "followups", success: false, inserted: 0, message: "error" }
  ]);
  assert.equal(partial.status, "failed");
  assert.equal(partial.partialSuccess, true);
  assert.ok(partial.summary.includes("partial success"));

  const ok = summarizeDemoSeedSteps([
    { name: "customers", success: true, inserted: 6, message: "ok" },
    { name: "followups", success: true, inserted: 6, message: "ok" }
  ]);
  assert.equal(ok.status, "completed");
  assert.equal(ok.partialSuccess, false);
  logPass("demo seed partial success fallback");
}

async function main(): Promise<void> {
  runAlertRuleTests();
  runSchemaTests();
  runDedupeTests();
  runProviderParseTests();
  runCustomerMatchTests();
  runCaptureDecisionTests();
  runReportFallbackTests();
  runBehaviorQualityTests();
  runShallowActivityTests();
  runMemoryFeedbackTests();
  runMemoryFallbackTests();
  runCoachingFallbackTests();
  runTaskPriorityTests();
  runDailyPlanFallbackTests();
  runWorkItemTransitionTests();
  runAlertToWorkItemTests();
  runFollowupTaskLinkageTests();
  runManagerRhythmFallbackTests();
  runPrepCardFallbackTests();
  runMorningBriefFallbackTests();
  runContentDraftFallbackTests();
  runTaskPrepAssociationTests();
  runPrepFeedbackLogicTests();
  runBriefingsAggregationTests();
  runOutcomeFallbackTests();
  runSuggestionAdoptionMappingTests();
  runAdoptionOutcomeLinkageTests();
  runPlaybookFallbackTests();
  runOutcomeReviewFallbackTests();
  runDealCommandFallbackTests();
  runDecisionLinkageTests();
  runInterventionFlowTests();
  runCheckpointLinkageTests();
  runTouchpointFallbackTests();
  runProductizationFallbackTests();
  runFeatureAccessLogicTests();
  runEntitlementTests();
  runMembershipFlowTests();
  runRolePermissionModelTests(logPass);
  runOrgOverrideWritePathGovernanceTests(logPass);
  runOverrideConcurrencyGuardTests(logPass);
  runTemplateOverrideEditorUiTests(logPass);
  runOrgConfigEditorUiTests(logPass);
  runRuntimeExplainDebugPanelTests(logPass);
  await runPersistedAuditVersionSnapshotFoundationTests(logPass);
  await runOrgTemplateOverrideRollbackTests(logPass);
  await runOrgConfigRollbackTests(logPass);
  await runOrgConfigGovernanceExpansionTests(logPass);
  runConfigOperationsHubTests(logPass);
  runConfigTimelineDiffViewerTests(logPass);
  runDemoSeedSummaryTests();
  runTemplateLayerTests(logPass);
  runIndustryTemplateFrameworkTests(logPass);
  runEnterpriseCustomizationFrameworkTests(logPass);
  runTemplateOrgRuntimeBridgeTests(logPass);
  runOrgRuntimeConfigReadPathTests(logPass);
  runRuntimeConfigExplainHardeningTests(logPass);
  runManagerExecutiveRuntimePreferenceBridgeTests(logPass);
  runImportLayerTests(logPass);
  runMobileLayerTests(logPass);
  runCommercializationLayerTests(logPass);
  runCommercialEntrySystemTests(logPass);
  runCommercialReadinessGateTests(logPass);
  runAutomationOpsLayerTests(logPass);
  runWorkItemActionHubTests(logPass);
  runGoldenPathClosedLoopTraceTests(logPass);
  runGoldenPathSmokeTests(logPass);
  console.log("All tests passed.");
}

void main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
