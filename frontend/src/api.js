import { requestJson } from "./services/httpClient";

async function fetchJson(path, options = {}) {
  return requestJson(path, {
    method: "GET",
    ...options,
  });
}

async function postJson(path, body, options = {}) {
  return requestJson(path, {
    method: "POST",
    body: body || {},
    retries: 0,
    ...options,
  });
}

export async function fetchWatchlistAnalyze(tickers, period = "5y") {
  const joined = encodeURIComponent(tickers.join(","));
  // Batch analysis can be slow on cold-start (multiple tickers + indicator computation).
  return fetchJson(`/watchlist-analyze?tickers=${joined}&period=${period}`, { timeoutMs: 45000, retries: 2 });
}

export async function fetchAnalyze(ticker, period = "5y") {
  return fetchJson(`/analyze?ticker=${encodeURIComponent(ticker)}&period=${period}`, { timeoutMs: 14000 });
}

export async function fetchChartData(ticker, period = "5y", market = "US") {
  return fetchJson(`/chart-data?ticker=${encodeURIComponent(ticker)}&period=${period}&market=${encodeURIComponent(market)}`, {
    timeoutMs: 18000,
    retries: 1,
  });
}

export async function fetchLiveMarketSnapshot(ticker, period = "3mo", market = "US") {
  return fetchJson(
    `/market-data/live-snapshot?ticker=${encodeURIComponent(ticker)}&period=${encodeURIComponent(period)}&market=${encodeURIComponent(market)}`,
    { timeoutMs: 18000, retries: 1 }
  );
}

export async function fetchForecast(ticker, period = "2y") {
  return fetchJson(`/forecast?ticker=${encodeURIComponent(ticker)}&period=${period}`, { timeoutMs: 14000 });
}

export async function fetchVirtualTraderSummary(
  ticker,
  period = "5y",
  modelName = null,
  equityLimit = 300,
  userId = null
) {
  const userQuery = userId ? `&user_id=${encodeURIComponent(userId)}` : "";
  const modelQuery = modelName ? `&model_name=${encodeURIComponent(modelName)}` : "";
  return fetchJson(
    `/virtual-trader-summary?ticker=${encodeURIComponent(ticker)}&period=${period}${modelQuery}&equity_limit=${equityLimit}${userQuery}`
  );
}

export async function fetchVirtualTraderTrades(
  ticker,
  period = "5y",
  modelName = null,
  limit = 120,
  userId = null
) {
  const userQuery = userId ? `&user_id=${encodeURIComponent(userId)}` : "";
  const modelQuery = modelName ? `&model_name=${encodeURIComponent(modelName)}` : "";
  return fetchJson(
    `/virtual-trader-trades?ticker=${encodeURIComponent(ticker)}&period=${period}${modelQuery}&limit=${limit}${userQuery}`
  );
}

export async function fetchLiveVirtualTraderStatus(
  userId,
  ticker = null,
  modelName = null,
  autoRun = false,
  market = "US"
) {
  const tickerQuery = ticker ? `&ticker=${encodeURIComponent(ticker)}` : "";
  const modelQuery = modelName ? `&model_name=${encodeURIComponent(modelName)}` : "";
  return fetchJson(
    `/virtual-trader/live-status?user_id=${encodeURIComponent(userId)}${tickerQuery}${modelQuery}&auto_run=${autoRun ? "true" : "false"}&market=${encodeURIComponent(market)}`,
    { timeoutMs: 15000, retries: 1 }
  );
}

export async function runLiveVirtualTraderNow(userId, tickers = null, modelName = null, market = "US") {
  return postJson("/virtual-trader/run-now", {
    user_id: userId,
    tickers,
    model_name: modelName,
    market,
  });
}

export async function fetchLiveVirtualTraderTrades(userId, ticker = null, limit = 50, market = "US") {
  const tickerQuery = ticker ? `&ticker=${encodeURIComponent(ticker)}` : "";
  return fetchJson(
    `/virtual-trader/live-trades?user_id=${encodeURIComponent(userId)}${tickerQuery}&limit=${limit}&market=${encodeURIComponent(market)}`,
    { timeoutMs: 15000, retries: 1 }
  );
}

