import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { renderToStaticMarkup } from "react-dom/server";

import TickerClassificationTags from "../components/TickerClassificationTags.js";
import {
  matchesTickerClassificationFilters,
  normalizeTickerSymbol,
  resolveTickerClassification,
} from "./tickerClassification.js";

test("manual overrides provide the primary and stock-subclass labels", () => {
  const classification = resolveTickerClassification("aapl");
  assert.equal(classification.primaryClass, "stock");
  assert.equal(classification.stockSubclass, "technology");

  const html = renderToStaticMarkup(
    TickerClassificationTags({ ticker: "AAPL", languageMode: "en" })
  );
  assert.match(html, />Stock</);
  assert.match(html, />Technology</);
});

test("non-stock assets never render a stock-subclass badge", () => {
  const html = renderToStaticMarkup(
    TickerClassificationTags({
      ticker: "SPY",
      primaryClass: "etf",
      stockSubclass: "technology",
    })
  );
  assert.match(html, />ETF</);
  assert.doesNotMatch(html, /Technology/);
});

test("unknown is the safe fallback and uncertain symbols are not called stocks", () => {
  const resolved = resolveTickerClassification("UNLISTED123");
  assert.equal(resolved.primaryClass, "unknown");
  assert.equal(resolved.stockSubclass, null);
  assert.match(
    renderToStaticMarkup(TickerClassificationTags({ ticker: "UNLISTED123" })),
    />Unknown</
  );
});

test("normalizes HK, class-share, forex, crypto and index symbols", () => {
  assert.equal(normalizeTickerSymbol("700.hk"), "0700.HK");
  assert.equal(normalizeTickerSymbol("brk.b"), "BRK-B");
  assert.equal(normalizeTickerSymbol("eur/usd"), "EURUSD=X");
  assert.equal(normalizeTickerSymbol("btc/usd"), "BTC-USD");
  assert.equal(resolveTickerClassification("^gspc").primaryClass, "index");
  assert.equal(resolveTickerClassification("gc=f").primaryClass, "derivative");
});

test("classification filters combine primary class and stock subclass", () => {
  assert.equal(matchesTickerClassificationFilters({ ticker: "AAPL" }, "stock", "technology"), true);
  assert.equal(matchesTickerClassificationFilters({ ticker: "AAPL" }, "etf", "all"), false);
  assert.equal(matchesTickerClassificationFilters({ ticker: "SPY" }, "all", "technology"), false);
});

test("tag styles include mobile wrapping and dark-theme contrast overrides", () => {
  const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8");
  assert.match(css, /\.ticker-classification-tags[\s\S]*?flex-wrap:\s*wrap/);
  assert.match(css, /@media \(prefers-color-scheme:\s*dark\)[\s\S]*?--ticker-tag-blue-bg/);
});

test("Dashboard and Virtual Trader wire the same reusable component without removing actions", () => {
  const appSource = readFileSync(new URL("../App.jsx", import.meta.url), "utf8");
  const traderSource = readFileSync(new URL("../pages/VirtualTraderPage.jsx", import.meta.url), "utf8");
  const watchlistSource = readFileSync(new URL("../components/WatchlistTable.jsx", import.meta.url), "utf8");
  const holdingsSource = readFileSync(new URL("../components/HoldingsTable.jsx", import.meta.url), "utf8");
  assert.match(appSource, /TickerClassificationTags/);
  assert.match(traderSource, /TickerClassificationTags/);
  assert.match(watchlistSource, /TickerClassificationTags/);
  assert.match(watchlistSource, /onClick=\{\(\) => onSelectTicker/);
  assert.match(holdingsSource, /TickerClassificationTags/);
  assert.match(holdingsSource, /onClick=\{\(\) => openHoldingSummary/);
});
