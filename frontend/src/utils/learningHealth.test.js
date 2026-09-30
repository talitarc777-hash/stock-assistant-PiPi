import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { learningReason, percentOrMissing } from "./learningHealth.js";

test("learning health distinguishes missing evidence from success and supports Traditional Chinese", () => {
  assert.equal(percentOrMissing(null), "N/A");
  assert.equal(percentOrMissing(0), "0.0%");
  assert.match(learningReason("no_recent_usable_context", "zh"), /沒有可用資訊/);
  assert.match(learningReason("missing_credentials", "en"), /Configuration missing/);
});

test("lifecycle includes operational status, bounded scrolling and non-promoting interim warnings", () => {
  const panel = fs.readFileSync(new URL("../components/LearningHealthPanel.jsx", import.meta.url), "utf8");
  const page = fs.readFileSync(new URL("../pages/ModelLifecyclePage.jsx", import.meta.url), "utf8");
  assert.match(page, /<LearningHealthPanel languageMode=/);
  assert.match(panel, /document.hidden/);
  assert.match(panel, /clearInterval/);
  assert.match(panel, /no tuning, early success claim or promotion/);
  assert.match(panel, /role="alert"/);
  assert.match(panel, /learning-attempts/);
});
