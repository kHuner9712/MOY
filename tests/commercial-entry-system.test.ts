import assert from "node:assert/strict";

import {
  buildClientPublicCommercialEntryInput,
  buildLeadPipelineHandoffSnapshot,
  buildLeadQualificationSnapshot,
  buildPublicCommercialEntryContext,
  buildTrialOnboardingIntent,
  readPublicCommercialEntryTrace,
  readTrialOnboardingIntentFromLeadSnapshot,
  toPublicCommercialEntryTracePayload,
  toTrialOnboardingIntentPayload
} from "../lib/commercial-entry";

export function runCommercialEntrySystemTests(logPass: (name: string) => void): void {
  const clientInput = buildClientPublicCommercialEntryInput({
    fallbackLandingPage: "/request-demo",
    entryTraceId: "demo_trace_1",
    sourceCampaign: "campaign_seed",
    now: "2026-03-22T08:00:00.000Z"
  });
  assert.equal(clientInput.entryTraceId, "demo_trace_1");
  assert.equal(clientInput.landingPage, "/request-demo");
  logPass("commercial entry client input fallback");

  const entryContext = buildPublicCommercialEntryContext({
    source: "website_demo",
    fallbackLandingPage: "/request-demo",
    input: {
      ...clientInput,
      landingPage: "request-demo",
      referrer: "https://example.com/source",
      utmSource: "wechat",
      utmMedium: "social",
      utmCampaign: "spring_campaign",
      locale: "zh-CN",
      timezone: "Asia/Shanghai",
      submittedAt: "invalid-date"
    },
    now: "2026-03-22T09:00:00.000Z"
  });
  assert.equal(entryContext.entryTraceId, "demo_trace_1");
  assert.equal(entryContext.landingPage, "/request-demo");
  assert.equal(entryContext.sourceCampaign, "campaign_seed");
  assert.equal(entryContext.submittedAt, "2026-03-22T09:00:00.000Z");

  const tracePayload = toPublicCommercialEntryTracePayload(entryContext);
  const parsedTrace = readPublicCommercialEntryTrace({
    entry_trace: tracePayload
  });
  assert.equal(parsedTrace?.traceId, "demo_trace_1");
  assert.equal(parsedTrace?.landingPage, "/request-demo");
  assert.equal(parsedTrace?.utmSource, "wechat");
  logPass("commercial entry trace payload roundtrip");

  const qualificationSnapshot = buildLeadQualificationSnapshot({
    qualificationRunId: "run_001",
    qualification: {
      qualificationAssessment: "中高匹配，建议尽快进入演示流程。",
      fitScore: 78,
      likelyUseCase: "销售节奏治理",
      suggestedOwnerType: "sales",
      suggestedNextActions: ["24 小时内预约演示", "确认决策链路"],
      riskFlags: ["timeline_uncertain"]
    },
    qualificationUsedFallback: false,
    qualificationFallbackReason: null,
    assignmentOwnerId: "owner_001",
    assignmentOwnerName: "销售A",
    matchedRuleId: "rule_001"
  });
  assert.equal(qualificationSnapshot.qualification_fit_score, 78);
  assert.equal(qualificationSnapshot.assignment_owner_id, "owner_001");
  assert.equal(Array.isArray(qualificationSnapshot.qualification_suggested_next_actions), true);
  logPass("commercial entry qualification snapshot");

  const handoffPending = buildLeadPipelineHandoffSnapshot({
    requested: true,
    attempted: false,
    pipelineCreated: false
  });
  assert.equal(handoffPending.status, "requested_pending");

  const handoffCreated = buildLeadPipelineHandoffSnapshot({
    requested: true,
    attempted: true,
    pipelineCreated: true,
    customerId: "customer_1",
    opportunityId: "opportunity_1"
  });
  assert.equal(handoffCreated.status, "created_new_pipeline");

  const handoffReused = buildLeadPipelineHandoffSnapshot({
    requested: true,
    attempted: true,
    pipelineCreated: false,
    customerId: "customer_2"
  });
  assert.equal(handoffReused.status, "reused_existing_pipeline");
  logPass("commercial entry pipeline handoff status");

  const onboardingIntent = buildTrialOnboardingIntent({
    needImportData: true,
    preferredTemplateKey: "b2b_software",
    useCaseHint: "两周内跑通线索到跟进的闭环。",
    industryHint: "software",
    teamSizeHint: "20 sales"
  });
  const intentPayload = toTrialOnboardingIntentPayload(onboardingIntent);
  const parsedIntent = readTrialOnboardingIntentFromLeadSnapshot({
    trial_onboarding_intent: intentPayload
  });
  assert.equal(parsedIntent?.needsDataImport, true);
  assert.equal(parsedIntent?.preferredTemplateKey, "b2b_software");
  assert.equal(parsedIntent?.onboardingMotion, "assisted_import");
  assert.equal(parsedIntent?.kickoffPriority, "high");
  logPass("commercial entry onboarding intent roundtrip");
}
