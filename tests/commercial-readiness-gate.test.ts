import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

import { validateCommercialReadinessChecklist } from "../lib/commercial-readiness-gate";

export function runCommercialReadinessGateTests(logPass: (name: string) => void): void {
  const checklistPath = path.join(process.cwd(), "docs", "moy-commercial-readiness-checklist-v1.md");
  const markdown = fs.readFileSync(checklistPath, "utf8");
  const result = validateCommercialReadinessChecklist(markdown);

  assert.equal(result.missingGateIds.length, 0, `Missing gate IDs: ${result.missingGateIds.join(", ")}`);
  assert.equal(result.missingGateKeys.length, 0, `Missing gate keys: ${result.missingGateKeys.join(", ")}`);
  assert.equal(
    result.missingRequiredSections.length,
    0,
    `Missing checklist sections: ${result.missingRequiredSections.join(", ")}`
  );
  logPass("commercial readiness checklist gate coverage");
}
