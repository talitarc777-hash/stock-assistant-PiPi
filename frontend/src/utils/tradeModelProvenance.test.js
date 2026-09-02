import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { modelUsedText } from "./tradeModelProvenance.js";

const recentTradesSource = readFileSync(
  new URL("../components/RecentTradesTable.jsx", import.meta.url),
  "utf8"
);

test("recent trades render the shared Model used column", () => {
  assert.match(recentTradesSource, /"Model used"/);
  assert.match(recentTradesSource, /modelUsedText\(trade, languageMode\)/);
  assert.match(recentTradesSource, /colSpan=\{8\}/);
});

test("account-ledger model metadata is displayed with its training period", () => {
  const row = {
    metadata: {
      model_name: "random_forest",
      model_period: "2y",
      decision_source: "production_model",
    },
  };

  assert.equal(modelUsedText(row, "en"), "random_forest (2y)");
});

test("fallback executions are never presented as an ML model", () => {
  const row = {
    metadata: {
      model_name: "auto_best",
      model_selector: "auto_best",
      decision_source: "fallback_rule",
    },
  };

  assert.equal(modelUsedText(row, "en"), "Backup rules");
  assert.equal(modelUsedText(row, "zh"), "\u5f8c\u5099\u898f\u5247");
});

test("actual model name has priority and missing legacy provenance is explicit", () => {
  assert.equal(
    modelUsedText({
      model_name: "auto_best",
      metadata: {
        actual_model_name: "linear_regression",
        model_period: "5y",
        decision_source: "production_model",
      },
    }, "en"),
    "linear_regression (5y)"
  );
  assert.equal(modelUsedText({}, "en"), "Not recorded");
});
