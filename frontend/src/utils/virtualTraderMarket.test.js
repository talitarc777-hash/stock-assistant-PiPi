import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const pageSource = readFileSync(new URL("../pages/VirtualTraderPage.jsx", import.meta.url), "utf8");
const apiSource = readFileSync(new URL("../api.js", import.meta.url), "utf8");
const styleSource = readFileSync(new URL("../styles.css", import.meta.url), "utf8");

test("Virtual Trader exposes one shared US/HK market interface", () => {
  assert.match(pageSource, /className="market-selector"/);
  assert.match(pageSource, /normalizeHkTickerInput/);
  assert.match(pageSource, /HK Virtual Trader/);
  assert.match(pageSource, /currencySymbol = market === "HK" \? "HK\$" : "\$"/);
  assert.match(pageSource, /runLiveVirtualTraderNow\(\s*profileId,\s*null,\s*AUTO_TRADING_MODEL,\s*market/s);
  assert.doesNotMatch(pageSource, /market === "HK" \? \[selectedTicker\] : null/);
  assert.match(pageSource, /fetchUserWatchlist\(profileId, "HK"\)/);
  assert.match(pageSource, /deactivateSelectedHkTicker/);
  assert.match(pageSource, /decisionModelText/);
  assert.match(pageSource, /Training pending/);
  assert.match(pageSource, /market === "HK" \? sortedRows : sortedRows\.slice\(0, 15\)/);
  assert.match(pageSource, /TickerHistorySummary/);
  assert.match(pageSource, /market=\{market\}/);
  assert.doesNotMatch(pageSource, /selectedMarket/);
});

test("market-aware API calls and narrow-screen controls remain wired", () => {
  assert.match(apiSource, /market = "US"/);
  assert.match(apiSource, /fetchAnalyze\(ticker, period = "5y", market = "US"\)/);
  assert.match(apiSource, /fetchForecast\(ticker, period = "2y", market = "US"\)/);
  assert.match(apiSource, /market=\$\{encodeURIComponent\(market\)\}/);
  assert.match(styleSource, /\.hk-ticker-control/);
  assert.match(styleSource, /@media \(max-width: 600px\)/);
});