export async function fetchTraderSchedulerStatus(recentHours = 24) {
  return fetchJson(
    `/virtual-trader/scheduler-status?recent_hours=${encodeURIComponent(recentHours)}`,
    { timeoutMs: 10000, retries: 1 }
  );
}

export async function fetchNewsSentimentLatest(ticker, period = "6mo") {
  return fetchJson(`/news-sentiment/latest?ticker=${encodeURIComponent(ticker)}&period=${period}`);
}

export async function fetchLiveVirtualTraderSync(userId, recentTradeLimit = 20, decisionLimit = 100, market = "US") {
  return fetchJson(
    `/virtual-trader/live-sync?user_id=${encodeURIComponent(userId)}`
      + `&recent_trade_limit=${encodeURIComponent(recentTradeLimit)}`
      + `&decision_limit=${encodeURIComponent(decisionLimit)}`
      + `&market=${encodeURIComponent(market)}`,
    { timeoutMs: 15000, retries: 1 }
  );
}

export async function fetchVirtualAccountHistory(userId, limit = 120, offset = 0, market = "US") {
  return fetchJson(
    `/virtual-account/history?user_id=${encodeURIComponent(userId)}&limit=${limit}&offset=${offset}&market=${encodeURIComponent(market)}`,
    { timeoutMs: 14000, retries: 1 }
  );
}

export async function fetchVirtualAccountRecentTrades(userId, limit = 20, market = "US") {
  return fetchJson(`/virtual-account/recent-trades?user_id=${encodeURIComponent(userId)}&limit=${limit}&market=${encodeURIComponent(market)}`, {
    timeoutMs: 12000,
    retries: 1,
  });
}

export async function postVirtualAccountDeposit(userId, amount, reason = "", market = "US") {
  return postJson("/virtual-account/deposit", {
    user_id: userId,
    amount,
    reason,
    source: "web",
    market,
  });
}

export async function postVirtualAccountWithdraw(userId, amount, reason = "", market = "US") {
  return postJson("/virtual-account/withdraw", {
    user_id: userId,
    amount,
    reason,
    source: "web",
    market,
  });
}

export async function postVirtualAccountReset(userId, resetMonthlyContributions = true, market = "US") {
  return postJson("/virtual-account/reset", {
    user_id: userId,
    confirm_reset: true,
    reset_monthly_contributions: Boolean(resetMonthlyContributions),
    market,
  });
}

export async function postVirtualTradingActivityReset(userId, market = "US") {
  return postJson("/virtual-account/reset-trading-activity", {
    user_id: userId,
    confirm_reset: true,
    market,
  });
}

export async function fetchModelLifecycleStatus(
  ticker = "VOO",
  period = "5y",
  targetName = "target_5d_updown",
  logLimit = 8,
  market = "US"
) {
  return fetchJson(
    `/model-lifecycle/status?ticker=${encodeURIComponent(ticker)}&period=${encodeURIComponent(
      period
    )}&target_name=${encodeURIComponent(targetName)}&log_limit=${encodeURIComponent(
      logLimit
    )}&market=${encodeURIComponent(market)}`
  );
}

export async function fetchModelLifecycleRegistry(limit = 200, filters = {}) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (filters.ticker) params.set("ticker", filters.ticker);
  if (filters.period) params.set("period", filters.period);
  if (filters.targetName) params.set("target_name", filters.targetName);
  if (filters.market) params.set("market", filters.market);
  return fetchJson(`/model-lifecycle/registry?${params.toString()}`);
}

export async function fetchModelLifecycleRuns(limit = 20) {
  return fetchJson(`/model-lifecycle/runs?limit=${encodeURIComponent(limit)}`);
}

export async function fetchModelImprovementStatus() {
  return fetchJson("/model-lifecycle/improvement-status");
}

export async function runModelLifecycleNow(
  workflowType = "daily_incremental",
  triggerReason = "manual_trigger",
  tickers = null,
  market = "US"
) {
  return postJson("/model-lifecycle/run-now", {
    workflow_type: workflowType,
    trigger_reason: triggerReason,
    tickers,
    market,
  });
}
