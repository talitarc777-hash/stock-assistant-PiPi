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

test("backend-normalized HK classifications are used for arbitrary HK equities", () => {
  const classification = resolveTickerClassification({
    ticker: "1810",
    market: "HK",
    primary_ticker_class: "stock",
    stock_subclass: "unknown",
    classification_source: "market_data",
  });
  assert.equal(classification.primaryClass, "stock");
  assert.equal(classification.stockSubclass, "unknown");

  const html = renderToStaticMarkup(
    TickerClassificationTags({
      ticker: "1810",
      primaryClass: classification.primaryClass,
      stockSubclass: classification.stockSubclass,
      languageMode: "en",
    })
  );
  assert.match(html, />Stock</);
  assert.match(html, />Unknown</);
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
  const historySource = readFileSync(new URL("../components/TickerHistorySummary.jsx", import.meta.url), "utf8");
  const traderSource = readFileSync(new URL("../pages/VirtualTraderPage.jsx", import.meta.url), "utf8");
  const topScoreSource = readFileSync(new URL("../components/TopScoredTickersTable.jsx", import.meta.url), "utf8");
  const watchlistSource = readFileSync(new URL("../components/WatchlistTable.jsx", import.meta.url), "utf8");
  const holdingsSource = readFileSync(new URL("../components/HoldingsTable.jsx", import.meta.url), "utf8");
  const identitySource = readFileSync(new URL("../components/TickerIdentity.jsx", import.meta.url), "utf8");
  assert.match(appSource, /TickerIdentity/);
  assert.match(identitySource, /TickerClassificationTags/);
  assert.match(appSource, /TickerHistorySummary/);
  assert.match(historySource, /fetchLiveMarketSnapshot/);
  assert.equal(historySource.includes('"5D": { period: "5d"'), true);
  assert.equal(historySource.includes('"1W": { period: "7d"'), true);
  assert.match(historySource, /firstSentence/);
  assert.match(topScoreSource, /resolveTickerClassification/);
  assert.match(topScoreSource, /slice\(0, 10\)/);
  assert.match(topScoreSource, /role="tablist"/);
  assert.match(topScoreSource, /activeMarket/);
  assert.match(topScoreSource, /\["US", labelByMode/);
  assert.match(topScoreSource, /\["HK", labelByMode/);
  assert.match(appSource, /fetchLiveVirtualTraderTrades\(profileId, null, 200, "US"\)/);
  assert.match(appSource, /fetchDashboardTopScores\(profileId, "HK", "all", DEFAULT_PERIOD, 200\)/);
  assert.match(appSource, /fetchAnalyze\(ticker, DEFAULT_PERIOD, market\)/);
  assert.match(appSource, /fetchChartData\(ticker, DEFAULT_PERIOD, market\)/);
  assert.match(appSource, /fetchForecast\(ticker, "2y", market\)/);
  assert.match(appSource, /market=\{selectedMarket\}/);
  assert.match(appSource, /rankTopScoredTickersByMarket/);
  assert.match(traderSource, /TickerIdentity/);
  assert.match(watchlistSource, /TickerIdentity/);
  assert.match(watchlistSource, /onClick=\{\(\) => onSelectTicker/);
  assert.match(holdingsSource, /TickerIdentity/);
  assert.match(holdingsSource, /onClick=\{\(\) => openHoldingSummary/);
});
