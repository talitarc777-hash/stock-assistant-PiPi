import assert from "node:assert/strict";
import test from "node:test";

import { marketDataReasonText, marketRegimeGuide } from "./decisionExplanations.js";

test("stress regime explains the risk and trading effect without internal codes", () => {
  const guide = marketRegimeGuide({
    level: "stress",
    new_position_allowed: false,
    position_size_multiplier: 0,
    reasons: ["benchmark_20d_selloff", "extreme_volatility"],
  }, "en");

  assert.equal(guide.label, "High-risk market conditions");
  assert.match(guide.effect, /buying is paused/i);
  assert.match(guide.reasons, /wider market fell sharply/i);
  assert.doesNotMatch(guide.reasons, /benchmark_20d_selloff/);
});

test("caution regime explains reduced sizing in Chinese", () => {
  const guide = marketRegimeGuide({
    level: "caution",
    new_position_allowed: true,
    position_size_multiplier: 0.5,
    reasons: ["ticker_drawdown"],
  }, "zh");

  assert.match(guide.effect, /50%/);
  assert.match(guide.reasons, /近期高位回落/);
  assert.doesNotMatch(guide.reasons, /ticker_drawdown/);
});

test("market-data reason translates internal validation codes", () => {
  assert.equal(
    marketDataReasonText("same_day_close_mismatch", "en"),
    "two sources report different closing prices",
  );
  assert.equal(
    marketDataReasonText("price_timestamp_missing", "zh"),
    "缺少價格時間",
  );
});
