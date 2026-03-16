import test from "node:test";
import assert from "node:assert/strict";

import { evaluateAlertRules } from "../lib/alert-rules";
import type { Customer } from "../types/customer";
import type { FollowupRecord } from "../types/followup";
import type { Opportunity } from "../types/opportunity";

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

test("alert rules should detect no-followup timeout and quoted stall", () => {
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
});

test("alert rules should not produce hits for won customer", () => {
  const hits = evaluateAlertRules({
    now: new Date(),
    customer: buildCustomer({ stage: "won", stalledDays: 0 }),
    followups: [buildFollowup()],
    opportunities: [buildOpportunity()]
  });
  assert.equal(hits.length, 0);
});
