import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import path from "node:path";

import { buildFallbackMobileBriefCompactSummary, buildFallbackMobileQuickCaptureRefine } from "../lib/mobile-fallback";
import { clipMobileList, buildMobilePriorityPreview } from "../lib/mobile-summary";
import { listLocalMobileDrafts } from "../lib/mobile-local-drafts";

export function runMobileLayerTests(logPass: (name: string) => void): void {
  const quick = buildFallbackMobileQuickCaptureRefine({
    rawInput: "今天电话沟通，客户希望下周拿到报价。",
    hasCustomerContext: false
  });
  assert.equal(quick.should_save_as_draft_only, true);
  assert.ok(quick.next_best_fields_to_fill.length > 0);
  logPass("mobile quick capture fallback");

  const compact = buildFallbackMobileBriefCompactSummary({
    focusTheme: "优先处理高风险客户",
    topPriorities: ["A", "B", "C"],
    urgentRisks: ["R1", "R2"]
  });
  assert.ok(compact.compact_headline.includes("优先"));
  assert.equal(compact.top_priorities.length, 3);
  logPass("mobile compact brief fallback");

  const clipped = clipMobileList(["  A ", "", "B", "C", "D"], 3);
  assert.deepEqual(clipped, ["A", "B", "C"]);
  const preview = buildMobilePriorityPreview(
    [{ title: "T1" }, { title: "T2" }, { title: "T3" }, { title: "T4" }],
    2
  );
  assert.deepEqual(preview, ["T1", "T2"]);
  logPass("mobile summary data clipping");

  const noWindowDrafts = listLocalMobileDrafts();
  assert.deepEqual(noWindowDrafts, []);
  logPass("offline draft local utility in non-browser");

  const manifestExists = existsSync(path.resolve(process.cwd(), "app/manifest.ts"));
  const swExists = existsSync(path.resolve(process.cwd(), "public/sw.js"));
  assert.equal(manifestExists, true);
  assert.equal(swExists, true);
  logPass("pwa manifest and service worker presence");
}
