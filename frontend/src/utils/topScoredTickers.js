function finiteNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function normalizeTicker(value) {
  return String(value || "").trim().toUpperCase();
}

function timestampValue(value) {
  const parsed = Date.parse(value || "");
  return Number.isFinite(parsed) ? parsed : 0;
}

export function rankTopScoredTickers(decisions = [], watchlistRows = [], limit = 10) {
  const latestDecisions = new Map();

  for (const item of decisions) {
    const ticker = normalizeTicker(item?.ticker);
    const score = finiteNumber(item?.metadata?.overall_score);
    if (!ticker || score === null) continue;

    const candidate = {
      ticker,
      score,
      latestPrice: finiteNumber(item?.price),
      scoredAt: item?.timestamp || null,
      source: "market_scan",
      primary_ticker_class: item?.primary_ticker_class,
      stock_subclass: item?.stock_subclass,
      classification_source: item?.classification_source,
    };
    const current = latestDecisions.get(ticker);
    if (!current || timestampValue(candidate.scoredAt) > timestampValue(current.scoredAt)) {
      latestDecisions.set(ticker, candidate);
    }
  }

  // The watchlist analysis is fetched with the dashboard, so prefer it over a
  // stored market-scan score when both sources contain the same ticker.
  for (const item of watchlistRows) {
    const ticker = normalizeTicker(item?.ticker);
    const score = finiteNumber(item?.score_breakdown?.total_score);
    if (!ticker || score === null) continue;
    latestDecisions.set(ticker, {
      ticker,
      score,
      latestPrice: finiteNumber(item?.latest_close),
      scoredAt: null,
      source: "watchlist_refresh",
      primary_ticker_class: item?.primary_ticker_class,
      stock_subclass: item?.stock_subclass,
      classification_source: item?.classification_source,
    });
  }

  const safeLimit = Math.max(1, Number.parseInt(limit, 10) || 10);
  return Array.from(latestDecisions.values())
    .sort((left, right) => right.score - left.score || left.ticker.localeCompare(right.ticker))
    .slice(0, safeLimit);
}

export function rankTopScoredTickersByMarket(
  decisionsByMarket = {},
  watchlistRowsByMarket = {},
  limit = 10
) {
  return {
    US: rankTopScoredTickers(decisionsByMarket.US, watchlistRowsByMarket.US, limit),
    HK: rankTopScoredTickers(decisionsByMarket.HK, watchlistRowsByMarket.HK, limit),
  };
}
