import test from "node:test";
import assert from "node:assert/strict";

import { normalizeDeepSeekContent, parseDeepSeekJsonText } from "../lib/ai/providers/deepseek";

test("deepseek content normalization should merge array text parts", () => {
  const content = normalizeDeepSeekContent([{ text: "{\"a\":1" }, { text: ",\"b\":2}" }]);
  assert.equal(content, "{\"a\":1,\"b\":2}");
});

test("deepseek json parser should return object for valid JSON string", () => {
  const parsed = parseDeepSeekJsonText("{\"ok\":true}");
  assert.deepEqual(parsed, { ok: true });
  assert.equal(parseDeepSeekJsonText("invalid"), null);
});

