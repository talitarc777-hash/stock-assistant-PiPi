import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { tickerDisplayName, tickerDisplayNames } from "./tickerIdentity.js";

const componentSource = readFileSync(
  new URL("../components/TickerIdentity.jsx", import.meta.url),
  "utf8"
);
const dashboardSource = readFileSync(new URL("../App.jsx", import.meta.url), "utf8");
const virtualTraderSource = readFileSync(
  new URL("../pages/VirtualTraderPage.jsx", import.meta.url),
  "utf8"
);

test("ticker names resolve from direct US and nested HK metadata", () => {
  assert.equal(tickerDisplayName({ ticker: "AAPL", company_name: "Apple Inc." }), "Apple Inc.");
  assert.equal(
    tickerDisplayName({ ticker: "0700", metadata: { security_name: "TENCENT" } }),
    "TENCENT"
  );
});

test("missing names do not duplicate the ticker symbol", () => {
  assert.equal(tickerDisplayName({ ticker: "XYZ" }), "");
  assert.equal(tickerDisplayName({ ticker: "XYZ", ticker_name: "xyz" }), "");
});

test("ticker names follow English, Traditional Chinese, and bilingual modes", () => {
  const apple = { ticker: "AAPL", company_name: "Apple Inc." };
  assert.equal(tickerDisplayName(apple, "AAPL", "en"), "Apple Inc.");
  assert.equal(tickerDisplayName(apple, "AAPL", "zh"), "Apple Inc.");
  assert.equal(tickerDisplayName(apple, "AAPL", "both"), "Apple Inc.");

  const providerLocalized = {
    ticker: "0388",
    ticker_name_en: "Hong Kong Exchanges and Clearing Limited",
    ticker_name_zh: "香港交易及結算所有限公司",
  };
  assert.deepEqual(tickerDisplayNames(providerLocalized, "0388"), {
    en: "Hong Kong Exchanges and Clearing Limited",
    zh: "香港交易及結算所有限公司",
  });
  assert.equal(
    tickerDisplayName(providerLocalized, "0388", "both"),
    "Hong Kong Exchanges and Clearing Limited / 香港交易及結算所有限公司"
  );
});

test("an unavailable Chinese translation safely falls back to the provider English name", () => {
  assert.equal(
    tickerDisplayName({ ticker: "XYZ", ticker_name: "Example Holdings" }, "XYZ", "zh"),
    "Example Holdings"
  );
});

test("Dashboard and Virtual Trader share the ticker identity component", () => {
  assert.match(componentSource, /className="ticker-name"/);
  assert.match(dashboardSource, /<TickerIdentity/);
  assert.match(virtualTraderSource, /<TickerIdentity/);
});
