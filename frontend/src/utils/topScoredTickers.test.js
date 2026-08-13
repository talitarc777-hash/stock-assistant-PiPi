import assert from "node:assert/strict";
import test from "node:test";

import { rankTopScoredTickers, rankTopScoredTickersByMarket } from "./topScoredTickers.js";

test("ranks unique tickers by their newest market score", () => {
  const decisions = [
    { ticker: "AAA", price: 10, timestamp: "2026-08-01T10:00:00Z", metadata: { overall_score: 90 } },
    { ticker: "BBB", price: 20, timestamp: "2026-08-01T10:00:00Z", metadata: { overall_score: 80 } },
    { ticker: "AAA", price: 9, timestamp: "2026-07-31T10:00:00Z", metadata: { overall_score: 100 } },
  ];

  assert.deepEqual(
    rankTopScoredTickers(decisions, [], 10).map((row) => [row.ticker, row.score]),
    [["AAA", 90], ["BBB", 80]]
  );
});

test("prefers current watchlist scores and limits the result", () => {
  const decisions = [
    { ticker: "AAA", timestamp: "2026-08-01T10:00:00Z", metadata: { overall_score: 90 } },
    { ticker: "BBB", timestamp: "2026-08-01T10:00:00Z", metadata: { overall_score: 80 } },
  ];
  const watchlistRows = [
    { ticker: "AAA", latest_close: 12, score_breakdown: { total_score: 70 } },
    { ticker: "CCC", latest_close: 30, score_breakdown: { total_score: 95 } },
  ];

  const rows = rankTopScoredTickers(decisions, watchlistRows, 2);
  assert.deepEqual(rows.map((row) => [row.ticker, row.score]), [["CCC", 95], ["BBB", 80]]);
  assert.equal(rows[0].source, "watchlist_refresh");
});

test("ignores decisions without a real overall score", () => {
  const decisions = [
    { ticker: "AAA", metadata: { overall_score: null } },
    { ticker: "BBB", metadata: {} },
    { ticker: "CCC", metadata: { overall_score: 75 } },
  ];

  assert.deepEqual(rankTopScoredTickers(decisions).map((row) => row.ticker), ["CCC"]);
});

test("ranks US and HK scores independently without mixing market rows", () => {
  const ranked = rankTopScoredTickersByMarket(
    {
      US: [{ ticker: "AAPL", metadata: { overall_score: 82 } }],
      HK: [{ ticker: "0700", metadata: { overall_score: 91 } }],
    },
    {
      US: [{ ticker: "MSFT", score_breakdown: { total_score: 88 } }],
      HK: [],
    },
    10
  );

  assert.deepEqual(ranked.US.map((row) => row.ticker), ["MSFT", "AAPL"]);
  assert.deepEqual(ranked.HK.map((row) => row.ticker), ["0700"]);
});

test("ranks the complete HK analysis universe and retains a zero score", () => {
  const hkRows = [
    ["0005", 60],
    ["0700", 0],
    ["1810", 85],
    ["3690", 70],
    ["9988", 40],
  ].map(([ticker, score]) => ({
    ticker,
    latest_close: 100,
    score_breakdown: { total_score: score },
    primary_ticker_class: "stock",
  }));

  const ranked = rankTopScoredTickersByMarket(
    { US: [], HK: [] },
    { US: [], HK: hkRows },
    10
  );

  assert.deepEqual(
    ranked.HK.map((row) => [row.ticker, row.score]),
    [["1810", 85], ["3690", 70], ["0005", 60], ["9988", 40], ["0700", 0]]
  );
});
