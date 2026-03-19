import assert from "node:assert/strict";

import { findCustomerDuplicateCandidates, mergeCustomerPayload } from "../lib/import-dedupe";
import { resolveImportExecutionOutcome } from "../lib/import-execution-utils";
import { buildRuleBasedMappingSuggestions, guessImportField } from "../lib/import-mapping";
import {
  normalizeCommunicationType,
  normalizeCurrencyAmount,
  normalizeCustomerStage,
  normalizeDateValue,
  normalizeOpportunityStage,
  normalizeRiskLevel
} from "../lib/import-normalization";
import { buildImportOwnerMap, resolveImportOwnerId } from "../lib/import-owner-mapping";
import { buildFallbackImportReviewSummary } from "../lib/import-review-fallback";

export function runImportLayerTests(logPass: (name: string) => void): void {
  const customerName = guessImportField("客户名称");
  assert.equal(customerName.mappedTargetEntity, "customer");
  assert.equal(customerName.mappedTargetField, "name");

  const amount = guessImportField("商机金额");
  assert.equal(amount.mappedTargetEntity, "opportunity");
  assert.equal(amount.mappedTargetField, "amount");

  const mixedSuggestions = buildRuleBasedMappingSuggestions(["客户名称", "负责人", "下次跟进", "备注"]);
  assert.ok(mixedSuggestions.some((item) => item.mappedTargetField === "owner"));
  logPass("import field guessing rules");

  const ownerMap = buildImportOwnerMap([
    {
      user_id: "u-1",
      profile: { display_name: "Lin Yue" },
      email: "linyue@example.com"
    },
    {
      user_id: "u-2",
      profile: { display_name: "王强" },
      email: "wangqiang@example.com"
    }
  ]);
  assert.equal(resolveImportOwnerId("Lin Yue", ownerMap), "u-1");
  assert.equal(resolveImportOwnerId("wangqiang@example.com", ownerMap), "u-2");
  assert.equal(resolveImportOwnerId("unknown", ownerMap), null);
  logPass("import owner mapping logic");

  const slashDate = normalizeDateValue("2026/03/21");
  const cnDate = normalizeDateValue("2026年3月21日");
  const excelDate = normalizeDateValue("45500");
  assert.ok(typeof slashDate === "string" && slashDate.startsWith("2026-"));
  assert.ok(typeof cnDate === "string" && cnDate.startsWith("2026-"));
  assert.ok(excelDate !== null);
  assert.equal(normalizeCustomerStage("报价"), "proposal");
  assert.equal(normalizeOpportunityStage("negotiation"), "negotiation");
  assert.equal(normalizeRiskLevel("高"), "high");
  assert.equal(normalizeCommunicationType("邮件"), "email");
  assert.equal(normalizeCurrencyAmount("¥ 120,000 "), 120000);
  logPass("import normalization rules");

  const dedupe = findCustomerDuplicateCandidates({
    incoming: {
      companyName: "星河制造有限公司",
      contactName: "李总",
      phone: "13800138000",
      email: "li@example.com",
      ownerId: "u-1"
    },
    existing: [
      {
        id: "c-1",
        companyName: "星河制造有限公司",
        contactName: "李总",
        phone: "13800138000",
        email: "li@example.com",
        ownerId: "u-1"
      },
      {
        id: "c-2",
        companyName: "天海电子",
        contactName: "王总",
        phone: "13900139000",
        email: "wang@example.com",
        ownerId: "u-2"
      }
    ]
  });
  assert.equal(dedupe[0]?.id, "c-1");
  assert.ok((dedupe[0]?.score ?? 0) > 0.9);
  logPass("import dedupe candidate generation");

  const merged = mergeCustomerPayload({
    existing: {
      name: "李总",
      company_name: "星河制造",
      contact_name: "李总",
      phone: "13800138000",
      email: null,
      source_channel: null,
      current_stage: "proposal",
      next_followup_at: null,
      risk_level: "high",
      tags: ["vip"],
      ai_summary: null
    },
    incoming: {
      name: "李总（新）",
      company_name: "星河制造股份",
      contact_name: "李总",
      phone: "13800990099",
      email: "li@example.com",
      tags: ["budget_sensitive"]
    }
  });
  assert.equal(merged.phone, "13800138000");
  assert.equal(merged.email, "li@example.com");
  assert.ok((merged.tags ?? []).includes("vip"));
  assert.ok((merged.tags ?? []).includes("budget_sensitive"));
  logPass("import merge_existing basic rule");

  const partial = resolveImportExecutionOutcome({
    importedRows: 10,
    errorRows: 2
  });
  const allFailed = resolveImportExecutionOutcome({
    importedRows: 0,
    errorRows: 3
  });
  assert.equal(partial.jobStatus, "completed");
  assert.equal(partial.partialSuccess, true);
  assert.equal(allFailed.jobStatus, "failed");
  assert.equal(allFailed.partialSuccess, false);
  logPass("import partial execution outcome");

  const fallback = buildFallbackImportReviewSummary({
    importType: "customers",
    totalRows: 30,
    importedRows: 24,
    invalidRows: 3,
    duplicateRows: 2,
    mergedRows: 1,
    skippedRows: 2,
    commonErrors: ["owner_not_found x2", "invalid_date x1"]
  });
  assert.ok(fallback.summary.includes("customers import completed"));
  assert.ok(fallback.issues.some((item) => item.includes("failed validation")));
  assert.ok(fallback.recommended_next_steps.length > 0);
  logPass("import review fallback");
}
