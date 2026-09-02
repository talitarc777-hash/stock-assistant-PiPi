import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { tickerDisplayName } from "./tickerIdentity.js";

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
  assert.equal(tickerDisplayName({ ticker: "VOO" }), "");
  assert.equal(tickerDisplayName({ ticker: "VOO", ticker_name: "voo" }), "");
});

test("Dashboard and Virtual Trader share the ticker identity component", () => {
  assert.match(componentSource, /className="ticker-name"/);
  assert.match(dashboardSource, /<TickerIdentity/);
  assert.match(virtualTraderSource, /<TickerIdentity/);
});
